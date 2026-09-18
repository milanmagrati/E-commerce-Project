# dashboard/bulk_batch.py
"""
Terminate and resume control for logistics bulk-send batches (NCM and PND).

Why this exists
---------------
A bulk send runs inline in the request that started it: `orders_bulk_ncm_send`
and friends create an `NCMBulkLog` with status='processing', loop over the
orders hitting the provider API (15s timeout each), and only at the very end
write the counts and the final status. Nothing else ever touches that row.

So if the request does not reach the end - Passenger recycles the worker, the
proxy times out, the deploy restarts mid-loop - the batch is left at
'processing' with 0/0 counts, forever. The Bulk Logs page has rows from April
still spinning. Those batches are not running; there is no thread anywhere that
remembers them.

This module gives the page two actions for that state:

  terminate - stop a batch. If a worker is genuinely alive it is asked to stop
              between orders (`cancel_requested`); nothing here can kill a
              thread mid-API-call. If no worker is alive, the batch is closed
              out immediately.

  resume    - work through the orders the batch never got to, on a background
              thread, with the same heartbeat/claim discipline the NCM status
              sync uses (see ncm/scheduler.py for the reasoning: shared cPanel
              hosting, LocMemCache is per-process, so the lock has to be a
              compare-and-swap against the row itself).

Both actions reconcile the batch's counts from its child rows first. The stored
counters were never written for a stalled batch, but every order that WAS
processed left a `*BulkLogOrder` row behind, so the truth is recoverable.

The one thing that is not recoverable, for batches created before this module
existed, is *which* orders a batch was supposed to send: the selection lived
only in the POST that started it. That is what `selected_order_ids` now
snapshots at creation time. For an old batch, resume can only re-try the orders
that have a child row; anything the loop never reached is gone, and
`describe()` reports that as `unrecoverable_count` so the UI can say so instead
of silently under-delivering.
"""

import logging
import threading
from datetime import timedelta
from time import monotonic
from types import SimpleNamespace

from django.db import connections, transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger('ncm')

#: A 'processing' batch whose worker hasn't checked in for this long is treated
#: as stalled: resume may claim it and terminate may close it out immediately.
#:
#: The heartbeat is written between orders, so this has to cover the slowest
#: single order, not a whole run. The budget for one order is two provider calls
#: at a 15s timeout each (the send, then the delivery-charge lookup) plus a
#: handful of local writes - well under a minute even when the courier is dead.
#: 300s leaves several times that margin, which matters because claiming a
#: batch that IS still running would mean two loops sending the same orders.
#: (Even then the send helpers refuse an order that already carries a
#: consignment number, so a duplicate delivery needs both loops to hit the same
#: order within the same instant.)
STALE_HEARTBEAT_SECONDS = 300

#: Smallest gap between two heartbeat writes. The send loop calls the heartbeat
#: once per order; this stops that from meaning an UPDATE per order.
HEARTBEAT_MIN_INTERVAL_SECONDS = 30

#: Wall-clock budget for one resume run. Past this the thread stops starting
#: new orders and leaves the batch resumable, so a 500-order backlog is worked
#: through in several runs instead of one thread that outlives its worker.
MAX_RESUME_SECONDS = 480

#: Statuses a batch can be in while work is (or should be) outstanding.
OPEN_STATUSES = ('processing',)

#: Statuses from which a resume is allowed. 'partial'/'failed' are included so
#: a batch that finished badly can be retried without re-selecting orders.
RESUMABLE_STATUSES = ('processing', 'partial', 'failed', 'cancelled')


class BatchError(Exception):
    """Something the caller should be told about, in words a user can act on."""


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------

def _options(batch):
    """The batch's original send parameters, tolerant of old rows and junk."""
    raw = batch.send_options if isinstance(batch.send_options, dict) else {}
    try:
        weight = float(raw.get('default_weight') or 1.0)
    except (TypeError, ValueError):
        weight = 1.0
    return {
        'api_config_id': raw.get('api_config_id') or None,
        'default_weight': weight,
        'auto_set_logistics': bool(raw.get('auto_set_logistics')),
    }


