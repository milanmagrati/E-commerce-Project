# ncm/scheduler.py
"""
Runs the NCM bulk status sync on a schedule, without a scheduler.

This project is deployed on cPanel shared hosting: no Celery beat, no
persistent worker, and (in practice) no crontab either. The bulk sync was
written to be driven by `manage.py sync_all_ncm_orders` from cron - and because
that cron entry was never installed, nothing ever refreshed NCM status in the
background. Order status only changed when somebody clicked "Sync Status" on an
individual order.

So the trigger moves into the application: every authenticated page quietly
pings a heartbeat endpoint, the heartbeat asks this module "is a sync due?",
and the first request to win an atomic claim runs the sync on a background
thread. With any staff tab open, the sync runs on the interval configured in
Settings -> API Sync Settings. (For coverage when nobody has a tab open, the
heartbeat also accepts a shared-secret token so an external pinger can hit it.)

The whole design rests on one requirement: **no two syncs may ever run at
once**, across threads, across Passenger worker processes, and across a real
cron run if one is ever added. Django's default cache here is LocMemCache,
which is per-process, so cache-based locking would be worthless. The lock is
therefore a compare-and-swap against the APISettings row, expressed as a single
UPDATE statement so the database - not Python - decides the winner.
"""

import logging
import threading
from datetime import timedelta
from time import monotonic

from django.db import connections, transaction
from django.db.models import Q
from django.utils import timezone

from dashboard.models import APISettings
from ncm.bulk_sync import run_bulk_ncm_status_sync

logger = logging.getLogger('ncm')

#: A claim whose liveness hasn't been refreshed in this long is treated as
#: abandoned and taken over. A running sync bumps `bulk_sync_running_since`
#: at most every TOUCH_MIN_INTERVAL_SECONDS while it works, so this only has to
#: cover the gap between two pings - not the total run time, which is unbounded.
#: That distinction matters: Passenger recycles idle workers, and a daemon
#: thread dies with its process, leaving the claim set with nobody to release it.
STALE_CLAIM_SECONDS = 300

#: Wall-clock budget for one run. Past this no new chunk is started, so a huge
#: backlog is worked through over several runs instead of one endless thread.
MAX_RUN_SECONDS = 480

#: Floor on the configured sync interval, enforced here rather than only in the
#: settings form - a hand-crafted POST or a legacy row must not be able to point
#: this at the NCM API every second.
MIN_SYNC_SECONDS = 60

#: Ceiling on the adaptive backoff in _claim(). However slow one run was, the
#: next is never postponed by more than this - a single pathological run (NCM
#: timing out on every request, say) must not effectively switch the sync off.
MAX_ADAPTIVE_INTERVAL_SECONDS = 1800

#: APISettings is a singleton pinned to this row (see its save()).
SETTINGS_PK = 1


def _now():
    return timezone.now()


