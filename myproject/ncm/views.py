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
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from django.db import transaction
from django.conf import settings
from functools import wraps

# Import NCM service from services folder
from accounts.decorators import has_any_permission
from services.ncm_service import NCMService
from services.status_override import (clear_manual_status_override,
                                      manual_override_holds)
from ncm.webhook_handler import NCMWebhookHandler
from ncm.bulk_sync import run_bulk_ncm_status_sync

# Import models from accounts app
from dashboard.models import Order, OrderActivityLog
from dashboard.timezone_utils import parse_ncm_datetime
from ncm.models import WebhookLog

import json
import logging
import hmac

logger = logging.getLogger('ncm')
webhook_logger = logging.getLogger('webhook')
ncm_service = NCMService()
webhook_handler = NCMWebhookHandler()


# ===================== NCM PERMISSION DECORATORS =====================

def ncm_permission_required(permission_field):
    """
    Decorator to check if user has required NCM permission.
    Redirects to forbidden page if user lacks permission.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Same admin bypass as every other permission check in the project
            if not has_any_permission(request.user, permission_field):
                messages.error(request, '❌ You do not have permission to access this page')
                logger.warning(f"Access denied for user {request.user.username} - Missing permission: {permission_field}")
                return redirect('orders_list')
            
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator



def get_client_ip(request):
    """Extract client IP from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def verify_ncm_webhook(request, payload_bytes):
    """
    Verify NCM webhook request authenticity.

    NCM does NOT send HMAC signatures. Authentication can be done via:
    1. Query parameter token in the webhook URL (e.g., ?token=your-secret)
    2. Checking the User-Agent header (NCM-Webhook/1.0)

    If NCM_WEBHOOK_SECRET is configured and the webhook URL includes a token
    query parameter, the token is validated against the secret.
    """
    # Verify User-Agent header (NCM sends 'NCM-Webhook/1.0')
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    if user_agent and 'NCM-Webhook' not in user_agent:
        webhook_logger.warning(f"Unexpected User-Agent for NCM webhook: {user_agent}")

    # If a token query parameter is present, validate against the secret
    webhook_secret = getattr(settings, 'NCM_WEBHOOK_SECRET', None)
    token = request.GET.get('token', '')

    if webhook_secret and token:
        if not hmac.compare_digest(token, webhook_secret):
            webhook_logger.error("Webhook token verification failed")
            return False
        webhook_logger.info("Webhook token verified successfully")

    return True


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


# ===================== BRANCHES JSON ENDPOINT =====================

