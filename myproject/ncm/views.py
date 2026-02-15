# ncm/views.py
"""
NCM (Nepal Can Move) Integration Views
Handles order creation, status sync, and webhook processing
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db import transaction
from django.conf import settings

# Import NCM service from services folder
from services.ncm_service import NCMService

# Import models from accounts app
from dashboard.models import Order, OrderActivityLog
from ncm.models import WebhookLog

import json
import logging
import hmac
import hashlib
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger('ncm')
ncm_service = NCMService()


# ===================== WEBHOOK SECURITY UTILITIES =====================

def get_client_ip(request):
    """Extract client IP from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def verify_ncm_webhook_signature(request, payload_bytes):
    """
    Verify NCM webhook signature if configured.
    Checks HMAC-SHA256 signature against a configured secret.
    """
    webhook_secret = getattr(settings, 'NCM_WEBHOOK_SECRET', None)
    if not webhook_secret:
        logger.warning("NCM_WEBHOOK_SECRET not configured. Skipping signature verification.")
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
    
    # Compare signatures (constant-time comparison to prevent timing attacks)
    return hmac.compare_digest(signature_header, expected_signature)


def get_or_create_system_user():
    """
    Get or create a system user for webhook operations.
    This allows webhook activity to be tracked with a system identity.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
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


def is_order_eligible_for_status_update(order):
    """
    Check if order is in a state that should receive status updates.
    Prevents updating already delivered orders or cancelled ones.
    """
    ineligible_statuses = ['delivered', 'cancelled', 'return_initiated', 'return_approved']
    
    if order.status in ineligible_statuses:
        logger.info(f"Order {order.order_number} not eligible for webhook update (status: {order.status})")
        return False
    
    if not order.ncm_order_id:
        logger.warning(f"Order {order.order_number} has no NCM order ID")
        return False
    
    return True


# ===================== BRANCHES JSON ENDPOINT =====================

@login_required
@require_http_methods(["GET"])
def branches_json(request):
    """Return NCM branches as JSON for frontend dropdown"""
    try:
        result = ncm_service.get_branches()
        
        if result['success']:
            branches = [
                {
                    'code': b.get('code', b.get('Code', '')).upper(),
                    'name': b.get('name', b.get('Name', b.get('code', b.get('Code', ''))))
                }
                for b in result['data']
            ]
            data = {'success': True, 'branches': branches}
        else:
            data = {'success': False, 'branches': [], 'error': result.get('error', 'Unable to fetch branches')}
    
    except Exception as e:
        logger.error(f"Error fetching branches: {str(e)}")
        data = {'success': False, 'branches': [], 'error': str(e)}
    
    return JsonResponse(data)


# ===================== ORDER CREATION IN NCM =====================

@login_required
@require_POST
def create_ncm_shipment(request, order_id):
    """Create NCM shipment for an existing order"""
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        
        if order.ncm_order_id:
            messages.error(request, f'Order already exists in NCM with ID: {order.ncm_order_id}')
            return redirect('order_detail', order_id=order_id)
        
        if order.logistics != 'ncm':
            messages.error(request, 'Please set order logistics to NCM first')
            return redirect('order_detail', order_id=order_id)
        
        if not order.customer_name or not order.customer_phone or not order.shipping_address:
            messages.error(request, 'Customer name, phone, and address are required')
            return redirect('order_detail', order_id=order_id)
        
        # Get the destination branch from the form (both code and name)
        to_branch_code = (request.POST.get('ncm_destination_branch', '').strip() or '').upper()
        to_branch_name = (request.POST.get('ncm_branch_name', '').strip() or '').upper()
        
        if not to_branch_code or not to_branch_name:
            messages.error(request, '❌ Please select a destination branch')
            return redirect('order_detail', order_id=order_id)
        
        # Validate branch exists in NCM
        branches_result = ncm_service.get_branches()
        if branches_result['success']:
            available_codes = {(b.get('code', b.get('Code', '')).upper()) for b in branches_result['data']}
            available_names = {(b.get('name', b.get('Name', '')).upper()) for b in branches_result['data']}
            
            logger.info(f"Available NCM branches (codes): {available_codes}")
            logger.info(f"Available NCM branches (names): {available_names}")
            logger.info(f"User selected code: {to_branch_code}, name: {to_branch_name}")
            
            if to_branch_code not in available_codes:
                logger.error(f"Invalid TO branch code: '{to_branch_code}'")
                messages.error(request, f"❌ Invalid branch code: '{to_branch_code}'. Please select a valid NCM branch.")
                return redirect('order_detail', order_id=order_id)
            
            if to_branch_name not in available_names:
                logger.error(f"Invalid TO branch name: '{to_branch_name}'")
                messages.error(request, f"❌ Invalid branch name: '{to_branch_name}'. Please select a valid NCM branch.")
                return redirect('order_detail', order_id=order_id)
        else:
            logger.warning(f"Could not fetch branches: {branches_result.get('error')}")
            messages.warning(request, f"Could not validate branches: {branches_result.get('error')}")
        
        # VALIDATE CUSTOMER NAME - must not be empty or contain user's name
        customer_name = (order.customer_name or '').strip()
        if not customer_name or len(customer_name) < 2:
            messages.error(request, '❌ Customer name is required and must be at least 2 characters')
            return redirect('order_detail', order_id=order_id)
        
        logger.info(f"Verified customer name: '{customer_name}' (length: {len(customer_name)})")
        
        from_branch = order.ncm_from_branch or 'TINKUNE'
        
        # Prepare NCM data - use branch NAME for NCM API (not code)
        # IMPORTANT: 'name' field is customer/receiver name, NOT admin/staff name
        ncm_data = {
            'name': customer_name,  # This MUST be the customer's name from order.customer_name
            'phone': order.customer_phone,
            'phone2': '',
            'cod_charge': str(order.total_amount),
            'address': order.shipping_address,
            'fbranch': from_branch,
            'branch': to_branch_name,
            'package': _get_package_description(order),
            'vref_id': order.order_number,
            'instruction': order.notes or '',
            'delivery_type': getattr(order, 'ncm_delivery_type', 'Door2Door'),
            'weight': str(getattr(order, 'package_weight', 1)),
        }
        
        if order.customer and hasattr(order.customer, 'alternate_phone') and order.customer.alternate_phone:
            ncm_data['phone2'] = order.customer.alternate_phone
        
        logger.info(f"Creating NCM order for: {order.order_number}")
        logger.info(f"✅ Customer Name: '{customer_name}' (will be sent to NCM as 'name' field)")
        logger.info(f"NCM Data: name={ncm_data['name']}, phone={ncm_data['phone']}, address={ncm_data['address']}, fbranch={from_branch}, branch={to_branch_name}")
        logger.info(f"Full NCM Data: {ncm_data}")
        
        result = ncm_service.create_order(ncm_data)
        
        if result['success']:
            ncm_order_id = result['data'].get('orderid')
            
            order.ncm_order_id = ncm_order_id
            order.ncm_status = 'Order Created'
            order.ncm_created_at = timezone.now()
            order.ncm_destination_branch = to_branch_name  # Store the branch name
            order.status = 'processing'
            order.save()
            
            OrderActivityLog.objects.create(
                order=order,
                action_type='updated',
                user=request.user,
                field_name='ncm_integration',
                new_value=f'NCM Order ID: {ncm_order_id}',
                description=f'Order created in NCM with ID: {ncm_order_id}'
            )
            
            logger.info(f"NCM Order created: {order.order_number} -> NCM ID: {ncm_order_id}")
            messages.success(request, f'✓ Order created in NCM! ID: {ncm_order_id}')
        else:
            error_msg = result.get('error', 'Unknown error')
            logger.error(f"Failed to create NCM order: {error_msg}")
            logger.error(f"NCM Data sent: {ncm_data}")
            messages.error(request, f'Failed: {error_msg}')
        
        return redirect('order_detail', order_id=order_id)
        
    except Order.DoesNotExist:
        messages.error(request, 'Order not found')
        return redirect('orders_list')
    
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        messages.error(request, f'Error: {str(e)}')
        return redirect('order_detail', order_id=order_id)


@login_required
@require_http_methods(["GET", "POST"])
def sync_ncm_status(request, order_id):
    """Manually sync order status from NCM"""
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        
        if not order.ncm_order_id:
            messages.error(request, 'Order not yet in NCM')
            return redirect('order_detail', order_id=order_id)
        
        logger.info(f"Syncing NCM Order ID: {order.ncm_order_id}")
        
        result = ncm_service.get_order_status(order.ncm_order_id)
        
        if result['success'] and result['data']:
            latest_status_data = result['data'][0]
            latest_status = latest_status_data['status']
            
            old_ncm_status = order.ncm_status
            order.ncm_status = latest_status
            order.status = ncm_service.map_ncm_status_to_system(latest_status)
            order.save()
            
            OrderActivityLog.objects.create(
                order=order,
                action_type='status_changed',
                user=request.user,
                field_name='ncm_status',
                old_value=old_ncm_status or 'None',
                new_value=latest_status,
                description=f'Manual sync: {old_ncm_status or "None"} → {latest_status}'
            )
            
            logger.info(f"Status synced: {order.order_number} -> {latest_status}")
            messages.success(request, f'✓ Synced! NCM: {latest_status} | System: {order.status}')
        else:
            error_msg = result.get('error', 'Unable to fetch')
            logger.error(f"Sync failed: {error_msg}")
            messages.error(request, f'Failed: {error_msg}')
        
        return redirect('order_detail', order_id=order_id)
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        messages.error(request, f'Error: {str(e)}')
        return redirect('order_detail', order_id=order_id)


@csrf_exempt
@require_POST
def ncm_webhook(request):
    """
    Receive and process webhook from NCM when status changes.
    
    Enhanced with:
    - Signature verification
    - Idempotency (prevents duplicate processing)
    - Transaction safety (atomic updates)
    - Order state validation
    - Comprehensive audit logging
    """
    payload_bytes = request.body
    webhook_log = None
    
    try:
        # 1. VERIFY WEBHOOK SIGNATURE
        if not verify_ncm_webhook_signature(request, payload_bytes):
            logger.error("❌ Webhook signature verification failed")
            return JsonResponse({
                'success': False,
                'message': 'Signature verification failed'
            }, status=401)
        
        # 2. PARSE JSON
        payload = json.loads(payload_bytes)
        logger.info(f"=== NCM Webhook Received ===")
        logger.info(f"Payload: {json.dumps(payload, indent=2)}")
        
        # 3. EXTRACT KEY FIELDS
        event = payload.get('event', 'unknown')
        timestamp_str = payload.get('timestamp')
        status = payload.get('status')
        webhook_id = payload.get('webhook_id', payload.get('id', 'unknown'))
        
        # Parse timestamp
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except (TypeError, ValueError):
            timestamp = timezone.now()
        
        # 4. HANDLE TEST WEBHOOKS
        if payload.get('test'):
            logger.info("✓ Test webhook received and processed")
            return JsonResponse({
                'success': True,
                'message': 'Test webhook received',
                'webhook_id': webhook_id
            })
        
        # 5. CHECK FOR DUPLICATE WEBHOOKS (IDEMPOTENCY)
        webhook_log, created = WebhookLog.objects.get_or_create(
            webhook_id=webhook_id,
            defaults={
                'event': event,
                'payload': payload,
                'status': 'processing',
                'source_ip': get_client_ip(request),
                'signature': request.META.get('HTTP_X_NCM_SIGNATURE', ''),
            }
        )
        
        if not created:
            # Webhook already processed
            logger.info(f"⚠️  Duplicate webhook detected: {webhook_id} (already processed)")
            return JsonResponse({
                'success': True,
                'message': 'Webhook already processed',
                'webhook_id': webhook_id,
                'processed_at': webhook_log.processed_at.isoformat() if webhook_log.processed_at else None,
                'status': 'duplicate'
            }, status=200)
        
        # 6. VALIDATE REQUIRED FIELDS
        order_ids = []
        if 'order_id' in payload:
            order_ids = [payload['order_id']]
        elif 'order_ids' in payload:
            order_ids = payload['order_ids']
        
        if not order_ids or not status:
            webhook_log.status = 'failed'
            webhook_log.error_message = 'Missing order_id(s) or status field'
            webhook_log.save()
            
            logger.error("❌ Missing required fields: order_ids or status")
            return JsonResponse({
                'success': False,
                'message': 'Missing required fields: order_id(s) or status',
                'webhook_id': webhook_id
            }, status=400)
        
        # 7. PROCESS UPDATES WITH TRANSACTION SAFETY
        updated_orders = []
        not_found_orders = []
        failed_orders = []
        system_user = get_or_create_system_user()
        
        with transaction.atomic():
            for ncm_order_id in order_ids:
                try:
                    # Get order with select_for_update to prevent race conditions
                    order = Order.objects.select_for_update().get(
                        ncm_order_id=ncm_order_id,
                        is_deleted=False
                    )
                    
                    # Validate order is eligible for status update
                    if not is_order_eligible_for_status_update(order):
                        failed_orders.append({
                            'ncm_order_id': ncm_order_id,
                            'order_number': order.order_number,
                            'reason': f'Order not eligible (current status: {order.status})'
                        })
                        continue
                    
                    # Map NCM status to system status
                    old_ncm_status = order.ncm_status
                    try:
                        system_status = ncm_service.map_ncm_status_to_system(status)
                    except Exception as e:
                        logger.error(f"Error mapping NCM status '{status}': {str(e)}")
                        failed_orders.append({
                            'ncm_order_id': ncm_order_id,
                            'order_number': order.order_number,
                            'reason': f'Status mapping failed: {str(e)}'
                        })
                        continue
                    
                    # Update order status
                    order.ncm_status = status
                    order.status = system_status
                    order.save(update_fields=['ncm_status', 'status', 'updated_at'])
                    
                    # Create activity log with system user
                    OrderActivityLog.objects.create(
                        order=order,
                        action_type='status_changed',
                        user=system_user,  # System user for webhooks
                        field_name='ncm_status',
                        old_value=old_ncm_status or 'None',
                        new_value=status,
                        description=f'NCM Webhook: {event} -> {status}'
                    )
                    
                    logger.info(f"✓ Updated: {order.order_number} ({ncm_order_id}) -> {status}")
                    updated_orders.append({
                        'order_number': order.order_number,
                        'ncm_order_id': ncm_order_id,
                        'old_status': old_ncm_status,
                        'new_status': status
                    })
                    
                except Order.DoesNotExist:
                    logger.warning(f"✗ Order not found: NCM ID {ncm_order_id}")
                    not_found_orders.append(ncm_order_id)
                
                except Exception as e:
                    logger.error(f"Error processing order {ncm_order_id}: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    failed_orders.append({
                        'ncm_order_id': ncm_order_id,
                        'reason': str(e)
                    })
        
        # 8. UPDATE WEBHOOK LOG
        webhook_log.status = 'completed'
        webhook_log.updated_orders_count = len(updated_orders)
        webhook_log.failed_orders_count = len(not_found_orders) + len(failed_orders)
        webhook_log.processed_at = timezone.now()
        webhook_log.response_data = {
            'updated_count': len(updated_orders),
            'not_found_count': len(not_found_orders),
            'failed_count': len(failed_orders)
        }
        webhook_log.save()
        
        # 9. RETURN RESPONSE
        response_data = {
            'success': True,
            'message': 'Webhook processed successfully',
            'webhook_id': webhook_id,
            'event': event,
            'ncm_status': status,
            'updated_count': len(updated_orders),
            'updated_orders': updated_orders,
            'not_found_count': len(not_found_orders),
            'not_found_orders': not_found_orders,
            'failed_count': len(failed_orders),
            'failed_orders': failed_orders
        }
        
        logger.info(f"✅ Webhook completed: {len(updated_orders)} updated, "
                   f"{len(not_found_orders)} not found, {len(failed_orders)} failed")
        
        return JsonResponse(response_data, status=200)
        
    except json.JSONDecodeError as e:
        logger.error(f"❌ Invalid JSON received: {str(e)}")
        if webhook_log:
            webhook_log.status = 'failed'
            webhook_log.error_message = f'Invalid JSON: {str(e)}'
            webhook_log.save()
        
        return JsonResponse({
            'success': False,
            'message': 'Invalid JSON payload'
        }, status=400)
    
    except Exception as e:
        logger.error(f"❌ Webhook processing error: {str(e)}")
        import traceback
        traceback.print_exc()
        
        if webhook_log:
            webhook_log.status = 'failed'
            webhook_log.error_message = str(e)
            webhook_log.save()
        
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=500)


@login_required
def ncm_branches_list(request):
    """Display NCM branches"""
    result = ncm_service.get_branches()
    
    branches = []
    error_message = None
    
    if result['success']:
        branches = result['data']
        logger.info(f"Fetched {len(branches)} branches")
    else:
        error_message = result.get('error')
        logger.error(f"Failed to fetch branches: {error_message}")
        messages.error(request, f"Failed: {error_message}")
    
    context = {
        'branches': branches,
        'total_branches': len(branches),
        'error_message': error_message
    }
    
    return render(request, 'ncm/branches.html', context)


@login_required
def track_ncm_order(request, order_id):
    """View tracking details"""
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        
        if not order.ncm_order_id:
            messages.error(request, 'Order not in NCM yet')
            return redirect('order_detail', order_id=order_id)
        
        details_result = ncm_service.get_order_details(order.ncm_order_id)
        status_result = ncm_service.get_order_status(order.ncm_order_id)
        
        context = {
            'order': order,
            'ncm_details': details_result.get('data') if details_result['success'] else None,
            'ncm_status_history': status_result.get('data') if status_result['success'] else [],
            'details_error': None if details_result['success'] else details_result.get('error'),
            'status_error': None if status_result['success'] else status_result.get('error')
        }
        
        return render(request, 'ncm/tracking.html', context)
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        messages.error(request, f'Error: {str(e)}')
        return redirect('order_detail', order_id=order_id)


@login_required
@require_http_methods(["GET", "POST"])
def bulk_sync_ncm_orders(request):
    """Sync multiple NCM orders at once"""
    try:
        ncm_orders = Order.objects.filter(
            ncm_order_id__isnull=False,
            is_deleted=False,
            status__in=['processing', 'shipped']
        )
        
        if not ncm_orders.exists():
            messages.info(request, 'No NCM orders to sync')
            return redirect('orders_list')
        
        ncm_order_ids = list(ncm_orders.values_list('ncm_order_id', flat=True))
        
        result = ncm_service.get_bulk_order_statuses(ncm_order_ids)
        
        if result['success']:
            status_data = result['data'].get('result', {})
            updated_count = 0
            
            for order in ncm_orders:
                if str(order.ncm_order_id) in status_data:
                    new_status = status_data[str(order.ncm_order_id)]
                    old_status = order.ncm_status
                    
                    order.ncm_status = new_status
                    order.status = ncm_service.map_ncm_status_to_system(new_status)
                    order.save()
                    
                    OrderActivityLog.objects.create(
                        order=order,
                        action_type='status_changed',
                        user=request.user,
                        field_name='ncm_status',
                        old_value=old_status,
                        new_value=new_status,
                        description=f'Bulk sync: {new_status}'
                    )
                    
                    updated_count += 1
            
            messages.success(request, f'✓ Synced {updated_count} orders')
            logger.info(f"Bulk sync: {updated_count} orders")
        else:
            messages.error(request, f'Failed: {result.get("error")}')
        
        return redirect('orders_list')
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        messages.error(request, f'Error: {str(e)}')
        return redirect('orders_list')


def _get_package_description(order):
    """Generate package description from order items"""
    try:
        items = order.items.select_related('product_variation').all()[:3]
        if items:
            product_names = []
            for item in items:
                qty = getattr(item, 'quantity', 1) or 1
                name = item.product_name or 'Item'
                var_name = item.variation_name or (item.product_variation.variation_name if item.product_variation else None)
                if var_name:
                    name = f"{name} ({var_name})"
                product_names.append(f"{qty}x {name}")
            description = ', '.join(product_names)

            total_items = order.items.count()
            if total_items > 3:
                description += f' and {total_items - 3} more'

            return description
        return 'Products'
    except Exception:
        return 'E-commerce Products'
