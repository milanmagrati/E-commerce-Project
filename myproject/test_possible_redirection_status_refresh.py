"""
Verify that the Possible Redirection page keeps itself up to date instead of
waiting for someone to open each order's detail page.

The bug: an RTV whose package NCM had already delivered stayed listed as a
redirection candidate. Its RTVOrder.last_status was frozen at "Dispatched"
(the RTV list sync only refreshes orders NCM still reports as RTVs), and the
linked Order.ncm_status was equally stale (the background bulk sync skips
orders whose local status is terminal, and an RTV'd order resolves to
'return'). Opening the order detail page was the only thing that wrote a fresh
status — which is why the row disappeared only after the operator visited it.

What is checked here:

  1. The stale RTV is listed (the starting state of the bug).
  2. POSTing the on-screen IDs to the refresh endpoint pulls NCM's real status,
     writes it to BOTH RTVOrder.last_status and the linked local Order, and
     reports the RTV in `dropped`.
  3. A reload of the page no longer lists it — with nobody having opened the
     order detail page.
  4. A row NCM still reports as in transit is left alone: its status is
     refreshed but it is NOT dropped.

The second half covers the same symptom from the other side, which is what the
operator actually hit: the RTV row correctly read "Dispatched to RETURN (…)"
while the LINKED ORDER's ncm_status was frozen at a stale "Arrived at (BRANCH)".
Eligibility accepts either copy, so the stale one kept the row listed;
the refresh endpoint then declined to correct it (it only wrote terminal
statuses through) and, reading the same stale copy, never reported it dropped.
The page sat wrong until someone opened the order detail page. Checked here:

  5. The view flags the row `status_uncertain` and the template marks it, so
     the page overrides its cross-tab cooldown and asks NCM on load.
  6. The refresh writes NCM's answer over the stale Order.ncm_status and
     reports the row dropped, and a reload no longer lists it.

NCM is stubbed — no network calls are made. Everything is created inside a
transaction that is rolled back at the end, so nothing is left behind.

Run:  python test_possible_redirection_status_refresh.py
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
from dashboard import views as dashboard_views  # noqa: E402
from dashboard.models import Order, OrderItem, RTVOrder  # noqa: E402
from dashboard.timezone_utils import get_nepali_now  # noqa: E402
from services.ncm_service import NCMService as RealNCMService  # noqa: E402

setup_test_environment()

BRANCH = 'ZZREFRESHBRANCH'
DELIVERED_NCM_ID = 99910001   # NCM says Delivered — must leave the page
TRANSIT_NCM_ID = 99910002     # NCM says Arrived — must stay
STALE_ORDER_NCM_ID = 99910003  # RTV row right, linked order's ncm_status stale

REFRESH_URL = '/api/possible-redirection/refresh-status/'

failures = []

#: What the stubbed NCM bulk-status endpoint answers per order.
STUB_STATUSES = {
    str(DELIVERED_NCM_ID): 'Delivered',
    str(TRANSIT_NCM_ID): 'Arrived',
    str(STALE_ORDER_NCM_ID): 'Dispatched to RETURN (TINKUNE)',
}

#: vendor_return as NCM states it on the full status entry. It is set for every
#: hop of a parcel travelling back, so the return-pipeline order keeps it True;
#: the delivered fixture's package went to the customer, so False.
STUB_VENDOR_RETURN = {
    str(STALE_ORDER_NCM_ID): 'True',
}


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


class StubNCMService(RealNCMService):
    """Stands in for services.ncm_service.NCMService.

    Only the two network calls the refresh path makes are overridden — status
    resolution stays the production logic rather than a second copy of it.
    Subclassing (rather than a bare stand-in) matters: the module-level name is
    what gets patched, and resolve_delivered_status() reaches back through that
    same name for its helpers.
    """

    def __init__(self, api_config_id=None):
        # No super().__init__ — that would go looking for real API credentials.
        self.api_config_id = api_config_id

    def get_bulk_order_statuses(self, order_ids):
        return {
            'success': True,
            'data': {'result': {oid: STUB_STATUSES.get(str(oid)) for oid in order_ids}},
        }

    def get_order_status(self, ncm_order_id):
        # bulk_sync re-fetches the full entry whenever the bare status string is
        # ambiguous — a 'Delivered' (to the customer, or back to the vendor?)
        # and any move on an order already in the return pipeline. Only the
        # vendor_return flag on this entry can tell those apart.
        return {
            'success': True,
            'data': [{
                'status': STUB_STATUSES.get(str(ncm_order_id), ''),
                'vendor_return': STUB_VENDOR_RETURN.get(str(ncm_order_id), 'False'),
                'added_time': '',
            }],
        }


def make_order(number, products, **extra):
    """products: list of (name, qty)."""
    order = Order.objects.create(
        order_number=number,
        customer_name=f'Cust {number}',
        customer_phone='9800000000',
        shipping_address='Test address',
        branch_city=BRANCH,
        order_status='confirmed',
        status='confirmed',
        total_amount=1000,
        **extra,
    )
    for name, qty in products:
        OrderItem.objects.create(
            order=order, product_name=name, quantity=qty, price=100, total=100 * qty,
        )
    return order


def listed_ids(resp):
    return [e['ncm_order_id'] for e in resp.context['rtv_entries']]


def entry_for(resp, ncm_id):
    for e in resp.context['rtv_entries']:
        if e['ncm_order_id'] == ncm_id:
            return e
    return None


def main():
    user = CustomUser.objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found — cannot exercise the view.")
        return 1

    client = Client()
    client.force_login(user)

    # The stale RTV from the report: NCM has moved on, the local copy has not.
    # 'Arrived' (at the branch) is what makes it a listed candidate in the
    # first place — the page only shows packages NCM will accept a redirect
    # for, so a fixture still in transit would never render.
    delivered_rtv = RTVOrder.objects.create(
        order_id=DELIVERED_NCM_ID,
        vendor=user,
        vendor_return=True,
        to_branch=BRANCH,
        last_status='Arrived',
        product_description='1x ZZ Refresh Serum',
        rtv_marked_at=get_nepali_now(),
        rtv_marked_at_source=RTVOrder.SOURCE_WEBHOOK,
    )
    delivered_local = make_order(
        'ZZ-RF-DELIVERED', [('ZZ Refresh Serum', 1)],
        ncm_order_id=DELIVERED_NCM_ID,
    )
    delivered_local.ncm_status = 'Dispatched'
    delivered_local.status = 'return'
    delivered_local.order_status = 'return'
    delivered_local.save(update_fields=['ncm_status', 'status', 'order_status'])

    # A second RTV that is genuinely still redirectable.
    transit_rtv = RTVOrder.objects.create(
        order_id=TRANSIT_NCM_ID,
        vendor=user,
        vendor_return=True,
        to_branch=BRANCH,
        last_status='Arrived',
        product_description='1x ZZ Refresh Shampoo',
        rtv_marked_at=get_nepali_now(),
        rtv_marked_at_source=RTVOrder.SOURCE_WEBHOOK,
    )
    transit_local = make_order(
        'ZZ-RF-TRANSIT', [('ZZ Refresh Shampoo', 1)],
        ncm_order_id=TRANSIT_NCM_ID,
    )
    transit_local.ncm_status = 'Dispatched'
    transit_local.status = 'return'
    transit_local.order_status = 'return'
    transit_local.save(update_fields=['ncm_status', 'status', 'order_status'])

    # The operator's case: NCM and the RTV row both say the parcel is back in
    # transit, but the linked order's ncm_status is frozen at the arrival it had
    # before that. Eligibility accepts either copy, so the stale one alone keeps
    # the row listed.
    stale_rtv = RTVOrder.objects.create(
        order_id=STALE_ORDER_NCM_ID,
        vendor=user,
        vendor_return=True,
        to_branch=BRANCH,
        last_status='Dispatched to RETURN (TINKUNE)',
        product_description='1x ZZ Refresh Balm',
        rtv_marked_at=get_nepali_now(),
        rtv_marked_at_source=RTVOrder.SOURCE_WEBHOOK,
    )
    stale_local = make_order(
        'ZZ-RF-STALE', [('ZZ Refresh Balm', 1)],
        ncm_order_id=STALE_ORDER_NCM_ID,
    )
    # A delivery-branch arrival, which IS an eligible status — that is what makes
    # this copy able to keep the row listed on its own. ("Arrived at RETURN …"
    # would not: a parcel that finished the return leg is excluded outright.)
    stale_local.ncm_status = f'Arrived at {BRANCH}'
    stale_local.status = 'return'
    stale_local.order_status = 'return'
    stale_local.save(update_fields=['ncm_status', 'status', 'order_status'])

    # Confirmed orders wanting exactly those packages, so the RTVs pass the
    # page's match pre-filter and actually render.
    make_order('ZZ-RF-WANT-SERUM', [('ZZ Refresh Serum', 1)])
    make_order('ZZ-RF-WANT-SHAMPOO', [('ZZ Refresh Shampoo', 1)])
    make_order('ZZ-RF-WANT-BALM', [('ZZ Refresh Balm', 1)])

    print("\n[1] Starting state — the stale RTV is still listed")
    resp = client.get('/orders/possible-redirection/')
    check("page renders", resp.status_code == 200, f"(got {resp.status_code})")
    ids = listed_ids(resp)
    check("already-delivered RTV is listed (the bug's starting state)",
          DELIVERED_NCM_ID in ids, f"(got {ids})")
    check("still-redirectable RTV is listed", TRANSIT_NCM_ID in ids, f"(got {ids})")
    check("the stale-order RTV is listed too (the other starting state)",
          STALE_ORDER_NCM_ID in ids, f"(got {ids})")

    stale_entry = entry_for(resp, STALE_ORDER_NCM_ID)
    check("the contradicted row is flagged unverified",
          bool(stale_entry and stale_entry.get('status_uncertain')),
          f"(got {stale_entry.get('status_uncertain') if stale_entry else None})")
    # This row disagrees the other way round — its RTV status ('Arrived') is the
    # copy every sync refreshes, and it is what lists the row; the order's
    # 'Dispatched' is merely behind. Flagging that direction too would mark most
    # healthy rows and force an NCM call on every page load.
    check("a row listed on its own fresh RTV status is NOT flagged",
          not (entry_for(resp, TRANSIT_NCM_ID) or {}).get('status_uncertain'))
    check("the template marks it, so the page overrides its cooldown on load",
          f'data-ncm-id="{STALE_ORDER_NCM_ID}" data-status-uncertain="1"'
          in resp.content.decode('utf-8', 'replace'))

    print("\n[2] The page's own status refresh, with NCM stubbed")
    cache.delete('possible_redirection_status_refresh')
    import services.ncm_service as _ncm_module
    real_service = _ncm_module.NCMService
    _ncm_module.NCMService = StubNCMService  # the view imports it at call time
    try:
        refresh = client.post(REFRESH_URL, {
            'ncm_ids': f'{DELIVERED_NCM_ID},{TRANSIT_NCM_ID}',
        })
    finally:
        _ncm_module.NCMService = real_service

    check("refresh endpoint responds 200", refresh.status_code == 200,
          f"(got {refresh.status_code})")
    payload = json.loads(refresh.content or b'{}')
    check("refresh reports success", payload.get('success') is True, f"(got {payload})")
    check("it checked both rows", payload.get('checked') == 2, f"(got {payload.get('checked')})")
    check("the delivered RTV is reported as dropped",
          DELIVERED_NCM_ID in (payload.get('dropped') or []), f"(got {payload.get('dropped')})")
    check("the still-redirectable RTV is NOT dropped",
          TRANSIT_NCM_ID not in (payload.get('dropped') or []), f"(got {payload.get('dropped')})")

    delivered_rtv.refresh_from_db()
    transit_rtv.refresh_from_db()
    delivered_local.refresh_from_db()
    transit_local.refresh_from_db()

    check("RTVOrder.last_status was refreshed from NCM",
          delivered_rtv.last_status == 'Delivered', f"(got {delivered_rtv.last_status!r})")
    check("the linked local order's ncm_status was refreshed too",
          delivered_local.ncm_status == 'Delivered', f"(got {delivered_local.ncm_status!r})")
    check("the still-redirectable RTV's status was refreshed as well",
          transit_rtv.last_status == 'Arrived', f"(got {transit_rtv.last_status!r})")
    # The bulk status string carries no vendor_return flag, so a non-terminal
    # status must not be written through to the order — doing so would resolve
    # "Arrived" to plain 'in_transit' and strip the order out of the RTV state.
    check("a still-in-pipeline order keeps its 'return' status",
          transit_local.status == 'return', f"(got {transit_local.status!r})")

    print("\n[3] Reload — no order detail page was ever opened")
    resp2 = client.get('/orders/possible-redirection/')
    ids2 = listed_ids(resp2)
    check("the delivered RTV is gone from the page",
          DELIVERED_NCM_ID not in ids2, f"(got {ids2})")
    check("the still-redirectable RTV is untouched and still listed",
          TRANSIT_NCM_ID in ids2, f"(got {ids2})")

    print("\n[3b] The stale linked order — NCM's answer must overwrite it")
    cache.delete('possible_redirection_status_refresh')
    _ncm_module.NCMService = StubNCMService
    try:
        stale_refresh = client.post(REFRESH_URL, {'ncm_ids': str(STALE_ORDER_NCM_ID)})
    finally:
        _ncm_module.NCMService = real_service

    stale_payload = json.loads(stale_refresh.content or b'{}')
    check("the stale-order RTV is reported as dropped",
          STALE_ORDER_NCM_ID in (stale_payload.get('dropped') or []),
          f"(got {stale_payload})")
    check("the linked order was written through, not skipped for being non-terminal",
          stale_payload.get('orders_updated') == 1,
          f"(got {stale_payload.get('orders_updated')})")

    stale_local.refresh_from_db()
    stale_rtv.refresh_from_db()
    check("the stale 'Arrived' on the order was replaced by NCM's answer",
          stale_local.ncm_status == 'Dispatched to RETURN (TINKUNE)',
          f"(got {stale_local.ncm_status!r})")
    # vendor_return is set on every hop back, so the write must not take the
    # order out of the return pipeline on its way past.
    check("the order stays in the return pipeline",
          stale_local.status in ('return', 'return_processing'),
          f"(got {stale_local.status!r})")
    check("the RTV row's own status is untouched (it was already right)",
          stale_rtv.last_status == 'Dispatched to RETURN (TINKUNE)',
          f"(got {stale_rtv.last_status!r})")

    resp3 = client.get('/orders/possible-redirection/')
    ids3 = listed_ids(resp3)
    check("the row is gone on reload — no order detail page was opened",
          STALE_ORDER_NCM_ID not in ids3, f"(got {ids3})")

    print("\n[3c] The redirect modal resolves the same disagreement before "
          "enabling its button")
    # Put the row back into the contradicted state the operator would click on.
    Order.objects.filter(ncm_order_id=STALE_ORDER_NCM_ID).update(
        ncm_status=f'Arrived at {BRANCH}',
    )
    _ncm_module.NCMService = StubNCMService
    try:
        modal = client.get(f'/api/rtv/{STALE_ORDER_NCM_ID}/redirect-get/',
                           HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    finally:
        _ncm_module.NCMService = real_service

    modal_payload = json.loads(modal.content or b'{}')
    modal_rtv = modal_payload.get('rtv') or {}
    check("redirect-get responds 200", modal.status_code == 200,
          f"(got {modal.status_code})")
    check("the Redirect button is NOT enabled on the stale 'Arrived'",
          modal_rtv.get('redirect_eligible') is False,
          f"(got {modal_rtv.get('redirect_eligible')})")
    check("the status it reports is the one that disabled it",
          modal_rtv.get('last_status') == 'Dispatched to RETURN (TINKUNE)',
          f"(got {modal_rtv.get('last_status')!r})")

    # The control: this row's two statuses disagree the other way, which is not
    # suspicious — so no NCM call is made at all (nothing is stubbed here, and a
    # real request would fail) and the stored verdict stands.
    control = client.get(f'/api/rtv/{TRANSIT_NCM_ID}/redirect-get/',
                         HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    control_rtv = (json.loads(control.content or b'{}').get('rtv') or {})
    check("an unsuspicious row is answered from stored status, no NCM call",
          control_rtv.get('redirect_eligible') is True,
          f"(got {control_rtv.get('redirect_eligible')})")

    print("\n[4] Guards")
    cache.delete('possible_redirection_status_refresh')
    empty = client.post(REFRESH_URL, {'ncm_ids': ''})
    check("an empty id list is a no-op, not an error", empty.status_code == 200,
          f"(got {empty.status_code})")
    check("GET is rejected (the endpoint writes)",
          client.get(REFRESH_URL).status_code == 405,
          f"(got {client.get(REFRESH_URL).status_code})")
    check("the row cap is enforced",
          dashboard_views.POSSIBLE_REDIRECTION_REFRESH_MAX <= 100,
          f"(got {dashboard_views.POSSIBLE_REDIRECTION_REFRESH_MAX})")

    print("\n[5] The drop signal survives the linked-order write cap")
    # Writing a linked order costs extra NCM requests, so it is capped per
    # refresh. A row must still be reported as dropped when its own
    # last_status already says the package is gone — otherwise a backlog of
    # stale rows would keep the page wrong for as many refreshes as it took to
    # work through the cap.
    RTVOrder.objects.filter(order_id=DELIVERED_NCM_ID).update(last_status='Dispatched')
    Order.objects.filter(ncm_order_id=DELIVERED_NCM_ID).update(
        ncm_status='Dispatched', status='return', order_status='return',
    )
    cache.delete('possible_redirection_status_refresh')
    real_cap = dashboard_views.POSSIBLE_REDIRECTION_REFRESH_MAX_ORDER_WRITES
    dashboard_views.POSSIBLE_REDIRECTION_REFRESH_MAX_ORDER_WRITES = 0
    _ncm_module.NCMService = StubNCMService
    try:
        capped = client.post(REFRESH_URL, {'ncm_ids': str(DELIVERED_NCM_ID)})
    finally:
        _ncm_module.NCMService = real_service
        dashboard_views.POSSIBLE_REDIRECTION_REFRESH_MAX_ORDER_WRITES = real_cap

    capped_payload = json.loads(capped.content or b'{}')
    check("no linked order was written once the cap was reached",
          capped_payload.get('orders_updated') == 0,
          f"(got {capped_payload.get('orders_updated')})")
    check("the RTV is still reported as dropped on its own last_status",
          DELIVERED_NCM_ID in (capped_payload.get('dropped') or []),
          f"(got {capped_payload.get('dropped')})")

    cache.delete('possible_redirection_status_refresh')
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
        print("ALL CHECKS PASSED")
    raise SystemExit(rc)
