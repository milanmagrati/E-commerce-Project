#!/usr/bin/env python
"""
Verification script for the RTV date-churn fix (apply_rtv_marked_at no-op
guard) and the ordering tiebreaker fix on possible_redirection_list /
ncm_rtvs_list. No DB writes are performed (save=False throughout).
"""

import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from datetime import datetime, timezone as dt_timezone
from services.ncm_service import NCMService
from dashboard.models import RTVOrder

print("\n" + "=" * 80)
print("RTV DATE STABILITY TEST SUITE")
print("=" * 80)


class FakeRTV:
    def __init__(self, rtv_marked_at=None, rtv_marked_at_source=None):
        self.rtv_marked_at = rtv_marked_at
        self.rtv_marked_at_source = rtv_marked_at_source
        self.rtv_marked_at_checked_at = None

    def save(self, update_fields=None):
        pass


dt1 = datetime(2026, 2, 20, 11, 19, 53, tzinfo=dt_timezone.utc)
dt1_iso = dt1.isoformat()
dt2 = datetime(2026, 2, 21, 9, 0, 0, tzinfo=dt_timezone.utc)
dt2_iso = dt2.isoformat()

# Test 1: identical value at equal rank -> no write
rtv = FakeRTV(rtv_marked_at=dt1, rtv_marked_at_source='comment')
changed = NCMService.apply_rtv_marked_at(rtv, dt1_iso, 'comment', save=False)
print(f"\n[TEST 1] Equal rank, identical instant -> changed={changed} (expect False)")
assert changed is False, "FAIL: no-op re-derivation should not count as a write"
assert rtv.rtv_marked_at == dt1, "FAIL: value should be untouched"
print("PASS")

# Test 2: different value at equal rank -> write (real unmark/re-mark)
rtv = FakeRTV(rtv_marked_at=dt1, rtv_marked_at_source='comment')
changed = NCMService.apply_rtv_marked_at(rtv, dt2_iso, 'comment', save=False)
print(f"\n[TEST 2] Equal rank, different instant -> changed={changed} (expect True)")
assert changed is True, "FAIL: genuine re-mark must still write"
assert rtv.rtv_marked_at == dt2
print("PASS")

# Test 3: higher rank + identical value -> still write (rank increase always writes)
rtv = FakeRTV(rtv_marked_at=dt1, rtv_marked_at_source='status_timeline')
changed = NCMService.apply_rtv_marked_at(rtv, dt1_iso, 'manual', save=False)
print(f"\n[TEST 3] Higher rank, identical instant -> changed={changed} (expect True)")
assert changed is True, "FAIL: rank increase should always write regardless of value"
print("PASS")

# Test 4: lower rank -> never write
rtv = FakeRTV(rtv_marked_at=dt1, rtv_marked_at_source='manual')
changed = NCMService.apply_rtv_marked_at(rtv, dt2_iso, 'status_timeline', save=False)
print(f"\n[TEST 4] Lower rank -> changed={changed} (expect False)")
assert changed is False
assert rtv.rtv_marked_at == dt1
print("PASS")

# Test 5: rtv_marked_at_checked_at is always stamped, even on no-op
rtv = FakeRTV(rtv_marked_at=dt1, rtv_marked_at_source='comment')
NCMService.apply_rtv_marked_at(rtv, dt1_iso, 'comment', save=False)
print(f"\n[TEST 5] checked_at stamped on no-op -> {rtv.rtv_marked_at_checked_at is not None} (expect True)")
assert rtv.rtv_marked_at_checked_at is not None, "FAIL: repair queue must still make progress"
print("PASS")

# Test 6: ordering tiebreaker includes '-id' for deterministic pagination
import inspect
from dashboard import views as dashboard_views
src_pr = inspect.getsource(dashboard_views.possible_redirection_list)
src_rtv = inspect.getsource(dashboard_views.ncm_rtvs_list)
print("\n[TEST 6] '-id' tiebreaker present in order_by() calls")
assert "'-created_at', '-id'" in src_pr, "FAIL: possible_redirection_list missing -id tiebreaker"
assert "'-created_at', '-id'" in src_rtv, "FAIL: ncm_rtvs_list missing -id tiebreaker"
print("PASS")

