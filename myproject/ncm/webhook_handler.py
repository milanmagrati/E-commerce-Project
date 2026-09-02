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
from dashboard.timezone_utils import parse_ncm_datetime
from ncm.models import WebhookLog
from services.sms_service import SMSService
import logging
import hmac
import hashlib

logger = logging.getLogger('ncm')
webhook_logger = logging.getLogger('webhook')
User = get_user_model()

class NCMWebhookHandler:
    """Handle NCM webhook events with comprehensive logging and error handling"""

    # NCM event names to human-readable status mapping.
    # Used only as a fallback when the webhook payload's 'status' field is
    # empty - NCM normally sends 'status' directly, so this rarely matters.
    #
    # The first 5 entries are NCM's officially documented "Order Status
    # Events" (confirmed against their webhook integration doc - this is
    # the complete list NCM publishes; no return/RTV events are documented
    # at all).
    #
    # 'order_marked_rtv' is NOT in that doc, but is included here because
    # it was observed firing for real in production (seen in this system's
    # own webhook activity log). NCM's RTV pipeline otherwise appears to be
    # undocumented and, per the evidence gathered so far, does not reliably
    # webhook the later RTV steps ("Sent to Vendor", final RTV "Delivered")
    # at all - see the auto-sync-on-page-load logic in order_detail.html,
    # which exists specifically to compensate by actively pulling status
    # instead of waiting on a push NCM may never send.
    EVENT_TO_STATUS = {
        'pickup_completed': 'Pickup Complete',
        'sent_for_delivery': 'Sent for Delivery',
        'order_dispatched': 'Dispatched',
        'order_arrived': 'Arrived',
        'delivery_completed': 'Delivered',
        'order_marked_rtv': 'Order Marked Return',
    }

    # Unified status mapping: NCM status -> system status
    # Kept in sync with NCMService.map_ncm_status_to_system, which is the
    # actual mapping used by both webhook updates and manual sync operations
    # (this dict documents the same mapping but is not directly referenced).
    STATUS_MAPPING = {
        'Pickup Order Created': 'Pickup Created',
        'Drop off Order Created': 'Pickup Created',
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
        'Return Initiated': 'return_processing',
        'Return Approved': 'return_processing',
        'Order Marked Return': 'return_processing',
        'Sent to Vendor': 'return_processing',
        'Returned to Warehouse': 'return',
    }
    # Note: a 'Delivered' carrying vendor_return=True is a delivery back to the
    # vendor, not to the customer - NCMService.resolve_delivered_status is what
    # tells the two apart and is what actually runs. NCM's branch-qualified
    # "Arrived at RETURN (BRANCH)" has no fixed key here; it resolves to
    # 'return_arrived' via NCMService.is_return_arrival. Every other RTV status,
    # flagged or not, stays at 'return_processing' until NCM confirms arrival.

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
        # NCM's own time for the event. Webhooks can arrive minutes or days
        # after the fact (NCM retries, and the RTV steps are pushed
        # unreliably), so stamping the activity log with receipt time made the
        # order timeline disagree with what NCM shows.
        event_at = parse_ncm_datetime(timestamp_str)

        webhook_log = None

        try:
            # Handle test webhooks
            if payload.get('test'):
                webhook_logger.info("Test webhook received and acknowledged")
                return {
                    'success': True,
                    'message': 'Test webhook acknowledged',
                    'status': 'test'
                }

            # Resolve status from event if status field is empty/missing
            if not status and event:
                status = self.EVENT_TO_STATUS.get(event, '')
                webhook_logger.info(f"Resolved status '{status}' from event '{event}'")

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
            # Hash the RAW timestamp string, never the parsed value: normalizing
            # it would change every webhook_id already stored and make past
            # webhooks look new.
            ids_part = ','.join(sorted(order_ids))
            raw_key = f"{ids_part}|{event}|{timestamp_str}"
            webhook_id = hashlib.sha256(raw_key.encode()).hexdigest()

            # Check for duplicate/idempotency
            source_ip = None
            if request:
                x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
                if x_forwarded_for:
                    source_ip = x_forwarded_for.split(',')[0].strip()
                else:
                    source_ip = request.META.get('REMOTE_ADDR')

            webhook_log, created = WebhookLog.objects.get_or_create(
                webhook_id=webhook_id,
                defaults={
                    'event': event,
                    'payload': payload,
                    'status': 'processing',
                    'source_ip': source_ip,
                }
            )

            if not created:
                webhook_logger.info(f"Duplicate webhook detected: {webhook_id}")
                return {
                    'success': True,
                    'message': 'Webhook already processed',
                    'webhook_id': webhook_id,
                    'status': 'duplicate'
                }

            # Process updates - each order in its own transaction so one
            # failure doesn't roll back updates to other orders.
            updated_orders = []
            failed_orders = []

            for ncm_order_id in order_ids:
                try:
                    with transaction.atomic():
                        # Get order with row-level locking
                        order = Order.objects.select_for_update().get(
                            ncm_order_id=ncm_order_id,
                            is_deleted=False
                        )

                        # Update order
                        result = self._update_order_from_webhook(
                            order,
                            status,
                            payload,
                            event_at=event_at
                        )

                        if result['success']:
                            updated_orders.append(result)
                            # Send SMS notification
                            self._send_status_notification(order, order.status)
                        else:
                            failed_orders.append(result)

                except Order.DoesNotExist:
                    webhook_logger.warning(f"Order not found: NCM ID {ncm_order_id}")
                    failed_orders.append({
                        'ncm_order_id': ncm_order_id,
                        'error': 'Order not found'
                    })
                except Exception as e:
                    webhook_logger.error(f"Error processing order {ncm_order_id}: {str(e)}")
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

            webhook_logger.info(f"Webhook {webhook_id}: {len(updated_orders)} updated, {len(failed_orders)} failed")

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
            webhook_logger.error(f"Webhook processing error: {str(e)}")
            if webhook_log:
                webhook_log.status = 'failed'
                webhook_log.error_message = str(e)
                webhook_log.save()

            return {
                'success': False,
                'message': f'Webhook processing failed: {str(e)}',
                'error': str(e)
            }
    
    def _update_order_from_webhook(self, order: Order, status: str,
                                   payload: dict = None, event_at=None) -> dict:
        """Update order fields from webhook data based on NCM payload

        Args:
            event_at: NCM's timestamp for this event (aware datetime) or None.
                      Recorded on the activity log so the order timeline shows
                      when NCM says it happened, not when we received the push.
        """
        try:
            from services.ncm_service import NCMService

            # Never let a webhook (which can arrive late or out of order)
            # resurrect an order the staff already cancelled.
            if order.status == 'cancelled':
                logger.info(f"Skipping webhook update for cancelled order {order.order_number} (NCM status: {status})")
                return {
                    'success': False,
                    'order_number': order.order_number,
                    'error': 'Order is cancelled; webhook update skipped'
                }

            # A status staff set by hand outranks NCM until the parcel really
            # moves. A webhook repeating the status that was already in force
            # when the choice was made carries no new information about the
            # parcel, so it must not overwrite that choice; anything newer does.
            from services.status_override import (clear_manual_status_override,
                                                  manual_override_holds)
            if manual_override_holds(order, status, event_at):
                logger.info(
                    f"Keeping manually set status on {order.order_number} "
                    f"(webhook still reports '{status}')"
                )
                return {
                    'success': False,
                    'order_number': order.order_number,
                    'error': 'Status was set manually; webhook update skipped'
                }

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

            # A webhook is news the cached /order/status answer predates, so
            # drop it - otherwise opening the order in the next few seconds
            # could draw a timeline from just before this hop.
            from services.ncm_service import invalidate_order_status_cache
            invalidate_order_status_cache(order.ncm_order_id)

            # Update all status-related fields (status, order_status, status_setup FK, payment fields)
            update_fields = NCMService.sync_order_status_fields(order, system_status, payment_status)
            update_fields.append('ncm_status')
            update_fields.append('updated_at')
            # NCM has moved past whatever was set by hand, so retire the hold.
            update_fields.extend(clear_manual_status_override(order))

            # Set delivered_at timestamp for delivered orders
            if system_status == 'delivered' and not order.delivered_at:
                order.delivered_at = event_at or timezone.now()
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
                event_at=event_at,
                description=f'NCM Webhook ({event_name}): {old_status} -> {system_status}'
            )

            # An RTV mark pushed by NCM is the authoritative moment the return
            # started — record it so the RTV page doesn't have to wait for the
            # rate-limited comment sync to discover it.
            if event_name == 'order_marked_rtv' and event_at:
                self._record_rtv_marked(order, event_at)

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
    
    def _record_rtv_marked(self, order: Order, event_at):
        """Store NCM's RTV-mark time on the matching RTVOrder row.

        The RTV list otherwise only learns this date from a per-order comment
        fetch that NCM rate-limits to a handful per sync, so pushes give us the
        right date for free. Ranked equal to a "RTV marked" comment: both come
        straight from NCM.

        Never raises — a bookkeeping miss must not fail the webhook. The inner
        atomic() is a savepoint, not decoration: this runs inside the caller's
        transaction, so swallowing a database error without one would leave that
        transaction unusable and take down the whole webhook anyway.
        """
        from dashboard.models import RTVOrder
        from services.ncm_service import NCMService

        try:
            with transaction.atomic():
                rtv, created = RTVOrder.objects.get_or_create(
                    order_id=int(order.ncm_order_id),
                    defaults={
                        'vendor_return': True,
                        'vendor': self.system_user,
                        'api_config_id': order.api_config_id,
                        'rtv_marked_at': event_at,
                        'rtv_marked_at_source': RTVOrder.SOURCE_WEBHOOK,
                        'rtv_marked_at_checked_at': timezone.now(),
                    },
                )
                if not created:
                    NCMService.apply_rtv_marked_at(rtv, event_at, RTVOrder.SOURCE_WEBHOOK)
        except (TypeError, ValueError):
            logger.warning(f"Could not record RTV mark: bad NCM order id {order.ncm_order_id!r}")
        except Exception as e:
            logger.error(f"Could not record RTV mark for order {order.order_number}: {e}")

    def _send_status_notification(self, order: Order, status: str):
        """Send SMS notification to customer based on resolved system status"""
        try:
            if not order.customer_phone:
                logger.warning(f"No phone number for order {order.order_number}")
                return

            # Map resolved system status to notification type.
            # 'return_processing', 'return_arrived' and 'return' are the three
            # statuses NCMService actually resolves to for the RTV pipeline (see
            # resolve_delivered_status/map_ncm_status_to_system);
            # 'returned'/'return_initiated'/'return_approved' are kept for
            # any legacy/other callers that still produce those values.
            if status in ['delivered']:
                notification_status = 'delivered'
            elif status in ['in_transit', 'shipped']:
                notification_status = 'in_transit'
            elif status in ['return', 'return_arrived', 'return_processing', 'returned',
                            'return_initiated', 'return_approved']:
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
