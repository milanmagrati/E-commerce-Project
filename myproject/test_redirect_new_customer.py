"""
Verify the "Redirected To (New Customer)" block of the Redirect Orders modal.

Covers:
  * the description parser used to recover destination details from a redirect log
  * get_redirect_order_details' new_customer_info fallback chain
    (order fields -> linked customer -> branch -> redirect log description)
  * redirect_order_save no longer blanks the order's customer identity fields
    when the form posts them empty (that is what emptied the modal in the
    first place)

Every DB write is made inside a transaction that is rolled back.

Run:  python test_redirect_new_customer.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402

# The Django test client sends Host: testserver
if 'testserver' not in settings.ALLOWED_HOSTS and '*' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

import json  # noqa: E402
from django.db import transaction  # noqa: E402
from django.template.loader import get_template  # noqa: E402
from django.test import Client, RequestFactory  # noqa: E402
from django.urls import reverse  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from dashboard.models import Branch, Customer, Order, OrderActivityLog  # noqa: E402
from dashboard.views import (  # noqa: E402
    _redirect_new_customer_from_description,
    get_redirect_order_details,
)

failures = []


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def call_api(user, order_id):
    request = RequestFactory().get(f'/api/orders/{order_id}/get-redirect-details/')
    request.user = user
    response = get_redirect_order_details(request, order_id)
    return response.status_code, json.loads(response.content.decode())


def new_info(user, order_id):
    status, payload = call_api(user, order_id)
    return status, payload.get('order', {}).get('new_customer_info', {}), payload.get('order', {})


class Rollback(RuntimeError):
    pass


def main():
    user = CustomUser.objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found — cannot exercise the permission-gated view.")
        return 1

    print("=" * 70)
    print("1. Template compiles")
    print("=" * 70)
    try:
        get_template('redirect_orders.html')
        check("redirect_orders.html compiles", True)
    except Exception as exc:
        check("redirect_orders.html compiles", False, f"-> {exc}")

    print()
    print("=" * 70)
    print("2. Redirect-log description parser")
    print("=" * 70)

    cases = [
        (
            "New customer phrasing",
            "Order redirected via NCM API (NCM Order #4412). "
            "New customer: Deeya Shrestha, Phone: 9801234567, Address: Baneshwor.",
            ('Deeya Shrestha', '9801234567', 'Baneshwor'),
        ),
        (
            "Customer details used phrasing",
            "Order redirected via NCM Possible Redirection (NCM Order #4412). "
            "Customer details used: Deeya Shrestha, Phone: 9801234567, Address: Baneshwor.",
            ('Deeya Shrestha', '9801234567', 'Baneshwor'),
        ),
        (
            "Customer details used for redirect phrasing",
            "Order redirected via NCM Possible Redirection (NCM Order #4412). "
            "Customer details used for redirect: Deeya Shrestha, Phone: 9801234567, "
            "Address: Baneshwor.",
            ('Deeya Shrestha', '9801234567', 'Baneshwor'),
        ),
        (
            "address containing a comma",
            "New customer: Deeya Shrestha, Phone: 9801234567, "
            "Address: Baneshwor, Kathmandu, near the temple.",
            ('Deeya Shrestha', '9801234567', 'Baneshwor, Kathmandu, near the temple'),
        ),
    ]
    for label, description, expected in cases:
        parsed = _redirect_new_customer_from_description(description)
        got = (parsed.get('name'), parsed.get('phone'), parsed.get('address'))
        check(label, got == expected, f"-> {got}")

    for label, description in (
        ("no customer sentence -> {}", "Order redirected via NCM API (NCM Order #4412)."),
        ("partial sentence -> {}", "New customer: Deeya Shrestha."),
        ("empty description -> {}", ""),
        ("None description -> {}", None),
    ):
        check(label, _redirect_new_customer_from_description(description) == {})

    print()
    print("=" * 70)
    print("3. new_customer_info fallback chain (synthetic, rolled back)")
    print("=" * 70)

    order = Order.objects.filter(is_deleted=False).order_by('-id').first()
    if not order:
        print("  (no orders in database — skipping)")
    else:
        DESCRIPTION = (
            "Order redirected via NCM API (NCM Order #99999). "
            "New customer: Deeya Shrestha, Phone: 9801234567, Address: Baneshwor."
        )

        # 3a. Order carries the destination itself -> used as-is, nothing recovered.
        try:
            with transaction.atomic():
                Order.objects.filter(pk=order.pk).update(
                    customer=None,
                    customer_name='Deeya Shrestha',
                    customer_phone='9801234567',
                    customer_email='deeya@example.com',
                    branch_city='Bhaktapur',
                    landmark='Near the temple',
                    shipping_address='Baneshwor',
                )
                status, info, payload = new_info(user, order.pk)
                check("order fields present: API returns 200", status == 200, f"-> {status}")
                check("order fields present: name/phone/address from the order",
                      (info.get('name'), info.get('phone'), info.get('address'))
                      == ('Deeya Shrestha', '9801234567', 'Baneshwor'), f"-> {info}")
                check("order fields present: email/branch/landmark included",
                      (info.get('email'), info.get('branch'), info.get('landmark'))
                      == ('deeya@example.com', 'Bhaktapur', 'Near the temple'), f"-> {info}")
                check("order fields present: not flagged as recovered",
                      info.get('recovered_from_log') is False, f"-> {info}")
                check("top-level customer_* keys mirror new_customer_info",
                      (payload.get('customer_name'), payload.get('customer_phone'),
                       payload.get('branch_city'), payload.get('landmark'),
                       payload.get('shipping_address'))
                      == ('Deeya Shrestha', '9801234567', 'Bhaktapur',
                          'Near the temple', 'Baneshwor'), f"-> {payload.get('customer_name')}")
                raise Rollback()
        except Rollback:
            pass

        # 3b. Order fields wiped -> recovered from the redirect log description.
        try:
            with transaction.atomic():
                Order.objects.filter(pk=order.pk).update(
                    customer=None, customer_name='', customer_phone='',
                    customer_email='', branch_city='', landmark='', shipping_address='',
                )
                OrderActivityLog.objects.create(
                    order=order, action_type='redirected', user=user,
                    description=DESCRIPTION, field_name='ncm_status',
                    old_value='', new_value='redirected',
                    metadata={'customer_name': 'Raj Kumar', 'customer_phone': '9851044348'},
                )
                status, info, _ = new_info(user, order.pk)
                check("wiped order: recovered name/phone/address from the log",
                      (info.get('name'), info.get('phone'), info.get('address'))
                      == ('Deeya Shrestha', '9801234567', 'Baneshwor'), f"-> {info}")
                check("wiped order: flagged as recovered",
                      info.get('recovered_from_log') is True, f"-> {info}")
                raise Rollback()
        except Rollback:
            pass

        # 3c. The old-customer snapshot must never leak into the new-customer block.
        try:
            with transaction.atomic():
                Order.objects.filter(pk=order.pk).update(
                    customer=None, customer_name='', customer_phone='',
                    customer_email='', branch_city='', landmark='', shipping_address='',
                )
                OrderActivityLog.objects.create(
                    order=order, action_type='redirected', user=user,
                    description='Order redirected via NCM API (NCM Order #99999).',
                    field_name='ncm_status', old_value='', new_value='redirected',
                    metadata={
                        'customer_name': 'Raj Kumar',
                        'customer_phone': '9851044348',
                        'shipping_address': 'Jaranku',
                        'branch_city': 'Kavresthali',
                    },
                )
                status, info, payload = new_info(user, order.pk)
                check("nothing recoverable: new-customer block is empty",
                      not any(info.get(k) for k in
                              ('name', 'phone', 'email', 'branch', 'landmark', 'address')),
                      f"-> {info}")
                check("nothing recoverable: old customer still returned",
                      payload.get('old_customer_info', {}).get('name') == 'Raj Kumar',
                      f"-> {payload.get('old_customer_info')}")
                check("nothing recoverable: not flagged as recovered",
                      info.get('recovered_from_log') is False, f"-> {info}")
                raise Rollback()
        except Rollback:
            pass

        # 3d. Branch falls back to the order's Branch when branch_city is empty.
        branch = Branch.objects.first()
        if branch:
            try:
                with transaction.atomic():
                    Order.objects.filter(pk=order.pk).update(
                        customer=None, customer_name='Deeya Shrestha',
                        customer_phone='9801234567', branch_city='', branch=branch,
                        shipping_address='Baneshwor',
                    )
                    status, info, _ = new_info(user, order.pk)
                    check("blank branch_city: falls back to the order's branch",
                          info.get('branch') == (branch.name or '').strip(), f"-> {info}")
                    raise Rollback()
            except Rollback:
                pass
        else:
            print("  (no branches in database — skipping branch fallback check)")

        # 3e. A linked Customer record that belongs to a *different* phone number
        #     is the pre-redirect customer — it must not leak into this block.
        try:
            with transaction.atomic():
                stale = Customer.objects.create(
                    name='Raj Kumar', phone='9851044348', email='raj@example.com',
                    city='Kavresthali', address='Jaranku',
                )
                Order.objects.filter(pk=order.pk).update(
                    customer=stale, customer_name='Deeya Shrestha',
                    customer_phone='9801234567', customer_email='',
                    branch_city='', landmark='', shipping_address='Baneshwor',
                    branch=None,
                )
                status, info, _ = new_info(user, order.pk)
                check("stale linked customer: email not borrowed",
                      not info.get('email'), f"-> {info}")
                check("stale linked customer: branch not borrowed",
                      not info.get('branch'), f"-> {info}")
                check("stale linked customer: order's own name/phone kept",
                      (info.get('name'), info.get('phone'))
                      == ('Deeya Shrestha', '9801234567'), f"-> {info}")

                # Same record, now matching the order's phone -> safe to borrow.
                Order.objects.filter(pk=order.pk).update(customer_phone='9851044348',
                                                         customer_name='')
                status, info, _ = new_info(user, order.pk)
                check("matching linked customer: fills the gaps",
                      (info.get('name'), info.get('email'), info.get('branch'))
                      == ('Raj Kumar', 'raj@example.com', 'Kavresthali'), f"-> {info}")
                raise Rollback()
        except Rollback:
            pass

        # 3f. The list table's "New Customer" column must agree with the modal.
        list_client = Client()
        list_client.force_login(user)
        try:
            with transaction.atomic():
                Order.objects.filter(pk=order.pk).update(
                    customer=None, customer_name='', customer_phone='',
                    customer_email='', branch_city='', landmark='', shipping_address='',
                )
                OrderActivityLog.objects.create(
                    order=order, action_type='redirected', user=user,
                    description=DESCRIPTION, field_name='ncm_status',
                    old_value='', new_value='redirected', metadata={},
                )
                resp = list_client.get(reverse('redirect_orders_list'))
                body = resp.content.decode('utf-8', 'replace')
                check("redirect orders list renders", resp.status_code == 200,
                      f"-> {resp.status_code}")
                check("list table shows the recovered new customer",
                      'Deeya Shrestha' in body and '9801234567' in body)
                raise Rollback()
        except Rollback:
            pass

        leftover = OrderActivityLog.objects.filter(description=DESCRIPTION).count()
        check("synthetic logs rolled back", leftover == 0, f"-> {leftover} left")
        check("synthetic customer rolled back",
              not Customer.objects.filter(phone='9851044348', email='raj@example.com').exists())

    print()
    print("=" * 70)
    print("4. redirect_order_save keeps identity fields when posted empty")
    print("=" * 70)

    order = Order.objects.filter(
        is_deleted=False
    ).exclude(customer_name='').order_by('-id').first()
    if not order:
        print("  (no order with a customer name — skipping)")
    else:
        client = Client()
        client.force_login(user)
        original = (order.customer_name, order.customer_phone, order.shipping_address)
        try:
            with transaction.atomic():
                resp = client.post(
                    reverse('redirect_order_save', args=[order.id]),
                    {
                        'customer_name': '',
                        'customer_phone': '',
                        'shipping_address': '',
                        'branch_city': '',
                        'send_to_logistics': '',
                    },
                    HTTP_X_REQUESTED_WITH='XMLHttpRequest',
                )
                check("empty post: request succeeds", resp.status_code == 200,
                      f"-> {resp.status_code}")
                order.refresh_from_db()
                check("empty post: customer name preserved",
                      order.customer_name == original[0],
                      f"-> {order.customer_name!r} was {original[0]!r}")
                check("empty post: phone preserved",
                      order.customer_phone == original[1],
                      f"-> {order.customer_phone!r} was {original[1]!r}")
                check("empty post: shipping address preserved",
                      order.shipping_address == original[2],
                      f"-> {order.shipping_address!r} was {original[2]!r}")
                raise Rollback()
        except Rollback:
            pass

        order.refresh_from_db()
        check("order restored after rollback",
              (order.customer_name, order.customer_phone, order.shipping_address) == original,
              f"-> {(order.customer_name, order.customer_phone, order.shipping_address)}")

        # A real value must still overwrite.
        try:
            with transaction.atomic():
                resp = client.post(
                    reverse('redirect_order_save', args=[order.id]),
                    {
                        'customer_name': 'Deeya Shrestha',
                        'customer_phone': '9801234567',
                        'shipping_address': 'Baneshwor',
                        'branch_city': 'Bhaktapur',
                        'send_to_logistics': '',
                    },
                    HTTP_X_REQUESTED_WITH='XMLHttpRequest',
                )
                order.refresh_from_db()
                check("non-empty post: fields still update",
                      (order.customer_name, order.customer_phone,
                       order.shipping_address, order.branch_city)
                      == ('Deeya Shrestha', '9801234567', 'Baneshwor', 'Bhaktapur'),
                      f"-> {order.customer_name!r}")
                raise Rollback()
        except Rollback:
            pass

        order.refresh_from_db()
        check("order restored after second rollback",
              (order.customer_name, order.customer_phone, order.shipping_address) == original,
              f"-> {(order.customer_name, order.customer_phone, order.shipping_address)}")

    print()
    print("=" * 70)
    if failures:
        print(f"RESULT: {len(failures)} check(s) failed")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
