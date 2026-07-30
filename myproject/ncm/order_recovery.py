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

from dashboard.models import Order, OrderActivityLog
from dashboard.timezone_utils import parse_ncm_datetime
from services.ncm_service import NCMService

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
        
        # Try to fetch status from NCM
        svc = NCMService(api_config_id=order.api_config_id) if order.api_config_id else ncm_service
        result = svc.get_order_status(order.ncm_order_id)
        
        if result['success'] and result['data']:
            # Order found and has status
            latest_entry = result['data'][0]
            latest_status = latest_entry.get('status', 'Unknown')
            old_status = order.ncm_status
            # NCM's timestamp for the status, not the moment we verified it.
            event_at = parse_ncm_datetime(latest_entry.get('added_time'))

            order.ncm_status = latest_status
            order.status = svc.map_ncm_status_to_system(latest_status)
            order.save()

            OrderActivityLog.objects.create(
                order=order,
                action_type='status_changed',
                user=request.user,
                field_name='ncm_status',
                old_value=old_status or 'Unknown',
                new_value=latest_status,
                event_at=event_at,
                description=f'✅ Order verified in NCM: {latest_status}'
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
