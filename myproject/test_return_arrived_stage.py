"""
Verify the 'Return Arrived' stage — NCM's "Arrived at RETURN (BRANCH)" hop.

The report: an RTV sat on the Possible Redirection page offering a redirect
NCM would never accept. Its status history read

    Dispatched to POKHARA -> Arrived at POKHARA
      -> Dispatched to RETURN NAYA BUSPARK -> Arrived at RETURN NAYA BUSPARK

so the parcel had already travelled all the way back to NCM's return counter.
Both stored copies said "Arrived at RETURN NAYA BUSPARK", which the redirect
gate matched on its "arrived" prefix — the same prefix a parcel *waiting at its
delivery branch* has, which is the only case a redirect is physically possible
for. Meanwhile the order read "Return Processing" ("still on its way back"),
understating where it actually was.

Checked here:

  1. NCMService.is_return_arrival tells the two "Arrived …" wordings apart.
  2. That hop resolves to the 'return_arrived' system status, with a Setup row
     behind it so the order-detail badge and dropdown agree.
  3. The Possible Redirection page does not list it — the SQL filter, not just
     the Python helper, since the two are written separately.
  4. Its matching candidate order's "Use" button is not rendered either.
  5. A redirect POSTed anyway is rejected, with a message that says the parcel
     came back rather than that it is "still in transit".
  6. A return-leg arrival on EITHER stored copy vetoes the row. This is the
     shape the bug actually had: NCM's vendor/orders endpoint (behind
     RTVOrder.last_status) answers with the coarse word "Arrived", while the
     tracking endpoint (behind Order.ncm_status) gives the branch-qualified
     wording — so OR-ing the two kept the parcel listed on the coarse copy.
  7. A row that was legitimately listed drops off by itself once NCM reports
     the return arrival on the page's own refresh — no order detail page opened.
  8. The Returns page carries the new stage and can filter to it.

Everything is created inside a transaction that is rolled back at the end.

Run:  python test_return_arrived_stage.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS and '*' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

import json  # noqa: E402

from django.core.cache import cache  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from dashboard.models import Order, OrderItem, RTVOrder  # noqa: E402
from dashboard.timezone_utils import get_nepali_now  # noqa: E402
from services.ncm_service import NCMService  # noqa: E402

setup_test_environment()

BRANCH = 'ZZRETARRBRANCH'
NCM_ID_BACK = 99940001      # "Arrived at RETURN NAYA BUSPARK" — must NOT list
NCM_ID_SPLIT = 99940002     # RTV copy coarse, order copy on the return leg
NCM_ID_LIVE = 99940003      # listed now; NCM reports the return arrival on refresh

RETURN_ARRIVAL = 'Arrived at RETURN NAYA BUSPARK'
REFRESH_URL = '/api/possible-redirection/refresh-status/'

failures = []


class StubNCMService(NCMService):
    """Stands in for the live service on the refresh path.

    Only the two network calls are overridden — status resolution stays the
    production logic rather than a second copy of it. Subclassed so
    resolve_delivered_status() still reaches the real helpers.
    """

    def __init__(self, api_config_id=None):
        self.api_config_id = api_config_id

    def get_bulk_order_statuses(self, order_ids):
        return {'success': True,
                'data': {'result': {oid: RETURN_ARRIVAL for oid in order_ids}}}

    def get_order_status(self, ncm_order_id):
        # bulk_sync re-fetches the full entry for any move on an order already
        # in the return pipeline; only vendor_return on this entry says which
        # pipeline the status belongs to.
        return {'success': True,
                'data': [{'status': RETURN_ARRIVAL, 'vendor_return': 'True', 'added_time': ''}]}


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def eq(label, got, want):
    check(label, got == want, f"(got {got!r}, want {want!r})")


def make_order(number, products, **extra):
    fields = {
        'order_number': number,
        'customer_name': f'Cust {number}',
        'customer_phone': '9800000000',
        'shipping_address': 'Test address',
        'branch_city': BRANCH,
        'order_status': 'confirmed',
        'status': 'confirmed',
        'total_amount': 1300,
    }
    fields.update(extra)
    order = Order.objects.create(**fields)
    for name, qty in products:
        OrderItem.objects.create(
            order=order, product_name=name, quantity=qty, price=100, total=100 * qty,
        )
    return order


def make_rtv(ncm_id, user, last_status):
    return RTVOrder.objects.create(
        order_id=ncm_id,
        vendor=user,
        vendor_return=True,
        to_branch=BRANCH,
        last_status=last_status,
        product_description='1x ZZ Return Shampoo',
        rtv_marked_at=get_nepali_now(),
        rtv_marked_at_source=RTVOrder.SOURCE_WEBHOOK,
        receiver_name='Original Customer',
        receiver_phone='9811111111',
    )


def listed_ids(resp):
    return {e['ncm_order_id'] for e in resp.context['rtv_entries']}


def main():
    print("\n[1] 'Arrived at RETURN …' is a different event from 'Arrived at …'")
    for text in (RETURN_ARRIVAL, 'Arrived at RETURN (TINKUNE)',
                 'arrived at return ( tinkune)', 'ARRIVED AT RETURN NAYA BUSPARK'):
        check(f"{text!r} is a return-leg arrival", NCMService.is_return_arrival(text))
    for text in ('Arrived at POKHARA', f'Arrived at {BRANCH}', 'Arrived',
                 'Dispatched to RETURN NAYA BUSPARK', 'Returned to Warehouse',
                 'Pickup Complete', '', None):
        check(f"{text!r} is NOT a return-leg arrival",
              not NCMService.is_return_arrival(text))

    print("\n[2] It resolves to the 'return_arrived' stage, badge included")
    eq("map_ncm_status_to_system",
       NCMService.map_ncm_status_to_system(RETURN_ARRIVAL), 'return_arrived')
    eq("resolve_delivered_status with NCM's vendor_return flag",
       NCMService.resolve_delivered_status(
           {'status': RETURN_ARRIVAL, 'vendor_return': 'True'})[0], 'return_arrived')
    eq("still 'return' once NCM confirms the warehouse",
       NCMService.resolve_delivered_status(
           {'status': 'Returned to Warehouse', 'vendor_return': 'True'})[0], 'return')
    eq("and 'return_processing' while it is still moving",
       NCMService.resolve_delivered_status(
           {'status': 'Dispatched to RETURN NAYA BUSPARK', 'vendor_return': 'True'})[0],
       'return_processing')
    setup = NCMService._resolve_setup('status', 'return_arrived')
    check("a Setup row backs it, so badge and dropdown agree",
          setup is not None and setup.name == 'Return Arrived',
          f"(got {setup and setup.name!r})")

    user = CustomUser.objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found — cannot exercise the views.")
        return 1

    client = Client()
    client.force_login(user)

    back_order = make_order(
        'ZZ-RA-BACK', [('ZZ Return Shampoo', 1)],
        ncm_order_id=NCM_ID_BACK, ncm_status=RETURN_ARRIVAL,
        customer_name='Original Customer', customer_phone='9811111111',
        order_status='return_arrived', status='return_arrived',
    )
    back_rtv = make_rtv(NCM_ID_BACK, user, RETURN_ARRIVAL)
    candidate = make_order('ZZ-RA-CANDIDATE', [('ZZ Return Shampoo', 1)],
                           customer_name='New Customer')

    print("\n[3] The page does not offer it (SQL filter, not just the helper)")
    resp = client.get('/orders/possible-redirection/')
    check("page renders", resp.status_code == 200, f"(got {resp.status_code})")
    check("the returned-to-counter RTV is NOT listed",
          NCM_ID_BACK not in listed_ids(resp), f"(got {listed_ids(resp)})")

    print("\n[4] ...and neither is its matching candidate's Use button")
    check("no 'Use' button for the candidate order",
          'data-order-num="ZZ-RA-CANDIDATE"' not in resp.content.decode('utf-8', 'replace'))

    # The control: move it back to a delivery-branch arrival and it returns.
    RTVOrder.objects.filter(order_id=NCM_ID_BACK).update(last_status=f'Arrived at {BRANCH}')
    Order.objects.filter(ncm_order_id=NCM_ID_BACK).update(
        ncm_status=f'Arrived at {BRANCH}',
        order_status='return_processing', status='return_processing',
    )
    resp_ok = client.get('/orders/possible-redirection/')
    check("the same RTV IS listed when NCM says it is at the delivery branch",
          NCM_ID_BACK in listed_ids(resp_ok), f"(got {listed_ids(resp_ok)})")
    RTVOrder.objects.filter(order_id=NCM_ID_BACK).update(last_status=RETURN_ARRIVAL)
    Order.objects.filter(ncm_order_id=NCM_ID_BACK).update(
        ncm_status=RETURN_ARRIVAL,
        order_status='return_arrived', status='return_arrived',
    )
    back_order.refresh_from_db()

    print("\n[5] A redirect POSTed anyway is refused, and says why truthfully")
    resp2 = client.post(
        f'/api/orders/{back_order.id}/redirect-save/',
        {
            'customer_name': candidate.customer_name,
            'customer_phone': candidate.customer_phone,
            'shipping_address': candidate.shipping_address,
            'branch_city': BRANCH,
            'send_to_logistics': 'ncm_redirect',
            'matched_order_id': str(candidate.id),
        },
        HTTP_X_REQUESTED_WITH='XMLHttpRequest',
    )
    check("the redirect POST is rejected", resp2.status_code != 200, f"(got {resp2.status_code})")
    message = (resp2.json().get('message') or '')
    check("the message says it already came back, not 'still in transit'",
          'return branch' in message.lower() and 'still in transit' not in message.lower(),
          f"(got {message!r})")

    back_order.refresh_from_db()
    eq("the order's customer was NOT overwritten", back_order.customer_name, 'Original Customer')

    resp3 = client.post(
        f'/api/rtv/{NCM_ID_BACK}/redirect-save/',
        {'customer_name': 'New Customer', 'customer_phone': '9822222222',
         'shipping_address': 'Somewhere'},
        HTTP_X_REQUESTED_WITH='XMLHttpRequest',
    )
    check("redirect_rtv_save refuses it as well", resp3.status_code != 200,
          f"(got {resp3.status_code})")

    print("\n[6] One copy is enough — this is a veto, not an OR")
    # The real shape of the reported bug: NCM's vendor/orders endpoint (which
    # feeds RTVOrder.last_status) answers with the coarse word "Arrived", while
    # the tracking endpoint behind Order.ncm_status gives "Arrived at RETURN
    # NAYA BUSPARK". OR-ing the two copies kept the parcel listed on the coarse
    # one alone. A return-leg arrival is monotonic — a parcel does not un-arrive
    # at the return counter — so whichever copy reports it wins.
    split_rtv = make_rtv(NCM_ID_SPLIT, user, 'Arrived')
    make_order(
        'ZZ-RA-SPLIT', [('ZZ Return Shampoo', 1)],
        ncm_order_id=NCM_ID_SPLIT, ncm_status=RETURN_ARRIVAL,
        order_status='return_processing', status='return_processing',
    )
    resp4 = client.get('/orders/possible-redirection/')
    check("a coarse 'Arrived' RTV row is vetoed by the order's return-leg status",
          NCM_ID_SPLIT not in listed_ids(resp4), f"(got {listed_ids(resp4)})")
    check("the row whose copies both say it came home stays hidden",
          NCM_ID_BACK not in listed_ids(resp4), f"(got {listed_ids(resp4)})")

    # The control: clear the return-leg wording and the coarse "Arrived" lists again.
    Order.objects.filter(ncm_order_id=NCM_ID_SPLIT).update(ncm_status=f'Arrived at {BRANCH}')
    resp4b = client.get('/orders/possible-redirection/')
    check("with no return-leg copy left, the same row lists normally",
          NCM_ID_SPLIT in listed_ids(resp4b), f"(got {listed_ids(resp4b)})")

    # ...and the linked order's own system status is a veto too, so a blank or
    # lagging ncm_status cannot lose the fact.
    Order.objects.filter(ncm_order_id=NCM_ID_SPLIT).update(
        ncm_status='', order_status='return_arrived', status='return_arrived')
    resp4c = client.get('/orders/possible-redirection/')
    check("a 'return_arrived' order status alone also hides the row",
          NCM_ID_SPLIT not in listed_ids(resp4c), f"(got {listed_ids(resp4c)})")

    print("\n[7] A listed row drops off when NCM reports the return arrival")
    # The page refreshes itself against NCM on load. A row that was legitimately
    # listed ("Arrived at ZZRETARRBRANCH") must disappear once NCM says the
    # parcel has reached the return counter — without anyone opening the order
    # detail page, which used to be the only other writer of Order.ncm_status.
    live_rtv = make_rtv(NCM_ID_LIVE, user, f'Arrived at {BRANCH}')
    live_order = make_order(
        'ZZ-RA-LIVE', [('ZZ Return Shampoo', 1)],
        ncm_order_id=NCM_ID_LIVE, ncm_status=f'Arrived at {BRANCH}',
        order_status='return_processing', status='return_processing',
    )
    resp_live = client.get('/orders/possible-redirection/')
    check("it starts out listed", NCM_ID_LIVE in listed_ids(resp_live),
          f"(got {listed_ids(resp_live)})")

    cache.delete('possible_redirection_status_refresh')
    import services.ncm_service as _ncm_module
    _real = _ncm_module.NCMService
    _ncm_module.NCMService = StubNCMService   # the view imports it at call time
    try:
        refreshed = client.post(REFRESH_URL, {'ncm_ids': str(NCM_ID_LIVE)})
    finally:
        _ncm_module.NCMService = _real

    payload = json.loads(refreshed.content or b'{}')
    check("the refresh endpoint responds 200", refreshed.status_code == 200,
          f"(got {refreshed.status_code})")
    check("it reports the row dropped",
          NCM_ID_LIVE in (payload.get('dropped') or []), f"(got {payload})")

    live_rtv.refresh_from_db()
    live_order.refresh_from_db()
    eq("the RTV row took NCM's answer", live_rtv.last_status, RETURN_ARRIVAL)
    eq("the linked order's raw status was written through too",
       live_order.ncm_status, RETURN_ARRIVAL)
    eq("...and its stage moved to 'return_arrived'", live_order.status, 'return_arrived')
    check("the order stayed in the return pipeline (not stripped to in_transit)",
          live_order.order_status == 'return_arrived', f"(got {live_order.order_status!r})")

    resp_live2 = client.get('/orders/possible-redirection/')
    check("and it is gone on reload, with no order detail page opened",
          NCM_ID_LIVE not in listed_ids(resp_live2), f"(got {listed_ids(resp_live2)})")

    print("\n[8] The Returns page carries the stage and can filter to it")
    resp5 = client.get('/orders/returns/')
    if resp5.status_code == 404:
        resp5 = client.get('/orders/return-orders/')
    check("returns page renders", resp5.status_code == 200, f"(got {resp5.status_code})")
    if resp5.status_code == 200:
        numbers = [o.order_number for o in resp5.context['orders'].object_list]
        check("the return_arrived order is listed there", 'ZZ-RA-BACK' in numbers,
              f"(got {numbers[:10]})")
        check("the stage badge is rendered",
              'Return Arrived' in resp5.content.decode('utf-8', 'replace'))
        check("its own stage count is non-zero",
              (resp5.context['stage_counts'] or {}).get('arrived', 0) >= 1,
              f"(got {resp5.context['stage_counts']})")

        filtered = client.get(resp5.request['PATH_INFO'] + '?stage=arrived')
        f_numbers = [o.order_number for o in filtered.context['orders'].object_list]
        check("filtering to the Arrived stage keeps it", 'ZZ-RA-BACK' in f_numbers,
              f"(got {f_numbers[:10]})")
        check("...and drops the other stages",
              all(o.order_status == 'return_arrived'
                  for o in filtered.context['orders'].object_list))

    back_rtv.delete()
    split_rtv.delete()
    live_rtv.delete()

    return 1 if failures else 0


if __name__ == '__main__':
    rc = 1
    try:
        with transaction.atomic():
            rc = main()
            raise RuntimeError('__rollback__')
    except RuntimeError as exc:
        if str(exc) != '__rollback__':
            raise

    print()
    if failures:
        print(f"FAILED ({len(failures)}): " + '; '.join(failures))
    else:
        print("ALL CHECKS PASSED (changes rolled back)")
    raise SystemExit(rc)