def _ncm_adapter():
    from ncm.models import NCMBulkLog, NCMBulkLogOrder, NCMBulkLogDetail

    def prepare(batch):
        """Per-run state shared by every order in this batch.

        NCM's branch catalogue: fetched once here rather than once per order,
        for the same reason the bulk view does it - the courier rate-limits at
        3 requests a second and a resume can be 500 orders long.
        """
        from ncm import branch_resolver
        return {'branch_catalogue': branch_resolver.catalogue(_options(batch)['api_config_id'])}

    def send(shim, order, batch, **extra):
        from dashboard.views import send_single_order_to_ncm
        opts = _options(batch)
        return send_single_order_to_ncm(
            shim,
            order,
            from_branch=batch.from_branch,
            delivery_type=batch.delivery_type,
            default_weight=opts['default_weight'],
            api_config_id=opts['api_config_id'],
            **extra,
        )

    def order_row_defaults(order):
        return {
            'ncm_order_id': order.ncm_order_id,
            # Matches the bulk view: the resolved NCM branch when there is one.
            'destination_branch': order.ncm_destination_branch or order.branch_city or '',
        }

    return SimpleNamespace(
        key='ncm',
        label='NCM',
        log_model=NCMBulkLog,
        order_model=NCMBulkLogOrder,
        detail_model=NCMBulkLogDetail,
        branch_field='from_branch',
        provider_id_field='ncm_order_id',
        logistics_value='ncm',
        send=send,
        prepare=prepare,
        order_row_defaults=order_row_defaults,
    )


def _pnd_adapter():
    from pick_and_drop.models import PNDBulkLog, PNDBulkLogOrder, PNDBulkLogDetail

    def prepare(batch):
        return {}

    def send(shim, order, batch, **extra):
        from dashboard.views import send_single_order_to_pnd
        opts = _options(batch)
        return send_single_order_to_pnd(
            shim,
            order,
            default_weight=opts['default_weight'],
            api_config_id=opts['api_config_id'],
        )

    def order_row_defaults(order):
        return {
            'pnd_order_id': order.pnd_order_id,
            'destination_branch': order.branch_city or '',
        }

    return SimpleNamespace(
        key='pnd',
        label='Pick and Drop',
        log_model=PNDBulkLog,
        order_model=PNDBulkLogOrder,
        detail_model=PNDBulkLogDetail,
        branch_field='destination_branch',
        provider_id_field='pnd_order_id',
        logistics_value='pick_and_drop',
        send=send,
        prepare=prepare,
        order_row_defaults=order_row_defaults,
    )


def get_adapter(provider):
    """Model/behaviour bundle for 'ncm' or 'pnd'. Imports lazily on purpose:
    this module is imported from dashboard.views, which ncm/pnd code imports
    back."""
    if provider == 'ncm':
        return _ncm_adapter()
    if provider == 'pnd':
        return _pnd_adapter()
    raise BatchError(f'Unknown logistics provider: {provider}')


def get_batch(provider, log_id):
    adapter = get_adapter(provider)
    batch = adapter.log_model.objects.filter(id=log_id, is_deleted=False).first()
    if batch is None:
        raise BatchError('Batch not found (or already in the trash).')
    return adapter, batch


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------

def _stale_cutoff():
    return timezone.now() - timedelta(seconds=STALE_HEARTBEAT_SECONDS)


def is_worker_alive(batch):
    """True when something is demonstrably still working this batch.

    A batch created by an older build has `worker_heartbeat_at` NULL and will
    read as not-alive, which is the right answer for the stuck April rows and
    harmless for anything that has since finished.
    """
    if batch.status not in OPEN_STATUSES:
        return False
    hb = batch.worker_heartbeat_at
    return bool(hb and hb > _stale_cutoff())


def is_stalled(batch):
    """A batch that claims to be processing but has nobody working it."""
    return batch.status in OPEN_STATUSES and not is_worker_alive(batch)


# ---------------------------------------------------------------------------
# Heartbeat + cancel, called from inside the send loops
# ---------------------------------------------------------------------------

