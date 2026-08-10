"""
Verify the NCM background sync scheduler (ncm/scheduler.py).

The scheduler replaces a cron job that was never installed: it lets whichever
web request first notices the sync is due run it on a background thread. That
only works if the claim is genuinely exclusive, so most of what follows is
about the lock rather than about NCM.

Run:  python test_ncm_sync_scheduler.py

Touches the real database (the APISettings singleton), like the other
standalone scripts in this repo. It saves the row's original values and puts
them back at the end. It never calls NCM: run_bulk_ncm_status_sync is
monkey-patched throughout.
"""

import os
import sys
import threading
import time
from datetime import timedelta

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.utils import timezone  # noqa: E402

from dashboard.models import APISettings  # noqa: E402
from ncm import scheduler  # noqa: E402

PASSED = []
FAILED = []


def check(name, condition, detail=''):
    if condition:
        PASSED.append(name)
        print(f'  [PASS] {name}')
    else:
        FAILED.append(f'{name}: {detail}')
        print(f'  [FAIL] {name} {detail}')


def reset(interval=600, started_at=None, running_since=None, finished_at=None):
    """Put the singleton into a known state without going through save()."""
    APISettings.get_settings()
    APISettings.objects.filter(pk=1).update(
        order_sync_interval=interval,
        last_bulk_sync_started_at=started_at,
        bulk_sync_running_since=running_since,
        last_bulk_sync_finished_at=finished_at,
        last_bulk_sync_summary={},
    )


# ---------------------------------------------------------------- test 1
def test_due_check():
    print('\n1. Runs when due, stays quiet when not')

    calls = []

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {'total_orders': 3, 'updated_count': 1, 'errors': [], 'deadline_reached': False}

    original = scheduler.run_bulk_ncm_status_sync
    scheduler.run_bulk_ncm_status_sync = fake_sync
    try:
        # Never run before -> due.
        reset(interval=600, started_at=None)
        started, summary = scheduler.maybe_run_bulk_sync(run_in_thread=False)
        check('first ever run starts', started is True)
        check('summary reports the sync result', (summary or {}).get('updated_count') == 1,
              f'got {summary}')
        check('sync was actually invoked once', len(calls) == 1, f'{len(calls)} calls')

        # Just ran -> not due.
        started, _ = scheduler.maybe_run_bulk_sync(run_in_thread=False)
        check('a second immediate run is skipped', started is False)
        check('skipped run made no NCM work', len(calls) == 1, f'{len(calls)} calls')

        # Interval elapsed -> due again.
        reset(interval=600, started_at=timezone.now() - timedelta(seconds=601))
        started, _ = scheduler.maybe_run_bulk_sync(run_in_thread=False)
        check('runs again once the interval has elapsed', started is True)

        # force ignores the interval.
        started, _ = scheduler.maybe_run_bulk_sync(force=True, run_in_thread=False)
        check('force=True overrides the interval', started is True)
    finally:
        scheduler.run_bulk_ncm_status_sync = original


# ---------------------------------------------------------------- test 2
def test_concurrent_claims():
    print('\n2. Only one of many simultaneous claims wins')

    reset(interval=600, started_at=None)

    results = []
    barrier = threading.Barrier(8)

    def worker():
        from django.db import connections
        barrier.wait()  # maximise the overlap
        try:
            results.append(scheduler._claim())
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check('exactly one claimer wins', results.count(True) == 1,
          f'winners={results.count(True)} of {len(results)}')


# ---------------------------------------------------------------- test 3
def test_running_lock_blocks():
    print('\n3. A live claim blocks another run; a dead one is taken over')

    # Someone is running right now, and the interval has long elapsed.
    reset(
        interval=60,
        started_at=timezone.now() - timedelta(hours=2),
        running_since=timezone.now(),
    )
    check('a fresh claim blocks a new run', scheduler._claim() is False)

    # force must respect the running lock too - otherwise two syncs overlap.
    check('force does not bypass the running lock', scheduler._claim(force=True) is False)

    # An abandoned claim (worker killed mid-sync) is reclaimed.
    reset(
        interval=60,
        started_at=timezone.now() - timedelta(hours=2),
        running_since=timezone.now() - timedelta(seconds=scheduler.STALE_CLAIM_SECONDS + 30),
    )
    check('a stale claim is taken over', scheduler._claim() is True)


