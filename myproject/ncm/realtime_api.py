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
from django.core.mail import send_mail
from dashboard.models import Order, OrderActivityLog
from dashboard.timezone_utils import format_nepali_datetime, parse_ncm_datetime
from services.ncm_service import NCMService
import logging
import json
import threading
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

logger = logging.getLogger('ncm')
ncm_service = NCMService()

# Simple in-memory cache for comments to reduce API calls
_comment_cache = {}
_cache_timeout = 5  # Cache for 5 seconds


def get_cached_comments(order_id):
    """Get cached comments if still valid"""
    cache_key = f"comments_{order_id}"
    if cache_key in _comment_cache:
        cached_data, timestamp = _comment_cache[cache_key]
        if (timezone.now() - timestamp).total_seconds() < _cache_timeout:
            return cached_data
        else:
            del _comment_cache[cache_key]
    return None


def set_cached_comments(order_id, data):
    """Cache comments with current timestamp"""
    cache_key = f"comments_{order_id}"
    _comment_cache[cache_key] = (data, timezone.now())


def _order_display_fields(order):
    """
    Display-ready status fields for clients that patch the order detail page's
    DOM in place instead of reloading (status/payment badges, etc.) - mirrors
    the `|replace:"_: "|upper` template filter used in order_detail.html.

    The raw `status`/`order_status`/`payment_status` values are included
    alongside the display strings because the page uses them as its
    change-detection baseline (`lastOrderState`). Without them a manual sync
    would patch the visible badges but leave that baseline stale, so the next
    poll would re-report the same change as if it were new.
    """
    if order.status_setup_id:
        raw_status = order.status_setup.name
    else:
        raw_status = order.order_status or order.status or 'pending'
    status_display = str(raw_status).replace('_', ' ').upper()

    if order.payment_status_setup_id:
        raw_payment = order.payment_status_setup.name
    else:
        raw_payment = order.payment_status or 'pending'
    payment_status_display = str(raw_payment).replace('_', ' ').upper()

    return {
        'status': order.status,
        'order_status': order.order_status,
        'status_display': status_display,
        'payment_status': order.payment_status,
        'payment_status_display': payment_status_display,
        'payment_status_css': order.payment_status or 'pending',
        'ncm_status': order.ncm_status,
        'delivered_at': order.delivered_at.isoformat() if order.delivered_at else None,
    }


