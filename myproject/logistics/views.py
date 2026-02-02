from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Count
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from decimal import Decimal
import json

from .models import LogisticsOrder, LogisticsProvider, StatusLog
from .ncm_service import NCMService, create_ncm_order, sync_ncm_status
from dashboard.models import Order


# ==================== WEBHOOK ====================
@csrf_exempt
@require_POST
def ncm_webhook(request):
    """
    Receive status updates from NCM
    URL: /logistics/webhook/ncm/
    """
    try:
        # Parse incoming data
        data = json.loads(request.body)
        
        ncm_order_id = data.get('order_id')
        new_status = data.get('status')
        
        # Find your order
        logistics_order = LogisticsOrder.objects.get(ncm_order_id=ncm_order_id)
        
        # Map status
        status_map = {
            'Delivered': 'DELIVERED',
            'In Transit': 'IN_TRANSIT',
            'Returned': 'RETURNED',
        }
        
        mapped_status = status_map.get(new_status, 'PENDING')
        
        # Update
        logistics_order.status = mapped_status
        logistics_order.last_synced = timezone.now()
        logistics_order.save()
        
        # Log
        StatusLog.objects.create(
            logistics_order=logistics_order,
            status=new_status,
            message='Updated via webhook'
        )
        
        # Update your main order status
        logistics_order.order.status = 'Dispatched' if mapped_status == 'DELIVERED' else logistics_order.order.status
        logistics_order.order.save()
        
        return JsonResponse({'status': 'success'})
        
    except LogisticsOrder.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Order not found'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


# ==================== DASHBOARD VIEWS ====================
@login_required
def logistics_dashboard(request):
    """Main logistics dashboard with stats"""
    
    # Get statistics
    total_orders = LogisticsOrder.objects.count()
    pending = LogisticsOrder.objects.filter(status='PENDING').count()
    in_transit = LogisticsOrder.objects.filter(status__in=['CREATED', 'IN_TRANSIT']).count()
    delivered = LogisticsOrder.objects.filter(status='DELIVERED').count()
    returned = LogisticsOrder.objects.filter(status='RETURNED').count()
    
    # Recent logistics orders
    recent_orders = LogisticsOrder.objects.select_related('provider').order_by('-created_at')[:15]
    
    # Orders ready to send to NCM (confirmed but not sent yet)
    try:
        ready_to_send = Order.objects.filter(
            order_status='confirmed',
            ncm_order_id__isnull=True,
            is_deleted=False
        ).count()
    except:
        ready_to_send = 0
    
    # Providers
    providers = LogisticsProvider.objects.filter(is_active=True)
    
    context = {
        'total_orders': total_orders,
        'pending': pending,
        'in_transit': in_transit,
        'delivered': delivered,
        'returned': returned,
        'ready_to_send': ready_to_send,
        'recent_orders': recent_orders,
        'providers': providers,
    }
    
    return render(request, 'logistics/dashboard.html', context)


@login_required
def send_order_to_ncm(request, order_id):
    """Send individual order to NCM"""
    
    order = get_object_or_404(Order, id=order_id, is_deleted=False)
    
    # Check if already sent
    if order.ncm_order_id:
        messages.warning(request, f'Order #{order.order_number} already sent to NCM (ID: {order.ncm_order_id})')
        return redirect('order_detail', order_id=order.id)
    
    try:
        # Prepare order data for NCM
        order_data = {
            'order_reference': str(order.order_number),
            'customer_name': order.customer_name,
            'customer_phone': order.customer_phone,
            'customer_address': f"{order.shipping_address}, {order.landmark}" if order.landmark else order.shipping_address,
            'cod_amount': float(order.total_amount) if order.payment_method == 'cod' else 0,
            'fbranch': order.branch_city.upper() if order.branch_city else 'TINKUNE',
            'branch': 'KATHMANDU',
            'package': f'Order #{order.order_number}',
        }
        
        # Create in NCM
        logistics_order = create_ncm_order(order_data)
        
        # Update order with NCM details
        order.ncm_order_id = logistics_order.ncm_order_id
        order.ncm_status = logistics_order.status
        order.ncm_last_synced = timezone.now()
        order.logistics_provider = 'ncm'
        order.tracking_number = logistics_order.ncm_order_id
        order.save()
        
        messages.success(request, f'✓ Order sent to NCM successfully! Tracking ID: {logistics_order.ncm_order_id}')
        
    except Exception as e:
        messages.error(request, f'✗ Failed to send to NCM: {str(e)}')
    
    return redirect('order_detail', order_id=order.id)


