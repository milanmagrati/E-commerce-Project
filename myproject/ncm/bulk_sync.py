# ncm/bulk_sync.py
"""
Shared bulk NCM status-sync logic, used by both the interactive
"Bulk Sync" view (ncm/views.py:bulk_sync_ncm_orders) and the
sync_all_ncm_orders management command (run via cPanel cron, since this
project's shared hosting doesn't support a persistent Celery worker/beat).
"""

import logging

from django.utils import timezone

from dashboard.models import Order, OrderActivityLog, APISettings
from services.ncm_service import NCMService

logger = logging.getLogger('ncm')
ncm_service = NCMService()

DEFAULT_TERMINAL_STATUSES = [
    'cancelled', 'delivered', 'return', 'returned',
    'return_initiated', 'return_approved',
]

#: Statuses a remote NCM status can never overwrite. A cancellation is a
#: local staff decision, and NCM can report a stale/late status long after
#: it was made - the single-order sync endpoint and the webhook handler both
#: refuse cancelled orders for the same reason, so bulk sync must too.
#: This is enforced regardless of how orders were selected, because
#: `bulk_sync_included_statuses` is admin-configurable (Settings -> API Sync
#: Settings) and could otherwise be set to include 'cancelled'.
PROTECTED_STATUSES = ('cancelled',)

#: NCM caps how many order IDs one bulk status request can carry, and long
#: URIs/timeouts start biting well before that - keep requests chunked.
CHUNK_SIZE = 100


def run_bulk_ncm_status_sync(user=None, order_ids=None):
    """
    Sync NCM status for all active NCM orders.

    When `order_ids` is given (an explicit selection from the interactive
    "Bulk Sync" UI), only those orders are synced. Otherwise, candidate
    orders are filtered by APISettings.bulk_sync_included_statuses when
    configured (admin-selected via Settings -> API Sync Settings);
    otherwise falls back to excluding DEFAULT_TERMINAL_STATUSES, matching
    this feature's original hardcoded behavior.

    A failure on one order (or one chunk) is recorded and skipped rather than
    aborting the run - this is driven by cron, so one malformed NCM payload
    must not stop every remaining order from syncing.

    Returns a summary dict: {'total_orders', 'updated_count', 'errors'}.
    """
    ncm_orders = Order.objects.filter(
        ncm_order_id__isnull=False,
        is_deleted=False,
    ).exclude(status__in=PROTECTED_STATUSES)

    if order_ids:
        ncm_orders = ncm_orders.filter(id__in=order_ids)
    else:
        included_statuses = APISettings.get_settings().bulk_sync_included_statuses
        if included_statuses:
            ncm_orders = ncm_orders.filter(status__in=included_statuses)
        else:
            ncm_orders = ncm_orders.exclude(status__in=DEFAULT_TERMINAL_STATUSES)

    summary = {'total_orders': 0, 'updated_count': 0, 'errors': []}

    # Materialize once - the loop below needs the objects anyway, so a
    # separate .exists() probe would just be an extra query.
    orders = list(ncm_orders)
    if not orders:
        return summary

    # Group orders by api_config_id to ensure correct credentials are used for each batch
    orders_by_config = {}
    for order in orders:
        orders_by_config.setdefault(order.api_config_id, []).append(order)

    updated_count = 0
    errors = []

    for config_id, group_orders in orders_by_config.items():
        svc = NCMService(api_config_id=config_id) if config_id else ncm_service
        config_label = config_id or 'default'

        for i in range(0, len(group_orders), CHUNK_SIZE):
            chunk_orders = group_orders[i:i + CHUNK_SIZE]
            chunk_ids = [str(o.ncm_order_id) for o in chunk_orders]
            chunk_label = f"config {config_label} (chunk {i // CHUNK_SIZE + 1})"

            try:
                result = svc.get_bulk_order_statuses(chunk_ids)
            except Exception as e:
                errors.append(f"Failed {chunk_label}: {e}")
                logger.exception(f"Bulk sync request failed for {chunk_label}")
                continue

            if not result.get('success'):
                errors.append(f"Failed {chunk_label}: {result.get('error')}")
                continue

            status_data = (result.get('data') or {})
            status_data = status_data.get('result') if isinstance(status_data, dict) else None
            if not isinstance(status_data, dict):
                errors.append(f"Failed {chunk_label}: unexpected response shape from NCM")
                continue

            for order in chunk_orders:
                raw_status = status_data.get(str(order.ncm_order_id))
                if raw_status is None:
                    continue
                try:
                    if _sync_one_order(svc, order, raw_status, user):
                        updated_count += 1
                except Exception as e:
                    errors.append(f"Order {order.order_number}: {e}")
                    logger.exception(f"Bulk sync failed for order {order.order_number}")

    summary['total_orders'] = len(orders)
    summary['updated_count'] = updated_count
    summary['errors'] = errors

    if updated_count or errors:
        logger.info(
            f"Bulk sync: {updated_count}/{len(orders)} active orders updated, {len(errors)} error(s)"
        )

    return summary


def _sync_one_order(svc, order, raw_status, user):
    """Resolve one order's NCM status and persist it if it changed.

    Returns True when the order was updated, False when nothing changed.
    """
    old_ncm_status = order.ncm_status
    old_system_status = order.status
    old_payment_status = order.payment_status

    # The bulk endpoint returns only a status string, which can't distinguish
    # a real delivery from an RTV "delivered back to vendor" - that needs the
    # vendor_return flag, so re-fetch the full entry for 'Delivered' only.
    if isinstance(raw_status, str) and raw_status == 'Delivered':
        detail_result = svc.get_order_status(order.ncm_order_id)
        if detail_result.get('success') and detail_result.get('data'):
            data = detail_result['data']
            entry = data[0] if isinstance(data, list) else data
            if isinstance(entry, dict):
                system_status, payment_status = svc.resolve_delivered_status(entry)
                new_status = entry.get('status') or entry.get('Status') or raw_status
            else:
                system_status = svc.map_ncm_status_to_system(raw_status)
                payment_status = None
                new_status = raw_status
        else:
            system_status = svc.map_ncm_status_to_system(raw_status)
            payment_status = None
            new_status = raw_status
    elif isinstance(raw_status, dict):
        system_status, payment_status = svc.resolve_delivered_status(raw_status)
        new_status = raw_status.get('status') or raw_status.get('Status') or ''
    else:
        new_status = raw_status
        system_status = svc.map_ncm_status_to_system(new_status)
        payment_status = None

    if order.ncm_status == new_status and order.status == system_status:
        return False

    order.ncm_status = new_status
    update_fields = svc.sync_order_status_fields(order, system_status, payment_status)
    update_fields.extend(['ncm_status', 'updated_at'])

    if system_status == 'delivered' and not order.delivered_at:
        order.delivered_at = timezone.now()
        update_fields.append('delivered_at')

    order.save(update_fields=list(dict.fromkeys(update_fields)))

    OrderActivityLog.objects.create(
        order=order,
        action_type='status_changed',
        user=user,
        field_name='ncm_status',
        old_value=old_ncm_status,
        new_value=new_status,
        description=f'Bulk sync: {old_system_status} → {system_status}'
                    + (f', payment: {old_payment_status} → {payment_status}' if payment_status else '')
    )
    return True