class BatchHeartbeat:
    """Per-loop helper: proves the loop is alive and notices cancel requests.

    One instance per running loop, so the rate-limiting state is not shared
    between two batches running in two threads (a module-level timestamp, as in
    ncm/scheduler.py, works there only because that sync is globally singular).
    """

    def __init__(self, batch):
        self.batch = batch
        self.model = type(batch)
        self._last_write = 0.0
        self._deadline = None

    def with_deadline(self, seconds):
        self._deadline = monotonic() + seconds
        return self

    def should_stop(self):
        """Call once per order, before doing the work.

        Returns a reason string ('cancelled' / 'deadline') to stop, or None to
        carry on. The cancel flag is read on every call - a single indexed
        SELECT next to a multi-second HTTP request is not worth batching, and a
        Terminate click should take effect on the next order, not in 30s.
        """
        if self._deadline is not None and monotonic() > self._deadline:
            return 'deadline'

        row = self.model.objects.filter(pk=self.batch.pk).values(
            'cancel_requested', 'is_deleted'
        ).first()
        if row is None or row['is_deleted']:
            return 'cancelled'
        if row['cancel_requested']:
            return 'cancelled'

        self.touch()
        return None

    def touch(self):
        """Rate-limited liveness write. Never raises: losing a heartbeat costs
        at worst a premature 'stalled' badge, and must not abort a send."""
        now = monotonic()
        if now - self._last_write < HEARTBEAT_MIN_INTERVAL_SECONDS:
            return
        self._last_write = now
        try:
            # .update() so this never touches any other column of a row the
            # loop is also writing counts to.
            self.model.objects.filter(pk=self.batch.pk).update(
                worker_heartbeat_at=timezone.now()
            )
        except Exception:
            logger.debug('Could not refresh bulk batch heartbeat', exc_info=True)

    def release(self):
        """Clear the heartbeat so the batch never looks alive after the loop
        ends. Also clears the cancel flag: it has been honoured by now, and
        leaving it set would abort the next resume before its first order."""
        try:
            self.model.objects.filter(pk=self.batch.pk).update(
                worker_heartbeat_at=None, cancel_requested=False
            )
        except Exception:
            logger.debug('Could not release bulk batch heartbeat', exc_info=True)


def start_heartbeat(batch):
    """Heartbeat for a loop running inline in a request. Stamps immediately so
    the batch reads as alive from its first order, not 30s in."""
    hb = BatchHeartbeat(batch)
    type(batch).objects.filter(pk=batch.pk).update(
        worker_heartbeat_at=timezone.now(), cancel_requested=False
    )
    hb._last_write = monotonic()
    return hb


# ---------------------------------------------------------------------------
# Counts and pending work
# ---------------------------------------------------------------------------

def _apply_counts(batch, tally, save=True):
    changed = (
        batch.success_count != tally['success']
        or batch.failed_count != tally['failed']
        or batch.skipped_count != tally['skipped']
    )
    batch.success_count = tally['success']
    batch.failed_count = tally['failed']
    batch.skipped_count = tally['skipped']
    if save and changed:
        batch.save(update_fields=['success_count', 'failed_count', 'skipped_count'])
    return tally['success'], tally['failed'], tally['skipped']


def reconcile_counts(adapter, batch, save=True):
    """Recompute the batch counters from the child rows.

    A stalled batch shows 0 success / 0 failed only because those columns are
    written once, after the loop. Every order it did process left a row, so the
    real figures are one query away.
    """
    return _apply_counts(batch, _recorded_for(adapter, [batch.id])[batch.id]['counts'], save)


def _selected_ids(batch):
    """The order ids this batch was asked to send, if we know them."""
    raw = batch.selected_order_ids
    if not raw:
        return []
    out = []
    for value in raw:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _blank_record():
    return {'by_order': {}, 'counts': {'success': 0, 'failed': 0, 'skipped': 0}}


def _recorded_for(adapter, batch_ids):
    """Child-row facts for a set of batches, in one query.

    Returns {batch_id: {'by_order': {order_id: {...}}, 'counts': {...}}}.
    Both readings come from the same pass because both callers - the count
    reconciliation and the pending-work calculation - need it for the same rows,
    and the list page needs it for every row it renders.

    `by_order` covers only rows that still point at an order (the FK is
    SET_NULL, so an order deleted since the send leaves an orphan row). `counts`
    covers every row, orphans included, because they were still real attempts.
    """
    out = {bid: _blank_record() for bid in batch_ids}
    if not batch_ids:
        return out
    rows = (
        adapter.order_model.objects
        .filter(batch_id__in=batch_ids)
        .values_list('batch_id', 'order_id', 'status', adapter.provider_id_field)
    )
    for batch_id, order_id, status, provider_id in rows:
        record = out.setdefault(batch_id, _blank_record())
        if order_id is not None:
            record['by_order'][order_id] = {
                'status': status,
                # A consignment number means the courier accepted this order,
                # whatever the row's status says.
                'dispatched': provider_id not in (None, ''),
            }
        if status in record['counts']:
            record['counts'][status] += 1
    return out


