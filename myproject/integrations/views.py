import logging
import hmac
import hashlib
import base64
from decimal import Decimal, InvalidOperation

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import WooCommerceOrderSerializer
from .services import upsert_woocommerce_order

logger = logging.getLogger('integrations')


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

            try:
                total_decimal = Decimal(str(data['total']))
            except (InvalidOperation, ValueError):
                total_decimal = Decimal('0.00')

            order, woo_obj, woo_created = upsert_woocommerce_order(
                woo_order_id=woo_order_id,
                status=data['status'],
                currency=data.get('currency', 'NPR'),
                total=total_decimal,
                billing=data.get('billing', {}),
                shipping=data.get('shipping', {}),
                line_items=data.get('line_items', []),
                raw_payload=request.data,
                sync_source='woocommerce_plugin',
            )

            return Response(
                {
                    'status': 'created' if woo_created else 'updated',
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
