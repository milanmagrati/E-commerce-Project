"""Verify the order detail page survives NCM's per-account rate limit.

The bug: opening an order fired several NCM calls at once (the page-load sync,
then details+status+comments in parallel). NCM allows about three requests per
second PER ACCOUNT and answers 429 "Request was throttled. Expected available
in 1 second." beyond that, so the first load of an order regularly reported
"Could not load status history from NCM" while a manual Retry - one lone
request - worked every time.

Measured against the live API before the fix: 3 parallel calls on one account
all return 200; 4+ start returning 429.

Run:  python test_ncm_rate_limit.py
(Sections marked LIVE hit the real NCM API and are skipped without network.)
"""
import os
import sys
import json
import time

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from concurrent.futures import ThreadPoolExecutor

from django.test import Client

from accounts.models import CustomUser
from services.ncm_service import (
    NCMService,
    NCM_MAX_CALLS_PER_SECOND,
    _await_rate_slot,
    _is_throttle_error,
    _throttle_wait_seconds,
    fetch_order_status_raw,
    invalidate_order_status_cache,
    peek_order_status_cache,
)

failures = []


def check(label, condition, detail=''):
    if condition:
        print('  PASS  ' + label)
    else:
        print('  FAIL  ' + label + '  ' + str(detail))
        failures.append(label)


class _FakeResponse:
    def __init__(self, headers=None, text=''):
        self.headers = headers or {}
        self.text = text


DRF_BODY = '{"detail":"Request was throttled. Expected available in 1 second."}'

print('\n1. Reading NCM "wait this long" answer')

check('DRF wording parsed out of the body',
      _throttle_wait_seconds(_FakeResponse(text=DRF_BODY)) == 1.0,
      _throttle_wait_seconds(_FakeResponse(text=DRF_BODY)))
check('multi-second wording parsed',
      _throttle_wait_seconds(_FakeResponse(
          text='{"detail":"Request was throttled. Expected available in 27 seconds."}')) == 27.0)
check('Retry-After header wins over the body',
      _throttle_wait_seconds(_FakeResponse({'Retry-After': '3'}, DRF_BODY)) == 3.0)
check('unparseable response falls back to 1s, never 0',
      _throttle_wait_seconds(_FakeResponse(text='who knows')) == 1.0)
check('garbage Retry-After falls back to the body',
      _throttle_wait_seconds(_FakeResponse({'Retry-After': 'soon'}, DRF_BODY)) == 1.0)

check('throttle recognised from a plain string',
      _is_throttle_error('Request was throttled. Expected available in 1 second.'))
check('throttle recognised from the dict envelope NCM sends',
      _is_throttle_error({'detail': 'Request was throttled. Expected available in 1 second.'}))
check('a 404 is not mistaken for a throttle',
      not _is_throttle_error({'detail': 'Not found.'}))
check('None is not a throttle', not _is_throttle_error(None))


print('\n2. The rate gate holds a burst under NCM per-second budget')

# Two accounts must not share one budget - NCM counts per API key, so gating
# them together would needlessly serialise unrelated work.
started = []


def _timed_slot(key):
    _await_rate_slot(key)
    started.append((key, time.monotonic()))


t0 = time.monotonic()
with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(_timed_slot, ['key-a'] * (NCM_MAX_CALLS_PER_SECOND + 2)))
elapsed = time.monotonic() - t0
check('a burst over the budget is delayed, not dropped',
      len(started) == NCM_MAX_CALLS_PER_SECOND + 2, started)
check('the overflow waits out the window (>=1s)', elapsed >= 0.9, round(elapsed, 2))

t0 = time.monotonic()
with ThreadPoolExecutor(max_workers=NCM_MAX_CALLS_PER_SECOND) as ex:
    list(ex.map(_timed_slot, ['key-b'] * NCM_MAX_CALLS_PER_SECOND))
within_budget = time.monotonic() - t0
check('a burst inside the budget is not delayed at all',
      within_budget < 0.5, round(within_budget, 2))

