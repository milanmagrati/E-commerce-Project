"""
Verify that a status set by hand survives the NCM syncs that run around it.

The bug: an order NCM still reported as "Pickup Order Created" could be set to
"Confirmed" from the order edit page (or the detail page's status form), and the
new status showed for about a second before snapping back to "Pickup Created".
Two things caused it:

  1. The manual paths wrote only `order_status` (and the status_setup FK),
     leaving the older duplicate `order.status` at NCM's value - which is the
     field every sync path compares against, so the order still looked
     untouched to them.
  2. The order detail page runs a silent NCM sync on every load. Right after
     the edit page redirects there, that sync re-derived the status from NCM's
     answer and overwrote the manual choice.

The fix (services/status_override.py) records which raw NCM status was in force
when staff made the change, and every sync path leaves the order's status alone
for as long as NCM keeps reporting that same status. The moment the parcel
really moves, NCM wins again and the hold is dropped.

What is checked here:

  1. Editing an order to "Confirmed" writes status, order_status and the FK,
     and stamps the hold.
  2. The page-load sync, with NCM still saying "Pickup Order Created", reports
     no change and leaves the order Confirmed - the reported bug.
  3. When NCM moves on ("Sent for Delivery"), the sync applies it and retires
     the hold, so an override can never strand an order.
  4. The orders-list bulk action ("Mark as Confirmed") behaves identically,
     including against the background bulk sync.
  5. A webhook repeating the held status is skipped; a newer one is applied.
  6. Orders nobody touched by hand still sync exactly as before.

NCM is stubbed - no network calls are made. Everything runs inside a
transaction that is rolled back at the end, so nothing is left behind.

Run:  python test_manual_status_override.py
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
from dashboard.models import Order, OrderItem, Product, Setup  # noqa: E402
from services.ncm_service import NCMService as RealNCMService  # noqa: E402

setup_test_environment()

NCM_ID_EDIT = 99920001       # driven through the order edit page
NCM_ID_BULK = 99920002       # driven through the orders-list bulk action
NCM_ID_UNTOUCHED = 99920003  # never touched by hand - must sync as before

HELD_NCM_STATUS = 'Pickup Order Created'   # what NCM keeps reporting
MOVED_NCM_STATUS = 'Sent for Delivery'     # the parcel finally moving

#: What the stubbed NCM answers per order, mutated during the run.
STUB_STATUSES = {
    NCM_ID_EDIT: HELD_NCM_STATUS,
    NCM_ID_BULK: HELD_NCM_STATUS,
    NCM_ID_UNTOUCHED: HELD_NCM_STATUS,
}

failures = []


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


class StubNCMService(RealNCMService):
    """Stands in for NCMService - only the network calls are overridden.

    Status resolution stays the production logic rather than a second copy of
    it, so what this exercises is the real mapping.
    """

    def __init__(self, api_config_id=None):
        # No super().__init__ - that would go looking for real API credentials.
        self.api_config_id = api_config_id

    def get_order_status(self, ncm_order_id):
        return {
            'success': True,
            'data': [{
                'status': STUB_STATUSES.get(int(ncm_order_id), ''),
                'vendor_return': 'False',
                # Dated well before the run, so it is never mistaken for an
                # event that happened after a manual change.
                'added_time': '2020-01-01T10:00:00+05:45',
            }],
        }

    def get_bulk_order_statuses(self, order_ids):
        return {
            'success': True,
            'data': {'result': {oid: STUB_STATUSES.get(int(oid)) for oid in order_ids}},
        }


def make_order(number, ncm_order_id, product, pickup_setup):
    order = Order.objects.create(
        order_number=number,
        customer_name=f'Cust {number}',
        customer_phone='9800000000',
        shipping_address='Test address',
        branch_city='ZZOVERRIDE',
        order_from='WhatsApp',
        payment_method='cod',
        # The starting state from the report: NCM created the pickup.
        status='Pickup Created',
        order_status='Pickup Created',
        status_setup=pickup_setup,
        ncm_order_id=ncm_order_id,
        ncm_status=HELD_NCM_STATUS,
        logistics='ncm',
        total_amount=1000,
    )
    OrderItem.objects.create(
        order=order, product=product, product_name=product.name,
        quantity=1, price=1000, total=1000,
    )
    return order


def status_snapshot(order):
    order.refresh_from_db()
    return (order.status, order.order_status,
            order.status_setup.name if order.status_setup else None)


def main():
    user = CustomUser.objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found - cannot exercise the views.")
        return 1

    client = Client()
    client.force_login(user)

    confirmed_setup, _ = Setup.objects.get_or_create(
        setup_type='status', name='Confirmed', defaults={'is_active': True})
    pickup_setup, _ = Setup.objects.get_or_create(
        setup_type='status', name='Pickup Created', defaults={'is_active': True})

    product = Product.objects.create(
        user=user, name='ZZ Override Shampoo', slug='zz-override-shampoo',
        description='fixture', price=1000,
    )

    edit_order = make_order('ZZ-MO-EDIT', NCM_ID_EDIT, product, pickup_setup)
    bulk_order = make_order('ZZ-MO-BULK', NCM_ID_BULK, product, pickup_setup)
    untouched = make_order('ZZ-MO-PLAIN', NCM_ID_UNTOUCHED, product, pickup_setup)

    import ncm.realtime_api as realtime_api
    import ncm.bulk_sync as bulk_sync_module
    import services.ncm_service as ncm_module

    def with_stub(fn):
        """Run fn with every NCM entry point stubbed."""
        real_cls = ncm_module.NCMService
        real_rt_cls, real_rt_inst = realtime_api.NCMService, realtime_api.ncm_service
        real_bs_cls, real_bs_inst = bulk_sync_module.NCMService, bulk_sync_module.ncm_service
        stub = StubNCMService()
        ncm_module.NCMService = StubNCMService
        realtime_api.NCMService, realtime_api.ncm_service = StubNCMService, stub
        bulk_sync_module.NCMService, bulk_sync_module.ncm_service = StubNCMService, stub
        try:
            return fn()
        finally:
            ncm_module.NCMService = real_cls
            realtime_api.NCMService, realtime_api.ncm_service = real_rt_cls, real_rt_inst
            bulk_sync_module.NCMService, bulk_sync_module.ncm_service = real_bs_cls, real_bs_inst

    def page_load_sync(order):
        """What the order detail page fires on every load."""
        cache.delete(f'ncm_sync_throttle_{order.id}')
        resp = with_stub(lambda: client.post(f'/ncm/api/order/{order.id}/sync/'))
        return json.loads(resp.content or b'{}')

    cart = json.dumps([{'id': product.id, 'qty': 1, 'price': '1000'}])

    def edit_payload(name, address, status_setup_id):
        return {
            'customer_name': name,
            'customer_phone': '9800000000',
            'shipping_address': address,
            'branch_city': 'ZZOVERRIDE',
            'in_out': 'in',
            'created_by': user.id,
            'order_from': 'WhatsApp',
            'status_setup': status_setup_id,
            'discount': '0',
            'shipping_charge': '0',
            'tax_percent': '0',
            'total_amount': '1000',
            'order_items': cart,
        }

    print("\n[1] Editing the order to Confirmed (the order edit page)")
    resp = client.post(f'/orders/{edit_order.id}/edit/',
                       edit_payload('Cust ZZ-MO-EDIT', 'Test address',
                                    confirmed_setup.id))
    check("edit POST redirects (saved)", resp.status_code == 302,
          f"(got {resp.status_code})")
    snap = status_snapshot(edit_order)
    check("order_status is confirmed", snap[1] == 'confirmed', f"(got {snap[1]!r})")
    check("status (the field every sync compares) is confirmed too",
          snap[0] == 'confirmed', f"(got {snap[0]!r})")
    check("status_setup points at Confirmed", snap[2] == 'Confirmed', f"(got {snap[2]!r})")
    check("the manual hold was stamped",
          edit_order.manual_status_override_at is not None)
    check("the hold remembers the NCM status it was made against",
          edit_order.manual_status_override_ncm_status == HELD_NCM_STATUS,
          f"(got {edit_order.manual_status_override_ncm_status!r})")

    print("\n[2] The page-load NCM sync, with NCM still saying Pickup Order Created")
    payload = page_load_sync(edit_order)
    check("sync succeeds", payload.get('success') is True, f"(got {payload})")
    check("it reports no change (so the page does not repaint the badge)",
          payload.get('changed') is False, f"(got {payload.get('changed')})")
    check("it says why", payload.get('manual_override') is True, f"(got {payload})")
    check("it reports Confirmed back to the page",
          payload.get('status_display') == 'CONFIRMED',
          f"(got {payload.get('status_display')!r})")
    snap = status_snapshot(edit_order)
    check("the order is STILL confirmed in the database - the reported bug",
          snap[:2] == ('confirmed', 'confirmed'), f"(got {snap})")

    print("\n[3] A second page load (the user going back and forth)")
    page_load_sync(edit_order)
    snap = status_snapshot(edit_order)
    check("still confirmed", snap[:2] == ('confirmed', 'confirmed'), f"(got {snap})")

    print("\n[4] NCM moves the parcel on - NCM must win again")
    STUB_STATUSES[NCM_ID_EDIT] = MOVED_NCM_STATUS
    payload = page_load_sync(edit_order)
    check("the sync reports a change", payload.get('changed') is True, f"(got {payload})")
    snap = status_snapshot(edit_order)
    check("the order follows NCM out of the manual status",
          snap[0] == 'in_transit', f"(got {snap[0]!r})")
    check("the hold was retired", edit_order.manual_status_override_at is None,
          f"(got {edit_order.manual_status_override_at!r})")
    check("and the raw NCM status was recorded",
          edit_order.ncm_status == MOVED_NCM_STATUS, f"(got {edit_order.ncm_status!r})")

    print("\n[5] The orders-list bulk action ('Mark as Confirmed')")
    resp = client.post('/orders/bulk-action/', {
        'order_ids': [str(bulk_order.id)],
        'bulk_action': f'status_setup_{confirmed_setup.id}',
        'redirect_to': 'orders_list',
    })
    check("bulk action redirects (applied)", resp.status_code == 302,
          f"(got {resp.status_code})")
    snap = status_snapshot(bulk_order)
    check("both status fields were written",
          snap[:2] == ('confirmed', 'confirmed'), f"(got {snap})")
    check("the hold was stamped", bulk_order.manual_status_override_at is not None)

    print("\n[6] The background bulk sync must respect it too")

    def run_bulk():
        return bulk_sync_module.run_bulk_ncm_status_sync(
            user=user, order_ids=[bulk_order.id, untouched.id],
            fetch_event_times=False)

    with_stub(run_bulk)
    snap = status_snapshot(bulk_order)
    check("the hand-set order is untouched by the bulk sync",
          snap[:2] == ('confirmed', 'confirmed'), f"(got {snap})")
    snap = status_snapshot(untouched)
    check("an order nobody touched still syncs from NCM as before",
          snap[0] == 'Pickup Created', f"(got {snap[0]!r})")

    print("\n[7] Webhooks")
    from ncm.webhook_handler import NCMWebhookHandler
    handler = NCMWebhookHandler()
    result = handler._update_order_from_webhook(bulk_order, HELD_NCM_STATUS)
    check("a webhook repeating the held status is skipped",
          result.get('success') is False, f"(got {result})")
    snap = status_snapshot(bulk_order)
    check("...and the order is still confirmed",
          snap[:2] == ('confirmed', 'confirmed'), f"(got {snap})")

    result = handler._update_order_from_webhook(bulk_order, 'Delivered')
    check("a webhook carrying a NEW status is applied",
          result.get('success') is True, f"(got {result})")
    snap = status_snapshot(bulk_order)
    check("the order follows it", snap[0] == 'delivered', f"(got {snap[0]!r})")
    check("and the hold is gone", bulk_order.manual_status_override_at is None)

    print("\n[8] Editing something other than the status must not create a hold")
    resp = client.post(f'/orders/{untouched.id}/edit/',
                       edit_payload('Renamed Cust', 'A different address',
                                    pickup_setup.id))
    check("the edit saved", resp.status_code == 302, f"(got {resp.status_code})")
    untouched.refresh_from_db()
    check("no hold was stamped for an unchanged status",
          untouched.manual_status_override_at is None,
          f"(got {untouched.manual_status_override_at!r})")
    check("the address change did land",
          untouched.shipping_address == 'A different address',
          f"(got {untouched.shipping_address!r})")

    for order in (edit_order, bulk_order, untouched):
        cache.delete(f'ncm_sync_throttle_{order.id}')
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
