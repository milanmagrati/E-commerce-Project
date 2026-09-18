import logging
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from store.models import Order
from .models import WooCommerceOrder

logger = logging.getLogger('integrations')
User = get_user_model()

# Map WooCommerce order statuses to internal Order statuses.
# Beyond WooCommerce's seven core statuses this store also uses custom ones
# registered by plugins/themes (`delivered`, `shipped`) - they arrive over the
# API with the `wc-` prefix already stripped, same as the core ones.
WOO_STATUS_MAP = {
    'pending': 'pending',
    'processing': 'confirmed',
    'on-hold': 'pending',
    'completed': 'delivered',
    'cancelled': 'cancelled',
    'refunded': 'cancelled',
    'failed': 'cancelled',
    'delivered': 'delivered',
    'shipped': 'shipped',
}


def parse_woo_datetime(raw: dict):
    """Pull the order's real placement time out of a WooCommerce payload.

    WooCommerce sends `date_created_gmt` (UTC, no offset) alongside
    `date_created` (the shop's local wall clock, also unmarked). Prefer the GMT
    one and stamp UTC onto it; fall back to the local field only if it's
    missing, since with USE_TZ a naive value would otherwise be read as
    Asia/Kathmandu and land the order ~5h45m off.
    """
    value = raw.get('date_created_gmt') or raw.get('date_created')
    if not value:
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def get_woocommerce_system_user():
    """Service account attributed to orders synced from WooCommerce (webhook or
    poll) - neither path carries a real Django session, so there's no real
    user to attribute the order to. Mirrors ncm_webhook_system in
    ncm/webhook_handler.py."""
    user, created = User.objects.get_or_create(
        username='woocommerce_webhook_system',
        defaults={
            'email': 'woocommerce-webhook@system.local',
            'first_name': 'WooCommerce',
            'last_name': 'Webhook System',
            'is_active': True,
        },
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
        logger.info("Created system user for WooCommerce sync operations")
    return user


def upsert_woocommerce_order(*, woo_order_id, status, currency, total: Decimal,
                              billing: dict, shipping: dict, line_items: list,
                              raw_payload: dict, sync_source: str,
                              woo_date_created=None):
    """Upsert one WooCommerce order into store.Order + WooCommerceOrder.

    Shared by the webhook receiver and the REST API poller - both normalize
    their differently-shaped source payload into these same keyword args
    first, so an order landing via either path converges on the same rows
    (keyed on woo_order_id / order_number).
    """
    first_name = billing.get('first_name', '')
    last_name = billing.get('last_name', '')
    customer_name = f'{first_name} {last_name}'.strip()

    internal_status = WOO_STATUS_MAP.get(status, 'pending')

    shipping_addr_parts = [
        shipping.get('address_1') or billing.get('address_1', ''),
        shipping.get('city') or billing.get('city', ''),
    ]
    shipping_address = ', '.join(p for p in shipping_addr_parts if p) or 'N/A'

    order, order_created = Order.objects.update_or_create(
        order_number=f'WOO-{woo_order_id}',
        defaults={
            'user': get_woocommerce_system_user(),
            'full_name': customer_name,
            'order_type': 'confirmed',
            'status': internal_status,
            'total_price': total,
            'shipping_address': shipping_address,
            'phone': billing.get('phone', ''),
            'email': billing.get('email', ''),
            'city': shipping.get('city') or billing.get('city', ''),
            'province': shipping.get('state') or billing.get('state', ''),
        },
    )

    defaults = {
        'order': order,
        'status': status,
        'currency': currency,
        'total': total,
        'customer_name': customer_name,
        'customer_email': billing.get('email', ''),
        'billing_phone': billing.get('phone', ''),
        'billing_data': billing,
        'shipping_data': shipping,
        'line_items_json': line_items,
        'raw_payload': raw_payload,
        'sync_source': sync_source,
    }

    # Only write the order date when this payload actually carries one - a
    # webhook sender that omits it must not blank out a date an earlier poll
    # already resolved.
    placed_at = woo_date_created or parse_woo_datetime(raw_payload)
    if placed_at is not None:
        defaults['woo_date_created'] = placed_at

    woo_obj, woo_created = WooCommerceOrder.objects.update_or_create(
        woo_order_id=woo_order_id,
        defaults=defaults,
    )

    action = 'created' if woo_created else 'updated'
    logger.info('WooCommerce order %s %s via %s (internal order: %s)',
                woo_order_id, action, sync_source, order.order_number)

    return order, woo_obj, woo_created


def ingest_polled_order(raw: dict):
    """Normalize one order object from WooCommerce's REST API
    (GET /wp-json/wc/v3/orders) and upsert it. Field names here are
    WooCommerce's actual REST resource fields (`id`, `billing`, `shipping`,
    `line_items`, ...) - distinct from the webhook receiver's serializer,
    which expects whatever shape the webhook sender posts."""
    try:
        total = Decimal(str(raw.get('total') or '0'))
    except (InvalidOperation, ValueError):
        total = Decimal('0.00')

    return upsert_woocommerce_order(
        woo_order_id=raw['id'],
        status=raw.get('status', 'pending'),
        currency=raw.get('currency', 'NPR'),
        total=total,
        billing=raw.get('billing') or {},
        shipping=raw.get('shipping') or {},
        line_items=raw.get('line_items', []),
        raw_payload=raw,
        sync_source='woocommerce_api_poll',
    )