# ---------------------------------------------------------------- test 4
def test_liveness_keeps_claim_alive():
    print('\n4. A long run that reports progress is not mistaken for a dead one')

    reset(
        interval=60,
        started_at=timezone.now() - timedelta(hours=2),
        running_since=timezone.now() - timedelta(seconds=scheduler.STALE_CLAIM_SECONDS + 30),
    )
    # The running sync pings progress; that must re-arm the staleness window.
    scheduler._touch()
    check('progress ping rescues an otherwise-stale claim', scheduler._claim() is False)


# ---------------------------------------------------------------- test 5
def test_start_to_start_gating():
    print('\n5. Gating measures from run start, not run finish')

    slow_seconds = 2

    def slow_sync(**kwargs):
        time.sleep(slow_seconds)
        return {'total_orders': 0, 'updated_count': 0, 'errors': [], 'deadline_reached': False}

    original = scheduler.run_bulk_ncm_status_sync
    scheduler.run_bulk_ncm_status_sync = slow_sync
    try:
        # Interval longer than the run: finishing must NOT make it due again.
        reset(interval=60, started_at=None)
        started, _ = scheduler.maybe_run_bulk_sync(run_in_thread=False)
        check('slow run starts', started is True)

        started, _ = scheduler.maybe_run_bulk_sync(run_in_thread=False)
        check('finishing a slow run does not immediately retrigger it', started is False,
              'gating must use last_bulk_sync_started_at, not finished_at')
    finally:
        scheduler.run_bulk_ncm_status_sync = original


# ---------------------------------------------------------------- test 6
def test_release_and_summary():
    print('\n6. The claim is released and the result recorded, even on failure')

    def boom(**kwargs):
        raise RuntimeError('NCM exploded')

    original = scheduler.run_bulk_ncm_status_sync
    scheduler.run_bulk_ncm_status_sync = boom
    try:
        reset(interval=600, started_at=None)
        started, summary = scheduler.maybe_run_bulk_sync(run_in_thread=False)
        check('a failing sync still counts as started', started is True)

        s = APISettings.get_settings()
        check('the claim is released after a failure', s.bulk_sync_running_since is None)
        check('the finish time is recorded', s.last_bulk_sync_finished_at is not None)
        check('the failure is stored in the summary',
              (s.last_bulk_sync_summary or {}).get('error_count') == 1,
              f'got {s.last_bulk_sync_summary}')
    finally:
        scheduler.run_bulk_ncm_status_sync = original


# ---------------------------------------------------------------- test 7
def test_interval_floor():
    print('\n7. A too-short configured interval is clamped server-side')

    reset(interval=1, started_at=timezone.now() - timedelta(seconds=5))
    # 5s since the last run is past the configured 1s but inside the 60s floor.
    check('interval below the floor is not honoured', scheduler._claim() is False,
          f'MIN_SYNC_SECONDS={scheduler.MIN_SYNC_SECONDS}')

    status = scheduler.get_status()
    check('get_status reports the clamped interval',
          status['server_sync_interval'] == scheduler.MIN_SYNC_SECONDS,
          f"got {status['server_sync_interval']}")


# ---------------------------------------------------------------- test 8
def test_page_refresh_field():
    print('\n8. page_refresh_interval survived the rename from webhook_check_interval')

    s = APISettings.get_settings()
    check('page_refresh_interval exists', hasattr(s, 'page_refresh_interval'))
    check('webhook_check_interval is gone', not hasattr(s, 'webhook_check_interval'))
    check('it holds a usable value', isinstance(s.page_refresh_interval, int)
          and s.page_refresh_interval > 0, f'got {s.page_refresh_interval!r}')


