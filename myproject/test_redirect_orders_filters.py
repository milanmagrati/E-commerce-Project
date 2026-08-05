"""
Verify the date-range presets, custom date range, and column sorting added
to the Redirect Orders list (dashboard.views.redirect_orders_list).

Creates 3 synthetic redirected orders with controlled amounts and
back-dated redirect timestamps, all inside a transaction that is rolled
back at the end — nothing is left behind.

Run:  python test_redirect_orders_filters.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS and '*' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from datetime import timedelta  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from dashboard.models import Order, OrderActivityLog  # noqa: E402
from dashboard.timezone_utils import get_nepali_now  # noqa: E402

setup_test_environment()

failures = []


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def main():
    user = CustomUser.objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found — cannot exercise the view.")
        return 1

    client = Client()
    client.force_login(user)

    def get(params):
        resp = client.get('/orders/redirect-orders/', params)
        return resp

    def order_numbers(resp):
        return [e['order_number'] for e in resp.context['redirect_entries']]

    nepal_today = get_nepali_now().date()

    try:
        with transaction.atomic():
            orders = {}
            # A: redirected "now" (today), amount 100
            # B: redirected 10 days ago (in last_30_days, out of last_7_days), amount 300
            # C: redirected 40 days ago (out of last_30_days), amount 200
            plan = [
                ('A', 100, timedelta(hours=1)),
                ('B', 300, timedelta(days=10)),
                ('C', 200, timedelta(days=40)),
            ]
            for suffix, amount, age in plan:
                order = Order.objects.create(
                    order_number=f'ZFILTERTEST-{suffix}',
                    customer_name=f'Filter Test {suffix}',
                    customer_phone='9800000000',
                    total_amount=amount,
                    is_deleted=False,
                    created_by=user,
                )
                log = OrderActivityLog.objects.create(
                    order=order,
                    action_type='redirected',
                    user=user,
                    description='synthetic filter test log',
                    field_name='ncm_status',
                    old_value='',
                    new_value='redirected',
                    metadata={},
                )
                backdated = get_nepali_now() - age
                OrderActivityLog.objects.filter(pk=log.pk).update(created_at=backdated)
                orders[suffix] = order

            print("=" * 70)
            print("1. Date presets")
            print("=" * 70)

            resp = get({'search': 'ZFILTERTEST', 'date_range': 'today', 'per_page': '500'})
            check("today -> only A", order_numbers(resp) == ['ZFILTERTEST-A'],
                  f"-> {order_numbers(resp)}")

            resp = get({'search': 'ZFILTERTEST', 'date_range': 'last_7_days', 'per_page': '500'})
            check("last_7_days -> only A", order_numbers(resp) == ['ZFILTERTEST-A'],
                  f"-> {order_numbers(resp)}")

            resp = get({'search': 'ZFILTERTEST', 'date_range': 'last_30_days', 'per_page': '500'})
            check("last_30_days -> A and B, not C",
                  set(order_numbers(resp)) == {'ZFILTERTEST-A', 'ZFILTERTEST-B'},
                  f"-> {order_numbers(resp)}")

            resp = get({'search': 'ZFILTERTEST', 'per_page': '500'})
            check("no date filter -> all three",
                  set(order_numbers(resp)) == {'ZFILTERTEST-A', 'ZFILTERTEST-B', 'ZFILTERTEST-C'},
                  f"-> {order_numbers(resp)}")

            print()
            print("=" * 70)
            print("2. Custom date range")
            print("=" * 70)

            c_date = (nepal_today - timedelta(days=40)).strftime('%Y-%m-%d')
            b_date = (nepal_today - timedelta(days=10)).strftime('%Y-%m-%d')
            resp = get({
                'search': 'ZFILTERTEST', 'date_range': 'custom',
                'start_date': c_date, 'end_date': b_date, 'per_page': '500',
            })
            check("custom range [C..B] -> B and C, not A",
                  set(order_numbers(resp)) == {'ZFILTERTEST-B', 'ZFILTERTEST-C'},
                  f"-> {order_numbers(resp)}")
            check("date_filter echoed back as 'custom'", resp.context['date_filter'] == 'custom')
            check("start_date/end_date echoed back",
                  resp.context['start_date'] == c_date and resp.context['end_date'] == b_date)

            print()
            print("=" * 70)
            print("3. Sorting")
            print("=" * 70)

            resp = get({'search': 'ZFILTERTEST', 'sort_by': 'amount_asc', 'per_page': '500'})
            check("amount_asc -> A(100), C(200), B(300)",
                  order_numbers(resp) == ['ZFILTERTEST-A', 'ZFILTERTEST-C', 'ZFILTERTEST-B'],
                  f"-> {order_numbers(resp)}")

            resp = get({'search': 'ZFILTERTEST', 'sort_by': 'amount_desc', 'per_page': '500'})
            check("amount_desc -> B(300), C(200), A(100)",
                  order_numbers(resp) == ['ZFILTERTEST-B', 'ZFILTERTEST-C', 'ZFILTERTEST-A'],
                  f"-> {order_numbers(resp)}")

            resp = get({'search': 'ZFILTERTEST', 'sort_by': 'order_number_asc', 'per_page': '500'})
            check("order_number_asc -> A, B, C",
                  order_numbers(resp) == ['ZFILTERTEST-A', 'ZFILTERTEST-B', 'ZFILTERTEST-C'],
                  f"-> {order_numbers(resp)}")

            resp = get({'search': 'ZFILTERTEST', 'sort_by': 'redirect_date_desc', 'per_page': '500'})
            check("redirect_date_desc (newest first) -> A, B, C",
                  order_numbers(resp) == ['ZFILTERTEST-A', 'ZFILTERTEST-B', 'ZFILTERTEST-C'],
                  f"-> {order_numbers(resp)}")

            resp = get({'search': 'ZFILTERTEST', 'sort_by': 'redirect_date_asc', 'per_page': '500'})
            check("redirect_date_asc (oldest first) -> C, B, A",
                  order_numbers(resp) == ['ZFILTERTEST-C', 'ZFILTERTEST-B', 'ZFILTERTEST-A'],
                  f"-> {order_numbers(resp)}")

            resp = get({'search': 'ZFILTERTEST', 'sort_by': 'not_a_real_sort', 'per_page': '500'})
            check("invalid sort_by falls back to redirect_date_desc",
                  resp.context['sort_by'] == 'redirect_date_desc')
            check("invalid sort_by still orders newest-first (falls back correctly)",
                  order_numbers(resp) == ['ZFILTERTEST-A', 'ZFILTERTEST-B', 'ZFILTERTEST-C'],
                  f"-> {order_numbers(resp)}")

            print()
            print("=" * 70)
            print("4. effective_redirect_date beats stale updated_at")
            print("=" * 70)

            # Touch order A's updated_at far in the past by saving a trivial
            # change — if date filtering used updated_at (like it used to)
            # this would wrongly drop A from "today".
            Order.objects.filter(pk=orders['A'].pk).update(
                updated_at=get_nepali_now() - timedelta(days=100)
            )
            resp = get({'search': 'ZFILTERTEST', 'date_range': 'today', 'per_page': '500'})
            check("today -> still finds A even though order.updated_at is stale",
                  order_numbers(resp) == ['ZFILTERTEST-A'],
                  f"-> {order_numbers(resp)}")

            raise RuntimeError('rollback')
    except RuntimeError as exc:
        if str(exc) != 'rollback':
            raise

    leftover = Order.objects.filter(order_number__startswith='ZFILTERTEST-').count()
    check("synthetic orders rolled back", leftover == 0, f"-> {leftover} left")

    print()
    print("=" * 70)
    if failures:
        print(f"RESULT: {len(failures)} check(s) FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    else:
        print("RESULT: all checks passed")
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