def _pending_from(batch, recorded):
    """Order ids this batch still owes, given its recorded `by_order` map.

    An order is done if it succeeded, or if its row carries a provider
    consignment number. Re-sending either would risk a duplicate delivery, and
    the second case matters for the count the button shows: 'skipped' covers
    both "already at the courier" (done) and PND's "missing customer details"
    (worth retrying once the data is fixed), and only the consignment number
    tells those apart.
    """
    done = {
        oid for oid, row in recorded.items()
        if row['status'] == 'success' or row['dispatched']
    }

    candidates = _selected_ids(batch)
    if not candidates:
        # Pre-snapshot batch: the only orders we can name are the ones that got
        # far enough to leave a row.
        candidates = list(recorded.keys())

    seen = set()
    pending = []
    for oid in candidates:
        if oid in done or oid in seen:
            continue
        seen.add(oid)
        pending.append(oid)
    return pending


def pending_order_ids(adapter, batch):
    """Order ids this batch still owes (single-batch convenience wrapper)."""
    return _pending_from(batch, _recorded_for(adapter, [batch.id])[batch.id]['by_order'])


def _state(adapter, batch, recorded):
    """Everything the Bulk Logs page needs to render this batch's controls.

    `recorded` is this batch's `by_order` map, or None when the caller decided
    the batch could not offer a resume anyway and skipped the lookup.
    """
    alive = is_worker_alive(batch)

    if recorded is None:
        pending = []
        unrecoverable = 0
    else:
        pending = _pending_from(batch, recorded)
        # Orders the batch owed but can no longer name: created before
        # selected_order_ids existed, and it died before recording them.
        known = len(_selected_ids(batch)) or len(recorded)
        unrecoverable = max((batch.total_orders or 0) - known, 0)

    return {
        'provider': adapter.key,
        'id': batch.id,
        'batch_number': batch.batch_number,
        'status': batch.status,
        'status_display': batch.get_status_display(),
        'total_orders': batch.total_orders,
        'success_count': batch.success_count,
        'failed_count': batch.failed_count,
        'skipped_count': batch.skipped_count,
        'is_running': alive,
        'is_stalled': is_stalled(batch),
        'cancel_requested': batch.cancel_requested,
        'pending_count': len(pending),
        'unrecoverable_count': unrecoverable,
        'can_terminate': batch.status in OPEN_STATUSES,
        'can_resume': batch.status in RESUMABLE_STATUSES and not alive and bool(pending),
        'is_open': batch.status in OPEN_STATUSES,
    }


def describe(adapter, batch, refresh_counts=False):
    """State for one batch. Prefer annotate_controls() for a whole page."""
    record = _recorded_for(adapter, [batch.id])[batch.id]
    if refresh_counts:
        _apply_counts(batch, record['counts'])
    return _state(adapter, batch, record['by_order'])


def annotate_controls(logs):
    """Attach a `.control` state dict to each log on a Bulk Logs page.

    One query per provider, not one per row. Logs must already carry
    `.log_provider`, as the list view sets it.
    """
    by_provider = {}
    for log in logs:
        by_provider.setdefault(getattr(log, 'log_provider', 'ncm'), []).append(log)

    for provider, provider_logs in by_provider.items():
        try:
            adapter = get_adapter(provider)
        except BatchError:
            continue
        # Only rows that could offer a control need their child rows counted.
        interesting_ids = {
            log.id for log in provider_logs if log.status in RESUMABLE_STATUSES
        }
        recorded = _recorded_for(adapter, interesting_ids)
        for log in provider_logs:
            record = recorded.get(log.id) if log.id in interesting_ids else None
            log.control = _state(
                adapter, log, record['by_order'] if record else None
            )
    return logs


