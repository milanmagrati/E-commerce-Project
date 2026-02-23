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
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger('ncm')
User = get_user_model()

class NCMWebhookHandler:
    """Handle NCM webhook events with comprehensive logging and error handling"""
    
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
    
    def verify_signature(self, request, payload_bytes) -> bool:
        """Verify NCM webhook signature"""
        webhook_secret = getattr(settings, 'NCM_WEBHOOK_SECRET', None)
        
        if not webhook_secret:
            logger.warning("NCM_WEBHOOK_SECRET not configured. Skipping verification.")
            return True
        
        signature_header = request.META.get('HTTP_X_NCM_SIGNATURE', '')
        if not signature_header:
            logger.error("Missing X-NCM-Signature header")
            return False
        
        # Compute expected signature
        expected_signature = hmac.new(
            webhook_secret.encode(),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()
        
        # Constant-time comparison
        return hmac.compare_digest(signature_header, expected_signature)
    
    def process_webhook(self, payload: dict, request=None) -> dict:
        """
        Process webhook payload and update orders
        
        Expected payload format:
        {
            'webhook_id': 'unique_id',
            'event': 'order_status_update',
            'order_id': ncm_order_id or 'order_ids': [list],
            'status': 'Delivered',
            'delivery_date': '2024-02-16',
            'cod_amount': 1500.00,
            'timestamp': '2024-02-16T10:30:00Z',
            'test': false
        }
        """
        webhook_id = payload.get('webhook_id', payload.get('id', str(timezone.now().timestamp())))
        event = payload.get('event', 'status_update')
        status = payload.get('status', '')
        delivery_date_str = payload.get('delivery_date')
        cod_amount = payload.get('cod_amount')
        
        webhook_log = None
        
        try:
            # Handle test webhooks
            if payload.get('test'):
                logger.info("✓ Test webhook received and acknowledged")
                return {
                    'success': True,
                    'message': 'Test webhook acknowledged',
                    'webhook_id': webhook_id,
                    'status': 'test'
                }
            
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
                logger.info(f"⚠️ Duplicate webhook detected: {webhook_id}")
                return {
                    'success': True,
                    'message': 'Webhook already processed',
                    'webhook_id': webhook_id,
                    'status': 'duplicate'
                }
            
            # Extract order IDs
            order_ids = []
            if 'order_id' in payload and payload['order_id']:
                order_ids = [payload['order_id']]
            elif 'order_ids' in payload and payload['order_ids']:
                order_ids = payload['order_ids']
            
            if not order_ids:
                raise ValueError("Missing order_id(s) in payload")
            
            if not status:
                raise ValueError("Missing status in payload")
            
            # Parse delivery date if present
            delivery_date = None
            if delivery_date_str:
                try:
                    delivery_date = datetime.fromisoformat(delivery_date_str.replace('Z', '+00:00'))
                except (ValueError, TypeError):
                    try:
                        delivery_date = datetime.strptime(delivery_date_str, '%Y-%m-%d')
                    except:
                        logger.warning(f"Could not parse delivery_date: {delivery_date_str}")
            
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
                            delivery_date, 
                            cod_amount,
                            payload
                        )
                        
                        if result['success']:
                            updated_orders.append(result)
                            # Send SMS notification using resolved system status
                            self._send_status_notification(order, order.status, cod_amount)
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
            
            logger.info(f"✅ Webhook {webhook_id}: {len(updated_orders)} updated, {len(failed_orders)} failed")
            
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
            logger.error(f"❌ Webhook processing error: {str(e)}")
            if webhook_log:
                webhook_log.status = 'failed'
                webhook_log.error_message = str(e)
                webhook_log.save()
            
            raise
    
    def _update_order_from_webhook(self, order: Order, status: str,
                                   delivery_date=None, cod_amount=None,
                                   payload: dict = None) -> dict:
        """Update order fields from webhook data"""
        try:
            from services.ncm_service import NCMService

            old_status = order.status
            old_ncm_status = order.ncm_status
            old_payment_status = order.payment_status
            old_delivery_charge = order.delivery_charge

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

            # Update delivery date if delivered
            if system_status == 'delivered' and delivery_date:
                order.delivered_at = delivery_date
                update_fields.append('delivered_at')

            # Handle COD collection (overrides resolved payment_status if applicable)
            if cod_amount is not None and cod_amount > 0:
                order.cod_collected = Decimal(str(cod_amount))
                order.payment_status = 'paid'
                if 'payment_status' not in update_fields:
                    update_fields.append('payment_status')
                if 'cod_collected' not in update_fields:
                    update_fields.append('cod_collected')
            
            # ✅ Extract and save delivery charge from webhook payload
            if payload:
                # Try multiple possible field names for delivery charge (common in logistics APIs)
                delivery_charge = (payload.get('chargeDetail') or 
                                 payload.get('deliveryCharge') or 
                                 payload.get('delivery_charge') or 
                                 payload.get('chargedetail') or 
                                 payload.get('shippingCharge') or 
                                 payload.get('shipping_charge') or 
                                 payload.get('charge') or 
                                 payload.get('amount'))
                if delivery_charge and float(delivery_charge) > 0:
                    try:
                        order.delivery_charge = Decimal(str(delivery_charge))
                        if 'delivery_charge' not in update_fields:
                            update_fields.append('delivery_charge')
                        logger.info(f"✓ Updated delivery charge: {delivery_charge} for order {order.order_number}")
                    except Exception as e:
                        logger.warning(f"Could not parse delivery_charge {delivery_charge}: {str(e)}")

            # Deduplicate
            update_fields = list(dict.fromkeys(update_fields))
            order.save(update_fields=update_fields)

            # Create activity log
            OrderActivityLog.objects.create(
                order=order,
                action_type='status_changed',
                user=self.system_user,
                field_name='ncm_status',
                old_value=old_ncm_status or 'None',
                new_value=status,
                description=f'NCM Webhook: {status} (vendor_return={status_entry.get("vendor_return", "N/A")})'
            )

            logger.info(f"✓ Updated: {order.order_number} - Status: {old_status}→{system_status}, NCM: {old_ncm_status}→{status}, Payment: {old_payment_status}→{order.payment_status}")
            
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
    
    def _send_status_notification(self, order: Order, status: str, cod_amount=None):
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
            elif cod_amount and cod_amount > 0:
                notification_status = 'cod_collected'
            else:
                return
            
            # Send SMS
            result = self.sms_service.send_order_status_sms(
                phone_number=order.customer_phone,
                order_number=order.order_number,
                status=notification_status,
                additional_info=f"रू {cod_amount}" if cod_amount else None
            )
            
            if result.get('sent'):
                logger.info(f"✓ SMS notification sent to {order.customer_phone} for order {order.order_number}")
            else:
                logger.warning(f"SMS notification failed for order {order.order_number}: {result.get('message')}")
                
        except Exception as e:
            logger.error(f"Error sending notification for order {order.order_number}: {str(e)}")
