"""
Verify the Possible Redirection "still in transit" fix in dashboard.views:

  1. An RTV whose NCM status shows it's still travelling back (e.g. "Dispatched
     to RETURN (TINKUNE)") is NOT listed as a candidate at all — NCM refuses to
     redirect a parcel that hasn't arrived, so offering it is only a trap. It
     appears once NCM reports one of the at-branch statuses.
  2. Attempting the actual redirect (POST to redirect_order_save or
     redirect_rtv_save with send_to_logistics=ncm_redirect) on such an RTV is
     rejected server-side *before* anything is written — the local order's
     customer identity must be byte-for-byte unchanged afterward. This is the
     exact bug from the screen recording: NCM rejected the redirect ("Order
     can only be redirected when status is: Arrived, Pickup Complete, Returned
     to Warehouse"), but the app had already overwritten the local customer
     with the new one before making that call.
  3. Once NCM reports an eligible status (Arrived / Pickup Complete / Returned
     to Warehouse), `redirect_eligible` flips to True.

Everything is created inside a transaction that is rolled back at the end —
nothing is left behind.

Run:  python test_possible_redirection_status_gate.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS and '*' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from decimal import Decimal  # noqa: E402
from unittest import mock  # noqa: E402

from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from dashboard.models import LogisticsAPIConfig, Order, OrderItem, RTVOrder  # noqa: E402
from dashboard.timezone_utils import get_nepali_now  # noqa: E402
from dashboard.views import _rtv_is_redirect_eligible  # noqa: E402

setup_test_environment()

BRANCH = 'ZZGATEBRANCH'
NCM_ID_TRANSIT = 99930001
NCM_ID_ARRIVED = 99930099

failures = []


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def make_order(number, products, **extra):
    fields = {
        'order_number': number,
        'customer_name': f'Cust {number}',
        'customer_phone': '9800000000',
        'shipping_address': 'Test address',
        'branch_city': BRANCH,
        'order_status': 'confirmed',
        'status': 'confirmed',
        'total_amount': 1400,
    }
    fields.update(extra)
    order = Order.objects.create(**fields)
    for name, qty in products:
        OrderItem.objects.create(
            order=order, product_name=name, quantity=qty, price=100, total=100 * qty,
        )
    return order


def entry_for(resp, ncm_id):
    for e in resp.context['rtv_entries']:
        if e['ncm_order_id'] == ncm_id:
            return e
    return None


def main():
    print("\n[1] _rtv_is_redirect_eligible matches NCM's own accepted statuses")
    check("in-transit ('Dispatched to Return (TINKUNE)') is NOT eligible",
          _rtv_is_redirect_eligible('Dispatched to Return (TINKUNE)') is False)
    check("generic 'Dispatched' is NOT eligible",
          _rtv_is_redirect_eligible('Dispatched') is False)
    check("'Pending' is NOT eligible",
          _rtv_is_redirect_eligible('Pending') is False)
    check("'Returned to Warehouse' IS eligible",
          _rtv_is_redirect_eligible('Returned to Warehouse') is True)
    check("'Arrived' IS eligible",
          _rtv_is_redirect_eligible('Arrived') is True)
    check("branch-qualified 'Arrived at RETURN (TINKUNE)' IS eligible",
          _rtv_is_redirect_eligible('Arrived at RETURN (TINKUNE)') is True)
    check("'Pickup Complete' IS eligible",
          _rtv_is_redirect_eligible('Pickup Complete') is True)
    check("falls back to the linked local order's ncm_status when RTV's own is blank",
          _rtv_is_redirect_eligible('', 'Returned to Warehouse') is True)

    user = CustomUser.objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found — cannot exercise the view.")
        return 1

    client = Client()
    client.force_login(user)

    linked_transit = make_order(
        'ZZ-GATE-LINKED', [('ZZ Gate Shampoo', 1)],
        ncm_order_id=NCM_ID_TRANSIT, customer_name='Original Customer', customer_phone='9811111111',
    )
    rtv_transit = RTVOrder.objects.create(
        order_id=NCM_ID_TRANSIT,
        vendor=user,
        vendor_return=True,
        to_branch=BRANCH,
        last_status='Dispatched to Return (TINKUNE)',
        product_description='1x ZZ Gate Shampoo',
        rtv_marked_at=get_nepali_now(),
        rtv_marked_at_source=RTVOrder.SOURCE_WEBHOOK,
        receiver_name='Original Customer',
        receiver_phone='9811111111',
    )
    candidate = make_order('ZZ-GATE-CANDIDATE', [('ZZ Gate Shampoo', 1)], customer_name='New Customer')

    print("\n[2] An in-transit RTV is not listed at all")
    resp = client.get('/orders/possible-redirection/')
    check("page renders", resp.status_code == 200, f"(got {resp.status_code})")
    check("the in-transit RTV is NOT listed as a candidate",
          entry_for(resp, NCM_ID_TRANSIT) is None)

    body = resp.content.decode('utf-8', 'replace')
    check("its matching candidate's 'Use' button is not rendered either",
          'data-order-num="ZZ-GATE-CANDIDATE"' not in body)

    # ...and it appears the moment NCM reports the arrival, with the match intact.
    RTVOrder.objects.filter(order_id=NCM_ID_TRANSIT).update(last_status='Arrived at RETURN (TINKUNE)')
    resp_arrived = client.get('/orders/possible-redirection/')
    entry_arrived = entry_for(resp_arrived, NCM_ID_TRANSIT)
    check("once NCM reports it arrived, the RTV IS listed", entry_arrived is not None)
    if entry_arrived is not None:
        matched_numbers = [r['order'].order_number for r in entry_arrived['matching_rows']]
        check("and its matching candidate comes with it", 'ZZ-GATE-CANDIDATE' in matched_numbers,
              f"(got {matched_numbers})")
    check("the enabled 'Use' button is rendered now",
          'data-order-num="ZZ-GATE-CANDIDATE"' in resp_arrived.content.decode('utf-8', 'replace'))

    # Put it back in transit for the server-guard checks below.
    RTVOrder.objects.filter(order_id=NCM_ID_TRANSIT).update(
        last_status='Dispatched to Return (TINKUNE)')

    print("\n[3] The actual redirect is rejected server-side, before anything changes")
    resp2 = client.post(
        f'/api/orders/{linked_transit.id}/redirect-save/',
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
    check("the redirect POST is rejected (not 200)", resp2.status_code != 200,
          f"(got {resp2.status_code})")
    data = resp2.json()
    check("the error explains the package is still in transit",
          data.get('status') == 'error' and 'transit' in (data.get('message') or '').lower(),
          f"(got {data})")

    linked_transit.refresh_from_db()
    check("the order's customer name was NOT overwritten",
          linked_transit.customer_name == 'Original Customer',
          f"(got {linked_transit.customer_name!r})")
    check("the order's customer phone was NOT overwritten",
          linked_transit.customer_phone == '9811111111',
          f"(got {linked_transit.customer_phone!r})")

    print("\n[4] Same guard for RTVs with no linked local order (redirect_rtv_save)")
    rtv_transit_2 = RTVOrder.objects.create(
        order_id=NCM_ID_TRANSIT + 1,
        vendor=user,
        vendor_return=True,
        to_branch=BRANCH,
        last_status='Dispatched to Return (TINKUNE)',
        product_description='1x ZZ Gate Shampoo',
        rtv_marked_at=get_nepali_now(),
        rtv_marked_at_source=RTVOrder.SOURCE_WEBHOOK,
    )
    resp3 = client.post(
        f'/api/rtv/{rtv_transit_2.order_id}/redirect-save/',
        {
            'customer_name': 'New Customer',
            'customer_phone': '9822222222',
            'shipping_address': 'Somewhere',
        },
        HTTP_X_REQUESTED_WITH='XMLHttpRequest',
    )
    check("redirect_rtv_save also rejects an in-transit RTV", resp3.status_code != 200,
          f"(got {resp3.status_code})")
    data3 = resp3.json()
    check("with the same in-transit explanation",
          data3.get('status') == 'error' and 'transit' in (data3.get('message') or '').lower(),
          f"(got {data3})")

    print("\n[5] Once NCM says it's arrived, the flag flips to eligible")
    rtv_arrived = RTVOrder.objects.create(
        order_id=NCM_ID_ARRIVED,
        vendor=user,
        vendor_return=True,
        to_branch=BRANCH,
        last_status='Returned to Warehouse',
        product_description='1x ZZ Gate Shampoo',
        rtv_marked_at=get_nepali_now(),
        rtv_marked_at_source=RTVOrder.SOURCE_WEBHOOK,
        receiver_name='Original Customer 2',
        receiver_phone='9833333333',
    )
    resp4 = client.get('/orders/possible-redirection/')
    check("the arrived RTV is listed", entry_for(resp4, NCM_ID_ARRIVED) is not None)

    print("\n[6] The Order Detail modal's own redirect button carries the same flag")
    # This is the third entry point into the redirect flow — clicking the NCM
    # order id link opens a detail modal with its own "Open Redirect" button,
    # which used to bypass the eligibility gate entirely.
    resp5 = client.get(f'/api/rtv/{NCM_ID_TRANSIT}/redirect-get/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    check("redirect-get responds 200 for the in-transit (linked) RTV",
          resp5.status_code == 200, f"(got {resp5.status_code})")
    data5 = resp5.json()
    check("the detail-modal payload flags it as NOT redirect eligible",
          data5.get('rtv', {}).get('redirect_eligible') is False, f"(got {data5.get('rtv')})")

    resp6 = client.get(f'/api/rtv/{NCM_ID_ARRIVED}/redirect-get/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    check("redirect-get responds 200 for the arrived (no local order) RTV",
          resp6.status_code == 200, f"(got {resp6.status_code})")
    data6 = resp6.json()
    check("the detail-modal payload flags it as redirect eligible",
          data6.get('rtv', {}).get('redirect_eligible') is True, f"(got {data6.get('rtv')})")

    print("\n[7] An eligible package: financial/branch fields also wait for NCM's OK")
    # Status-eligible, so it clears the early guard — this exercises the
    # deeper fix: redirect_order_save used to commit total_amount,
    # discount_amount, shipping_charge, branch_city and the partial-payment
    # state unconditionally too, not just the customer identity. A redirect
    # that failed for any OTHER reason (bad credentials, NCM downtime) still
    # left this order's totals/branch overwritten with the destination
    # order's figures even though nothing was actually redirected.
    api_config = LogisticsAPIConfig.objects.create(
        api_name='ZZ Gate Test Config',
        logistics_provider='ncm',
        api_key='zz-test-key',
        base_urls=['https://zz-fake-ncm.example/api/v1/vendor', 'https://zz-fake-ncm.example/api/v2/vendor'],
        is_active=True,
    )
    linked_eligible = make_order(
        'ZZ-GATE-ELIGIBLE', [('ZZ Gate Shampoo', 1)],
        ncm_order_id=NCM_ID_ARRIVED, customer_name='Original Eligible Customer',
        customer_phone='9844444444', branch_city=BRANCH,
        total_amount=Decimal('1000.00'), discount_amount=Decimal('0.00'),
        shipping_charge=Decimal('100.00'), is_partial_payment=False,
    )
    other_branch_candidate = make_order(
        'ZZ-GATE-OTHERBRANCH', [('ZZ Gate Shampoo', 1)], customer_name='Destination Customer',
        branch_city='ZZOTHERBRANCH', total_amount=Decimal('2500.00'),
    )

    def post_redirect():
        return client.post(
            f'/api/orders/{linked_eligible.id}/redirect-save/',
            {
                'customer_name': other_branch_candidate.customer_name,
                'customer_phone': '9855555555',
                'shipping_address': 'New destination address',
                'branch_city': 'ZZOTHERBRANCH',
                'total_amount': '2500.00',
                'discount_amount': '50.00',
                'shipping_charge': '150.00',
                'is_partial_payment': 'true',
                'partial_amount_paid': '500.00',
                'send_to_logistics': 'ncm_redirect',
                'api_config_id': str(api_config.id),
                'cod_charge': '2000.00',
                'matched_order_id': str(other_branch_candidate.id),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    class _FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)
        def json(self):
            return self._payload

    with mock.patch('requests.post', return_value=_FakeResponse(
        400, {'message': 'Order can only be redirected when status is: Arrived, '
                          'Pickup Complete, Returned to Warehouse'})):
        resp7 = post_redirect()
    check("save succeeds even though the NCM call itself failed (matches existing UX)",
          resp7.status_code == 200, f"(got {resp7.status_code})")
    data7 = resp7.json()
    check("the response discloses the logistics failure",
          data7.get('logistics', {}).get('status') == 'error', f"(got {data7})")

    linked_eligible.refresh_from_db()
    check("customer_name NOT overwritten by the failed attempt",
          linked_eligible.customer_name == 'Original Eligible Customer',
          f"(got {linked_eligible.customer_name!r})")
    check("branch_city NOT overwritten by the failed attempt",
          linked_eligible.branch_city == BRANCH, f"(got {linked_eligible.branch_city!r})")
    check("total_amount NOT overwritten by the failed attempt",
          linked_eligible.total_amount == Decimal('1000.00'), f"(got {linked_eligible.total_amount!r})")
    check("discount_amount NOT overwritten by the failed attempt",
          linked_eligible.discount_amount == Decimal('0.00'), f"(got {linked_eligible.discount_amount!r})")
    check("shipping_charge NOT overwritten by the failed attempt",
          linked_eligible.shipping_charge == Decimal('100.00'), f"(got {linked_eligible.shipping_charge!r})")
    check("is_partial_payment NOT flipped by the failed attempt",
          linked_eligible.is_partial_payment is False, f"(got {linked_eligible.is_partial_payment!r})")

    print("\n[8] ...but a CONFIRMED redirect does persist every one of those fields")
    with mock.patch('requests.post', return_value=_FakeResponse(
        200, {'message': 'Redirected', 'order': NCM_ID_ARRIVED, 'delivery_charge': 120, 'cod_charge': 2000})):
        resp8 = post_redirect()
    check("save succeeds on a confirmed redirect", resp8.status_code == 200, f"(got {resp8.status_code})")
    data8 = resp8.json()
    check("the response discloses the logistics success",
          data8.get('logistics', {}).get('status') == 'success', f"(got {data8})")

    linked_eligible.refresh_from_db()
    check("customer_name IS now updated", linked_eligible.customer_name == other_branch_candidate.customer_name,
          f"(got {linked_eligible.customer_name!r})")
    check("branch_city IS now updated", linked_eligible.branch_city == 'ZZOTHERBRANCH',
          f"(got {linked_eligible.branch_city!r})")
    check("total_amount IS now updated", linked_eligible.total_amount == Decimal('2500.00'),
          f"(got {linked_eligible.total_amount!r})")
    check("discount_amount IS now updated", linked_eligible.discount_amount == Decimal('50.00'),
          f"(got {linked_eligible.discount_amount!r})")
    check("shipping_charge IS now updated", linked_eligible.shipping_charge == Decimal('150.00'),
          f"(got {linked_eligible.shipping_charge!r})")
    check("is_partial_payment IS now updated", linked_eligible.is_partial_payment is True,
          f"(got {linked_eligible.is_partial_payment!r})")
    check("partial_amount_paid IS now updated", linked_eligible.partial_amount_paid == Decimal('500.00'),
          f"(got {linked_eligible.partial_amount_paid!r})")

    rtv_transit.delete()
    rtv_transit_2.delete()
    rtv_arrived.delete()

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
