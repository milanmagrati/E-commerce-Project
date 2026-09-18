# ncm/order_recovery.py
"""
Order Recovery & Troubleshooting Helper
Handles cases where NCM orders are not found or need resync
"""

from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.utils import timezone

from dashboard.models import Order, OrderActivityLog
from dashboard.timezone_utils import parse_ncm_datetime
from services.ncm_service import NCMService
from services.status_override import (clear_manual_status_override,
                                      manual_override_holds)

import logging

logger = logging.getLogger('ncm')
ncm_service = NCMService()


@login_required
@require_POST
def clear_ncm_order_id(request, order_id):
    """Clear NCM ID from local system (e.g., if order not found in NCM)"""
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        
        old_ncm_id = order.ncm_order_id
        old_status = order.ncm_status
        
        # Clear NCM fields
        order.ncm_order_id = None
        order.ncm_status = None
        order.ncm_created_at = None
        order.ncm_destination_branch = None
        order.save()
        
        # Log activity
        OrderActivityLog.objects.create(
            order=order,
            action_type='updated',
            user=request.user,
            field_name='ncm_integration',
            old_value=f'NCM ID: {old_ncm_id}, Status: {old_status}',
            new_value='Cleared - order ready to resend',
            description=f'❌ Cleared NCM ID {old_ncm_id} (order not found in NCM system)'
        )
        
        logger.warning(f"Cleared NCM ID {old_ncm_id} for order {order.order_number}")
        messages.success(request, f'✅ NCM ID cleared. You can now resend this order to NCM.')
        
    except Exception as e:
        logger.error(f"Error clearing NCM ID: {str(e)}")
        messages.error(request, f'❌ Error: {str(e)}')
    
    return redirect('order_detail', order_id=order_id)


@login_required
@require_POST
def verify_ncm_order(request, order_id):
    """Verify if order exists in NCM and sync status if it does"""
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        
        if not order.ncm_order_id:
            messages.error(request, 'Order has not been sent to NCM yet')
            return redirect('order_detail', order_id=order_id)

        # Verifying is a lookup; it must not resurrect an order staff already
        # cancelled. Every other NCM sync path refuses cancelled orders for
        # this reason - this one used to write NCM's answer straight over it.
        if order.status == 'cancelled':
            messages.info(
                request,
                'Order is cancelled; status not overwritten from NCM.'
            )
            return redirect('order_detail', order_id=order_id)
        
        # Try to fetch status from NCM
        svc = NCMService(api_config_id=order.api_config_id) if order.api_config_id else ncm_service
        result = svc.get_order_status(order.ncm_order_id)
        
        if result['success'] and result['data']:
            # Order found and has status
            latest_entry = result['data'][0]
            latest_status = latest_entry.get('status', 'Unknown')
            old_status = order.ncm_status
            old_system_status = order.status
            # NCM's timestamp for the status, not the moment we verified it.
            event_at = parse_ncm_datetime(latest_entry.get('added_time'))

            # Resolve through the same vendor_return-aware path as every other
            # sync. This used to call map_ncm_status_to_system on the raw text,
            # which cannot see the flag - so an RTV "Delivered" back to the
            # vendor was recorded here as a delivery to the customer.
            system_status, payment_status = svc.resolve_delivered_status(latest_entry)

            order.ncm_status = latest_status
            update_fields = ['ncm_status', 'updated_at']

            # A status staff set by hand outranks NCM until the parcel really
            # moves - the same guard the webhook, the page-load sync and the
            # bulk sync apply. Verifying an order is a lookup, not a reason to
            # discard someone's decision.
            if manual_override_holds(order, latest_status, event_at):
                logger.info(
                    f"Keeping manually set status on {order.order_number} "
                    f"(NCM still reports '{latest_status}')"
                )
            else:
                # Writes status, order_status AND the status_setup FK together.
                # Only `status` was written before, so the order detail header
                # badge (which renders status_setup) and the list pages (which
                # read order_status) kept showing the status it had before.
                update_fields.extend(
                    svc.sync_order_status_fields(order, system_status, payment_status)
                )
                update_fields.extend(clear_manual_status_override(order))

                if system_status == 'delivered' and not order.delivered_at:
                    order.delivered_at = event_at or timezone.now()
                    update_fields.append('delivered_at')

            order.save(update_fields=list(dict.fromkeys(update_fields)))

            OrderActivityLog.objects.create(
                order=order,
                action_type='status_changed',
                user=request.user,
                field_name='ncm_status',
                old_value=old_status or 'Unknown',
                new_value=latest_status,
                event_at=event_at,
                description=f'✅ Order verified in NCM: {latest_status} '
                            f'({old_system_status} → {order.status})'
            )
            
            logger.info(f"Order {order.order_number} verified in NCM")
            messages.success(request, f'✅ Order found in NCM! Status: {latest_status}')
        else:
            error_msg = result.get('error', 'Unknown error')
            if '404' in str(error_msg).lower() or 'not found' in str(error_msg).lower():
                logger.warning(f"Order {order.ncm_order_id} not found in NCM")
                messages.error(request, f'❌ Order ID {order.ncm_order_id} not found in NCM system')
                messages.info(request, 'Options: 1) Check if NCM ID is correct, 2) Resend order to create new NCM entry, 3) Clear ID if incorrect')
            else:
                logger.error(f"Verification failed: {error_msg}")
                messages.error(request, f'Verification failed: {error_msg}')
        
        return redirect('order_detail', order_id=order_id)
        
    except Exception as e:
        logger.error(f"Error verifying order: {str(e)}")
        messages.error(request, f'❌ Error: {str(e)}')
        return redirect('order_detail', order_id=order_id)
