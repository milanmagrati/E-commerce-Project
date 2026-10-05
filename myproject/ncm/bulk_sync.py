# ncm/bulk_sync.py
"""
Shared bulk NCM status-sync logic, used by both the interactive
"Bulk Sync" view (ncm/views.py:bulk_sync_ncm_orders) and the
sync_all_ncm_orders management command (run via cPanel cron, since this
project's shared hosting doesn't support a persistent Celery worker/beat).
"""

import logging

from django.utils import timezone

from dashboard.models import Order, OrderActivityLog, APISettings
from dashboard.timezone_utils import parse_ncm_datetime
from services.ncm_service import NCMService
from services.status_override import (clear_manual_status_override,
                                      manual_override_holds)

logger = logging.getLogger('ncm')
ncm_service = NCMService()

#: Statuses meaning "this parcel is going back to, or is back with, the vendor".
#: Used to decide when the bulk endpoint's bare status string is not trustworthy
#: enough to write through - see _sync_one_order.
RETURN_PIPELINE_STATUSES = ('return_processing', 'return_arrived', 'return', 'returned')


def _carries_vendor_return(entry):
    """True when a status entry actually states the vendor_return flag.

    Absence is not the same as False: resolve_delivered_status defaults a
    missing flag to False, which reads as "not a return". For an order already
    heading back to the vendor that default is a guess, and the guard in
    _sync_one_order refuses to act on a guess.
    """
    return isinstance(entry, dict) and (
        'vendor_return' in entry or 'vendorReturn' in entry
    )

DEFAULT_TERMINAL_STATUSES = [
    'cancelled', 'delivered', 'return', 'returned',
    'return_initiated', 'return_approved',
]

#: Statuses a remote NCM status can never overwrite. A cancellation is a
#: local staff decision, and NCM can report a stale/late status long after
#: it was made - the single-order sync endpoint and the webhook handler both
#: refuse cancelled orders for the same reason, so bulk sync must too.
#: This is enforced regardless of how orders were selected, because
#: `bulk_sync_included_statuses` is admin-configurable (Settings -> API Sync
#: Settings) and could otherwise be set to include 'cancelled'.
PROTECTED_STATUSES = ('cancelled',)

#: NCM caps how many order IDs one bulk status request can carry, and long
#: URIs/timeouts start biting well before that - keep requests chunked.
CHUNK_SIZE = 100