def _claim(force=False, advance_schedule=True):
    """Atomically take ownership of the next sync run.

    Returns True only for the caller that won. Everyone else gets False and
    does nothing.

    `advance_schedule=False` takes the lock without moving the schedule clock.
    That is for runs covering only a handful of orders - the "Sync Now" button
    on a single page of results. Such a run isn't a substitute for a full one,
    so letting it reset the clock would silently postpone the next real sync by
    up to a whole interval.

    The due-check and the lock are deliberately the *same* statement. Splitting
    them ("is it due?" then "is it unlocked?") leaves a window where a worker
    that read a stale `last_bulk_sync_started_at` can claim the instant the
    previous run releases, producing back-to-back full syncs. As one UPDATE,
    the row lock serialises the decision and only one caller sees rowcount 1.

    The due-check measures from when the last run *started*, not when it
    finished. Measuring from the finish would mean a run that takes longer than
    the interval is due again the moment it ends, i.e. a permanent sync loop.
    """
    settings_obj = APISettings.get_settings()  # ensures pk=1 exists

    interval = max(int(settings_obj.order_sync_interval or 0), MIN_SYNC_SECONDS)

    # Adaptive floor: rest at least as long as the last run took to work.
    #
    # The configured interval is a wish, and on shared hosting it can be a
    # harmful one - at 60s, a run that takes 90s would start again immediately
    # every time, so a sync thread would be doing NCM I/O essentially forever,
    # competing with real user requests for the handful of workers and DB
    # connections the plan allows. Pages elsewhere in the app go sluggish and
    # nobody connects it to a setting on the NCM screen.
    #
    # The gap is measured start-to-start, so resting "as long as the last run
    # took" would mean starting again the instant it finished - no rest at all.
    # Twice the duration is what actually caps the sync at roughly half of
    # wall-clock time: work for D, idle for D. A fast run (the normal case: a
    # few requests, well under a second) never reaches the configured interval
    # and is therefore unaffected.
    #
    # The duration is read from the last run's own summary rather than derived
    # from finished_at - started_at. Those two columns do not always describe
    # the same run: a partial "Sync Now" deliberately leaves started_at alone
    # but still stamps finished_at, so subtracting them turned a two-second
    # button click into a "duration" of however long ago the last full sync
    # began - and suppressed background syncing for the next several minutes.
    last_duration = 0.0
    try:
        last_duration = float((settings_obj.last_bulk_sync_summary or {}).get('duration_seconds') or 0)
    except (TypeError, ValueError):
        last_duration = 0.0
    effective_interval = min(
        max(interval, 2 * last_duration), MAX_ADAPTIVE_INTERVAL_SECONDS
    )

    now = _now()
    stale_cutoff = now - timedelta(seconds=STALE_CLAIM_SECONDS)
    due_cutoff = now - timedelta(seconds=effective_interval)

    claim = APISettings.objects.filter(pk=settings_obj.pk).filter(
        Q(bulk_sync_running_since__isnull=True) | Q(bulk_sync_running_since__lt=stale_cutoff)
    )
    if not force:
        claim = claim.filter(
            Q(last_bulk_sync_started_at__isnull=True) | Q(last_bulk_sync_started_at__lte=due_cutoff)
        )

    # queryset.update() bypasses auto_now, so this never touches `updated_at` -
    # which the Settings page shows as "Last updated" and should keep meaning
    # "when an admin last saved these settings".
    fields = {'bulk_sync_running_since': now}
    if advance_schedule:
        fields['last_bulk_sync_started_at'] = now
    won = claim.update(**fields) == 1

    if won:
        logger.info(
            'NCM bulk sync claimed (interval=%ss, effective=%ss, force=%s, advance_schedule=%s)',
            interval, int(effective_interval), force, advance_schedule,
        )
    return won


#: Smallest gap between two liveness writes. The sync calls the callback after
#: every order; this is what keeps that from meaning an UPDATE per order.
TOUCH_MIN_INTERVAL_SECONDS = 30

#: Monotonic timestamp of the last liveness write. Only ever touched by the one
#: thread holding the claim, so it needs no lock.
_last_touch = 0.0


def _touch():
    """Prove the running sync is still alive. bulk_sync's progress_callback.

    Rate-limited rather than driven by a count of orders: the callback has to
    fire often enough in WALL-CLOCK terms to stay inside STALE_CLAIM_SECONDS,
    and orders take wildly different amounts of time - a run whose NCM requests
    are all timing out at 60s spends half an hour on 25 orders, and a
    count-based ping would let its own claim look abandoned and be stolen
    while it was still working.
    """
    global _last_touch
    now = monotonic()
    if now - _last_touch < TOUCH_MIN_INTERVAL_SECONDS:
        return
    _last_touch = now
    try:
        APISettings.objects.filter(pk=SETTINGS_PK).update(bulk_sync_running_since=_now())
    except Exception:
        logger.debug('Could not refresh bulk sync liveness', exc_info=True)


def _release(summary=None):
    """Drop the claim and record the result. Must run even when the sync raised."""
    global _last_touch
    _last_touch = 0.0  # so the next run's first ping isn't skipped
    try:
        APISettings.objects.filter(pk=SETTINGS_PK).update(
            bulk_sync_running_since=None,
            last_bulk_sync_finished_at=_now(),
            last_bulk_sync_summary=summary if summary is not None else {},
        )
    except Exception:
        # Nothing left to do but log: the stale-claim window will free the lock.
        logger.exception('Could not release the NCM bulk sync claim')


