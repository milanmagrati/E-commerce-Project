"""
Verify the two Possible Redirection fixes in dashboard.views:

  1. An RTV that has already been redirected is no longer listed on the
     Possible Redirection page (it belongs to Redirect Orders from that point
     on). Previously it stayed listed unless the user picked a filter, which
     invited redirecting the same physical package twice.

  2. A candidate order is only surfaced when its items correspond EXACTLY to
     the RTV package — every product, every quantity, nothing extra on either
     side — so the "Order Products" and "RTV Product Ref" columns can never
     disagree. Previously one matching item was enough, so RTV Product Ref
     listed products the candidate order never contained.

Everything is created inside a transaction that is rolled back at the end —
nothing is left behind.

Run:  python test_possible_redirection_exclusions.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS and '*' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from dashboard.models import Order, OrderActivityLog, OrderItem, RTVOrder  # noqa: E402
from dashboard.timezone_utils import get_nepali_now  # noqa: E402

setup_test_environment()

BRANCH = 'ZZTESTBRANCH'
NCM_ID = 99900001

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
        'total_amount': 1000,
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
    user = CustomUser.objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found — cannot exercise the view.")
        return 1

    client = Client()
    client.force_login(user)

    rtv = RTVOrder.objects.create(
        order_id=NCM_ID,
        vendor=user,
        vendor_return=True,
        to_branch=BRANCH,
        # At the branch, so the page lists it at all — this file is about
        # product matching, not the at-branch status gate (see
        # test_possible_redirection_status_gate.py for that).
        last_status='Arrived',
        product_description='2x ZZ Test Serum, 1x ZZ Test Vitamin',
        rtv_marked_at=get_nepali_now(),
        rtv_marked_at_source=RTVOrder.SOURCE_WEBHOOK,
        receiver_name='RTV Receiver',
        receiver_phone='9811111111',
    )

    # Exact counterpart of the RTV package — must be offered as a candidate.
    exact = make_order('ZZ-EXACT', [('ZZ Test Serum', 2), ('ZZ Test Vitamin', 1)])
    # Holds only one of the two products — the old matcher accepted this and then
    # printed "ZZ Test Serum" in RTV Product Ref against an order that never had it.
    partial = make_order('ZZ-PARTIAL', [('ZZ Test Vitamin', 1)])
    # Right products, wrong quantity.
    wrong_qty = make_order('ZZ-QTY', [('ZZ Test Serum', 5), ('ZZ Test Vitamin', 1)])
    # Everything from the package plus an extra item the package can't fulfil.
    extra_item = make_order(
        'ZZ-EXTRA',
        [('ZZ Test Serum', 2), ('ZZ Test Vitamin', 1), ('ZZ Test Shampoo', 1)],
    )
    # Dispatched order with identical products - must NOT be offered!
    dispatched_order = make_order(
        'ZZ-DISPATCHED',
        [('ZZ Test Serum', 2), ('ZZ Test Vitamin', 1)],
        order_status='dispatched',
    )

    print("\n[1] Exact-correspondence matching")
    resp = client.get('/orders/possible-redirection/')
    check("page renders", resp.status_code == 200, f"(got {resp.status_code})")

    entry = entry_for(resp, NCM_ID)
    check("the RTV is listed as a redirection candidate", entry is not None)
    if entry is None:
        return 1

    matched_numbers = [r['order'].order_number for r in entry['matching_rows']]
    check("order with identical products+quantities IS offered",
          'ZZ-EXACT' in matched_numbers, f"(got {matched_numbers})")
    check("order missing one of the RTV's products is NOT offered",
          'ZZ-PARTIAL' not in matched_numbers, f"(got {matched_numbers})")
    check("order with a quantity mismatch is NOT offered",
          'ZZ-QTY' not in matched_numbers, f"(got {matched_numbers})")
    check("order carrying an extra product is NOT offered",
          'ZZ-EXTRA' not in matched_numbers, f"(got {matched_numbers})")
    check("dispatched order with identical products is NOT offered",
          'ZZ-DISPATCHED' not in matched_numbers, f"(got {matched_numbers})")

    print("\n[2] Every displayed RTV Product Ref belongs to the matched order")
    for row in entry['matching_rows']:
        order_names = {(p['item'].product_name or '').lower() for p in row['products']}
        for p in row['products']:
            ref = (p['rtv_ref'] or '').lower()
            check(f"{row['order'].order_number}: ref {p['rtv_ref']!r} sits beside "
                  f"its own item {p['item'].product_name!r}",
                  any(n and (n in ref or ref.strip('0123456789x× ') in n)
                      for n in order_names))
        check(f"{row['order'].order_number}: one RTV ref per order item, none left blank",
              len(row['products']) == row['order'].items.count()
              and all(p['rtv_ref'] for p in row['products']))

    print("\n[3] Already-redirected RTVs leave this page")
    rtv.comment = '[REDIRECTED] handled'
    rtv.save(update_fields=['comment'])

    resp2 = client.get('/orders/possible-redirection/')
    check("redirected RTV is no longer listed",
          entry_for(resp2, NCM_ID) is None)
    check("it is still counted under 'Already Redirected'",
          resp2.context['already_redirected'] >= 1,
          f"(got {resp2.context['already_redirected']})")

    print("\n[3b] Detection survives NCM status polling overwriting ncm_status")
    # A linked local order carries the redirect; NCM polling then rewrites
    # ncm_status back to a live transit status. The activity log and
    # order_status must still keep the RTV off this page.
    rtv.comment = ''
    rtv.save(update_fields=['comment'])
    linked = make_order('ZZ-LINKED', [('ZZ Test Serum', 2), ('ZZ Test Vitamin', 1)],
                        ncm_order_id=NCM_ID)
    OrderActivityLog.objects.create(
        order=linked, action_type='redirected', field_name='ncm_status',
        old_value='', new_value='redirected', description='test redirect',
    )
    linked.ncm_status = 'Pickup Complete'   # what polling would write back
    linked.order_status = 'confirmed'       # and a status sync could reset this too
    linked.save(update_fields=['ncm_status', 'order_status'])

    resp2b = client.get('/orders/possible-redirection/')
    check("RTV with a 'redirected' activity log stays off the page even after "
          "ncm_status was overwritten",
          entry_for(resp2b, NCM_ID) is None)

    linked.delete()
    rtv.comment = '[REDIRECTED] handled'
    rtv.save(update_fields=['comment'])

    print("\n[3c] The exact case from the screenshot, via the local-name fallback")
    # NCM #23975706 had an EMPTY product_description, so RTV Product Ref was
    # built from the linked local order's items ("matched from linked local
    # order"): Hair Growth serum x1 + Black Bottle shampoo x1. Candidate order
    # T12725 wanted only Black Bottle shampoo x1, yet was offered as a match and
    # rendered with the serum listed beside it — a redirect that would ship a
    # serum nobody ordered. This path never sets product_description, so it must
    # be exercised separately from the description-driven cases above.
    fallback_ncm_id = NCM_ID + 1
    fallback_rtv = RTVOrder.objects.create(
        order_id=fallback_ncm_id,
        vendor=user,
        vendor_return=True,
        to_branch=BRANCH,
        last_status='Arrived',           # at the branch, so it is a listed candidate
        product_description='',          # empty — forces the local-name fallback
        rtv_marked_at=get_nepali_now(),
        rtv_marked_at_source=RTVOrder.SOURCE_WEBHOOK,
    )
    # The returned package, as reconstructed from the RTV'd order's own items.
    returned = make_order(
        'ZZ-RETURNED',
        [('ZZ Hair Growth serum', 1), ('ZZ Black Bottle shampoo', 1)],
        ncm_order_id=fallback_ncm_id,
    )
    # The candidate that used to be offered: only one of the two products.
    shampoo_only = make_order('ZZ-SHAMPOO-ONLY', [('ZZ Black Bottle shampoo', 1)])

    resp3c = client.get('/orders/possible-redirection/')
    fb_entry = entry_for(resp3c, fallback_ncm_id)
    fb_matches = [r['order'].order_number for r in fb_entry['matching_rows']] if fb_entry else []
    check("shampoo-only order is NOT offered for a package that also holds a serum",
          'ZZ-SHAMPOO-ONLY' not in fb_matches, f"(got {fb_matches})")
    check("with no other candidate, the RTV drops off the page entirely "
          "instead of showing a mismatched suggestion",
          fb_entry is None)

    # ...and it comes back the moment a genuinely equivalent order exists.
    make_order('ZZ-BOTH', [('ZZ Hair Growth serum', 1), ('ZZ Black Bottle shampoo', 1)])
    resp3d = client.get('/orders/possible-redirection/')
    fb_entry2 = entry_for(resp3d, fallback_ncm_id)
    check("an order wanting BOTH products is still offered", fb_entry2 is not None)
    if fb_entry2:
        rows = fb_entry2['matching_rows']
        check("only the equivalent order is offered",
              [r['order'].order_number for r in rows] == ['ZZ-BOTH'],
              f"(got {[r['order'].order_number for r in rows]})")
        for p in rows[0]['products']:
            check(f"ref {p['rtv_ref']!r} names the same product as the order item "
                  f"{p['item'].product_name!r}",
                  p['item'].product_name.lower() in (p['rtv_ref'] or '').lower())

    returned.delete()
    fallback_rtv.delete()

    print("\n[4] Redirect Orders page still shows the redirected order")
    exact.order_status = 'redirected'
    exact.ncm_status = 'redirected'
    exact.save(update_fields=['order_status', 'ncm_status'])

    resp3 = client.get('/orders/redirect-orders/')
    check("redirect orders page renders", resp3.status_code == 200,
          f"(got {resp3.status_code})")
    numbers = [e['order'].order_number for e in resp3.context['redirect_entries']]
    check("the redirected order appears there", 'ZZ-EXACT' in numbers,
          f"(got {numbers[:10]})")

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