def run_bulk_ncm_status_sync(user=None, order_ids=None, fetch_event_times=None,
                             progress_callback=None, deadline=None):
    """
    Sync NCM status for all active NCM orders.

    When `order_ids` is given (an explicit selection from the interactive
    "Bulk Sync" UI), only those orders are synced. Otherwise, candidate
    orders are filtered by APISettings.bulk_sync_included_statuses when
    configured (admin-selected via Settings -> API Sync Settings);
    otherwise falls back to excluding DEFAULT_TERMINAL_STATUSES, matching
    this feature's original hardcoded behavior.

    A failure on one order (or one chunk) is recorded and skipped rather than
    aborting the run - this runs unattended, so one malformed NCM payload
    must not stop every remaining order from syncing.

    Stopping early is always safe: each order is resolved and committed on its
    own, and re-syncing an order that didn't change is a no-op (see the
    early return in _sync_one_order). A run cut short by `deadline` therefore
    just means the remaining orders wait for the next run.

    Args:
        fetch_event_times: None = use the APISettings toggle (default on);
            True/False overrides it. Costs one extra NCM request per order
            whose status actually changed, in exchange for activity-log
            timestamps that match NCM instead of the run's own clock.
        progress_callback: called with no arguments after every order and
            every chunk. The scheduler uses it to prove the run is still
            alive, so its lock isn't mistaken for an abandoned one; a run's
            total duration is unbounded (a first sync after a long gap can be
            hundreds of sequential requests), but the gap between two
            callbacks is not. Callbacks are cheap by contract - the callee
            rate-limits, so this can be called freely.
        deadline: a timezone-aware datetime past which no NEW chunk is
            started. The in-flight chunk always finishes.

    Returns a summary dict:
        {'total_orders', 'updated_count', 'errors', 'deadline_reached'}
    """
    if fetch_event_times is None:
        fetch_event_times = APISettings.get_settings().bulk_sync_fetch_event_times

    def _ping():
        """Report liveness; a broken callback must never abort the sync."""
        if progress_callback is None:
            return
        try:
            progress_callback()
        except Exception:
            logger.debug('Bulk sync progress callback failed', exc_info=True)

    ncm_orders = Order.objects.filter(
        ncm_order_id__isnull=False,
        is_deleted=False,
    ).exclude(status__in=PROTECTED_STATUSES)

    if order_ids:
        ncm_orders = ncm_orders.filter(id__in=order_ids)
    else:
        included_statuses = APISettings.get_settings().bulk_sync_included_statuses
        if included_statuses:
            ncm_orders = ncm_orders.filter(status__in=included_statuses)
        else:
            from dashboard.models import RTVOrder
            from services.ncm_service import NCMService
            from django.db.models import Q
            # Active RTV orders: orders with an active RTVOrder that haven't arrived/completed return yet
            _terminal_rtv_statuses = list(NCMService.RETURN_COMPLETED_STATUSES) + ['return_arrived']
            active_rtv_ncm_ids = list(
                RTVOrder.objects.filter(vendor_return=True)
                .exclude(last_status__in=_terminal_rtv_statuses)
                .values_list('order_id', flat=True)
            )
            if active_rtv_ncm_ids:
                ncm_orders = ncm_orders.filter(
                    ~Q(status__in=DEFAULT_TERMINAL_STATUSES) | Q(ncm_order_id__in=active_rtv_ncm_ids)
                )
            else:
                ncm_orders = ncm_orders.exclude(status__in=DEFAULT_TERMINAL_STATUSES)

    summary = {'total_orders': 0, 'updated_count': 0, 'errors': [], 'deadline_reached': False}

    # Materialize once - the loop below needs the objects anyway, so a
    # separate .exists() probe would just be an extra query.
    orders = list(ncm_orders)
    if not orders:
        return summary

    # Group orders by api_config_id to ensure correct credentials are used for each batch
    orders_by_config = {}
    for order in orders:
        orders_by_config.setdefault(order.api_config_id, []).append(order)

    updated_count = 0
    errors = []
    processed = 0
    deadline_reached = False

    for config_id, group_orders in orders_by_config.items():
        if deadline_reached:
            break
        svc = NCMService(api_config_id=config_id) if config_id else ncm_service
        config_label = config_id or 'default'

        for i in range(0, len(group_orders), CHUNK_SIZE):
            # Checked before starting a chunk, never mid-chunk: abandoning a
            # chunk half-way would leave its orders unsynced anyway, and the
            # next run picks them up either way.
            if deadline is not None and timezone.now() >= deadline:
                deadline_reached = True
                logger.warning(
                    'Bulk sync hit its time budget after %d orders; remaining orders '
                    'will be picked up by the next run.', processed
                )
                break

            chunk_orders = group_orders[i:i + CHUNK_SIZE]
            chunk_ids = [str(o.ncm_order_id) for o in chunk_orders]
            chunk_label = f"config {config_label} (chunk {i // CHUNK_SIZE + 1})"

            try:
                result = svc.get_bulk_order_statuses(chunk_ids)
            except Exception as e:
                errors.append(f"Failed {chunk_label}: {e}")
                logger.exception(f"Bulk sync request failed for {chunk_label}")
                continue

            if not result.get('success'):
                errors.append(f"Failed {chunk_label}: {result.get('error')}")
                continue

            status_data = (result.get('data') or {})
            status_data = status_data.get('result') if isinstance(status_data, dict) else None
            if not isinstance(status_data, dict):
                errors.append(f"Failed {chunk_label}: unexpected response shape from NCM")
                continue

            for order in chunk_orders:
                raw_status = status_data.get(str(order.ncm_order_id))
                if raw_status is None and (
                    order.status in RETURN_PIPELINE_STATUSES
                    or (order.ncm_order_id in active_rtv_ncm_ids if 'active_rtv_ncm_ids' in locals() else False)
                ):
                    # NCM's bulk /orders/statuses omits RTV orders (returns in 'errors').
                    # Call get_order_status for the latest status entry!
                    try:
                        detail_res = svc.get_order_status(order.ncm_order_id)
                        if detail_res.get('success') and detail_res.get('data'):
                            d = detail_res['data']
                            raw_status = d[0] if isinstance(d, list) and d else d
                    except Exception:
                        pass
                if raw_status is None:
                    continue
                try:
                    if _sync_one_order(svc, order, raw_status, user,
                                       fetch_event_times=fetch_event_times):
                        updated_count += 1
                except Exception as e:
                    errors.append(f"Order {order.order_number}: {e}")
                    logger.exception(f"Bulk sync failed for order {order.order_number}")

                processed += 1
                # Reported after every order, not every Nth: orders take wildly
                # different amounts of time (a timing-out NCM request costs
                # ncm_api_timeout on its own), so a count is a poor proxy for
                # elapsed time. The callback is responsible for rate-limiting
                # itself - see ncm.scheduler._touch.
                _ping()

            _ping()

    summary['total_orders'] = len(orders)
    summary['updated_count'] = updated_count
    summary['errors'] = errors
    summary['deadline_reached'] = deadline_reached

    if updated_count or errors:
        logger.info(
            f"Bulk sync: {updated_count}/{len(orders)} active orders updated, {len(errors)} error(s)"
        )

    return summary