t0 = time.monotonic()
with ThreadPoolExecutor(max_workers=6) as ex:
    list(ex.map(_timed_slot, ['key-c', 'key-d', 'key-e'] * 2))
per_key = time.monotonic() - t0
check('separate accounts get separate budgets', per_key < 0.5, round(per_key, 2))


print('\n3. LIVE: a parallel burst that used to 429 now comes back clean')

PROBE_ORDER_ID = 25070858  # the order from the report; owned by a non-default account

probe = fetch_order_status_raw(PROBE_ORDER_ID)
offline = (not probe[0].get('success')
           and ('Connection' in str(probe[0].get('error'))
                or 'timeout' in str(probe[0].get('error')).lower()))

if offline:
    print('  SKIP  no network to NCM')
else:
    owner_config_id = probe[1]
    check('probe order resolves to an account', owner_config_id is not None, probe[0])

    svc = NCMService(api_config_id=owner_config_id)

    # Six at once against one account. Before the gate + 429 retry this
    # produced 429s on roughly half of them.
    def _one(i):
        return svc.get_order_status(PROBE_ORDER_ID)

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(_one, range(6)))

    throttled = [r for r in results if _is_throttle_error(r.get('error'))]
    failed = [r for r in results if not r.get('success')]
    check('no call in a 6-wide burst comes back throttled', not throttled,
          str(len(throttled)) + ' throttled')
    check('every call in the burst succeeded', not failed,
          str(len(failed)) + ' failed: ' + str(failed[:1]))


print('\n4. LIVE: the page-load sync and the detail read share one NCM call')

if offline:
    print('  SKIP  no network to NCM')
else:
    invalidate_order_status_cache(PROBE_ORDER_ID)
    check('cache starts empty', peek_order_status_cache(PROBE_ORDER_ID) is None)

    first, first_cfg = fetch_order_status_raw(PROBE_ORDER_ID, use_cache=True)
    check('first read succeeds', first.get('success'), first.get('error'))

    cached = peek_order_status_cache(PROBE_ORDER_ID)
    check('a populated answer is cached for the next reader', cached is not None)
    check('the cached answer carries the resolved account',
          cached is not None and cached[1] == first_cfg, cached[1] if cached else None)

    t0 = time.monotonic()
    second, second_cfg = fetch_order_status_raw(PROBE_ORDER_ID, use_cache=True)
    cached_elapsed = time.monotonic() - t0
    check('the second read is served locally, not from NCM',
          cached_elapsed < 0.05, round(cached_elapsed, 3))
    check('the cached read returns the same data', second.get('data') == first.get('data'))

    # A deliberate "Sync Status" click must never be served a snapshot.
    t0 = time.monotonic()
    fetch_order_status_raw(PROBE_ORDER_ID, use_cache=False)
    uncached_elapsed = time.monotonic() - t0
    check('use_cache=False still goes to NCM', uncached_elapsed > 0.05,
          round(uncached_elapsed, 3))

    invalidate_order_status_cache(PROBE_ORDER_ID)
    check('invalidation clears it', peek_order_status_cache(PROBE_ORDER_ID) is None)

    # A failed answer must not be cached, or one bad moment would stick around
    # answering for everyone who opens the order.
    fetch_order_status_raw(1, use_cache=True)
    check('a failed lookup is not cached', peek_order_status_cache(1) is None)


print('\n5. LIVE: a throttle is never reported as "no status history"')

user = CustomUser.objects.filter(is_superuser=True).first()
if offline or not user:
    print('  SKIP  no network to NCM or no superuser')