def progress_states(keys):
    """Live state for a page's worth of batches, given "<provider>:<id>" keys.

    Three queries per provider at most, however many rows the page is watching:
    the polling loop fires every few seconds from every open tab, so this must
    not scale with the number of spinning rows.
    """
    wanted = {}
    for key in keys:
        provider, _, raw_id = key.partition(':')
        try:
            wanted.setdefault(provider, set()).add(int(raw_id))
        except (TypeError, ValueError):
            continue

    states = []
    for provider, ids in wanted.items():
        try:
            adapter = get_adapter(provider)
        except BatchError:
            continue
        batches = list(adapter.log_model.objects.filter(id__in=ids, is_deleted=False))
        recorded = _recorded_for(adapter, [b.id for b in batches])
        for batch in batches:
            record = recorded.get(batch.id) or _blank_record()
            if batch.status in OPEN_STATUSES:
                # A running batch writes its counters only at the end, so read
                # them from the child rows to make progress visible while it
                # works. Only for open batches: a finished one's stored counts
                # are what its run actually did, and must not be silently
                # rewritten from child rows some later cleanup may have pruned.
                _apply_counts(batch, record['counts'])
            states.append(_state(adapter, batch, record['by_order']))
    return states


def _final_status(batch):
    """Terminal status implied by the counts now on the batch."""
    total = batch.total_orders or 0
    attempted = batch.success_count + batch.failed_count + batch.skipped_count
    if attempted < total:
        # Never finished the list - 'partial' if anything landed, else failed.
        return 'partial' if batch.success_count else 'failed'
    if batch.failed_count == 0:
        return 'completed'
    if batch.success_count == 0 and batch.skipped_count == 0:
        return 'failed'
    return 'partial'


# ---------------------------------------------------------------------------
# Terminate
# ---------------------------------------------------------------------------

def terminate(provider, log_id, user):
    """Stop a batch. Returns (message, state)."""
    adapter, batch = get_batch(provider, log_id)

    if batch.status not in OPEN_STATUSES:
        raise BatchError(
            f'Batch {batch.batch_number} is already {batch.get_status_display().lower()}.'
        )

    reconcile_counts(adapter, batch)

    if is_worker_alive(batch):
        # Something is genuinely mid-send. All we can do is ask it to stop
        # after the order it is on; killing it there would leave an order sent
        # at the provider with nothing recorded here.
        adapter.log_model.objects.filter(pk=batch.pk).update(cancel_requested=True)
        adapter.detail_model.objects.create(
            batch=batch,
            action='error',
            message=f'Stop requested by {getattr(user, "username", "system")} - '
                    f'the batch will stop after the order it is currently sending.',
            user=user if getattr(user, 'pk', None) else None,
        )
        batch.refresh_from_db()
        logger.info('Bulk batch %s: stop requested by %s', batch.batch_number, user)
        return (
            f'Stop requested. Batch {batch.batch_number} is mid-send and will '
            f'stop after the current order.',
            describe(adapter, batch),
        )

    # Nobody is working it: close it out now.
    unreached = max(
        (batch.total_orders or 0)
        - (batch.success_count + batch.failed_count + batch.skipped_count),
        0,
    )
    batch.status = 'cancelled'
    batch.cancel_requested = False
    batch.worker_heartbeat_at = None
    batch.completed_at = timezone.now()
    batch.save(update_fields=[
        'status', 'cancel_requested', 'worker_heartbeat_at', 'completed_at',
    ])

    adapter.detail_model.objects.create(
        batch=batch,
        action='batch_completed',
        message=(
            f'Batch terminated by {getattr(user, "username", "system")}. '
            f'{batch.success_count} sent, {batch.failed_count} failed, '
            f'{batch.skipped_count} skipped, {unreached} never attempted.'
        ),
        user=user if getattr(user, 'pk', None) else None,
    )
    logger.info(
        'Bulk batch %s terminated by %s (%s unreached)',
        batch.batch_number, user, unreached,
    )
    return (
        f'Batch {batch.batch_number} terminated. '
        f'{batch.success_count} sent, {batch.failed_count} failed, '
        f'{batch.skipped_count} skipped'
        + (f', {unreached} never attempted.' if unreached else '.'),
        describe(adapter, batch),
    )


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def _claim(adapter, batch):
    """Atomically take ownership of this batch. Only one caller wins.

    The status flip and the liveness stamp are one UPDATE for the same reason
    the NCM sync's claim is: split into a read then a write, two workers that
    both saw 'stalled' would both start sending the same orders.
    """
    now = timezone.now()
    won = adapter.log_model.objects.filter(
        pk=batch.pk,
        is_deleted=False,
        status__in=RESUMABLE_STATUSES,
    ).filter(
        Q(worker_heartbeat_at__isnull=True) | Q(worker_heartbeat_at__lt=_stale_cutoff())
    ).update(
        status='processing',
        worker_heartbeat_at=now,
        cancel_requested=False,
        completed_at=None,
    ) == 1
    if won:
        batch.refresh_from_db()
    return won