# Test 7: extract_rtv_marked_at is order-independent (tied added_time)
# NCM's comment endpoint has no documented ordering; the same comment set
# arriving in a different order must not flip the winning comment, or
# rtv.comment churns and the page reloads on every poll.
c1 = {'comment': 'RTV marked - damaged box', 'added_time': '2026-02-20T11:19:53+05:45', 'added_by': 'NCM Staff'}
c2 = {'comment': 'RTV marked - customer refused', 'added_time': '2026-02-20T11:19:53+05:45', 'added_by': 'NCM Staff'}
r1 = NCMService.extract_rtv_marked_at([c1, c2])
r2 = NCMService.extract_rtv_marked_at([c2, c1])
print(f"\n[TEST 7] Tied added_time, reversed list -> {r1['comment']!r} vs {r2['comment']!r}")
assert r1 == r2, "FAIL: comment/date pick must not depend on NCM list order"
print("PASS")

# Test 8: newest still wins when timestamps actually differ
old = {'comment': 'RTV marked - old reason', 'added_time': '2026-02-19T10:00:00+05:45', 'added_by': 'NCM Staff'}
new = {'comment': 'RTV marked - new reason', 'added_time': '2026-02-21T10:00:00+05:45', 'added_by': 'NCM Staff'}
r_a = NCMService.extract_rtv_marked_at([old, new])
r_b = NCMService.extract_rtv_marked_at([new, old])
print(f"\n[TEST 8] Distinct times -> {r_a['comment']!r} / {r_b['comment']!r} (expect 'new reason')")
assert r_a == r_b and r_a['comment'] == 'new reason', "FAIL: newest comment must win regardless of order"
print("PASS")

# Test 9: NCM Staff fallback is also order-independent
s1 = {'comment': 'aaa staff note', 'added_time': '2026-02-20T11:19:53+05:45', 'added_by': 'NCM Staff'}
s2 = {'comment': 'zzz staff note', 'added_time': '2026-02-20T11:19:53+05:45', 'added_by': 'NCM Staff'}
f1 = NCMService.extract_rtv_marked_at([s1, s2])
f2 = NCMService.extract_rtv_marked_at([s2, s1])
print(f"\n[TEST 9] Staff-fallback tie, reversed list -> {f1['comment']!r} vs {f2['comment']!r}")
assert f1 == f2, "FAIL: staff-comment fallback must not depend on list order"
print("PASS")

# Test 10: _iter_matching_orders — empty match sources yield nothing (branch
# alone must never be sufficient), and exclude_ids suppresses claimed orders.
from dashboard.views import _iter_matching_orders


class FakeItem:
    def __init__(self, name, qty):
        self.product_name = name
        self.quantity = qty


class FakeItems:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class FakeOrder:
    def __init__(self, oid, name, qty):
        self.id = oid
        self.items = FakeItems([FakeItem(name, qty)])


orders = [FakeOrder(1, 'Hair Growth Serum', 2), FakeOrder(2, 'Vitamin C', 1)]
print("\n[TEST 10] _iter_matching_orders behaviour")
assert list(_iter_matching_orders([], orders)) == [], "FAIL: no product info must yield no matches"
hit = list(_iter_matching_orders(['Hair Growth Serum x2'], orders))
assert [o.id for o, _refs in hit] == [1], f"FAIL: expected order 1 to match, got {[o.id for o, _ in hit]}"
assert hit[0][1] == ['Hair Growth Serum x2'], \
    f"FAIL: expected the RTV token aligned to the matched item, got {hit[0][1]}"
excluded = list(_iter_matching_orders(['Hair Growth Serum x2'], orders, exclude_ids={1}))
assert excluded == [], "FAIL: exclude_ids must suppress already-claimed orders"
print("PASS")

print("\n" + "=" * 80)
print("ALL TESTS PASSED")
print("=" * 80 + "\n")
