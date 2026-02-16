# ncm/realtime_api.py
"""
Real-time API endpoints for order status fetching and updates
Enables real-time synchronization for order detail, list, and edit pages
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from dashboard.models import Order, OrderActivityLog
from services.ncm_service import NCMService
import logging
import json
from decimal import Decimal

logger = logging.getLogger('ncm')
ncm_service = NCMService()


@login_required
@require_http_methods(["GET"])
def api_get_order_status(request, order_id):
    """
    Real-time API to fetch current order status from NCM and local DB
    Used by frontend for auto-refresh
    
    Returns JSON with order details and status info
    """
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        
        # If no NCM order ID, return local status
        if not order.ncm_order_id:
            return JsonResponse({
                'success': True,
                'order_id': order.id,
                'order_number': order.order_number,
                'status': order.status,
                'order_status': order.order_status,
                'ncm_status': order.ncm_status or 'Not sent to NCM',
                'ncm_order_id': None,
                'payment_status': order.payment_status,
                'payment_method': order.payment_method,
                'delivered_at': order.delivered_at.isoformat() if order.delivered_at else None,
                'cod_collected': float(order.cod_collected or 0),
                'total_amount': float(order.total_amount or 0),
                'source': 'local',
                'timestamp': timezone.now().isoformat()
            })
        
        # Try to fetch latest status from NCM
        ncm_status_result = ncm_service.get_order_status(order.ncm_order_id)
        
        status_data = {
            'success': True,
            'order_id': order.id,
            'order_number': order.order_number,
            'status': order.status,
            'order_status': order.order_status,
            'ncm_status': order.ncm_status,
            'ncm_order_id': order.ncm_order_id,
            'payment_status': order.payment_status,
            'payment_method': order.payment_method,
            'delivered_at': order.delivered_at.isoformat() if order.delivered_at else None,
            'cod_collected': float(order.cod_collected or 0),
            'total_amount': float(order.total_amount or 0),
            'source': 'hybrid',  # local + NCM
            'timestamp': timezone.now().isoformat()
        }
        
        # Add NCM remote status if available
        if ncm_status_result['success']:
            ncm_data = ncm_status_result['data']
            status_data['ncm_remote_status'] = ncm_data
        
        return JsonResponse(status_data)
        
    except Exception as e:
        logger.error(f"Error fetching order status: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=500)


@login_required
@require_http_methods(["POST"])
def api_sync_order_status(request, order_id):
    """
    Manually sync order status from NCM immediately
    Called when user clicks refresh button
    """
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        
        if not order.ncm_order_id:
            return JsonResponse({
                'success': False,
                'message': 'Order not in NCM system yet'
            })
        
        # Fetch latest status from NCM
        result = ncm_service.get_order_status(order.ncm_order_id)
        
        if not result['success']:
            return JsonResponse({
                'success': False,
                'message': f"Failed to fetch from NCM: {result.get('error')}"
            }, status=400)
        
        status_data = result['data']
        
        # Extract latest status
        if isinstance(status_data, dict) and 'last_status' in status_data:
            latest_status = status_data['last_status']
        elif isinstance(status_data, list) and len(status_data) > 0:
            latest_status = status_data[0].get('status') or status_data[0].get('Status')
        else:
            latest_status = order.ncm_status
        
        # Map to system status
        old_status = order.status
        old_ncm_status = order.ncm_status
        system_status = ncm_service.map_ncm_status_to_system(latest_status)
        
        # Update if different
        if latest_status != order.ncm_status or system_status != order.status:
            order.ncm_status = latest_status
            order.status = system_status
            order.save(update_fields=['ncm_status', 'status', 'updated_at'])
            
            # Create activity log
            OrderActivityLog.objects.create(
                order=order,
                action_type='status_changed',
                user=request.user,
                field_name='ncm_status',
                old_value=old_ncm_status,
                new_value=latest_status,
                description=f'Manual API sync: {old_status} → {system_status}'
            )
            
            logger.info(f"✓ API Sync: {order.order_number} -> {system_status}")
            
            return JsonResponse({
                'success': True,
                'message': 'Status updated',
                'order_id': order.id,
                'order_number': order.order_number,
                'old_status': old_status,
                'new_status': system_status,
                'ncm_status': latest_status,
                'changed': True
            })
        else:
            return JsonResponse({
                'success': True,
                'message': 'Status unchanged',
                'order_id': order.id,
                'order_number': order.order_number,
                'status': system_status,
                'changed': False
            })
        
    except Exception as e:
        logger.error(f"Error syncing order: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def api_get_orders_status_batch(request):
    """
    Fetch status for multiple orders at once
    Used by orders list page for real-time updates
    
    Query params:
    - order_ids: comma-separated list of order IDs
    - status_filter: filter by status (optional)
    """
    try:
        # Parse order IDs from query params
        order_ids_param = request.GET.get('order_ids', '')
        order_ids = [int(oid.strip()) for oid in order_ids_param.split(',') if oid.strip()]
        
        if not order_ids:
            return JsonResponse({
                'success': False,
                'message': 'No order IDs provided'
            }, status=400)
        
        orders = Order.objects.filter(
            id__in=order_ids,
            is_deleted=False
        ).values(
            'id', 'order_number', 'status', 'order_status', 
            'ncm_status', 'ncm_order_id', 'payment_status',
            'delivered_at', 'cod_collected', 'created_at', 'updated_at'
        )
        
        orders_data = []
        for order in orders:
            orders_data.append({
                'id': order['id'],
                'order_number': order['order_number'],
                'status': order['status'],
                'order_status': order['order_status'],
                'ncm_status': order['ncm_status'],
                'ncm_order_id': order['ncm_order_id'],
                'payment_status': order['payment_status'],
                'delivered_at': order['delivered_at'].isoformat() if order['delivered_at'] else None,
                'cod_collected': float(order['cod_collected'] or 0),
                'created_at': order['created_at'].isoformat(),
                'updated_at': order['updated_at'].isoformat()
            })
        
        return JsonResponse({
            'success': True,
            'count': len(orders_data),
            'orders': orders_data,
            'timestamp': timezone.now().isoformat()
        })
        
    except ValueError:
        return JsonResponse({
            'success': False,
            'message': 'Invalid order IDs format'
        }, status=400)
    except Exception as e:
        logger.error(f"Error fetching batch orders: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def api_get_order_activity_log(request, order_id):
    """
    Fetch activity log for an order
    Used for real-time updates in order detail page
    
    Query params:
    - limit: number of recent activities to fetch (default: 10)
    """
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        limit = int(request.GET.get('limit', 10))
        
        activities = OrderActivityLog.objects.filter(
            order=order
        ).select_related('user').order_by('-created_at')[:limit]
        
        activities_data = []
        for activity in activities:
            activities_data.append({
                'id': activity.id,
                'action_type': activity.action_type,
                'field_name': activity.field_name,
                'old_value': activity.old_value,
                'new_value': activity.new_value,
                'description': activity.description,
                'user': activity.user.get_full_name() if activity.user else 'System',
                'created_at': activity.created_at.isoformat()
            })
        
        return JsonResponse({
            'success': True,
            'order_id': order_id,
            'activities': activities_data,
            'count': len(activities_data),
            'timestamp': timezone.now().isoformat()
        })
        
    except ValueError:
        return JsonResponse({
            'success': False,
            'message': 'Invalid limit parameter'
        }, status=400)
    except Exception as e:
        logger.error(f"Error fetching activity log: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def api_check_pending_ncm_updates(request):
    """
    Check for NCM orders that might have updates
    Used for periodic polling to detect status changes
    
    Returns list of NCM orders that haven't been updated in last X minutes
    """
    try:
        from datetime import timedelta
        
        # Find NCM orders not updated in last 30 minutes
        threshold = timezone.now() - timedelta(minutes=30)
        
        pending_orders = Order.objects.filter(
            ncm_order_id__isnull=False,
            is_deleted=False,
            status__in=['processing', 'in_transit', 'shipped'],
            updated_at__lt=threshold
        ).values('id', 'order_number', 'ncm_order_id', 'status', 'ncm_status', 'updated_at')
        
        pending_data = list(pending_orders)
        
        return JsonResponse({
            'success': True,
            'pending_count': len(pending_data),
            'pending_orders': pending_data,
            'threshold_minutes': 30,
            'timestamp': timezone.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error checking pending updates: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=500)