@login_required
def bulk_send_to_ncm(request):
    """Send multiple orders to NCM at once"""
    
    if request.method == 'POST':
        order_ids = request.POST.getlist('order_ids')
        
        if not order_ids:
            messages.warning(request, 'No orders selected')
            return redirect('orders_list')
        
        success_count = 0
        error_count = 0
        
        for order_id in order_ids:
            try:
                order = Order.objects.get(id=order_id, is_deleted=False)
                
                if order.ncm_order_id:
                    continue
                
                order_data = {
                    'order_reference': str(order.order_number),
                    'customer_name': order.customer_name,
                    'customer_phone': order.customer_phone,
                    'customer_address': f"{order.shipping_address}, {order.landmark}" if order.landmark else order.shipping_address,
                    'cod_amount': float(order.total_amount) if order.payment_method == 'cod' else 0,
                    'fbranch': order.branch_city.upper() if order.branch_city else 'TINKUNE',
                    'branch': 'KATHMANDU',
                    'package': f'Order #{order.order_number}',
                }
                
                logistics_order = create_ncm_order(order_data)
                
                order.ncm_order_id = logistics_order.ncm_order_id
                order.ncm_status = logistics_order.status
                order.ncm_last_synced = timezone.now()
                order.logistics_provider = 'ncm'
                order.tracking_number = logistics_order.ncm_order_id
                order.save()
                
                success_count += 1
                
            except Exception as e:
                error_count += 1
                print(f"Error sending order {order_id}: {str(e)}")
        
        if success_count > 0:
            messages.success(request, f'✓ {success_count} order(s) sent to NCM')
        if error_count > 0:
            messages.error(request, f'✗ {error_count} order(s) failed')
    
    return redirect('orders_list')


@login_required
def sync_ncm_status_view(request, order_id):
    """Sync single order status from NCM"""
    
    order = get_object_or_404(Order, id=order_id, is_deleted=False)
    
    if not order.ncm_order_id:
        messages.warning(request, 'Order not sent to NCM yet')
        return redirect('order_detail', order_id=order.id)
    
    try:
        logistics_order = LogisticsOrder.objects.get(order_reference=order.order_number)
        
        service = NCMService()
        new_status = service.sync_status(logistics_order)
        
        order.ncm_status = new_status
        order.ncm_last_synced = timezone.now()
        order.save()
        
        messages.success(request, f'✓ Status updated: {new_status}')
        
    except LogisticsOrder.DoesNotExist:
        messages.error(request, 'Logistics order not found')
    except Exception as e:
        messages.error(request, f'✗ Failed to sync: {str(e)}')
    
    return redirect('order_detail', order_id=order.id)


@login_required
def bulk_sync_ncm_status(request):
    """Sync status for all NCM orders"""
    
    if request.method == 'POST':
        ncm_orders = Order.objects.filter(
            ncm_order_id__isnull=False,
            is_deleted=False
        ).exclude(ncm_status__in=['DELIVERED', 'RETURNED', 'CANCELLED'])
        
        service = NCMService()
        success_count = 0
        error_count = 0
        
        for order in ncm_orders:
            try:
                logistics_order = LogisticsOrder.objects.get(order_reference=order.order_number)
                new_status = service.sync_status(logistics_order)
                
                order.ncm_status = new_status
                order.ncm_last_synced = timezone.now()
                order.save()
                
                success_count += 1
                
            except:
                error_count += 1
        
        if success_count > 0:
            messages.success(request, f'✓ Synced {success_count} order(s)')
        if error_count > 0:
            messages.warning(request, f'⚠ {error_count} order(s) failed')
    
    return redirect('logistics_dashboard')


@login_required
def ncm_branches_list(request):
    """Show all NCM branches"""
    
    search = request.GET.get('search', '')  # ✅ Define search FIRST
    
    try:
        service = NCMService()
        branches = service.get_branches()
        
        if search:
            branches = [b for b in branches if search.lower() in b['name'].lower() or search.lower() in b['district_name'].lower()]
        
    except Exception as e:
        branches = []
        messages.error(request, f'Failed to load branches: {str(e)}')
    
    context = {
        'branches': branches,
        'search': search,
    }
    
    return render(request, 'logistics/branches.html', context)

@login_required
def logistics_orders_list(request):
    """List all logistics orders"""
    
    orders = LogisticsOrder.objects.select_related('provider').order_by('-created_at')
    
    status_filter = request.GET.get('status')
    if status_filter:
        orders = orders.filter(status=status_filter)
    
    search = request.GET.get('search')
    if search:
        orders = orders.filter(
            Q(order_reference__icontains=search) |
            Q(ncm_order_id__icontains=search) |
            Q(customer_name__icontains=search)
        )
    
    context = {
        'orders': orders,
        'status_filter': status_filter,
        'search': search,
    }
    
    return render(request, 'logistics/orders_list.html', context)