def _run(order_ids=None, user=None):
    """Body of a claimed run. Never raises - callers may be a bare thread."""
    summary = {}
    # Timed here, so `duration_seconds` always describes THIS run - see the
    # note in _claim() about why the stored timestamps can't be subtracted.
    began = monotonic()
    try:
        summary = run_bulk_ncm_status_sync(
            user=user,
            order_ids=order_ids,
            progress_callback=_touch,
            deadline=_now() + timedelta(seconds=MAX_RUN_SECONDS),
        )
        # Keep the stored summary small and JSON-safe; the full error list can
        # be long and is already in the log.
        summary = {
            'total_orders': summary.get('total_orders', 0),
            'updated_count': summary.get('updated_count', 0),
            'error_count': len(summary.get('errors') or []),
            'errors': [str(e) for e in (summary.get('errors') or [])[:10]],
            'deadline_reached': summary.get('deadline_reached', False),
        }
    except Exception as e:
        logger.exception('NCM bulk sync run failed')
        summary = {'total_orders': 0, 'updated_count': 0, 'error_count': 1, 'errors': [str(e)]}
    finally:
        # monotonic() so a clock adjustment mid-run can't produce a negative or
        # wildly inflated duration that the next claim would then act on.
        summary['duration_seconds'] = round(monotonic() - began, 2)
        _release(summary)
    return summary


def _run_in_thread(order_ids=None, user=None):
    """Thread entrypoint: same as _run, plus this thread's DB connections.

    Django opens a connection per thread and CONN_MAX_AGE keeps it around, so a
    thread that exits without closing leaks one MySQL connection per run.
    """
    try:
        _run(order_ids=order_ids, user=user)
    finally:
        connections.close_all()


def maybe_run_bulk_sync(force=False, run_in_thread=True, order_ids=None, user=None):
    """Run the NCM bulk status sync if it is due (or if `force`).

    Args:
        force: skip the interval check (still respects the running-lock, so a
            forced run can't stack on top of an in-flight one).
        run_in_thread: True for web requests, which must return immediately.
            False for the management command, which should run inline and
            report its result.
        order_ids: restrict the run to specific orders (the list page's
            "Sync Now" button). A restricted run does not count as the
            scheduled full sync, so it leaves the schedule clock alone.

    Returns:
        (started, summary) - `started` is False when another run holds the lock
        or the interval hasn't elapsed. `summary` is None for threaded runs,
        which finish after this returns.
    """
    if not _claim(force=force, advance_schedule=not order_ids):
        return False, None

    if not run_in_thread:
        return True, _run(order_ids=order_ids, user=user)

    thread = threading.Thread(
        target=_run_in_thread,
        kwargs={'order_ids': order_ids, 'user': user},
        name='ncm-bulk-sync',
        daemon=True,
    )
    try:
        # on_commit so the claim is durable before the thread can act on it.
        # Outside a transaction Django runs this immediately, which is the
        # normal case here (there is no ATOMIC_REQUESTS).
        transaction.on_commit(thread.start)
    except Exception:
        # Shared hosting caps process/thread counts (cPanel LVE nproc), so
        # "can't start new thread" is a real failure here, not a theoretical
        # one. Release immediately rather than leaving the sync locked out
        # until the stale window expires.
        logger.exception('Could not start the NCM bulk sync thread')
        _release({'error': 'could not start sync thread'})
        return False, None

    return True, None


def get_status():
    """Scheduler state for the heartbeat endpoint and the logistics list page."""
    s = APISettings.get_settings()
    interval = max(int(s.order_sync_interval or 0), MIN_SYNC_SECONDS)
    running_since = s.bulk_sync_running_since
    is_running = bool(
        running_since
        and running_since > _now() - timedelta(seconds=STALE_CLAIM_SECONDS)
    )
    return {
        'server_sync_interval': interval,
        'page_refresh_interval': max(int(s.page_refresh_interval or 0), 5),
        'last_sync_started_at': s.last_bulk_sync_started_at,
        'last_sync_finished_at': s.last_bulk_sync_finished_at,
        'syncing': is_running,
        'last_summary': s.last_bulk_sync_summary or {},
    }