else:
    invalidate_order_status_cache(PROBE_ORDER_ID)

    # Hammer the endpoint the page calls, in parallel, the way several open
    # tabs (or several staff on one NCM account) would.
    def _hit(i):
        c = Client(SERVER_NAME='localhost')
        c.force_login(user)
        r = c.get('/api/ncm-rtv/' + str(PROBE_ORDER_ID) + '/detail/')
        return r.status_code, json.loads(r.content)

    with ThreadPoolExecutor(max_workers=4) as ex:
        hits = list(ex.map(_hit, range(4)))

    check('every concurrent detail read is HTTP 200',
          all(code == 200 for code, _ in hits), [c for c, _ in hits])
    check('every concurrent detail read returns the timeline',
          all(len(p.get('status_history') or []) > 0 for _, p in hits),
          [p.get('status_history_error') for _, p in hits])
    check('no read reports an error alongside a populated history',
          all(not (p.get('status_history_error') and p.get('status_history'))
              for _, p in hits))

    # And when a throttle does surface, it must be flagged so the page retries
    # instead of showing a dead end.
    payload = hits[0][1]
    check('the response carries the throttle flag field',
          'status_history_throttled' in payload, sorted(payload.keys())[:8])
    check('a healthy read is not flagged as throttled',
          payload.get('status_history_throttled') is False,
          payload.get('status_history_throttled'))


print('\n6. The rate gate covers the v1 endpoints and leaves v2 alone')

svc_scope = NCMService()
check('v1 /order/status is paced',
      svc_scope._rate_scope(svc_scope.base_url + '/order/status') is not None)
check('v1 /order is paced',
      svc_scope._rate_scope(svc_scope.base_url + '/order') is not None)
check('v1 /order/comment is paced',
      svc_scope._rate_scope(svc_scope.base_url + '/order/comment') is not None)
if svc_scope.base_url_v2 != svc_scope.base_url:
    check('v2 /vendor/orders is NOT paced - it absorbs 100 at a time',
          svc_scope._rate_scope(svc_scope.base_url_v2 + '/vendor/orders') is None,
          svc_scope._rate_scope(svc_scope.base_url_v2 + '/vendor/orders'))
    check('the pacing bucket is the API key, so accounts stay independent',
          svc_scope._rate_scope(svc_scope.base_url + '/order') == svc_scope.api_key)
else:
    print('  SKIP  no separate v2 base URL configured')


print('\n7. LIVE: a short RTV fetch is reported, not passed off as complete')

if offline:
    print('  SKIP  no network to NCM')
else:
    rtv_svc = NCMService(api_config_id=probe[1])

    real = rtv_svc.get_vendor_rtvs_by_status(include_recent=False)
    check('RTV fetch succeeds', real.get('success'), real.get('error'))
    check('a clean fetch is not flagged partial', real.get('partial') is False,
          real.get('error'))
    check('a clean fetch carries no error', real.get('error') is None, real.get('error'))
    print('        -> ' + str(len(real.get('data') or [])) + ' RTVs, partial='
          + str(real.get('partial')))

    # Force every page to fail and confirm the result says so instead of
    # quietly returning a short list with success=True.
    original = NCMService._make_request
    try:
        NCMService._make_request = lambda self, *a, **k: {
            'success': False, 'error': 'Request was throttled. Expected available in 1 second.'
        }
        broken = rtv_svc.get_vendor_rtvs_by_status(include_recent=True)
    finally:
        NCMService._make_request = original

    check('a fully failed fetch is flagged partial', broken.get('partial') is True, broken)
    check('a fully failed fetch names the reason',
          _is_throttle_error(broken.get('error')), broken.get('error'))
    check('a fully failed fetch returns no rows rather than stale ones',
          broken.get('data') == [], len(broken.get('data') or []))


print('')
print('8. Concurrent callers share one request instead of queueing')

import threading as _threading
from services.ncm_service import single_flight

runs = []
gate = _threading.Event()


def _slow_work():
    gate.wait(timeout=5)
    runs.append(1)
    return 'answer'


with ThreadPoolExecutor(max_workers=5) as ex:
    futures = [ex.submit(single_flight, 'shared-key', _slow_work) for _ in range(5)]
    time.sleep(0.2)   # let all five arrive before the leader finishes
    gate.set()
    answers = [f.result() for f in futures]

check('five concurrent callers ran the work once', len(runs) == 1, len(runs))
check('every caller got the answer', answers == ['answer'] * 5, answers)

# Sequential callers must NOT share - that would be a cache, and this is not one.
runs.clear()
gate.set()
single_flight('later-key', _slow_work)
single_flight('later-key', _slow_work)
check('sequential callers each do their own work', len(runs) == 2, len(runs))

