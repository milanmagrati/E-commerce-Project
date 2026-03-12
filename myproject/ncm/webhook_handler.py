# ncm/webhook_handler.py
"""
Enhanced NCM Webhook Handler with Real-time Synchronization
Handles automatic order status updates, SMS notifications, and signal broadcasting
"""

from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model
from dashboard.models import Order, OrderActivityLog
from ncm.models import WebhookLog
from services.sms_service import SMSService
import logging
import hmac
import hashlib

logger = logging.getLogger('ncm')
User = get_user_model()

class NCMWebhookHandler:
    """Handle NCM webhook events with comprehensive logging and error handling"""

    # NCM event names to human-readable status mapping (from NCM docs)
    EVENT_TO_STATUS = {
        'pickup_completed': 'Pickup Complete',
        'sent_for_delivery': 'Sent for Delivery',
        'order_dispatched': 'Dispatched',
        'order_arrived': 'Arrived',
        'delivery_completed': 'Delivered',
    }

    # Unified status mapping: NCM status -> system status
    # This is the single source of truth for all webhook and sync operations
    STATUS_MAPPING = {
        'Pickup Order Created': 'processing',
        'Drop off Order Created': 'processing',
        'Pickup Complete': 'in_transit',
        'Drop off Order Collected': 'in_transit',
        'Dispatched': 'in_transit',
        'In Transit': 'in_transit',
        'Arrived': 'in_transit',
        'Sent for Delivery': 'in_transit',
        'Out for Delivery': 'in_transit',
        'Delivered': 'delivered',
        'Confirmed': 'delivered',
        'Returned': 'returned',
        'Return Initiated': 'return_initiated',
        'Return Approved': 'return_approved',
    }

    PAYMENT_STATUS_MAPPING = {
        'COD Collected': 'paid',
        'Payment Collected': 'paid',
        'Pending': 'pending',
        'COD Pending': 'cod_pending',
    }
    
    def __init__(self):
        self.sms_service = SMSService()
        self._system_user = None

    @property
    def system_user(self):
        """Lazy-load system user to avoid DB query at import time"""
        if self._system_user is None:
            self._system_user = self._get_or_create_system_user()
        return self._system_user

    @staticmethod
    def _get_or_create_system_user():
        """Get or create system user for webhook operations"""
        user, created = User.objects.get_or_create(
            username='ncm_webhook_system',
            defaults={
                'email': 'ncm-webhook@system.local',
                'first_name': 'NCM',
                'last_name': 'Webhook System',
                'is_active': True,
            }
        )
        if created:
            logger.info("Created system user for webhook operations")
        return user
    
    def verify_webhook(self, request, payload_bytes) -> bool:
        """
        Verify NCM webhook request authenticity.

        NCM does NOT send HMAC signatures. If a token query parameter is
        present and NCM_WEBHOOK_SECRET is configured, the token is validated.
        """
        webhook_secret = getattr(settings, 'NCM_WEBHOOK_SECRET', None)
        token = request.GET.get('token', '') if request else ''

        if webhook_secret and token:
            if not hmac.compare_digest(token, webhook_secret):
                logger.error("Webhook token verification failed")
                return False
            logger.info("Webhook token verified successfully")

        return True
    
    def process_webhook(self, payload: dict, request=None) -> dict:
        """
        Process webhook payload and update orders

        NCM webhook payload format (per documentation):

        Single order:
        {
            "order_id": "123456",
            "status": "Delivered",
            "timestamp": "2024-01-15T10:30:00Z",
            "event": "delivery_completed"
        }

        Bulk orders:
        {
            "order_ids": ["123456", "123457"],
            "status": "Dispatched",
            "timestamp": "2024-01-15T10:30:00Z",
            "event": "order_dispatched"
        }

        Test webhook:
        {
            "event": "order.status.changed",
            "order_id": "TEST-123456",
            "status": "In Transit",
            "timestamp": "2024-01-15T10:30:00Z",
            "test": true
        }
        """
        event = payload.get('event', '')
        status = payload.get('status', '')
        timestamp_str = payload.get('timestamp', '')

        webhook_log = None

        try:
            # Handle test webhooks
            if payload.get('test'):
                logger.info("Test webhook received and acknowledged")
                return {
                    'success': True,
                    'message': 'Test webhook acknowledged',
                    'status': 'test'
                }

            # Resolve status from event if status field is empty/missing
            if not status and event:
                status = self.EVENT_TO_STATUS.get(event, '')
                logger.info(f"Resolved status '{status}' from event '{event}'")

            # Extract order IDs
            order_ids = []
            if 'order_id' in payload and payload['order_id']:
                order_ids = [str(payload['order_id'])]
            elif 'order_ids' in payload and payload['order_ids']:
                order_ids = [str(oid) for oid in payload['order_ids']]

            if not order_ids:
                raise ValueError("Missing order_id(s) in payload")

            if not status:
                raise ValueError("Missing status in payload")

            # Generate deterministic idempotency key from payload fields.
            # NCM does not send a webhook_id, so we derive one by hashing
            # the content. Using SHA-256 keeps it within the 100-char DB limit.
            ids_part = ','.join(sorted(order_ids))
            raw_key = f"{ids_part}|{event}|{timestamp_str}"
            webhook_id = hashlib.sha256(raw_key.encode()).hexdigest()

            # Check for duplicate/idempotency
            webhook_log, created = WebhookLog.objects.get_or_create(
                webhook_id=webhook_id,
                defaults={
                    'event': event,
                    'payload': payload,
                    'status': 'processing',
                }
            )

            if not created:
                logger.info(f"Duplicate webhook detected: {webhook_id}")
                return {
                    'success': True,
                    'message': 'Webhook already processed',
                    'webhook_id': webhook_id,
                    'status': 'duplicate'
                }

            # Process updates
            updated_orders = []
            failed_orders = []

            with transaction.atomic():
                for ncm_order_id in order_ids:
                    try:
                        # Get order with row-level locking
                        order = Order.objects.select_for_update().get(
                            ncm_order_id=ncm_order_id,
                            is_deleted=False
                        )

                        # Update order
                        result = self._update_order_from_webhook(
                            order,
                            status,
                            payload
                        )

                        if result['success']:
                            updated_orders.append(result)
                            # Send SMS notification
                            self._send_status_notification(order, order.status)
                        else:
                            failed_orders.append(result)

                    except Order.DoesNotExist:
                        logger.warning(f"Order not found: NCM ID {ncm_order_id}")
                        failed_orders.append({
                            'ncm_order_id': ncm_order_id,
                            'error': 'Order not found'
                        })
                    except Exception as e:
                        logger.error(f"Error processing order {ncm_order_id}: {str(e)}")
                        failed_orders.append({
                            'ncm_order_id': ncm_order_id,
                            'error': str(e)
                        })

            # Update webhook log
            webhook_log.status = 'completed'
            webhook_log.updated_orders_count = len(updated_orders)
            webhook_log.failed_orders_count = len(failed_orders)
            webhook_log.processed_at = timezone.now()
            webhook_log.response_data = {
                'updated_count': len(updated_orders),
                'failed_count': len(failed_orders)
            }
            webhook_log.save()

            logger.info(f"Webhook {webhook_id}: {len(updated_orders)} updated, {len(failed_orders)} failed")

            return {
                'success': True,
                'message': 'Webhook processed successfully',
                'webhook_id': webhook_id,
                'event': event,
                'status': status,
                'updated_count': len(updated_orders),
                'updated_orders': updated_orders,
                'failed_count': len(failed_orders),
                'failed_orders': failed_orders
            }

        except Exception as e:
            logger.error(f"Webhook processing error: {str(e)}")
            if webhook_log:
                webhook_log.status = 'failed'
                webhook_log.error_message = str(e)
                webhook_log.save()

            raise
    
    def _update_order_from_webhook(self, order: Order, status: str,
                                   payload: dict = None) -> dict:
        """Update order fields from webhook data based on NCM payload"""
        try:
            from services.ncm_service import NCMService

            old_status = order.status
            old_ncm_status = order.ncm_status
            old_payment_status = order.payment_status

            # Build a status entry dict for resolve_delivered_status
            status_entry = {'status': status}
            if payload:
                # Copy vendor_return flag from payload if present
                for key in ('vendor_return', 'vendorReturn'):
                    if key in payload:
                        status_entry[key] = payload[key]

            # Use vendor_return-aware resolution for 'Delivered' status
            system_status, payment_status = NCMService.resolve_delivered_status(status_entry)

            # Update NCM status (always store the raw NCM status)
            order.ncm_status = status

            # Update all status-related fields (status, order_status, status_setup FK, payment fields)
            update_fields = NCMService.sync_order_status_fields(order, system_status, payment_status)
            update_fields.append('ncm_status')
            update_fields.append('updated_at')

            # Set delivered_at timestamp for delivered orders
            if system_status == 'delivered' and not order.delivered_at:
                order.delivered_at = timezone.now()
                update_fields.append('delivered_at')

            # Deduplicate
            update_fields = list(dict.fromkeys(update_fields))
            order.save(update_fields=update_fields)

            # Create activity log
            event_name = payload.get('event', '') if payload else ''
            OrderActivityLog.objects.create(
                order=order,
                action_type='status_changed',
                user=self.system_user,
                field_name='ncm_status',
                old_value=old_ncm_status or 'None',
                new_value=status,
                description=f'NCM Webhook ({event_name}): {old_status} -> {system_status}'
            )

            logger.info(f"Updated: {order.order_number} - Status: {old_status}->{system_status}, NCM: {old_ncm_status}->{status}, Payment: {old_payment_status}->{order.payment_status}")

            return {
                'success': True,
                'order_number': order.order_number,
                'ncm_order_id': order.ncm_order_id,
                'old_status': old_status,
                'new_status': system_status,
                'ncm_status': status
            }

        except Exception as e:
            logger.error(f"Error updating order {order.order_number}: {str(e)}")
            return {
                'success': False,
                'order_number': order.order_number,
                'error': str(e)
            }
    
    def _send_status_notification(self, order: Order, status: str):
        """Send SMS notification to customer based on resolved system status"""
        try:
            if not order.customer_phone:
                logger.warning(f"No phone number for order {order.order_number}")
                return

            # Map resolved system status to notification type
            if status in ['delivered']:
                notification_status = 'delivered'
            elif status in ['in_transit', 'shipped']:
                notification_status = 'in_transit'
            elif status in ['returned', 'return_initiated', 'return_approved']:
                notification_status = 'returned'
            else:
                return

            # Send SMS
            result = self.sms_service.send_order_status_sms(
                phone_number=order.customer_phone,
                order_number=order.order_number,
                status=notification_status,
            )

            if result.get('sent'):
                logger.info(f"SMS notification sent to {order.customer_phone} for order {order.order_number}")
            else:
                logger.warning(f"SMS notification failed for order {order.order_number}: {result.get('message')}")

        except Exception as e:
            logger.error(f"Error sending notification for order {order.order_number}: {str(e)}")
