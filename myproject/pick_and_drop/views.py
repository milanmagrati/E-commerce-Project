# pick_and_drop/views.py
"""
Pick and Drop Logistics Integration Views
Handles order creation and status tracking
"""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.conf import settings
from functools import wraps

from services.pick_and_drop_service import PickAndDropService
from dashboard.models import Order, OrderActivityLog

import logging

logger = logging.getLogger('pick_and_drop')
pnd_service = PickAndDropService()


# ===================== PERMISSION DECORATOR =====================

def pnd_permission_required(permission_field):
    """
    Decorator to check if user has required Pick and Drop permission.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_superuser or request.user.role == 'administrator':
                return view_func(request, *args, **kwargs)

            if not getattr(request.user, permission_field, False):
                messages.error(request, 'You do not have permission to access this page')
                return redirect('orders_list')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ===================== HELPER =====================

def _get_package_description(order):
    """Build package description from order items"""
    try:
        if hasattr(order, 'items'):
            items = order.items.select_related('product_variation').all()[:3]
            if items:
                parts = []
                for item in items:
                    qty = getattr(item, 'quantity', 1) or 1
                    name = item.product_name or 'Item'
                    var_name = item.variation_name or (
                        item.product_variation.variation_name if item.product_variation else None
                    )
                    if var_name:
                        name = f"{name} ({var_name})"
                    parts.append(f"{qty}x {name}")
                desc = ', '.join(parts)
                total_items = order.items.count()
                if total_items > 3:
                    desc += f' and {total_items - 3} more'
                return desc
    except Exception:
        pass
    return 'General Items'


# ===================== CREATE SHIPMENT =====================

@login_required
def create_pnd_shipment(request, order_id):
    """Create Pick and Drop shipment for an existing order"""
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)

        if order.pnd_order_id:
            messages.error(request, f'Order already exists in Pick and Drop with ID: {order.pnd_order_id}')
            return redirect('order_detail', order_id=order_id)

        if order.logistics != 'pick_and_drop':
            messages.error(request, 'Please set order logistics to Pick and Drop first')
            return redirect('order_detail', order_id=order_id)

        if not order.customer_name or not order.customer_phone or not order.shipping_address:
            messages.error(request, 'Customer name, phone, and address are required')
            return redirect('order_detail', order_id=order_id)

        destination_branch = request.POST.get('pnd_destination_branch', '').strip()
        if not destination_branch:
            destination_branch = getattr(order, 'branch_city', '') or 'KATHMANDU VALLEY'

        customer_name = (order.customer_name or '').strip()
        if not customer_name or len(customer_name) < 2:
            messages.error(request, 'Customer name is required and must be at least 2 characters')
            return redirect('order_detail', order_id=order_id)

        # Sanitize phone number: PND API requires exactly 10 digits
        import re
        raw_phone = str(order.customer_phone or '')
        digits_only = re.sub(r'\D', '', raw_phone)
        # If number starts with country code 977, strip it
        if digits_only.startswith('977') and len(digits_only) == 13:
            digits_only = digits_only[3:]
        # Take last 10 digits if longer
        if len(digits_only) > 10:
            digits_only = digits_only[-10:]
        if len(digits_only) != 10:
            messages.error(request, f'Phone number must be exactly 10 digits. Got: {raw_phone} ({len(digits_only)} digits after cleanup)')
            return redirect('order_detail', order_id=order_id)

        # Prepare Pick and Drop data
        pnd_data = {
            'customerName': customer_name,
            'primaryMobileNo': digits_only,
            'destinationBranch': destination_branch,
            'destinationCityArea': (order.shipping_address or destination_branch),
            'codAmount': float(order.total_amount or 0),
            'orderDescription': _get_package_description(order),
            'vendorTrackingNumber': str(order.order_number),
            'landmark': order.landmark or order.shipping_address or 'N/A',
            'weight': str(getattr(order, 'package_weight', 1)),
            'orderType': 'Regular',
            'instruction': order.notes or '',
        }

        # Add secondary phone if available
        if order.customer and hasattr(order.customer, 'alternate_phone') and order.customer.alternate_phone:
            pnd_data['secondaryMobileNo'] = order.customer.alternate_phone

        logger.info(f"Creating Pick and Drop order for: {order.order_number}")
        logger.info(f"PND Data: {pnd_data}")

        # Use dynamic API config if provided, otherwise use default module-level service
        api_config_id = request.POST.get('api_config_id', '') or None
        if api_config_id:
            active_service = PickAndDropService(api_config_id=int(api_config_id))
        else:
            active_service = pnd_service

        result = active_service.create_order(pnd_data)

        if result['success']:
            response_data = result['data']
            pnd_order_id = response_data.get('orderID', '')
            tracking_url = response_data.get('tracking_url', '')
            delivery_charge = response_data.get('delivery_charge', '')
            vendor_tracking = response_data.get('vendor_tracking_number', '')

            order.pnd_order_id = str(pnd_order_id)
            order.pnd_status = response_data.get('status', 'Order Created')
            order.pnd_created_at = timezone.now()
            order.pnd_destination_branch = destination_branch
            order.pnd_tracking_url = tracking_url or ''
            order.status = 'processing'
            order.save()

            OrderActivityLog.objects.create(
                order=order,
                action_type='updated',
                user=request.user,
                field_name='pnd_integration',
                new_value=f'PND Order ID: {pnd_order_id}',
                description=f'Order created in Pick and Drop with ID: {pnd_order_id}'
            )

            logger.info(f"PND Order created: {order.order_number} -> PND ID: {pnd_order_id}")
            messages.success(request, f'Order created in Pick and Drop! ID: {pnd_order_id}')
        else:
            error_msg = result.get('error', 'Unknown error')
            logger.error(f"Failed to create PND order: {error_msg}")
            messages.error(request, f'Failed: {error_msg}')

        return redirect('order_detail', order_id=order_id)

    except Order.DoesNotExist:
        messages.error(request, 'Order not found')
        return redirect('orders_list')

    except Exception as e:
        logger.error(f"Error creating PND shipment: {str(e)}")
        messages.error(request, f'Error: {str(e)}')
        return redirect('order_detail', order_id=order_id)


# ===================== SYNC STATUS =====================

@login_required
def sync_pnd_status(request, order_id):
    """Sync order status from Pick and Drop (placeholder for future API)"""
    order = get_object_or_404(Order, id=order_id, is_deleted=False)

    if not order.pnd_order_id:
        messages.error(request, 'Order is not in Pick and Drop system')
        return redirect('order_detail', order_id=order_id)

    # Placeholder: When Pick and Drop provides a status check API, implement here
    messages.info(request, 'Status sync will be available when the API endpoint is provided')
    return redirect('order_detail', order_id=order_id)


# ===================== CANCEL ORDER =====================

@login_required
def cancel_pnd_order(request, order_id):
    """Cancel an order in Pick and Drop system"""
    if request.method != 'POST':
        messages.error(request, 'Invalid request method')
        return redirect('order_detail', order_id=order_id)

    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)

        if not order.pnd_order_id:
            messages.error(request, 'Order is not in Pick and Drop system')
            return redirect('order_detail', order_id=order_id)

        logger.info(f"Canceling PND order: {order.order_number} (PND ID: {order.pnd_order_id})")

        result = pnd_service.cancel_order(order.pnd_order_id)

        old_status = order.pnd_status

        if result['success']:
            order.pnd_status = 'Cancelled'
            order.save()

            OrderActivityLog.objects.create(
                order=order,
                action_type='updated',
                user=request.user,
                field_name='pnd_status',
                old_value=old_status,
                new_value='Cancelled',
                description=f'Order cancelled in Pick and Drop (PND ID: {order.pnd_order_id})'
            )

            logger.info(f"PND Order cancelled: {order.order_number} (PND ID: {order.pnd_order_id})")
            messages.success(request, f'Order cancelled in Pick and Drop successfully (ID: {order.pnd_order_id})')
        else:
            error_msg = result.get('error', 'Unknown error')
            logger.warning(f"PND API cancel failed: {error_msg} — cancelling locally")

            # Cancel locally even if PND API rejects (e.g. permission issue)
            order.pnd_status = 'Cancelled'
            order.save()

            OrderActivityLog.objects.create(
                order=order,
                action_type='updated',
                user=request.user,
                field_name='pnd_status',
                old_value=old_status,
                new_value='Cancelled',
                description=(
                    f'Order cancelled locally (PND ID: {order.pnd_order_id}). '
                    f'PND API returned: {error_msg}. '
                    f'Please also cancel on Pick and Drop portal if needed.'
                )
            )

            messages.warning(
                request,
                f'Order {order.pnd_order_id} cancelled locally. '
                f'Could not cancel on Pick and Drop ({error_msg}). '
                f'Please cancel manually on the PND portal if needed.'
            )

        return redirect('order_detail', order_id=order_id)

    except Order.DoesNotExist:
        messages.error(request, 'Order not found')
        return redirect('orders_list')

    except Exception as e:
        logger.error(f"Error cancelling PND order: {str(e)}")
        messages.error(request, f'Error: {str(e)}')
        return redirect('order_detail', order_id=order_id)


# ===================== TRACK ORDER =====================

@login_required
def track_pnd_order(request, order_id):
    """Redirect to Pick and Drop tracking URL"""
    order = get_object_or_404(Order, id=order_id, is_deleted=False)

    if order.pnd_tracking_url:
        from django.shortcuts import redirect as redir
        return redir(order.pnd_tracking_url)

    messages.info(request, 'No tracking URL available for this order')
    return redirect('order_detail', order_id=order_id)


# ===================== BULK SYNC =====================

@login_required
def bulk_sync_pnd_orders(request):
    """Bulk sync Pick and Drop orders (placeholder for future API)"""
    messages.info(request, 'Bulk sync will be available when the status API endpoint is provided')
    return redirect('orders_list')
