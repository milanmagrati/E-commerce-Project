import logging
import hmac
import hashlib
import base64
from decimal import Decimal, InvalidOperation

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from store.models import Order
from .models import WooCommerceOrder
from .serializers import WooCommerceOrderSerializer

logger = logging.getLogger('integrations')

# Map WooCommerce statuses to internal Order statuses
WOO_STATUS_MAP = {
    'pending': 'pending',
    'processing': 'confirmed',
    'on-hold': 'pending',
    'completed': 'delivered',
    'cancelled': 'cancelled',
    'refunded': 'cancelled',
    'failed': 'cancelled',
}


class WooCommerceOrderReceiveView(APIView):
    # We remove TokenAuthentication because WooCommerce uses HMAC signatures
    authentication_classes = []
    permission_classes = []

    def verify_webhook_signature(self, request):
        secret = getattr(settings, 'WOOCOMMERCE_WEBHOOK_SECRET', '')
        if not secret:
            logger.error("WOOCOMMERCE_WEBHOOK_SECRET is not set in settings")
            return False

        header_signature = request.headers.get('x-wc-webhook-signature')
        if not header_signature:
            logger.warning("Missing x-wc-webhook-signature header")
            return False

        payload = request.body
        expected_signature = base64.b64encode(
            hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).digest()
        ).decode('utf-8')

        if not hmac.compare_digest(expected_signature, header_signature):
            logger.warning("Invalid WooCommerce webhook signature")
            return False

        return True

    def post(self, request):
        if not self.verify_webhook_signature(request):
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            serializer = WooCommerceOrderSerializer(data=request.data)
            if not serializer.is_valid():
                logger.warning('WooCommerce order validation failed: %s', serializer.errors)
                return Response(
                    {'error': 'Validation failed', 'details': serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            data = serializer.validated_data
            woo_order_id = data['order_id']
            billing = data.get('billing', {})
            shipping = data.get('shipping', {})
            line_items = data.get('line_items', [])

            first_name = billing.get('first_name', '')
            last_name = billing.get('last_name', '')
            customer_name = f'{first_name} {last_name}'.strip()

            try:
                total_decimal = Decimal(str(data['total']))
            except (InvalidOperation, ValueError):
                total_decimal = Decimal('0.00')

            internal_status = WOO_STATUS_MAP.get(data['status'], 'pending')

            # Build shipping address from billing/shipping data
            shipping_addr_parts = [
                shipping.get('address_1') or billing.get('address_1', ''),
                shipping.get('city') or billing.get('city', ''),
            ]
            shipping_address = ', '.join(p for p in shipping_addr_parts if p) or 'N/A'

            # Upsert the local Order (store app)
            order, order_created = Order.objects.update_or_create(
                order_number=f'WOO-{woo_order_id}',
                defaults={
                    'user': request.user,
                    'full_name': customer_name,
                    'order_type': 'confirmed',
                    'status': internal_status,
                    'total_price': total_decimal,
                    'shipping_address': shipping_address,
                    'phone': billing.get('phone', ''),
                    'email': billing.get('email', ''),
                    'city': shipping.get('city') or billing.get('city', ''),
                    'province': shipping.get('state') or billing.get('state', ''),
                },
            )

            # Upsert the WooCommerce tracking record
            woo_obj, woo_created = WooCommerceOrder.objects.update_or_create(
                woo_order_id=woo_order_id,
                defaults={
                    'order': order,
                    'status': data['status'],
                    'currency': data.get('currency', 'NPR'),
                    'total': total_decimal,
                    'customer_name': customer_name,
                    'customer_email': billing.get('email', ''),
                    'billing_phone': billing.get('phone', ''),
                    'billing_data': billing,
                    'shipping_data': shipping,
                    'line_items_json': line_items,
                    'raw_payload': request.data,
                    'sync_source': 'woocommerce_plugin',
                },
            )

            action = 'created' if woo_created else 'updated'
            logger.info('WooCommerce order %s %s (internal order: %s)', woo_order_id, action, order.order_number)

            return Response(
                {
                    'status': action,
                    'woo_order_id': woo_order_id,
                    'internal_order_number': order.order_number,
                },
                status=status.HTTP_201_CREATED if woo_created else status.HTTP_200_OK,
            )

        except Exception:
            logger.exception('Unexpected error processing WooCommerce order')
            return Response(
                {'error': 'Internal server error'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
