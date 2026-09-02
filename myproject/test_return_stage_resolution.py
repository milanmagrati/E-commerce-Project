"""
Verify that an order only reaches the final 'return' status once NCM says the
parcel is actually back - and stays at 'return_processing' the whole way there.

The bug: NCM sets its vendor_return flag the moment an order is marked RTV and
keeps reporting it on every hop of the journey back. That flag was read as
"the return is finished", so an order was written straight to 'return'. On the
order detail page the header badge (rendered from status_setup) flipped to
RETURN seconds after the page rendered "Return Processing" in the status
dropdown, and because 'return' is in bulk sync's terminal set, nothing ever
synced the order again to correct it.

This project has no test database, so everything runs inside a transaction that
is rolled back at the end. Run:
    python test_return_stage_resolution.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

import django
django.setup()

from django.conf import settings
from django.db import transaction

if 'testserver' not in settings.ALLOWED_HOSTS and '*' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from dashboard.models import Order, Setup
from services.ncm_service import NCMService
import ncm.bulk_sync as bulk_sync

failures = []

# Verbatim from logs/ncm_integration.log - NCM's /order/status response for a
# real RTV, newest first. vendor_return is stamped on every row, including the
# hops where the parcel was still travelling back.
RTV_TIMELINE = [
    {"status": "Delivered", "added_time": "2026-02-20T11:19:53.209447+05:45", "vendor_return": "True"},
    {"status": "Sent to Vendor", "added_time": "2026-02-19T13:15:45.660907+05:45", "vendor_return": "True"},
    {"status": "Arrived at RETURN ( TINKUNE)", "added_time": "2026-02-19T10:49:08.106535+05:45", "vendor_return": "True"},
    {"status": "Dispatched to RETURN ( TINKUNE)", "added_time": "2026-02-18T10:15:56.800213+05:45", "vendor_return": "True"},
    {"status": "Arrived at BARDAGHAT", "added_time": "2026-02-10T11:05:56.192139+05:45", "vendor_return": "True"},
    {"status": "Order Marked Return", "added_time": "2026-02-09T16:00:00.000000+05:45", "vendor_return": "True"},
]


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def eq(label, got, want):
    check(label, got == want, f"(got {got!r}, want {want!r})")


def make_order(number, **extra):
    fields = dict(
        order_number=number,
        customer_name='Return Stage Test',
        customer_phone='9800000000',
        shipping_address='Test address',
        total_amount=1300,
        order_status='in_transit',
        status='in_transit',
    )
    fields.update(extra)
    return Order.objects.create(**fields)


class StubNCM(NCMService):
    """Production resolution, stubbed network.

    `detail` is what /order/status answers; None means NCM gave nothing back.
    """

    def __init__(self, detail):
        self.api_config_id = None
        self._detail = detail
        self.calls = 0

    def get_order_status(self, ncm_order_id):
        self.calls += 1
        if self._detail is None:
            return {'success': False, 'error': 'stubbed failure'}
        return {'success': True, 'data': [self._detail]}


def main():
    print("\n1. is_return_completed - which NCM words mean 'it is back'")
    for text in ('Delivered', 'delivered', 'Returned to Warehouse',
                 'Returned to Warehouse ( TINKUNE)', 'Returned', 'Confirmed'):
        check(f"{text!r} is a completed return", NCMService.is_return_completed(text))
    for text in ('Order Marked Return', 'Sent to Vendor', 'Arrived at RETURN ( TINKUNE)',
                 'Dispatched to RETURN ( TINKUNE)', 'Arrived at BARDAGHAT', 'In Transit', ''):
        check(f"{text!r} is NOT a completed return", not NCMService.is_return_completed(text))

    print("\n1b. is_return_arrival - the hop that ends the return leg")
    for text in ('Arrived at RETURN ( TINKUNE)', 'Arrived at RETURN NAYA BUSPARK',
                 'arrived at return naya buspark'):
        check(f"{text!r} is a return-leg arrival", NCMService.is_return_arrival(text))
    for text in ('Arrived at BARDAGHAT', 'Arrived', 'Dispatched to RETURN ( TINKUNE)',
                 'Returned to Warehouse', 'Sent to Vendor', ''):
        check(f"{text!r} is NOT a return-leg arrival", not NCMService.is_return_arrival(text))

    print("\n2. resolve_delivered_status walks the real RTV timeline")
    expected = ['return', 'return_processing', 'return_arrived',
                'return_processing', 'return_processing', 'return_processing']
    for entry, want in zip(RTV_TIMELINE, expected):
        got, _ = NCMService.resolve_delivered_status(entry)
        eq(f"{entry['status']!r}", got, want)

    print("\n3. A delivery to the customer is still a delivery")
    eq("Delivered without vendor_return",
       NCMService.resolve_delivered_status({'status': 'Delivered', 'vendor_return': 'False'}),
       ('delivered', 'paid'))
    eq("Arrived without vendor_return",
       NCMService.resolve_delivered_status({'status': 'Arrived'})[0], 'in_transit')

    print("\n4. Every RTV stage maps to a status that HAS a Setup row")
    for ncm_status in ('Order Marked Return', 'Sent to Vendor', 'Return Initiated',
                       'Return Approved', 'Returned to Warehouse',
                       'Arrived at RETURN ( TINKUNE)'):
        system = NCMService.map_ncm_status_to_system(ncm_status)
        setup = NCMService._resolve_setup('status', system)
        check(f"{ncm_status!r} -> {system!r} has a Setup",
              setup is not None and setup.name.lower().replace(' ', '_') == system,
              f"(got {setup and setup.name!r})")

    print("\n5. The order detail badge and the status dropdown agree")
    # The header badge renders status_setup.name; the dropdown selects by
    # status_setup id; every list page reads the status strings. All three must
    # come out of one sync saying the same thing.
    order = make_order('ZZ-RS-BADGE')
    NCMService.sync_order_status_fields(order, 'return_processing')
    order.save()
    order.refresh_from_db()
    eq("order_status string", order.order_status, 'return_processing')
    eq("status string", order.status, 'return_processing')
    eq("status_setup FK (what the badge shows)", order.status_setup.name, 'Return Processing')
    check("badge text matches the dropdown selection",
          order.status_setup.name.lower().replace(' ', '_') == order.order_status)

    print("\n6. A status with no Setup row gets one instead of leaving a stale FK")
    Setup.objects.filter(setup_type='status', name__iexact='in transit').delete()
    stale = make_order('ZZ-RS-STALE')
    NCMService.sync_order_status_fields(stale, 'return_processing')
    stale.save()
    NCMService.sync_order_status_fields(stale, 'in_transit')
    stale.save()
    stale.refresh_from_db()
    eq("string moved on", stale.order_status, 'in_transit')
    check("FK moved with it (was stuck on 'Return Processing')",
          stale.status_setup is not None
          and stale.status_setup.name.lower().replace(' ', '_') == 'in_transit',
          f"(got {stale.status_setup and stale.status_setup.name!r})")

    print("\n7. A finished return never reopens")
    done = make_order('ZZ-RS-DONE')
    NCMService.sync_order_status_fields(done, 'return')
    done.save()
    changed = NCMService.sync_order_status_fields(done, 'return_processing')
    eq("a late in-pipeline hop changes nothing", changed, [])
    eq("status stays 'return'", done.status, 'return')

    changed = NCMService.sync_order_status_fields(done, 'return_arrived')
    eq("a replayed return-leg arrival changes nothing either", changed, [])
    eq("status still 'return'", done.status, 'return')

    scanned = make_order('ZZ-RS-SCANNED')
    NCMService.sync_order_status_fields(scanned, 'returned')
    scanned.save()
    NCMService.sync_order_status_fields(scanned, 'return_processing')
    eq("a parcel staff scanned in stays 'returned'", scanned.status, 'returned')
    NCMService.sync_order_status_fields(scanned, 'return_arrived')
    eq("...and a replayed 'Arrived at RETURN' does not reopen it either",
       scanned.status, 'returned')

    print("\n8. ...except for the repair command, which may reopen it")
    stuck = make_order('ZZ-RS-STUCK')
    NCMService.sync_order_status_fields(stuck, 'return')
    stuck.save()
    NCMService.sync_order_status_fields(stuck, 'return_processing', allow_return_reopen=True)
    stuck.save()
    stuck.refresh_from_db()
    eq("repair moves it back to the real stage", stuck.status, 'return_processing')
    eq("and the FK follows", stuck.status_setup.name, 'Return Processing')

    print("\n9. Bulk sync asks for the vendor_return flag on in-return orders")
    # The bulk endpoint answers with a bare status string. "Arrived" on an order
    # travelling back would resolve to plain 'in_transit' and strip it out of
    # the return pipeline, so the full entry has to be re-fetched.
    in_return = make_order('ZZ-RS-BULK', ncm_order_id='99000001', ncm_status='Dispatched')
    NCMService.sync_order_status_fields(in_return, 'return_processing')
    in_return.save()
    svc = StubNCM({'status': 'Arrived at RETURN ( TINKUNE)', 'vendor_return': 'True',
                   'added_time': '2026-02-19T10:49:08.106535+05:45'})
    bulk_sync._sync_one_order(svc, in_return, 'Arrived', None, fetch_event_times=False)
    in_return.refresh_from_db()
    check("the full entry was re-fetched", svc.calls == 1, f"(got {svc.calls})")
    eq("it stays in the return pipeline, now at the return counter",
       in_return.status, 'return_arrived')
    eq("badge agrees", in_return.status_setup.name, 'Return Arrived')
    eq("and the raw NCM status is recorded", in_return.ncm_status, 'Arrived at RETURN ( TINKUNE)')

    print("\n10. The last hop completes the return")
    svc = StubNCM({'status': 'Delivered', 'vendor_return': 'True',
                   'added_time': '2026-02-20T11:19:53.209447+05:45'})
    bulk_sync._sync_one_order(svc, in_return, 'Delivered', None, fetch_event_times=False)
    in_return.refresh_from_db()
    eq("delivered back to the vendor -> 'return'", in_return.status, 'return')
    eq("order_status agrees", in_return.order_status, 'return')
    eq("badge agrees", in_return.status_setup.name, 'Return')
    check("delivered_at was NOT set (it never reached the customer)",
          in_return.delivered_at is None, f"(got {in_return.delivered_at!r})")

    print("\n11. An unchanged bulk status costs no call and corrupts nothing")
    # The bulk endpoint keeps repeating the same string for a parcel that has
    # not moved. Re-fetching the full entry every run just to learn that would
    # be one wasted NCM call per RTV order per run - but resolving the bare
    # string instead reads "Arrived at BUTWAL" as plain transit and strips the
    # return. Neither is acceptable: the order must simply be left alone.
    idle = make_order('ZZ-RS-IDLE', ncm_order_id='99000004',
                      ncm_status='Arrived at BUTWAL')
    NCMService.sync_order_status_fields(idle, 'return_processing')
    idle.save()
    idle_svc = StubNCM({'status': 'Arrived at BUTWAL', 'vendor_return': 'True'})
    wrote = bulk_sync._sync_one_order(idle_svc, idle, 'Arrived at BUTWAL', None,
                                      fetch_event_times=False)
    idle.refresh_from_db()
    check("no NCM call was made", idle_svc.calls == 0, f"(got {idle_svc.calls})")
    check("nothing was written", wrote is False)
    eq("status untouched", idle.status, 'return_processing')

    print("\n12. When NCM cannot answer, the stage is kept, not guessed")
    unknown = make_order('ZZ-RS-UNKNOWN', ncm_order_id='99000002', ncm_status='Dispatched')
    NCMService.sync_order_status_fields(unknown, 'return_processing')
    unknown.save()
    wrote = bulk_sync._sync_one_order(StubNCM(None), unknown, 'Arrived', None,
                                      fetch_event_times=False)
    unknown.refresh_from_db()
    check("nothing was written", wrote is False)
    eq("status untouched", unknown.status, 'return_processing')

    # An entry that simply omits vendor_return is a guess, not a False:
    # resolve_delivered_status has to default it somewhere, and defaulting to
    # "not a return" must not be enough to end a return.
    silent = make_order('ZZ-RS-SILENT', ncm_order_id='99000006',
                        ncm_status='Dispatched')
    NCMService.sync_order_status_fields(silent, 'return_processing')
    silent.save()
    wrote = bulk_sync._sync_one_order(
        StubNCM({'status': 'Arrived at BUTWAL', 'added_time': ''}),
        silent, 'Arrived at BUTWAL', None, fetch_event_times=False)
    silent.refresh_from_db()
    check("an entry with no vendor_return key is not acted on", wrote is False)
    eq("status untouched", silent.status, 'return_processing')

    print("\n13. Webhook: 'Order Marked Return' is the start, not the end")
    from ncm.webhook_handler import NCMWebhookHandler
    handler = NCMWebhookHandler()
    hooked = make_order('ZZ-RS-HOOK', ncm_order_id='99000003', ncm_status='Arrived at BUTWAL')
    handler._update_order_from_webhook(
        hooked, 'Order Marked Return',
        {'event': 'order_marked_rtv', 'vendor_return': 'True'},
    )
    hooked.refresh_from_db()
    eq("marked RTV -> 'return_processing'", hooked.status, 'return_processing')
    eq("badge shows the same", hooked.status_setup.name, 'Return Processing')

    handler._update_order_from_webhook(
        hooked, 'Delivered', {'event': 'delivery_completed', 'vendor_return': 'True'},
    )
    hooked.refresh_from_db()
    eq("delivered back -> 'return'", hooked.status, 'return')
    check("still not counted as a customer delivery", hooked.delivered_at is None,
          f"(got {hooked.delivered_at!r})")

    print("\n14. 'Verify in NCM' resolves and writes like every other sync")
    # It used to map the raw status without the vendor_return flag (so an RTV
    # "Delivered" back to the vendor read as a customer delivery) and wrote
    # only `status` - leaving order_status and the badge's status_setup FK
    # behind on whatever the order had before.
    from django.test import Client as _Client
    from accounts.models import CustomUser as _User
    import ncm.order_recovery as recovery
    admin = _User.objects.filter(is_superuser=True).first()
    if admin is None:
        print("  SKIP  no superuser in this database")
    else:
        verified = make_order('ZZ-RS-VERIFY', ncm_order_id='99000005',
                              ncm_status='Arrived at BUTWAL')
        stub = StubNCM({'status': 'Delivered', 'vendor_return': 'True',
                        'added_time': '2026-02-20T11:19:53.209447+05:45'})
        # Both names: the view picks the module-level singleton when the order
        # has no api_config_id, and the class otherwise.
        real_service, real_singleton = recovery.NCMService, recovery.ncm_service
        recovery.NCMService = lambda **kw: stub
        recovery.ncm_service = stub
        try:
            c = _Client()
            c.force_login(admin)
            resp = c.post(f'/ncm/orders/{verified.id}/verify-ncm/')
        finally:
            recovery.NCMService = real_service
            recovery.ncm_service = real_singleton
        verified.refresh_from_db()
        check("verify redirects back to the order", resp.status_code == 302,
              f"(got {resp.status_code})")
        eq("an RTV 'Delivered' is a return, not a delivery", verified.status, 'return')
        eq("order_status was written too", verified.order_status, 'return')
        eq("and the badge FK with it", verified.status_setup.name, 'Return')
        check("delivered_at was not set", verified.delivered_at is None,
              f"(got {verified.delivered_at!r})")

    print("\n15. resolve_delivered_status understands the 'last_status' shape")
    eq("last_status + vendor_return -> completed return",
       NCMService.resolve_delivered_status(
           {'last_status': 'Delivered', 'vendor_return': 'True'})[0], 'return')
    eq("last_status mid-pipeline -> still processing",
       NCMService.resolve_delivered_status(
           {'last_status': 'Sent to Vendor', 'vendor_return': 'True'})[0], 'return_processing')

    print("\n16. The Return Orders page lists both stages, and can filter them")
    from django.test import Client
    from accounts.models import CustomUser
    user = CustomUser.objects.filter(is_superuser=True).first()
    if user is None:
        print("  SKIP  no superuser in this database")
        return
    client = Client()
    client.force_login(user)
    resp = client.get('/orders/returns/')
    check("page renders", resp.status_code == 200, f"(got {resp.status_code})")
    body = resp.content.decode('utf-8', 'replace')
    check("both stages are listed", 'ZZ-RS-BULK' in body and 'ZZ-RS-HOOK' in body)
    check("the stage column is rendered", 'Return Stage' in body)
    processing = client.get('/orders/returns/?stage=processing').content.decode('utf-8', 'replace')
    check("filtering to Return Processing keeps the in-transit ones",
          'ZZ-RS-UNKNOWN' in processing)
    check("...and hides the completed ones", 'ZZ-RS-HOOK' not in processing)


if __name__ == '__main__':
    with transaction.atomic():
        main()
        transaction.set_rollback(True)

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL CHECKS PASSED (changes rolled back)")