# ---------------------------------------------------------------- test 9
def test_partial_run_does_not_move_the_clock():
    print('\n9. "Sync Now" on one page does not postpone the next full sync')

    def fake_sync(**kwargs):
        return {'total_orders': 2, 'updated_count': 0, 'errors': [], 'deadline_reached': False}

    original = scheduler.run_bulk_ncm_status_sync
    scheduler.run_bulk_ncm_status_sync = fake_sync
    try:
        # A full sync ran a moment ago; the schedule clock points at it.
        stamp = timezone.now() - timedelta(seconds=30)
        reset(interval=600, started_at=stamp)

        started, _ = scheduler.maybe_run_bulk_sync(
            force=True, run_in_thread=False, order_ids=[1, 2])
        check('a forced partial run starts', started is True)

        s = APISettings.get_settings()
        drift = abs((s.last_bulk_sync_started_at - stamp).total_seconds())
        check('the schedule clock is untouched by a partial run', drift < 1,
              f'moved by {drift:.1f}s - the next full sync would be delayed')

        # A full forced run, by contrast, does move it.
        started, _ = scheduler.maybe_run_bulk_sync(force=True, run_in_thread=False)
        s = APISettings.get_settings()
        check('a full run does move the schedule clock',
              (s.last_bulk_sync_started_at - stamp).total_seconds() > 1)
    finally:
        scheduler.run_bulk_ncm_status_sync = original


# ---------------------------------------------------------------- test 10
def test_badge_classes_shared():
    print('\n10. Status badge colours come from one shared table')

    from dashboard.logistics_status import logistics_badge_class, logistics_status_text

    check('Delivered is green', logistics_badge_class('Delivered') == 'bg-success')
    check('In Transit is blue', logistics_badge_class('In Transit') == 'bg-primary')
    check('Order Marked Return is red', logistics_badge_class('Order Marked Return') == 'bg-danger')
    check('Pickup Order Created is amber',
          logistics_badge_class('Pickup Order Created') == 'bg-warning text-dark')
    check('an unknown status falls back to grey',
          logistics_badge_class('Something New') == 'bg-secondary')
    check('blank NCM status shows the NCM default',
          logistics_status_text('', 'ncm') == 'Pickup Order Created')
    check('blank PND status shows the PND default',
          logistics_status_text('', 'pick_and_drop') == 'Order Created')


# ---------------------------------------------------------------- test 12
def test_adaptive_backoff():
    """A slow run must not be allowed to run back-to-back forever.

    At a 60s interval a run that takes 90s would otherwise always be overdue
    the moment it finished, so a sync thread would be hammering NCM and the
    database continuously and every other page in the app would feel it.
    """
    print('\n12. A slow run backs the schedule off automatically')

    now = timezone.now()

    # Last run: started 5 min ago, took 4 minutes. Configured interval 60s.
    reset(
        interval=60,
        started_at=now - timedelta(seconds=300),
        finished_at=now - timedelta(seconds=60),
    )
    check('a slow previous run defers the next one', scheduler._claim() is False,
          'a 240s run at a 60s interval would otherwise run continuously')

    # Once a full "last duration" has passed since the start, it may run again.
    reset(
        interval=60,
        started_at=now - timedelta(seconds=300),
        finished_at=now - timedelta(seconds=245),
    )
    check('it runs again once the backoff has elapsed', scheduler._claim() is True)

    # A fast run must not be penalised at all.
    reset(
        interval=60,
        started_at=now - timedelta(seconds=61),
        finished_at=now - timedelta(seconds=60),
    )
    check('a fast run keeps the configured interval', scheduler._claim() is True)

    # A pathological run can't switch the sync off for good.
    reset(
        interval=60,
        started_at=now - timedelta(seconds=scheduler.MAX_ADAPTIVE_INTERVAL_SECONDS + 60),
        finished_at=now - timedelta(seconds=30),
    )
    check('backoff is capped so the sync always resumes', scheduler._claim() is True,
          f'cap is {scheduler.MAX_ADAPTIVE_INTERVAL_SECONDS}s')