@login_required
@ncm_permission_required('can_view_ncm_branches')
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
@ncm_permission_required('can_create_ncm_orders')
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
        
        # Use dynamic API config if provided, otherwise use default module-level service
        api_config_id = request.POST.get('api_config_id', '') or None
        if api_config_id:
            active_service = NCMService(api_config_id=int(api_config_id))
        else:
            active_service = ncm_service
        
        # Validate branch exists in NCM
        branches_result = active_service.get_branches()
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
        # For partial payments, send remaining amount as COD (not full total)
        cod_amount = order.remaining_amount if order.is_partial_payment and order.remaining_amount is not None else order.total_amount
        ncm_data = {
            'name': customer_name,  # This MUST be the customer's name from order.customer_name
            'phone': order.customer_phone,
            'phone2': '',
            'cod_charge': str(cod_amount),
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
        
        result = active_service.create_order(ncm_data)
        
        if result['success']:
            ncm_order_id = result['data'].get('orderid')
            
            order.ncm_order_id = ncm_order_id
            order.ncm_status = 'Order Created'
            order.ncm_created_at = timezone.now()
            order.ncm_destination_branch = to_branch_name  # Store the branch name
            order.status = 'processing'
            # A fresh NCM order restarts the parcel's lifecycle, so any manual
            # status hold from before it was handed over no longer applies.
            clear_manual_status_override(order)
            order.save()
            
            # Immediately fetch the actual status from NCM so it shows "Pickup Created"
            status_result = active_service.get_order_status(ncm_order_id)
            if status_result['success'] and status_result['data']:
                latest_status_data = status_result['data'][0]
                latest_status = latest_status_data.get('status') or latest_status_data.get('Status', '')
                system_status, payment_status = active_service.resolve_delivered_status(latest_status_data)
                
                order.ncm_status = latest_status
                update_fields = active_service.sync_order_status_fields(order, system_status, payment_status)
                update_fields.extend(['ncm_status', 'updated_at'])
                
                if system_status == 'delivered' and not order.delivered_at:
                    order.delivered_at = (
                        parse_ncm_datetime(latest_status_data.get('added_time')) or timezone.now()
                    )
                    update_fields.append('delivered_at')

                order.save(update_fields=list(dict.fromkeys(update_fields)))
            
            OrderActivityLog.objects.create(
                order=order,
                action_type='updated',
                user=request.user,
                field_name='ncm_integration',
                new_value=f'NCM Order ID: {ncm_order_id}',
                description=f'Order created in NCM with ID: {ncm_order_id}'
            )
            
            logger.info(f"NCM Order created: {order.order_number} -> NCM ID: {ncm_order_id}")
            messages.success(request, f'[SUCCESS] Order created in NCM! ID: {ncm_order_id}')
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
@ncm_permission_required('can_sync_ncm_orders')
@require_http_methods(["GET", "POST"])
def sync_ncm_status(request, order_id):
    """Manually sync order status from NCM"""
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)

        if not order.ncm_order_id:
            messages.error(request, 'Order not yet in NCM')
            return redirect('order_detail', order_id=order_id)

        if order.status == 'cancelled':
            messages.info(request, 'Order is cancelled; status sync skipped to avoid overwriting the cancellation')
            return redirect('order_detail', order_id=order_id)

        logger.info(f"Syncing NCM Order ID: {order.ncm_order_id}")
        
        # Use order-specific NCM API account
        svc = NCMService(api_config_id=order.api_config_id) if order.api_config_id else ncm_service
        
        result = svc.get_order_status(order.ncm_order_id)
        
        if result['success'] and result['data']:
            latest_status_data = result['data'][0]
            latest_status = latest_status_data.get('status') or latest_status_data.get('Status', '')

            old_ncm_status = order.ncm_status
            old_status = order.status
            old_payment_status = order.payment_status

            # NCM's own timestamp for this status, not the moment we synced.
            event_at = parse_ncm_datetime(latest_status_data.get('added_time'))

            # A status staff set by hand outranks NCM until the parcel really
            # moves - the same guard the page-load sync and the webhook apply.
            if manual_override_holds(order, latest_status, event_at):
                messages.info(
                    request,
                    'Status was set manually and NCM still reports the same status; keeping the manual status.'
                )
                return redirect('order_detail', order_id=order_id)

            # Use resolve_delivered_status to handle vendor_return flag
            system_status, payment_status = svc.resolve_delivered_status(latest_status_data)

            order.ncm_status = latest_status

            # Update all status-related fields (status, order_status, status_setup FK, payment fields)
            update_fields = svc.sync_order_status_fields(order, system_status, payment_status)
            update_fields.append('ncm_status')
            update_fields.append('updated_at')
            # NCM has moved past whatever was set by hand, so retire the hold.
            update_fields.extend(clear_manual_status_override(order))

            if system_status == 'delivered' and not order.delivered_at:
                order.delivered_at = event_at or timezone.now()
                update_fields.append('delivered_at')

            # Deduplicate
            update_fields = list(dict.fromkeys(update_fields))
            order.save(update_fields=update_fields)

            OrderActivityLog.objects.create(
                order=order,
                action_type='status_changed',
                user=request.user,
                field_name='ncm_status',
                old_value=old_ncm_status or 'None',
                new_value=latest_status,
                event_at=event_at,
                description=f'Manual sync: {old_status} → {system_status}'
                            + (f', payment: {old_payment_status} → {payment_status}' if payment_status else '')
            )

            logger.info(f"Status synced: {order.order_number} -> {system_status} (NCM: {latest_status})")
            messages.success(request, f'[SUCCESS] Synced! NCM: {latest_status} | System: {system_status}'
                           + (f' | Payment: {payment_status}' if payment_status else ''))
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
    NCM Webhook Endpoint

    Receives POST requests from NCM with order status updates.

    NCM Payload format (per documentation):
    Single: {"order_id": "123456", "status": "Delivered", "timestamp": "...", "event": "delivery_completed"}
    Bulk:   {"order_ids": ["123456", ...], "status": "Dispatched", "timestamp": "...", "event": "order_dispatched"}
    Test:   {"event": "order.status.changed", "order_id": "TEST-123456", "status": "In Transit", "timestamp": "...", "test": true}

    NCM Headers: Content-Type: application/json, User-Agent: NCM-Webhook/1.0
    """
    payload_bytes = request.body
    
    try:
        webhook_logger.info("=" * 70)
        webhook_logger.info("NCM WEBHOOK RECEIVED")
        webhook_logger.info("=" * 70)
        webhook_logger.info(f"Client IP: {get_client_ip(request)}")
        webhook_logger.info(f"Method: {request.method}")
        webhook_logger.info(f"Content-Type: {request.content_type}")
        webhook_logger.info(f"User-Agent: {request.META.get('HTTP_USER_AGENT', 'N/A')}")
        webhook_logger.info(f"Query String: {request.META.get('QUERY_STRING', '')}")

        # 1. VERIFY WEBHOOK AUTHENTICITY
        if not verify_ncm_webhook(request, payload_bytes):
            webhook_logger.error("Webhook verification FAILED")
            return JsonResponse({
                'success': False,
                'message': 'Webhook verification failed',
                'error_code': 'INVALID_TOKEN'
            }, status=401)

        webhook_logger.info("Webhook verification passed")

        # 2. PARSE JSON PAYLOAD
        payload = json.loads(payload_bytes)
        webhook_logger.info(f"Payload: {json.dumps(payload, indent=2)}")

        # 3. PROCESS WEBHOOK USING HANDLER
        response = webhook_handler.process_webhook(payload, request)

        webhook_logger.info("=" * 70)
        webhook_logger.info(f"Webhook processing completed: {response.get('message')}")
        webhook_logger.info("=" * 70)
        
        return JsonResponse(response, status=200)
        
    except json.JSONDecodeError as e:
        webhook_logger.error(f"Invalid JSON in webhook payload: {str(e)}")
        webhook_logger.error(f"Raw body: {payload_bytes[:500]}")
        return JsonResponse({
            'success': False,
            'message': 'Invalid JSON payload',
            'error_code': 'INVALID_JSON',
        }, status=400)

    except Exception as e:
        webhook_logger.error(f"Webhook processing error: {str(e)}")
        import traceback
        webhook_logger.error(traceback.format_exc())

        # Always return 200 to acknowledge receipt.
        # Returning 500 can cause NCM to consider delivery failed
        # and stop sending future webhooks or flag the endpoint.
        return JsonResponse({
            'success': False,
            'message': 'Webhook received but processing encountered an error',
            'error_code': 'PROCESSING_ERROR',
        }, status=200)


@login_required
@ncm_permission_required('can_view_ncm_branches')
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
@ncm_permission_required('can_view_ncm_orders')
def track_ncm_order(request, order_id):
    """View tracking details"""
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        
        if not order.ncm_order_id:
            messages.error(request, 'Order not in NCM yet')
            return redirect('order_detail', order_id=order_id)
        
        # Use order-specific NCM API account
        svc = NCMService(api_config_id=order.api_config_id) if order.api_config_id else ncm_service
        
        details_result = svc.get_order_details(order.ncm_order_id)
        status_result = svc.get_order_status(order.ncm_order_id)
        
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
@ncm_permission_required('can_sync_ncm_orders')
@require_http_methods(["GET", "POST"])
def bulk_sync_ncm_orders(request):
    """Sync multiple NCM orders at once"""
    try:
        # Check if specific order IDs were provided (selected orders)
        selected_order_ids = request.POST.getlist('order_ids')

        if selected_order_ids:
            summary = run_bulk_ncm_status_sync(user=request.user, order_ids=selected_order_ids)
        else:
            summary = run_bulk_ncm_status_sync(user=request.user)

        total_orders = summary['total_orders']
        updated_count = summary['updated_count']
        errors = summary['errors']

        if total_orders == 0:
            messages.info(request, 'No active NCM orders to sync')
            return redirect('orders_list')

        if updated_count > 0:
            messages.success(request, f'[SUCCESS] Synced {updated_count} out of {total_orders} active orders')
        if errors:
            messages.warning(request, "Some batches failed: " + "; ".join(errors))
        elif updated_count == 0 and not errors:
            messages.info(request, f"Checked {total_orders} active orders. No statuses had changed.")

        return redirect('orders_list')

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        messages.error(request, f'Error: {str(e)}')
        return redirect('orders_list')


def _may_sync_ncm(user):
    """Permission check for the JSON sync endpoint.

    Deliberately not ncm_permission_required: that answers a refusal with a
    redirect and a queued Django message, which for an AJAX caller means an
    unparseable HTML response now and a stray error toast on some later,
    unrelated page. See accounts.decorators.has_any_permission.
    """
    return has_any_permission(user, 'can_sync_ncm_orders')


@login_required
@require_http_methods(["POST"])
def bulk_sync_ncm_orders_json(request):
    """Kick off a bulk NCM sync and answer immediately with JSON.

    This is the "Sync Now" button on the Logistics Orders page. It deliberately
    does NOT wait for the sync: a page of 25 orders can mean fifty sequential
    NCM requests at up to `ncm_api_timeout` each, which would sit well past any
    reverse proxy's patience. The sync runs on the scheduler's background
    thread and the page's status poller picks the results up as they land.

    Passing `order_ids` limits the run to the orders currently on screen;
    without it, every eligible NCM order is synced.
    """
    from ncm.scheduler import maybe_run_bulk_sync

    if not _may_sync_ncm(request.user):
        logger.warning(
            'Bulk sync denied for %s - missing can_sync_ncm_orders', request.user.username
        )
        return JsonResponse(
            {'success': False, 'message': 'You do not have permission to sync orders.'},
            status=403,
        )

    try:
        order_ids = [
            int(oid) for oid in request.POST.getlist('order_ids')
            if str(oid).strip().isdigit()
        ]

        started, _ = maybe_run_bulk_sync(
            force=True, order_ids=order_ids or None, user=request.user
        )

        if not started:
            # force=True still respects the running lock, so this means a sync
            # is already in flight - the results are coming either way.
            return JsonResponse({
                'success': True,
                'started': False,
                'message': 'A sync is already running - results will appear shortly.',
            })

        scope = f'{len(order_ids)} order(s) on this page' if order_ids else 'all active NCM orders'
        return JsonResponse({
            'success': True,
            'started': True,
            'message': f'Syncing {scope} from NCM. Statuses will update automatically.',
        })

    except Exception as e:
        logger.exception('Error starting bulk NCM sync')
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


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
