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

REFRESH_URL = '/api/possible-redirection/refresh-status/'

failures = []

#: What the stubbed NCM bulk-status endpoint answers per order.
STUB_STATUSES = {
    str(DELIVERED_NCM_ID): 'Delivered',
    str(TRANSIT_NCM_ID): 'Arrived',
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
        # Reached only for 'Delivered', which bulk_sync re-fetches to tell a
        # real delivery from an RTV delivered back to the vendor. This package
        # went to the customer, so vendor_return is False.
        return {
            'success': True,
            'data': [{
                'status': STUB_STATUSES.get(str(ncm_order_id), ''),
                'vendor_return': 'False',
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

    # Confirmed orders wanting exactly those packages, so both RTVs pass the
    # page's match pre-filter and actually render.
    make_order('ZZ-RF-WANT-SERUM', [('ZZ Refresh Serum', 1)])
    make_order('ZZ-RF-WANT-SHAMPOO', [('ZZ Refresh Shampoo', 1)])

    print("\n[1] Starting state — the stale RTV is still listed")
    resp = client.get('/orders/possible-redirection/')
    check("page renders", resp.status_code == 200, f"(got {resp.status_code})")
    ids = listed_ids(resp)
    check("already-delivered RTV is listed (the bug's starting state)",
          DELIVERED_NCM_ID in ids, f"(got {ids})")
    check("still-redirectable RTV is listed", TRANSIT_NCM_ID in ids, f"(got {ids})")

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