def sync_order_status_from_raw(svc, order, raw_status, user=None, fetch_event_times=True):
    """Persist one order's NCM status when the caller already holds NCM's answer.

    Public entry point onto the same resolution used by the bulk run, for
    callers that fetched statuses themselves and would otherwise have to ask
    NCM a second time for the same order (see
    dashboard.views.possible_redirection_refresh_status, which pulls one bulk
    status response and needs it for both the RTV row and its linked order).

    PROTECTED_STATUSES is enforced here rather than only in the run's queryset:
    callers that pick their own orders bypass that filter, and a late NCM status
    must never resurrect an order staff already cancelled locally (the same
    guard the single-order sync endpoint applies).

    Returns True when the order was updated, False when nothing changed.
    """
    if order.status in PROTECTED_STATUSES:
        return False
    return _sync_one_order(svc, order, raw_status, user,
                           fetch_event_times=fetch_event_times)


def _sync_one_order(svc, order, raw_status, user, fetch_event_times=True):
    """Resolve one order's NCM status and persist it if it changed.

    Args:
        fetch_event_times: spend one extra /order/status call on orders whose
            status actually changed, to learn NCM's real event time. The bulk
            endpoint returns only a status string, so without this the activity
            log falls back to the cron run's own clock — which is how a status
            change from days ago ends up dated "now".

    Returns True when the order was updated, False when nothing changed.
    """
    old_ncm_status = order.ncm_status
    old_system_status = order.status
    old_payment_status = order.payment_status
    event_at = None

    # An order travelling back to the vendor. Its remaining hops are worded
    # exactly like ordinary transit ("Dispatched", "Arrived at BUTWAL"), so the
    # bulk endpoint's bare status string cannot tell them apart from a delivery
    # run - only the vendor_return flag on a full status entry can.
    in_return_pipeline = (
        (order.status or '').strip().lower() in RETURN_PIPELINE_STATUSES
    )

    # Re-fetch the full entry in the two cases where the bare string is
    # ambiguous. `resolved_with_flag` records whether the verdict below is
    # actually backed by vendor_return, which the guard after it relies on.
    #
    #   * 'Delivered'  - delivered to the customer, or back to the vendor?
    #   * an order in the return pipeline whose status just moved - which way
    #     did it move? Gated on the status having changed, so an order sitting
    #     in 'return_processing' for a week costs nothing extra per run.
    needs_vendor_return_flag = (
        isinstance(raw_status, str)
        and (raw_status == 'Delivered'
             or (in_return_pipeline and raw_status != order.ncm_status))
    )
    resolved_with_flag = False

    if needs_vendor_return_flag:
        detail_result = svc.get_order_status(order.ncm_order_id)
        entry = None
        if detail_result.get('success') and detail_result.get('data'):
            data = detail_result['data']
            candidate = data[0] if isinstance(data, list) else data
            if isinstance(candidate, dict):
                entry = candidate

        if entry is not None:
            system_status, payment_status = svc.resolve_delivered_status(entry)
            new_status = entry.get('status') or entry.get('Status') or raw_status
            event_at = parse_ncm_datetime(entry.get('added_time'))
            resolved_with_flag = _carries_vendor_return(entry)
        else:
            system_status = svc.map_ncm_status_to_system(raw_status)
            payment_status = None
            new_status = raw_status
    elif isinstance(raw_status, dict):
        system_status, payment_status = svc.resolve_delivered_status(raw_status)
        new_status = raw_status.get('status') or raw_status.get('Status') or ''
        event_at = parse_ncm_datetime(raw_status.get('added_time'))
        resolved_with_flag = _carries_vendor_return(raw_status)
    else:
        new_status = raw_status
        system_status = svc.map_ncm_status_to_system(new_status)
        payment_status = None

    # Nothing but a flag-backed verdict may take an order OUT of the return
    # pipeline. Without that, "Arrived at BUTWAL" on a parcel heading back
    # resolves to plain 'in_transit' and the return is lost - and since the
    # bulk string keeps saying "Arrived", it is lost silently and for good.
    # Return-worded outcomes are unambiguous and still allowed through.
    if (in_return_pipeline and not resolved_with_flag
            and system_status not in RETURN_PIPELINE_STATUSES):
        logger.debug(
            f"Bulk sync: '{raw_status}' carries no vendor_return flag; keeping "
            f"{order.order_number} at '{order.status}'"
        )
        return False

    if order.ncm_status == new_status and order.status == system_status:
        return False

    # A status staff set by hand outranks NCM until the parcel actually moves.
    # The early return above can't cover this: a manual change deliberately
    # leaves order.status different from what NCM reports, which reads as
    # "needs updating" and is exactly what used to overwrite it.
    if manual_override_holds(order, new_status, event_at):
        logger.debug(
            f"Bulk sync: keeping manually set status on {order.order_number} "
            f"(NCM still reports '{new_status}')"
        )
        return False

    # Only reached for orders that actually changed, so this costs one request
    # per real change - not one per order in the run.
    if event_at is None and fetch_event_times:
        event_at = _fetch_event_time(svc, order, new_status)

    order.ncm_status = new_status
    update_fields = svc.sync_order_status_fields(order, system_status, payment_status)
    update_fields.extend(['ncm_status', 'updated_at'])
    # NCM has moved past whatever was set by hand, so retire the hold.
    update_fields.extend(clear_manual_status_override(order))

    if system_status == 'delivered' and not order.delivered_at:
        order.delivered_at = event_at or timezone.now()
        update_fields.append('delivered_at')

    order.save(update_fields=list(dict.fromkeys(update_fields)))

    # Keep linked RTVOrder.last_status in sync with Order.ncm_status
    if order.ncm_order_id:
        try:
            from dashboard.models import RTVOrder
            RTVOrder.objects.filter(order_id=order.ncm_order_id).update(last_status=new_status)
        except Exception:
            pass

    OrderActivityLog.objects.create(
        order=order,
        action_type='status_changed',
        user=user,
        field_name='ncm_status',
        old_value=old_ncm_status,
        new_value=new_status,
        event_at=event_at,
        description=f'Bulk sync: {old_system_status} → {system_status}'
                    + (f', payment: {old_payment_status} → {payment_status}' if payment_status else '')
    )
    return True


def _fetch_event_time(svc, order, new_status):
    """NCM's added_time for `new_status` on this order, or None.

    Prefers the timeline entry matching the status we're recording; falls back
    to the newest entry. Never raises - a missing event time just means the log
    keeps its local timestamp.
    """
    try:
        result = svc.get_order_status(order.ncm_order_id)
    except Exception as e:
        logger.debug(f"Event-time fetch failed for {order.order_number}: {e}")
        return None

    if not result.get('success') or not result.get('data'):
        return None

    data = result['data']
    entries = data if isinstance(data, list) else [data]
    entries = [e for e in entries if isinstance(e, dict)]
    if not entries:
        return None

    for entry in entries:
        status = entry.get('status') or entry.get('Status') or ''
        if status == new_status:
            return parse_ncm_datetime(entry.get('added_time'))

    # /order/status is newest-first.
    return parse_ncm_datetime(entries[0].get('added_time'))