runs.clear()
with ThreadPoolExecutor(max_workers=4) as ex:
    list(ex.map(lambda k: single_flight(k, _slow_work), ['k1', 'k2', 'k3', 'k4']))
check('different keys do not share a result', len(runs) == 4, len(runs))


def _boom():
    raise ValueError('upstream exploded')


errs = []
with ThreadPoolExecutor(max_workers=3) as ex:
    futs = [ex.submit(single_flight, 'boom-key', _boom) for _ in range(3)]
    for f in futs:
        try:
            f.result()
        except ValueError as e:
            errs.append(str(e))
check('a failure reaches every waiter, not just the leader',
      errs == ['upstream exploded'] * 3, errs)
check('a failed key is not left behind blocking the next caller',
      single_flight('boom-key', lambda: 'recovered') == 'recovered')


print('')
print('9. LIVE: six tabs cost no more NCM requests than one')

if offline or not user:
    print('  SKIP  no network to NCM or no superuser')
else:
    import requests as _requests
    seen = []
    seen_lock = _threading.Lock()
    _real_get = _requests.get

    def _counting_get(url, *a, **k):
        with seen_lock:
            seen.append(url)
        return _real_get(url, *a, **k)

    def _load(_):
        c = Client(SERVER_NAME='localhost')
        c.force_login(user)
        r = c.get('/api/ncm-rtv/' + str(PROBE_ORDER_ID) + '/detail/')
        return r.status_code, json.loads(r.content)

    _requests.get = _counting_get
    try:
        invalidate_order_status_cache(PROBE_ORDER_ID)
        seen.clear()
        one_result = _load(0)
        one_count = len(seen)

        invalidate_order_status_cache(PROBE_ORDER_ID)
        seen.clear()
        with ThreadPoolExecutor(max_workers=6) as ex:
            six = list(ex.map(_load, range(6)))
        six_count = len(seen)
    finally:
        _requests.get = _real_get

    check('six concurrent loads all return the timeline',
          all(code == 200 and len(p.get('status_history') or []) > 0 for code, p in six),
          [(c, len(p.get('status_history') or [])) for c, p in six])
    check('six concurrent loads cost no more requests than one',
          six_count <= one_count, str(six_count) + ' vs ' + str(one_count))
    print('        -> 1 load = ' + str(one_count) + ' NCM requests; '
          '6 concurrent loads = ' + str(six_count))


print('')
print('10. LIVE: the wrong account never leaves the page looking empty')

if offline or not user:
    print('  SKIP  no network to NCM or no superuser')
else:
    # get_order_comments answers success-with-empty-list when every URL 404s,
    # which is indistinguishable from "this order has no comments". Asked with
    # an account that does not own the order, that emptiness is a lie - and the
    # detail endpoint used to pass it straight through to the page.
    owner = NCMService(api_config_id=probe[1])
    truth = owner.get_order_comments(PROBE_ORDER_ID)
    truth_count = len(truth.get('data') or [])

    wrong = NCMService().get_order_comments(PROBE_ORDER_ID)
    check('a wrong-account comment fetch still claims success',
          wrong.get('success') and not wrong.get('data'),
          'this is the trap the endpoint has to see through')

    invalidate_order_status_cache(PROBE_ORDER_ID)
    c = Client(SERVER_NAME='localhost')
    c.force_login(user)
    payload = json.loads(c.get('/api/ncm-rtv/' + str(PROBE_ORDER_ID) + '/detail/').content)
    served = len(payload.get('comments') or [])
    if truth_count:
        check('the page is served the comments the owning account holds',
              served == truth_count, str(served) + ' of ' + str(truth_count))
    else:
        print('  SKIP  probe order has no comments to check against')
    check('the page is served the order details too',
          bool(payload.get('ncm_data')), payload.get('ncm_data'))


print('')
print('ALL CHECKS PASSED' if not failures else str(len(failures)) + ' FAILED: ' + str(failures))
sys.exit(1 if failures else 0)