def send_ncm_comment_async(ncm_order_id, comment_text, order_id, user_id, api_config_id=None):
    """
    Send comment to NCM in background thread to avoid blocking the response.
    This allows the API to return immediately to the user.
    Uses the order's api_config_id to ensure the correct API credentials are used.
    """
    try:
        # Use order-specific API config if available (same credentials used when creating the NCM shipment)
        svc = NCMService(api_config_id=api_config_id) if api_config_id else ncm_service
        result = svc.create_order_comment(ncm_order_id, comment_text)
        
        if result['success']:
            logger.info(f"NCM comment sent successfully for order {order_id}")
            # Invalidate cache when new comment is sent
            cache_key = f"comments_{order_id}"
            if cache_key in _comment_cache:
                del _comment_cache[cache_key]
        else:
            logger.warning(f"NCM comment failed for order {order_id}: {result.get('error')}")
    except Exception as e:
        logger.error(f"Error sending NCM comment for order {order_id}: {str(e)}")



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
            local_status = {
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
            }
            local_status.update(_order_display_fields(order))
            return JsonResponse(local_status)

        # Use order-specific NCM API account
        svc = NCMService(api_config_id=order.api_config_id) if order.api_config_id else ncm_service
        # Try to fetch latest status from NCM
        ncm_status_result = svc.get_order_status(order.ncm_order_id)

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
        status_data.update(_order_display_fields(order))
        
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

        if order.status == 'cancelled':
            # Don't let a sync (manual click, or the automatic sync that now
            # fires when the order page loads) silently resurrect an order
            # the staff already cancelled locally.
            cancelled_response = {
                'success': True,
                'message': 'Order is cancelled; status sync skipped',
                'order_id': order.id,
                'order_number': order.order_number,
                'changed': False
            }
            cancelled_response.update(_order_display_fields(order))
            return JsonResponse(cancelled_response)

        # Use order-specific NCM API account
        svc = NCMService(api_config_id=order.api_config_id) if order.api_config_id else ncm_service

        # Fetch latest status from NCM
        result = svc.get_order_status(order.ncm_order_id)

        if not result['success']:
            return JsonResponse({
                'success': False,
                'message': f"Failed to fetch from NCM: {result.get('error')}"
            }, status=400)

        status_data = result['data']

        # Extract latest status entry
        latest_entry = None
        if isinstance(status_data, dict) and 'last_status' in status_data:
            latest_status = status_data['last_status']
            latest_entry = status_data  # Use the whole dict as the entry
        elif isinstance(status_data, list) and len(status_data) > 0:
            latest_entry = status_data[0]
            latest_status = latest_entry.get('status') or latest_entry.get('Status')
        else:
            latest_status = order.ncm_status
            latest_entry = {}

        # When NCM says this status actually happened. Without it the activity
        # log would be stamped with the moment this sync ran — and this sync
        # fires on every order-page load, so an event from days ago would look
        # like it happened just now.
        event_at = parse_ncm_datetime((latest_entry or {}).get('added_time'))

        # Map to system status using vendor_return-aware resolution
        old_status = order.status
        old_ncm_status = order.ncm_status
        old_payment_status = order.payment_status

        if latest_entry:
            system_status, payment_status = svc.resolve_delivered_status(latest_entry)
        else:
            system_status = svc.map_ncm_status_to_system(latest_status)
            payment_status = None

        # Update all status-related fields (status, order_status, status_setup
        # FK, payment fields) - sync_order_status_fields only reports a field
        # here if its value actually changed, so `changed` below reflects a
        # real difference rather than re-deriving a fuzzy "does it match"
        # check on every call. That distinction matters: the order detail
        # page reloads itself whenever `changed` is true, so a check that
        # could stay permanently "unmatched" (e.g. a stale status_setup FK
        # left behind by other code paths) would reload the page forever.
        update_fields = svc.sync_order_status_fields(order, system_status, payment_status)

        ncm_status_changed = latest_status != order.ncm_status
        delivered_at_missing = system_status == 'delivered' and not order.delivered_at
        needs_update = bool(update_fields) or ncm_status_changed or delivered_at_missing

        if needs_update:
            order.ncm_status = latest_status
            update_fields.append('ncm_status')
            update_fields.append('updated_at')

            if system_status == 'delivered' and not order.delivered_at:
                order.delivered_at = event_at or timezone.now()
                update_fields.append('delivered_at')

            # Deduplicate
            update_fields = list(dict.fromkeys(update_fields))
            order.save(update_fields=update_fields)

            # Create activity log
            OrderActivityLog.objects.create(
                order=order,
                action_type='status_changed',
                user=request.user,
                field_name='ncm_status',
                old_value=old_ncm_status,
                new_value=latest_status,
                event_at=event_at,
                description=f'Manual API sync: {old_status} → {system_status}'
                            + (f', payment: {old_payment_status} → {payment_status}' if payment_status else '')
            )

            logger.info(f"[SUCCESS] API Sync: {order.order_number} -> {system_status}" + (f", payment: {payment_status}" if payment_status else ""))

            changed_response = {
                'success': True,
                'message': 'Status updated',
                'order_id': order.id,
                'order_number': order.order_number,
                'old_status': old_status,
                'new_status': system_status,
                'ncm_status': latest_status,
                'payment_status': order.payment_status,
                'changed': True
            }
            changed_response.update(_order_display_fields(order))
            return JsonResponse(changed_response)
        else:
            unchanged_response = {
                'success': True,
                'message': 'Status unchanged',
                'order_id': order.id,
                'order_number': order.order_number,
                'status': system_status,
                'changed': False
            }
            unchanged_response.update(_order_display_fields(order))
            return JsonResponse(unchanged_response)
        
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
        
        from django.db.models.functions import Coalesce
        activities = OrderActivityLog.objects.filter(
            order=order
        ).select_related('user').annotate(
            _at=Coalesce('event_at', 'created_at')
        ).order_by('-_at')[:limit]

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
                'created_at': activity.created_at.isoformat(),
                # When NCM says it happened vs when we recorded it.
                'event_at': activity.event_at.isoformat() if activity.event_at else None,
                'effective_at': activity.effective_at.isoformat(),
                'effective_at_display': format_nepali_datetime(activity.effective_at),
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
def api_get_order_comments(request, order_id):
    """
    Fetch comments for an NCM order.
    Pulls from both local database (OrderActivityLog) and NCM system.
    Uses caching to reduce API calls to NCM (429 rate limiting).
    """
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)

        if not order.ncm_order_id:
            return JsonResponse({
                'success': True,
                'comments': [],
                'message': 'Order not in NCM system'
            })

        # Check cache first
        cached_comments = get_cached_comments(order_id)
        if cached_comments:
            return JsonResponse({
                'success': True,
                'order_id': order.id,
                'ncm_order_id': order.ncm_order_id,
                'comments': cached_comments,
                'timestamp': timezone.now().isoformat(),
                'cached': True
            })

        all_comments = []

        # Use order's specific API config (same credentials used when creating the NCM shipment)
        order_ncm_service = NCMService(api_config_id=order.api_config_id) if order.api_config_id else ncm_service

        # 0. Fetch local comments from OrderActivityLog (NCM comments only)
        local_comments = OrderActivityLog.objects.filter(
            order=order,
            action_type__in=['notes_added', 'notes_updated'],
            field_name='ncm_comment'
        ).select_related('user').order_by('created_at')

        for log in local_comments:
            username = 'You'
            if log.user:
                username = log.user.get_full_name() or log.user.username
            all_comments.append({
                'comment': log.new_value or '',
                'created_by': username,
                'created_at': log.created_at.isoformat() if log.created_at else '',
                'created_at_display': format_nepali_datetime(log.created_at),
                'role': 'admin',
                'is_local': True
            })

        # 1. Fetch ALL comments from the NCM /order/comment endpoint (all authors)
        # This matches the working ncm_rtvs.html implementation which uses get_order_comments
        try:
            all_ncm_result = order_ncm_service.get_order_comments(order.ncm_order_id)
            if all_ncm_result.get('success'):
                ncm_comments = all_ncm_result.get('data', [])
                existing_texts = {c.get('comment', '').strip().lower() for c in all_comments}
                for nc in ncm_comments:
                    comment_text = nc.get('comment', '').strip().lower()
                    if comment_text not in existing_texts:
                        # Normalize field names to match createCommentElement
                        # expectations. created_at is normalized to an
                        # offset-bearing ISO string so the browser can't parse
                        # it as local time — local rows already emit isoformat(),
                        # and mixing the two forms in one list made the
                        # displayed times depend on the viewer's timezone.
                        nc_dt = parse_ncm_datetime(nc.get('added_time', nc.get('created_at', '')))
                        all_comments.append({
                            'comment': nc.get('comment', ''),
                            'created_by': nc.get('added_by', nc.get('created_by', 'NCM Staff')),
                            'created_at': nc_dt.isoformat() if nc_dt else '',
                            'created_at_display': format_nepali_datetime(nc_dt),
                            'role': 'ncm' if 'ncm' in nc.get('added_by', '').lower() else 'admin',
                            'is_ncm_staff': 'ncm' in nc.get('added_by', '').lower(),
                        })
                        existing_texts.add(comment_text)
        except Exception as ncm_err:
            logger.warning(f"Could not fetch NCM comments for order {order.ncm_order_id}: {ncm_err}")

        # 2. Also check order details endpoint for any embedded comments
        try:
            details_result = order_ncm_service.get_order_details(order.ncm_order_id)
            if details_result['success']:
                data = details_result['data']
                if isinstance(data, dict):
                    raw_comments = data.get('comments', data.get('comment', []))
                    existing_texts = {c.get('comment', '').strip().lower() for c in all_comments}
                    if isinstance(raw_comments, list):
                        for c in raw_comments:
                            if isinstance(c, dict):
                                ct = c.get('comment', c.get('comments', '')).strip().lower()
                                if ct not in existing_texts:
                                    all_comments.append(c)
                                    existing_texts.add(ct)
                            elif isinstance(c, str) and c.strip() and c.strip().lower() not in existing_texts:
                                all_comments.append({
                                    'comment': c,
                                    'created_by': 'NCM',
                                    'role': 'ncm'
                                })
                                existing_texts.add(c.strip().lower())
        except Exception as detail_err:
            logger.warning(f"Could not fetch NCM order details for order {order.ncm_order_id}: {detail_err}")

        # Local comments were appended ascending and NCM's in whatever order
        # the API returned, so the merged list had no defined order at all.
        # Sort newest-first on the parsed timestamp; undated entries sink.
        def _comment_sort_key(c):
            dt = parse_ncm_datetime(c.get('created_at') or c.get('added_time'))
            return (dt is not None, dt or datetime.min.replace(tzinfo=dt_timezone.utc))

        all_comments.sort(key=_comment_sort_key, reverse=True)

        # Cache the results to reduce API calls (429 rate limiting)
        set_cached_comments(order_id, all_comments)

        return JsonResponse({
            'success': True,
            'order_id': order.id,
            'ncm_order_id': order.ncm_order_id,
            'comments': all_comments,
            'timestamp': timezone.now().isoformat()
        })

    except Exception as e:
        logger.error(f"Error fetching NCM comments: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=500)


@login_required
@require_http_methods(["POST"])
def api_add_order_comment(request, order_id):
    """
    Add a comment to an NCM order.
    Returns immediately while comment is sent to NCM in background.
    """
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)

        if not order.ncm_order_id:
            return JsonResponse({
                'success': False,
                'message': 'Order not in NCM system'
            }, status=400)

        body = json.loads(request.body) if request.body else {}
        comment_text = body.get('comment', '').strip()

        if not comment_text:
            return JsonResponse({
                'success': False,
                'message': 'Comment text is required'
            }, status=400)

        # Create activity log immediately
        OrderActivityLog.objects.create(
            order=order,
            action_type='notes_added',
            user=request.user,
            field_name='ncm_comment',
            old_value='',
            new_value=comment_text[:255],
            description=f'NCM comment added: {comment_text[:100]}'
        )

        # Invalidate comments cache so next fetch includes this new comment
        cache_key = f"comments_{order_id}"
        if cache_key in _comment_cache:
            del _comment_cache[cache_key]

        # Send comment to NCM in background (non-blocking)
        # Pass order's api_config_id so the correct API credentials are used
        thread = threading.Thread(
            target=send_ncm_comment_async,
            args=(order.ncm_order_id, comment_text, order.id, request.user.id, order.api_config_id),
            daemon=True
        )
        thread.start()

        # Return success immediately to user
        return JsonResponse({
            'success': True,
            'message': 'Comment sent successfully'
        })

    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Invalid JSON body'
        }, status=400)
    except Exception as e:
        logger.error(f"Error adding NCM comment: {str(e)}")
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
