"""
Verify that NCM's own event times reach the DB, instead of the moment we synced.

Covers the two bugs this change targets:
  1. RTVOrder.rtv_marked_at must come from the "RTV marked" comment, never from
     NCM's order created_date, and a weaker source must not overwrite a
     stronger one.
  2. OrderActivityLog.event_at must carry NCM's timestamp so the order-detail
     timeline shows when the event happened, not when we noticed it.

This project has no test database, so everything runs inside a transaction that
is rolled back at the end. Run:
    python test_ncm_event_time_wiring.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone as dt_timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.functions import Coalesce
from django.test import RequestFactory
from django.utils import timezone

from dashboard.models import Order, OrderActivityLog, ReturnActivityLog, RTVOrder
from dashboard.timezone_utils import format_nepali_datetime, parse_ncm_datetime
from services.ncm_service import NCMService

failures = []

# Verbatim from logs/ncm_integration.log — NCM's /order/status response shape,
# newest-first, with vendor_return stamped on every historical row.
STATUS_TIMELINE = [
    {"orderid": 18514300, "status": "Delivered", "added_time": "2026-02-20T11:19:53.209447+05:45", "vendor_return": "True"},
    {"orderid": 18514300, "status": "Sent to Vendor", "added_time": "2026-02-19T13:15:45.660907+05:45", "vendor_return": "True"},
    {"orderid": 18514300, "status": "Arrived at RETURN ( TINKUNE)", "added_time": "2026-02-19T10:49:08.106535+05:45", "vendor_return": "True"},
    {"orderid": 18514300, "status": "Dispatched to RETURN ( TINKUNE)", "added_time": "2026-02-18T10:15:56.800213+05:45", "vendor_return": "True"},
    {"orderid": 18514300, "status": "Arrived at BARDAGHAT", "added_time": "2026-02-10T11:05:56.192139+05:45", "vendor_return": "True"},
    {"orderid": 18514300, "status": "Dispatched to BARDAGHAT", "added_time": "2026-02-09T21:08:06.115610+05:45", "vendor_return": "True"},
    {"orderid": 18514300, "status": "Pickup Complete", "added_time": "2026-02-09T20:18:19.295175+05:45", "vendor_return": "True"},
    {"orderid": 18514300, "status": "Sent for Pickup", "added_time": "2026-02-09T20:09:49.143890+05:45", "vendor_return": "True"},
    {"orderid": 18514300, "status": "Pickup Order Created", "added_time": "2026-02-09T14:26:20.031210+05:45", "vendor_return": "True"},
]

TEST_NCM_ORDER_ID = 99999999


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ''))
        failures.append(label)


def eq(label, actual, expected):
    check(label, actual == expected, f"got {actual!r}, expected {expected!r}")


def near_now(dt, seconds=120):
    return dt is not None and abs((timezone.now() - dt).total_seconds()) < seconds


def run():
    User = get_user_model()
    user = User.objects.filter(is_superuser=True).first() or User.objects.first()
    if user is None:
        print("No users in DB — cannot build fixtures.")
        sys.exit(1)

    # ---------------------------------------------------------------------
    print("\n1. extract_rtv_marked_at picks the newest RTV comment, not the first")
    comments = [
        {'comment': 'RTV marked - PNR/PUR/PSO', 'added_by': 'NCM Staff',
         'added_time': '2026-07-20T13:52:00.100000+05:45'},
        {'comment': 'Customer called', 'added_by': 'NCM Staff',
         'added_time': '2026-07-25T09:00:00.000000+05:45'},
        {'comment': 'RTV marked - Customer did not ordered', 'added_by': 'NCM Staff',
         'added_time': '2026-07-30T11:15:28.404081+05:45'},
    ]
    expected = parse_ncm_datetime('2026-07-30T11:15:28.404081+05:45')
    found = NCMService.extract_rtv_marked_at(comments)
    eq("newest-last: marked_at", found['marked_at'], expected)
    eq("newest-last: source", found['source'], 'comment')
    eq("newest-last: comment", found['comment'], 'Customer did not ordered')
    check("newest-last: vendor_return", found['vendor_return'] is True)
    reversed_found = NCMService.extract_rtv_marked_at(list(reversed(comments)))
    eq("reversed list gives same marked_at", reversed_found['marked_at'], expected)
    eq("formatted date is Jul 30", format_nepali_datetime(found['marked_at']),
       'Jul 30, 2026 11:15 AM')

    # ---------------------------------------------------------------------
    print("\n2. RTV removed / unparseable / staff fallback")
    removed = NCMService.extract_rtv_marked_at([
        {'comment': 'RTV marked - x', 'added_by': 'NCM Staff', 'added_time': '2026-07-20T10:00:00+05:45'},
        {'comment': 'RTV removed', 'added_by': 'NCM Staff', 'added_time': '2026-07-28T10:00:00+05:45'},
    ])
    check("newer 'RTV removed' wins", removed['vendor_return'] is False)

    mixed = NCMService.extract_rtv_marked_at([
        {'comment': 'RTV marked - a', 'added_by': 'NCM Staff', 'added_time': 'garbage'},
        {'comment': 'RTV marked - b', 'added_by': 'NCM Staff', 'added_time': '2026-07-22T08:00:00+05:45'},
    ])
    eq("parseable entry beats unparseable", mixed['marked_at'],
       parse_ncm_datetime('2026-07-22T08:00:00+05:45'))

    staff_only = NCMService.extract_rtv_marked_at([
        {'comment': 'Package received', 'added_by': 'NCM Staff', 'added_time': '2026-07-21T08:00:00+05:45'},
        {'comment': 'Older note', 'added_by': 'NCM Staff', 'added_time': '2026-07-19T08:00:00+05:45'},
    ])
    eq("staff fallback source", staff_only['source'], 'ncm_staff_comment')
    eq("staff fallback takes newest", staff_only['marked_at'],
       parse_ncm_datetime('2026-07-21T08:00:00+05:45'))
    check("staff fallback leaves vendor_return unknown", staff_only['vendor_return'] is None)

    eq("empty list", NCMService.extract_rtv_marked_at([]),
       {'marked_at': None, 'comment': '', 'vendor_return': None, 'source': ''})

    # ---------------------------------------------------------------------
    print("\n3. extract_return_step_time (approximate, earliest return step)")
    dt, source = NCMService.extract_return_step_time(STATUS_TIMELINE)
    eq("earliest RETURN-matching entry", dt,
       parse_ncm_datetime('2026-02-18T10:15:56.800213+05:45'))
    eq("source label", source, 'status_timeline')
    check("no return steps -> None",
          NCMService.extract_return_step_time([
              {'status': 'Pickup Complete', 'added_time': '2026-02-09T20:18:19+05:45'}
          ])[0] is None)

    # ---------------------------------------------------------------------
    print("\n4. apply_rtv_marked_at precedence")
    rtv = RTVOrder.objects.create(
        order_id=TEST_NCM_ORDER_ID, vendor=user, vendor_return=True,
    )
    dt_comment = parse_ncm_datetime('2026-07-30T11:15:28.404081+05:45')
    dt_timeline = parse_ncm_datetime('2026-02-18T10:15:56.800213+05:45')

    NCMService.apply_rtv_marked_at(rtv, dt_comment, RTVOrder.SOURCE_COMMENT)
    rtv.refresh_from_db()
    eq("comment written", rtv.rtv_marked_at, dt_comment)
    check("checked_at stamped", near_now(rtv.rtv_marked_at_checked_at))

    before_checked = rtv.rtv_marked_at_checked_at
    wrote = NCMService.apply_rtv_marked_at(rtv, dt_timeline, RTVOrder.SOURCE_STATUS_TIMELINE)
    rtv.refresh_from_db()
    check("weaker source refused", wrote is False)
    eq("value preserved", rtv.rtv_marked_at, dt_comment)
    eq("source preserved", rtv.rtv_marked_at_source, RTVOrder.SOURCE_COMMENT)
    check("checked_at still advanced", rtv.rtv_marked_at_checked_at >= before_checked)

    # order_created (the legacy wrong value) must be overridable by anything
    RTVOrder.objects.filter(pk=rtv.pk).update(
        rtv_marked_at=parse_ncm_datetime('2026-07-20T13:52:00+05:45'),
        rtv_marked_at_source=RTVOrder.SOURCE_ORDER_CREATED,
    )
    rtv.refresh_from_db()
    check("timeline overrides order_created",
          NCMService.apply_rtv_marked_at(rtv, dt_timeline, RTVOrder.SOURCE_STATUS_TIMELINE))
    rtv.refresh_from_db()
    check("comment overrides timeline",
          NCMService.apply_rtv_marked_at(rtv, dt_comment, RTVOrder.SOURCE_COMMENT))
    rtv.refresh_from_db()
    eq("final value is the comment date", rtv.rtv_marked_at, dt_comment)
    check("final value is trusted", rtv.rtv_marked_at_is_trusted)

    # Equal rank overwrites: a re-mark is newer information.
    dt_remark = parse_ncm_datetime('2026-07-31T09:00:00+05:45')
    check("equal rank (re-mark) overwrites",
          NCMService.apply_rtv_marked_at(rtv, dt_remark, RTVOrder.SOURCE_COMMENT))
    rtv.refresh_from_db()
    eq("re-mark stored", rtv.rtv_marked_at, dt_remark)

    check("None date is not written",
          NCMService.apply_rtv_marked_at(rtv, None, RTVOrder.SOURCE_COMMENT) is False)
    rtv.refresh_from_db()
    eq("value untouched by None", rtv.rtv_marked_at, dt_remark)

    # ---------------------------------------------------------------------
    print("\n5. Repair candidacy")
    from dashboard.views import rtv_needs_date_verification

    trusted_ids = set(rtv_needs_date_verification(
        RTVOrder.objects.filter(pk=rtv.pk)).values_list('pk', flat=True))
    check("trusted row excluded", rtv.pk not in trusted_ids)

    RTVOrder.objects.filter(pk=rtv.pk).update(
        rtv_marked_at_source=RTVOrder.SOURCE_ORDER_CREATED, rtv_marked_at_checked_at=None)
    untrusted_ids = set(rtv_needs_date_verification(
        RTVOrder.objects.filter(pk=rtv.pk)).values_list('pk', flat=True))
    check("order_created row included", rtv.pk in untrusted_ids)

    RTVOrder.objects.filter(pk=rtv.pk).update(rtv_marked_at=None, rtv_marked_at_source='')
    null_ids = set(rtv_needs_date_verification(
        RTVOrder.objects.filter(pk=rtv.pk)).values_list('pk', flat=True))
    check("NULL-date row included", rtv.pk in null_ids)

    ordering = list(rtv_needs_date_verification(RTVOrder.objects.all())
                    .values_list('rtv_marked_at_checked_at', flat=True)[:5])
    check("never-checked rows come first",
          not ordering or ordering[0] is None or all(v is not None for v in ordering),
          f"got {ordering}")

    # ---------------------------------------------------------------------
    print("\n6. Webhook: NCM timestamp lands on the log and on delivered_at")
    from ncm.webhook_handler import NCMWebhookHandler

    order = Order.objects.create(
        order_number='TSTEV-1',
        customer_name='Timestamp Test',
        customer_phone='9800000000',
        ncm_order_id=str(TEST_NCM_ORDER_ID),
        status='in_transit',
        ncm_status='Arrived',
        is_deleted=False,
        created_by=user,
    )
    handler = NCMWebhookHandler()

    def latest_log():
        return OrderActivityLog.objects.filter(order=order).order_by('-id').first()

    # (a) offset-bearing ISO
    result = handler._update_order_from_webhook(
        order, 'Delivered',
        {'event': 'delivery_completed', 'timestamp': '2026-07-20T11:19:53.209447+05:45'},
        event_at=parse_ncm_datetime('2026-07-20T11:19:53.209447+05:45'),
    )
    check("(a) webhook succeeded", result.get('success'), result.get('error'))
    order.refresh_from_db()
    log = latest_log()
    expected_a = parse_ncm_datetime('2026-07-20T11:19:53.209447+05:45')
    eq("(a) event_at is NCM's time", log.event_at, expected_a)
    eq("(a) delivered_at is NCM's time", order.delivered_at, expected_a)
    check("(a) delivered_at is NOT now", not near_now(order.delivered_at))
    check("(a) created_at is now", near_now(log.created_at))
    eq("(a) effective_at prefers event_at", log.effective_at, log.event_at)
    eq("(a) displayed time", format_nepali_datetime(log.effective_at), 'Jul 20, 2026 11:19 AM')

    # (b) naive string — Nepal wall clock
    order.delivered_at = None
    order.status = 'in_transit'
    order.save(update_fields=['delivered_at', 'status'])
    handler._update_order_from_webhook(
        order, 'Delivered', {'event': 'delivery_completed', 'timestamp': '2026-07-20 11:19:53'},
        event_at=parse_ncm_datetime('2026-07-20 11:19:53'),
    )
    eq("(b) naive treated as Nepal time",
       format_nepali_datetime(latest_log().event_at), 'Jul 20, 2026 11:19 AM')

    # (c) 'Z' form — same instant, must not be double-localized
    handler._update_order_from_webhook(
        order, 'Arrived', {'event': 'order_arrived', 'timestamp': '2026-07-20T05:34:53Z'},
        event_at=parse_ncm_datetime('2026-07-20T05:34:53Z'),
    )
    eq("(c) Z form -> same wall clock",
       format_nepali_datetime(latest_log().event_at), 'Jul 20, 2026 11:19 AM')

    # (d) missing / garbage timestamp
    order.delivered_at = None
    order.status = 'in_transit'
    order.save(update_fields=['delivered_at', 'status'])
    handler._update_order_from_webhook(
        order, 'Delivered', {'event': 'delivery_completed', 'timestamp': ''},
        event_at=parse_ncm_datetime(''),
    )
    order.refresh_from_db()
    log = latest_log()
    check("(d) event_at is NULL", log.event_at is None)
    check("(d) delivered_at falls back to now", near_now(order.delivered_at))
    eq("(d) effective_at falls back to created_at", log.effective_at, log.created_at)

    handler._update_order_from_webhook(
        order, 'Arrived', {'event': 'order_arrived', 'timestamp': 'not-a-date'},
        event_at=parse_ncm_datetime('not-a-date'),
    )
    check("(d) garbage timestamp doesn't raise or store", latest_log().event_at is None)

    # ---------------------------------------------------------------------
    print("\n7. order_marked_rtv webhook records the RTV date")
    RTVOrder.objects.filter(order_id=TEST_NCM_ORDER_ID).delete()
    rtv_event = parse_ncm_datetime('2026-07-30T11:15:28.404081+05:45')
    order.status = 'in_transit'
    order.save(update_fields=['status'])
    handler._update_order_from_webhook(
        order, 'Order Marked Return',
        {'event': 'order_marked_rtv', 'timestamp': '2026-07-30T11:15:28.404081+05:45'},
        event_at=rtv_event,
    )
    created_rtv = RTVOrder.objects.filter(order_id=TEST_NCM_ORDER_ID).first()
    check("RTV row created by webhook", created_rtv is not None)
    if created_rtv:
        eq("RTV date is NCM's mark time", created_rtv.rtv_marked_at, rtv_event)
        eq("RTV source is webhook", created_rtv.rtv_marked_at_source, RTVOrder.SOURCE_WEBHOOK)
        check("RTV date is trusted", created_rtv.rtv_marked_at_is_trusted)
        eq("RTV date displays as Jul 30",
           format_nepali_datetime(created_rtv.rtv_marked_at), 'Jul 30, 2026 11:15 AM')

    # ---------------------------------------------------------------------
    print("\n7b. A failing RTV bookkeeping write can't poison the transaction")
    # _record_rtv_marked runs inside the caller's atomic block, so it must use
    # a savepoint — otherwise swallowing a DB error leaves the transaction
    # unusable and the whole webhook fails on the next query.
    bad_order = Order(order_number='TSTEV-BAD', customer_name='x',
                      ncm_order_id='not-an-int', status='in_transit')
    handler._record_rtv_marked(bad_order, rtv_event)  # must not raise
    check("subsequent query still works after a swallowed failure",
          Order.objects.filter(order_number='TSTEV-1').exists())

    # ---------------------------------------------------------------------
    print("\n8. Interactive sync path uses the timeline's added_time")
    from ncm import realtime_api

    original = NCMService.get_order_status
    NCMService.get_order_status = lambda self, oid: {'success': True, 'data': STATUS_TIMELINE}
    try:
        order.status = 'in_transit'
        order.ncm_status = 'Pickup Complete'
        order.delivered_at = None
        order.save(update_fields=['status', 'ncm_status', 'delivered_at'])

        request = RequestFactory().post(f'/ncm/api/order/{order.id}/sync-status/')
        request.user = user
        response = realtime_api.api_sync_order_status(request, order.id)
        check("sync responded 200", response.status_code == 200, str(response.status_code))
        log = latest_log()
        eq("event_at from newest timeline entry", log.event_at,
           parse_ncm_datetime('2026-02-20T11:19:53.209447+05:45'))
        check("created_at still 'now'", near_now(log.created_at))
    finally:
        NCMService.get_order_status = original

    # ---------------------------------------------------------------------
    print("\n9. Timeline ordering uses Coalesce(event_at, created_at)")
    # The point: a row recorded seconds ago but reporting a WEEK-OLD NCM event
    # must sink below a local row, while a recent NCM event stays on top. Dates
    # are relative to now so this doesn't rot as the calendar moves.
    OrderActivityLog.objects.filter(order=order).delete()
    now = timezone.now()
    a = OrderActivityLog.objects.create(order=order, action_type='status_changed',
                                        description='A (NCM event 10d ago)',
                                        event_at=now - timedelta(days=10))
    b = OrderActivityLog.objects.create(order=order, action_type='updated',
                                        description='B (local, 1m ago)')
    c = OrderActivityLog.objects.create(order=order, action_type='status_changed',
                                        description='C (NCM event 1h ago)',
                                        event_at=now - timedelta(hours=1))
    # A and C were both *recorded* just now, in that order — so ordering by
    # created_at alone would put C first and A second, and A would outrank B.
    OrderActivityLog.objects.filter(pk=b.pk).update(created_at=now - timedelta(minutes=1))

    ordered = list(
        order.activity_logs.annotate(_at=Coalesce('event_at', 'created_at'))
        .order_by('-_at').values_list('description', flat=True)
    )
    eq("ordered by real event time", ordered,
       ['B (local, 1m ago)', 'C (NCM event 1h ago)', 'A (NCM event 10d ago)'])

    created_order = list(
        order.activity_logs.order_by('-created_at').values_list('description', flat=True)
    )
    check("differs from created_at ordering (the bug)", created_order != ordered,
          f"created_at order was also {created_order}")

    # ---------------------------------------------------------------------
    print("\n10. Untouched call sites and models are unaffected")
    plain = OrderActivityLog.objects.create(
        order=order, action_type='updated', user=user, description='local edit')
    check("event_at defaults to NULL", plain.event_at is None)
    eq("effective_at falls back to created_at", plain.effective_at, plain.created_at)
    check("ReturnActivityLog deliberately has no event_at",
          not any(f.name == 'event_at' for f in ReturnActivityLog._meta.fields))


try:
    with transaction.atomic():
        try:
            run()
        finally:
            transaction.set_rollback(True)
except Exception:
    import traceback
    traceback.print_exc()
    failures.append('unhandled exception')

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("All NCM event-time wiring checks passed (changes rolled back).")