# ---------------------------------------------------------------- test 11
def test_ajax_permission_contract():
    """A refused AJAX call must answer in JSON and leave no message behind.

    The project's permission decorators refuse by redirecting to a page AND
    queueing a Django message. On a normal view that is the right behaviour; on
    an endpoint that a page polls every few seconds it is not - the browser
    gets unparseable HTML, and the queued message ambushes the user on some
    later, unrelated page. These endpoints therefore check permissions inline.
    """
    print('\n11. Polled endpoints refuse in JSON, without queueing messages')

    from django.conf import settings as dj_settings
    from django.contrib.messages import get_messages
    from django.test import Client

    from accounts.models import CustomUser
    from dashboard.models import Order

    dj_settings.ALLOWED_HOSTS = list(dj_settings.ALLOWED_HOSTS) + ['testserver']

    user, created = CustomUser.objects.get_or_create(
        username='_test_ncm_sync_perms',
        defaults={'role': 'staff', 'is_staff': False, 'is_superuser': False},
    )
    try:
        user.role = 'staff'
        user.is_superuser = False
        for perm in ('can_view_orders', 'can_view_orders_list',
                     'can_view_ncm_orders', 'can_sync_ncm_orders'):
            setattr(user, perm, False)
        user.save()

        ids = ','.join(str(i) for i in Order.objects.values_list('id', flat=True)[:3])

        client = Client()
        client.force_login(user)
        r = client.get(f'/ncm/api/orders/batch-status/?order_ids={ids}')
        check('batch-status refuses with 403', r.status_code == 403, f'got {r.status_code}')
        check('batch-status answers in JSON',
              r['Content-Type'].startswith('application/json'), r['Content-Type'])
        check('batch-status queues no message',
              len(list(get_messages(r.wsgi_request))) == 0)

        client = Client()
        client.force_login(user)
        r = client.post('/ncm/bulk-sync/start/', {'order_ids': []})
        check('bulk-sync-start refuses with 403', r.status_code == 403, f'got {r.status_code}')
        check('bulk-sync-start answers in JSON',
              r['Content-Type'].startswith('application/json'), r['Content-Type'])
        check('bulk-sync-start queues no message',
              len(list(get_messages(r.wsgi_request))) == 0)

        # Granting any one of the view permissions is enough to poll.
        user.can_view_ncm_orders = True
        user.save()
        client = Client()
        client.force_login(user)
        r = client.get(f'/ncm/api/orders/batch-status/?order_ids={ids}')
        check('a viewer with can_view_ncm_orders may poll', r.status_code == 200,
              f'got {r.status_code}')
    finally:
        if created:
            user.delete()


def main():
    print('=' * 66)
    print('NCM background sync scheduler')
    print('=' * 66)

    original = APISettings.objects.filter(pk=1).values(
        'order_sync_interval', 'page_refresh_interval',
        'last_bulk_sync_started_at', 'last_bulk_sync_finished_at',
        'bulk_sync_running_since', 'last_bulk_sync_summary',
    ).first()

    try:
        test_due_check()
        test_concurrent_claims()
        test_running_lock_blocks()
        test_liveness_keeps_claim_alive()
        test_start_to_start_gating()
        test_release_and_summary()
        test_interval_floor()
        test_page_refresh_field()
        test_partial_run_does_not_move_the_clock()
        test_badge_classes_shared()
        test_adaptive_backoff()
        test_ajax_permission_contract()
    finally:
        if original:
            APISettings.objects.filter(pk=1).update(**original)
            print('\n(restored the original API settings row)')

    print('\n' + '=' * 66)
    print(f'{len(PASSED)} passed, {len(FAILED)} failed')
    if FAILED:
        for f in FAILED:
            print(f'  FAILED: {f}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