def resume(provider, log_id, user):
    """Start (or restart) work on a batch's outstanding orders.

    Returns (message, state). The work happens on a background thread, so the
    counts in `state` are the pre-run ones; the page polls for the rest.
    """
    adapter, batch = get_batch(provider, log_id)

    if is_worker_alive(batch):
        raise BatchError(
            f'Batch {batch.batch_number} is already being processed right now.'
        )
    if batch.status not in RESUMABLE_STATUSES:
        raise BatchError(
            f'Batch {batch.batch_number} is {batch.get_status_display().lower()} '
            f'and has nothing left to send.'
        )

    reconcile_counts(adapter, batch)
    pending = pending_order_ids(adapter, batch)

    if not pending:
        # Distinguish "everything went out" from "we no longer know what it
        # was" - the second needs a different action from the user.
        known = len(_selected_ids(batch)) or adapter.order_model.objects.filter(batch=batch).count()
        if (batch.total_orders or 0) > known:
            raise BatchError(
                f'Batch {batch.batch_number} stalled before it recorded which orders '
                f'it still had to send, so they cannot be resumed. Terminate it and '
                f're-send the remaining orders from the Orders page.'
            )
        raise BatchError(f'Batch {batch.batch_number} has no orders left to send.')

    if not _claim(adapter, batch):
        raise BatchError(
            f'Batch {batch.batch_number} was just claimed by someone else. Refresh the page.'
        )

    adapter.detail_model.objects.create(
        batch=batch,
        action='batch_started',
        message=f'Resume requested by {getattr(user, "username", "system")}: '
                f'{len(pending)} order(s) outstanding.',
        user=user if getattr(user, 'pk', None) else None,
    )

    thread = threading.Thread(
        target=_resume_in_thread,
        kwargs={
            'provider': adapter.key,
            'log_id': batch.id,
            'order_ids': pending,
            'user_id': getattr(user, 'pk', None),
        },
        name=f'bulk-resume-{adapter.key}-{batch.id}',
        daemon=True,
    )
    try:
        # on_commit so the claim is durable before the thread can act on it.
        transaction.on_commit(thread.start)
    except Exception:
        # cPanel caps process/thread counts, so this is a real failure mode.
        # Release the claim rather than leave the batch locked for 5 minutes.
        logger.exception('Could not start bulk resume thread for %s', batch.batch_number)
        adapter.log_model.objects.filter(pk=batch.pk).update(worker_heartbeat_at=None)
        raise BatchError('Could not start the background worker. Please try again.')

    batch.refresh_from_db()
    state = describe(adapter, batch)
    state['is_running'] = True  # the thread starts on commit, just after this
    return (
        f'Resuming batch {batch.batch_number}: sending {len(pending)} outstanding order(s).',
        state,
    )


def _resume_in_thread(provider, log_id, order_ids, user_id):
    """Thread entrypoint. Closes this thread's DB connections on the way out -
    Django opens one per thread and CONN_MAX_AGE would otherwise keep it."""
    try:
        _run_resume(provider, log_id, order_ids, user_id)
    except Exception:
        logger.exception('Bulk resume thread failed for %s #%s', provider, log_id)
    finally:
        connections.close_all()


