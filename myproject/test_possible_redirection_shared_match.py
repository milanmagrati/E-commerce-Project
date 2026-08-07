"""
Verify that a confirmed order matching more than one RTV is offered under EVERY
matching RTV row, flagged as contested rather than silently hidden.

Before this, the second (older) RTV had its only candidate filtered out and
rendered a dead "1 match claimed above" badge — a grey span with no click
target, so the operator could neither see which order it meant nor choose to
send that package to that customer instead.

Everything is created inside a transaction that is rolled back at the end.

Run:  python test_possible_redirection_shared_match.py
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
from dashboard.models import Order, OrderItem, RTVOrder  # noqa: E402
from dashboard.timezone_utils import get_nepali_now  # noqa: E402

setup_test_environment()

BRANCH = 'ZZSHAREDBRANCH'
NEWER_NCM_ID = 99911001   # sorts first (most recent rtv_marked_at)
OLDER_NCM_ID = 99911002

failures = []


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def entry_for(resp, ncm_id):
    for entry in resp.context['rtv_entries']:
        if entry['ncm_order_id'] == ncm_id:
            return entry
    return None


def main():
    user = CustomUser.objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found — cannot exercise the view.")
        return 1

    client = Client()
    client.force_login(user)
    now = get_nepali_now()

    # Two RTV packages holding the same product, at the same branch. Both can
    # legitimately be redirected to the one confirmed order that wants it.
    for ncm_id, marked_at in ((NEWER_NCM_ID, now), (OLDER_NCM_ID, now - timedelta(days=1))):
        RTVOrder.objects.create(
            order_id=ncm_id,
            vendor=user,
            vendor_return=True,
            to_branch=BRANCH,
            last_status='Pending',
            product_description='1x ZZ Shared Serum',
            rtv_marked_at=marked_at,
            rtv_marked_at_source=RTVOrder.SOURCE_WEBHOOK,
            receiver_name=f'Receiver {ncm_id}',
            receiver_phone='9811111111',
        )

    shared = Order.objects.create(
        order_number='ZZ-SHARED',
        customer_name='Shared Customer',
        customer_phone='9800000000',
        shipping_address='Test address',
        branch_city=BRANCH,
        order_status='confirmed',
        status='confirmed',
        total_amount=1000,
    )
    OrderItem.objects.create(
        order=shared, product_name='ZZ Shared Serum', quantity=1, price=100, total=100,
    )

    print("\n[1] Both RTV rows offer the shared order")
    resp = client.get('/orders/possible-redirection/')
    check("page renders", resp.status_code == 200, f"(got {resp.status_code})")

    newer = entry_for(resp, NEWER_NCM_ID)
    older = entry_for(resp, OLDER_NCM_ID)
    check("the newer RTV is listed", newer is not None)
    check("the older RTV is listed", older is not None)
    if newer is None or older is None:
        return 1

    newer_numbers = [r['order'].order_number for r in newer['matching_rows']]
    older_numbers = [r['order'].order_number for r in older['matching_rows']]
    check("newer RTV offers the shared order",
          'ZZ-SHARED' in newer_numbers, f"(got {newer_numbers})")
    check("older RTV also offers it instead of hiding it",
          'ZZ-SHARED' in older_numbers, f"(got {older_numbers})")
    check("older RTV's match is openable (matching_count > 0)",
          older['matching_count'] >= 1, f"(got {older['matching_count']})")

    print("\n[2] The contest is labelled, not silent")
    newer_row = next(r for r in newer['matching_rows'] if r['order'].order_number == 'ZZ-SHARED')
    older_row = next(r for r in older['matching_rows'] if r['order'].order_number == 'ZZ-SHARED')
    check("the first row claiming it carries no 'also suggested' note",
          newer_row['claimed_by'] == [], f"(got {newer_row['claimed_by']})")
    check("the later row names the RTV above that shares it",
          older_row['claimed_by'] == [NEWER_NCM_ID], f"(got {older_row['claimed_by']})")
    check("newer RTV is not marked contested",
          newer['contested_count'] == 0, f"(got {newer['contested_count']})")
    check("older RTV is marked contested",
          older['contested_count'] == 1, f"(got {older['contested_count']})")

    print("\n[3] The page renders the cross-reference the operator can click")
    body = resp.content.decode('utf-8', 'replace')
    check("no dead 'claimed above' badge remains",
          'claimed above' not in body)
    check("the 'Also suggested for' cross-reference is rendered",
          'Also suggested for' in body)
    check("it links back to the contesting RTV row",
          f'focusRtvRow({NEWER_NCM_ID})' in body)
    check("the row-level 'shared' warning badge is rendered",
          '1 shared' in body)

    print("\n[4] Redirecting one of them frees nothing for the other")
    # The claim is real only once the order is redirected: it stops being
    # 'confirmed' and drops out of the candidate pool for every RTV.
    shared.order_status = 'redirected'
    shared.status = 'redirected'
    shared.save(update_fields=['order_status', 'status'])

    resp2 = client.get('/orders/possible-redirection/')
    newer2 = entry_for(resp2, NEWER_NCM_ID)
    older2 = entry_for(resp2, OLDER_NCM_ID)
    check("once redirected, the order is gone from the newer RTV",
          newer2 is None or 'ZZ-SHARED' not in
          [r['order'].order_number for r in newer2['matching_rows']])
    check("once redirected, the order is gone from the older RTV",
          older2 is None or 'ZZ-SHARED' not in
          [r['order'].order_number for r in older2['matching_rows']])

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
    leftover = (
        RTVOrder.objects.filter(order_id__in=[NEWER_NCM_ID, OLDER_NCM_ID]).count()
        + Order.objects.filter(order_number='ZZ-SHARED').count()
    )
    print(f"cleanup: {leftover} synthetic row(s) left behind (expected 0)")
    raise SystemExit(exit_code or (1 if leftover else 0))
