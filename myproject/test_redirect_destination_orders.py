"""
Verify that an order which merely RECEIVED a redirected package is not
presented as if it had been redirected itself.

Redirecting a package writes a 'redirected' activity log on two orders: the
package's own order (which really did change customer) and the confirmed order
whose customer is receiving it. The second one's log used to store a snapshot of
its own *current* customer, which Redirect Orders then read as the "old
customer" — so the row and the modal showed the same person on both sides of the
arrow, as if the order had been redirected to itself.

Covers the new explicit role marker and both legacy-row fallbacks.

Everything is created inside a transaction that is rolled back at the end.

Run:  python test_redirect_destination_orders.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS and '*' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

import json  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from dashboard.models import Order, OrderActivityLog, OrderItem  # noqa: E402

setup_test_environment()

failures = []


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def make_order(number, name, phone):
    order = Order.objects.create(
        order_number=number,
        customer_name=name,
        customer_phone=phone,
        customer_email=f'{number.lower()}@example.com',
        shipping_address=f'{number} address',
        branch_city='ZZDESTBRANCH',
        order_status='redirected',
        status='redirected',
        total_amount=1000,
        order_from='web',
        payment_method='cod',
    )
    OrderItem.objects.create(
        order=order, product_name='ZZ Dest Item', quantity=1, price=1000, total=1000,
    )
    return order


def log_for(order, user, description, metadata):
    return OrderActivityLog.objects.create(
        order=order, action_type='redirected', user=user,
        description=description, field_name='ncm_status',
        old_value='', new_value='redirected', metadata=metadata,
    )


def modal(client, order):
    resp = client.get(f'/api/orders/{order.id}/get-redirect-details/')
    return resp.status_code, json.loads(resp.content.decode()).get('order', {})


def row_for(resp, number):
    for entry in resp.context['redirect_entries']:
        if entry['order_number'] == number:
            return entry
    return None


def main():
    user = CustomUser.objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found — cannot exercise the view.")
        return 1

    client = Client()
    client.force_login(user)

    # ── The four shapes a 'redirected' log can take ───────────────────────────
    # 1. Destination, new style: an explicit role marker.
    dest_new = make_order('ZZ-DEST-NEW', 'Receiving Customer', '9800000010')
    log_for(dest_new, user,
            'Order redirected via NCM Possible Redirection (NCM Order #99933001). '
            'Customer details used for redirect: Receiving Customer, '
            'Phone: 9800000010, Address: ZZ-DEST-NEW address.',
            {'redirect_role': 'destination',
             'source_order_number': 'ZZ-SRC', 'source_ncm_order_id': 99933001})

    # 2. Destination, legacy row: no role marker, recognised by the phrasing.
    dest_desc = make_order('ZZ-DEST-DESC', 'Receiving Two', '9800000011')
    log_for(dest_desc, user,
            'Order redirected via NCM Possible Redirection (NCM Order #99933002). '
            'Customer details used: Receiving Two, Phone: 9800000011, '
            'Address: ZZ-DEST-DESC address.',
            {'customer_name': 'Receiving Two', 'customer_phone': '9800000011',
             'shipping_address': 'ZZ-DEST-DESC address', 'branch_city': 'ZZDESTBRANCH'})

    # 3. Destination, legacy row with an unhelpful description: recognised
    #    because the "old" snapshot IS the order's current customer.
    dest_snap = make_order('ZZ-DEST-SNAP', 'Receiving Three', '9800000012')
    log_for(dest_snap, user,
            'Order redirected.',
            {'customer_name': 'Receiving Three', 'customer_phone': '9800000012',
             'shipping_address': 'ZZ-DEST-SNAP address', 'branch_city': 'ZZDESTBRANCH'})

    # 4. A genuine redirect — must NOT be mistaken for a destination.
    source = make_order('ZZ-SOURCE', 'New Customer', '9800000020')
    log_for(source, user,
            'Order redirected via NCM API (NCM Order #99933004). '
            'New customer: New Customer, Phone: 9800000020, Address: ZZ-SOURCE address.',
            {'redirect_role': 'redirected',
             'customer_name': 'Original Customer', 'customer_phone': '9800000021',
             'shipping_address': 'Original address', 'branch_city': 'ZZDESTBRANCH'})

    # 5. Both roles on one order: it was a destination once and later redirected
    #    onward. The onward redirect wins — it really does have an old customer.
    both = make_order('ZZ-BOTH', 'Final Customer', '9800000030')
    log_for(both, user,
            'Order redirected via NCM Possible Redirection (NCM Order #99933005). '
            'Customer details used: Middle Customer, Phone: 9800000031, Address: mid.',
            {'redirect_role': 'destination', 'source_ncm_order_id': 99933005})
    log_for(both, user,
            'Order redirected via NCM API (NCM Order #99933006). '
            'New customer: Final Customer, Phone: 9800000030, Address: ZZ-BOTH address.',
            {'redirect_role': 'redirected',
             'customer_name': 'Middle Customer', 'customer_phone': '9800000031',
             'shipping_address': 'mid', 'branch_city': 'ZZDESTBRANCH'})

    print("\n[1] Destination orders are flagged, not shown as self-redirects")
    for order, label in ((dest_new, 'explicit role marker'),
                         (dest_desc, 'legacy row, description phrasing'),
                         (dest_snap, 'legacy row, snapshot equals current customer')):
        status, payload = modal(client, order)
        check(f"{label}: API returns 200", status == 200, f"-> {status}")
        check(f"{label}: flagged as a redirect destination",
              payload.get('is_redirect_destination') is True,
              f"-> {payload.get('is_redirect_destination')}")
        check(f"{label}: no bogus old customer",
              not payload.get('old_customer_info'),
              f"-> {payload.get('old_customer_info')}")
        check(f"{label}: its own customer is still shown",
              payload.get('new_customer_info', {}).get('name') == order.customer_name,
              f"-> {payload.get('new_customer_info')}")
        check(f"{label}: history entry carries the destination role",
              all(h.get('role') == 'destination' for h in payload.get('redirect_history', [])),
              f"-> {[h.get('role') for h in payload.get('redirect_history', [])]}")

    print("\n[2] The source order is unaffected")
    status, payload = modal(client, source)
    check("genuine redirect is NOT flagged as a destination",
          payload.get('is_redirect_destination') is False,
          f"-> {payload.get('is_redirect_destination')}")
    check("genuine redirect keeps its old customer",
          payload.get('old_customer_info', {}).get('name') == 'Original Customer',
          f"-> {payload.get('old_customer_info')}")
    check("genuine redirect shows the new customer",
          payload.get('new_customer_info', {}).get('name') == 'New Customer',
          f"-> {payload.get('new_customer_info')}")
    check("old and new customer differ",
          payload.get('old_customer_info', {}).get('name')
          != payload.get('new_customer_info', {}).get('name'))

    print("\n[3] An order redirected onward after receiving is a source, not a destination")
    status, payload = modal(client, both)
    check("mixed history is not flagged as a destination",
          payload.get('is_redirect_destination') is False,
          f"-> {payload.get('is_redirect_destination')}")
    check("mixed history keeps the onward redirect's old customer",
          payload.get('old_customer_info', {}).get('name') == 'Middle Customer',
          f"-> {payload.get('old_customer_info')}")
    check("both history entries are present with their own roles",
          sorted(h.get('role') for h in payload.get('redirect_history', []))
          == ['destination', 'redirected'],
          f"-> {[h.get('role') for h in payload.get('redirect_history', [])]}")

    print("\n[4] The Redirect Orders table says the same thing")
    resp = client.get('/orders/redirect-orders/?per_page=500')
    check("page renders", resp.status_code == 200, f"-> {resp.status_code}")

    for order, expected in ((dest_new, True), (dest_desc, True), (dest_snap, True),
                            (source, False), (both, False)):
        row = row_for(resp, order.order_number)
        check(f"{order.order_number}: listed", row is not None)
        if row is None:
            continue
        check(f"{order.order_number}: is_destination == {expected}",
              row['is_destination'] is expected, f"-> {row['is_destination']}")
        if expected:
            check(f"{order.order_number}: old customer left blank",
                  row['old_customer_info']['name'] == '—',
                  f"-> {row['old_customer_info']}")
        else:
            check(f"{order.order_number}: old customer differs from new",
                  row['old_customer_info']['name'] != row['new_customer_name'],
                  f"-> {row['old_customer_info']['name']} vs {row['new_customer_name']}")

    dest_row = row_for(resp, 'ZZ-DEST-NEW')
    check("destination row names its source order",
          dest_row and dest_row['redirect_source'].get('order_number') == 'ZZ-SRC',
          f"-> {dest_row['redirect_source'] if dest_row else None}")

    body = resp.content.decode('utf-8', 'replace')
    check("the Destination badge is rendered",
          'Destination — customer unchanged' in body)

    print()
    if failures:
        print(f"RESULT: {len(failures)} check(s) failed")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == '__main__':
    exit_code = 1
    try:
        with transaction.atomic():
            exit_code = main()
            raise RuntimeError('rollback')
    except RuntimeError as exc:
        if str(exc) != 'rollback':
            raise
    leftover = Order.objects.filter(order_number__startswith='ZZ-DEST').count() \
        + Order.objects.filter(order_number__in=['ZZ-SOURCE', 'ZZ-BOTH']).count()
    print(f"cleanup: {leftover} synthetic order(s) left behind (expected 0)")
    raise SystemExit(exit_code or (1 if leftover else 0))