def _run_resume(provider, log_id, order_ids, user_id):
    from dashboard.models import Order
    from django.contrib.auth import get_user_model

    adapter = get_adapter(provider)
    batch = adapter.log_model.objects.filter(pk=log_id).first()
    if batch is None:
        return

    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    # send_single_order_to_* want a request only for `request.user` on the
    # activity log; a thread has none, so hand them the one thing they read.
    shim = SimpleNamespace(user=user)

    heartbeat = BatchHeartbeat(batch).with_deadline(MAX_RESUME_SECONDS)
    heartbeat.touch()

    auto_set_logistics = _options(batch)['auto_set_logistics']
    try:
        send_extra = adapter.prepare(batch)
    except Exception:
        logger.warning('Could not prepare %s resume state', provider, exc_info=True)
        send_extra = {}

    stop_reason = None
    processed = 0
    try:
        for order_id in order_ids:
            stop_reason = heartbeat.should_stop()
            if stop_reason:
                break

            # Read each order immediately before sending it, not all of them up
            # front. This run can last minutes, and the send helpers call
            # order.save() on the whole object - an order loaded at the start
            # would write stale field values over anything staff edited while
            # the run was working through the queue.
            order = Order.objects.filter(id=order_id, is_deleted=False).first()
            if order is None:
                # Deleted between the click and now. Reconciliation at the end
                # accounts for it.
                continue

            try:
                result = adapter.send(shim, order, batch, **send_extra)
            except Exception as e:
                logger.exception('Resume: %s order %s failed', provider, order_id)
                result = {'status': 'error', 'message': str(e)}

            # The NCM helper stamps order.logistics itself; the PND one does
            # not - its bulk view does, so a resume has to do the same or a
            # resumed PND order silently keeps whatever logistics it had.
            if (auto_set_logistics and result.get('status') == 'success'
                    and order.logistics != adapter.logistics_value):
                order.logistics = adapter.logistics_value
                order.save(update_fields=['logistics'])

            _record_order_result(adapter, batch, order, result, user)
            processed += 1
    finally:
        _finalise(adapter, batch, heartbeat, user, stop_reason, processed)


def _record_order_result(adapter, batch, order, result, user):
    """Write (or overwrite) this order's row in the batch.

    update_or_create, not create: a resumed order usually already has a row
    from the run that failed it, and a second row would double-count it in
    reconcile_counts and show the order twice in the expanded view.
    """
    status_map = {'success': ('success', 'order_sent'),
                  'skipped': ('skipped', 'order_skipped')}
    log_status, log_action = status_map.get(result.get('status'), ('failed', 'order_failed'))

    try:
        order.refresh_from_db()
    except Exception:
        pass

    defaults = {
        'order_number': order.order_number or '',
        'customer_name': order.customer_name or '',
        'customer_phone': order.customer_phone or '',
        'shipping_address': order.shipping_address or '',
        'cod_amount': order.amount_due or 0,
        'status': log_status,
        'message': result.get('message', ''),
    }
    defaults.update(adapter.order_row_defaults(order))

    existing = adapter.order_model.objects.filter(batch=batch, order=order).order_by('id')
    first = existing.first()
    if first is None:
        adapter.order_model.objects.create(batch=batch, order=order, **defaults)
    else:
        for field, value in defaults.items():
            setattr(first, field, value)
        first.save(update_fields=list(defaults.keys()))
        # Older runs could leave duplicates for the same order; collapse them
        # so the counts stop drifting.
        existing.exclude(pk=first.pk).delete()

    adapter.detail_model.objects.create(
        batch=batch,
        action=log_action,
        order_number=order.order_number or '',
        message=result.get('message', ''),
        user=user if getattr(user, 'pk', None) else None,
    )


def _finalise(adapter, batch, heartbeat, user, stop_reason, processed):
    """Close out a resume run, whatever ended it."""
    try:
        batch.refresh_from_db()
        reconcile_counts(adapter, batch, save=False)

        if stop_reason == 'deadline':
            # Out of time, not out of work: leave it 'processing' so the page
            # still offers Resume. The heartbeat is cleared in the finally
            # below, so the next claim succeeds immediately rather than waiting
            # out the stale window.
            status = 'processing'
            note = (f'Resume paused after {processed} order(s) - time budget reached. '
                    f'Press Resume again to continue.')
        elif stop_reason == 'cancelled':
            status = 'cancelled'
            note = f'Resume stopped on request after {processed} order(s).'
        else:
            status = _final_status(batch)
            note = (f'Resume finished: {batch.success_count} sent, '
                    f'{batch.failed_count} failed, {batch.skipped_count} skipped.')

        batch.status = status
        batch.completed_at = None if status == 'processing' else timezone.now()
        batch.save(update_fields=[
            'success_count', 'failed_count', 'skipped_count', 'status', 'completed_at',
        ])

        adapter.detail_model.objects.create(
            batch=batch,
            action='batch_completed',
            message=note,
            user=user if getattr(user, 'pk', None) else None,
        )
        logger.info('Bulk batch %s resume ended (%s): %s', batch.batch_number, status, note)
    except Exception:
        logger.exception('Could not finalise bulk batch %s', getattr(batch, 'batch_number', '?'))
    finally:
        heartbeat.release()
