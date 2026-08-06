"""Verify the RTV comment-sync reports *why* a row changed, not just that it did.

The RTV page used to toast "N date(s) updated" for every local write the
repair queue made — including comment-text refreshes and first-time backfills
of dates NCM had held unchanged all along. That made it look like NCM's data
had moved when it hadn't. sync_rtv_from_ncm_comments now separates:

    fields_changed  — comment / vendor_return refreshed (no date involved)
    date_backfilled — missing/untrusted date resolved (us catching up to NCM)
    date_changed    — an already-trusted date moved (a real NCM-side change)

Run: python test_rtv_date_toast_labels.py
"""
import os
from datetime import timedelta

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.contrib.auth import get_user_model             # noqa: E402

from dashboard.models import RTVOrder                      # noqa: E402
from dashboard.timezone_utils import get_nepali_now        # noqa: E402
from dashboard.views import (                              # noqa: E402
    select_rtv_comment_sync_batch,
    sync_rtv_from_ncm_comments,
)

ORDER_ID = -99987654  # negative: cannot collide with a real NCM order id


class FakeNCMService:
    """Stands in for NCMService.get_order_comments with a canned reply."""

    def __init__(self, comments, success=True):
        self._comments = comments
        self._success = success

    def get_order_comments(self, order_id):
        return {'success': self._success, 'data': self._comments}


def rtv_marked_comment(when, text='RTV marked'):
    # Key names must match NCMService.extract_rtv_marked_at: comment / added_time / added_by
    return [{'comment': text, 'added_time': when, 'added_by': 'NCM Staff'}]


def check(label, got, want):
    status = 'PASS' if got == want else 'FAIL'
    print(f'  [{status}] {label}: got {got!r}, want {want!r}')
    return got == want


def main():
    RTVOrder.objects.filter(order_id=ORDER_ID).delete()
    now = get_nepali_now()
    ok = True

    vendor = get_user_model().objects.order_by('id').first()
    if vendor is None:
        print('No user in DB to own the test RTV — create one first.')
        raise SystemExit(1)

    try:
        # ── Case 1: untrusted legacy date is repaired → backfill, not a change
        rtv = RTVOrder.objects.create(
            order_id=ORDER_ID,
            comment='',
            vendor=vendor,
            rtv_marked_at=now.replace(hour=1, minute=0),
            rtv_marked_at_source=RTVOrder.SOURCE_ORDER_CREATED,  # known-wrong legacy
        )
        real_time = now.replace(hour=12, minute=26, second=0, microsecond=0)
        svc = FakeNCMService(rtv_marked_comment(real_time.strftime('%Y-%m-%d %H:%M:%S')))
        res = sync_rtv_from_ncm_comments(svc, ORDER_ID)
        print('Case 1 — legacy/untrusted date repaired against unchanged NCM data:')
        ok &= check('resolved', res.resolved, True)
        ok &= check('date_backfilled', res.date_backfilled, True)
        ok &= check('date_changed (must NOT claim NCM moved)', res.date_changed, False)
        rtv.refresh_from_db()
        ok &= check('source now trusted', rtv.rtv_marked_at_source, RTVOrder.SOURCE_COMMENT)

        # ── Case 2: same data re-polled → no write at all, no toast
        res = sync_rtv_from_ncm_comments(svc, ORDER_ID)
        print('Case 2 — identical NCM data re-polled (no churn):')
        ok &= check('date_changed', res.date_changed, False)
        ok &= check('date_backfilled', res.date_backfilled, False)
        ok &= check('changed', res.changed, False)

        # ── Case 3: trusted date genuinely moves (unmark -> re-mark on NCM)
        later = now.replace(hour=15, minute=45, second=0, microsecond=0)
        svc2 = FakeNCMService(rtv_marked_comment(later.strftime('%Y-%m-%d %H:%M:%S')))
        res = sync_rtv_from_ncm_comments(svc2, ORDER_ID)
        print('Case 3 — already-trusted date moved on NCM:')
        ok &= check('date_changed', res.date_changed, True)
        ok &= check('date_backfilled', res.date_backfilled, False)

        # ── Case 4: only the comment text changes → never a "date" update
        svc3 = FakeNCMService(rtv_marked_comment(
            later.strftime('%Y-%m-%d %H:%M:%S'), text='RTV marked - Late Delivery'
        ))
        res = sync_rtv_from_ncm_comments(svc3, ORDER_ID)
        print('Case 4 — comment text refreshed, date untouched:')
        ok &= check('fields_changed', res.fields_changed, True)
        ok &= check('date_changed (must NOT be labelled a date update)', res.date_changed, False)

        # ── Case 5: NCM unreachable → nothing counted, row stays in repair queue
        res = sync_rtv_from_ncm_comments(FakeNCMService([], success=False), ORDER_ID)
        print('Case 5 — NCM unreachable:')
        ok &= check('resolved', res.resolved, False)
        ok &= check('changed', res.changed, False)

    finally:
        RTVOrder.objects.filter(order_id=ORDER_ID).delete()

    ok &= batch_rotation_checks(vendor, now)

    print('\n' + ('ALL PASSED' if ok else 'FAILURES ABOVE'))
    raise SystemExit(0 if ok else 1)


def batch_rotation_checks(vendor, now):
    """The empty-comment tier must rotate instead of re-picking the same rows.

    Regression guard: that tier used to fall back to the model's default
    ordering, so the same three comment-less rows were re-fetched on every
    sync forever while the rest were never checked.
    """
    from dashboard.models import LogisticsAPIConfig

    print('\nBatch selection — empty-comment tier rotates by staleness:')
    # Its own inactive config, so the batch query sees only these rows and the
    # real 189-row backlog can't crowd them out.
    cfg = LogisticsAPIConfig.objects.create(
        api_name='__rtv_batch_test__',
        logistics_provider='ncm',
        api_key='test',
        is_active=False,
    )

    base_id = -99980000
    ids = [base_id - n for n in range(5)]
    RTVOrder.objects.filter(order_id__in=ids).delete()
    ok = True
    try:
        # All comment-less and already trusted, so only the empty-comment tier
        # can claim them. Staggered checked_at: oldest must be picked first.
        for n, oid in enumerate(ids):
            RTVOrder.objects.create(
                order_id=oid,
                comment='',
                vendor=vendor,
                api_config=cfg,
                vendor_return=True,
                rtv_marked_at=now,
                rtv_marked_at_source=RTVOrder.SOURCE_COMMENT,
                rtv_marked_at_checked_at=now - timedelta(hours=n),
            )

        picked = [
            oid for oid, _cfg in select_rtv_comment_sync_batch([cfg.id], limit=50)
            if oid in ids
        ]
        # ids[4] has the oldest checked_at (now - 4h) → must come first.
        want_first = ids[4]
        ok &= check('stalest row picked first', picked[0] if picked else None, want_first)

        # Simulate that row being synced: its checked_at jumps to now, so the
        # next batch must move on to a different row instead of re-picking it.
        RTVOrder.objects.filter(order_id=want_first).update(rtv_marked_at_checked_at=now)
        picked2 = [
            oid for oid, _cfg in select_rtv_comment_sync_batch([cfg.id], limit=50)
            if oid in ids
        ]
        ok &= check('batch advances after sync', picked2[0] if picked2 else None, ids[3])
        ok &= check('no duplicate order_ids in batch', len(picked2), len(set(picked2)))
    finally:
        RTVOrder.objects.filter(order_id__in=ids).delete()
        cfg.delete()

    return ok


if __name__ == '__main__':
    main()
