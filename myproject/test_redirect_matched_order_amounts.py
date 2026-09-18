"""
Verify the Possible Redirection page quotes the amount that will actually be
collected from the matched (destination) order — not the returned order's stale
total, and not the gross total of a partially paid order.

The bug: an order totalling Rs.1800 with Rs.200 already paid showed "Rs.1800.00"
in the match list, and clicking "Use" left the redirect modal holding the
RETURNED order's figures (Rs.1400 total / Rs.1400 COD). NCM would then have been
told to collect Rs.1400 from a customer who owed Rs.1600.

Everything is created inside a transaction that is rolled back at the end.

Run:  python test_redirect_matched_order_amounts.py
"""
import io
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS and '*' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from decimal import Decimal  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from dashboard.models import Order, OrderItem, RTVOrder  # noqa: E402
from dashboard.timezone_utils import get_nepali_now  # noqa: E402

setup_test_environment()

BRANCH = 'ZZAMOUNTBRANCH'
RTV_NCM_ID = 99922001
PARTIAL_ORDER = 'ZZ-AMT-PARTIAL'
FULL_ORDER = 'ZZ-AMT-FULL'
RETURNED_ORDER = 'ZZ-AMT-RETURNED'
PRODUCT = 'ZZ Amount Shampoo'

failures = []


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def make_order(number, total, partial=False, paid=None, remaining=None):
    order = Order.objects.create(
        order_number=number,
        customer_name=f'Customer {number}',
        customer_phone='9847645850',
        shipping_address='Test address',
        branch_city=BRANCH,
        order_status='confirmed',
        status='confirmed',
        discount_amount=Decimal('0.00'),
        shipping_charge=Decimal('500.00'),
        total_amount=Decimal(total),
        is_partial_payment=partial,
        partial_amount_paid=Decimal(paid) if paid is not None else None,
        remaining_amount=Decimal(remaining) if remaining is not None else None,
    )
    OrderItem.objects.create(
        order=order, product_name=PRODUCT, quantity=1,
        price=Decimal('1300.00'), total=Decimal('1300.00'),
    )
    return order


def button_attrs(body, order_number):
    """Pull the data-* attributes off the 'Use' button for one matched order."""
    marker = 'data-order-num="%s"' % order_number
    idx = body.find(marker)
    if idx == -1:
        return {}
    start = body.rfind('<button', 0, idx)
    end = body.find('>', idx)
    chunk = body[start:end]
    attrs = {}
    for part in chunk.split('data-')[1:]:
        name, _, rest = part.partition('="')
        value, _, _ = rest.partition('"')
        attrs[name.strip()] = value
    return attrs


def main():
    user = CustomUser.objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found — cannot exercise the view.")
        return 1

    print("\n[1] Order.amount_due is the collectible figure, not the gross total")
    partial = make_order(PARTIAL_ORDER, '1800.00', partial=True, paid='200.00', remaining='1600.00')
    full = make_order(FULL_ORDER, '1800.00')
    check("partially paid order is due its remaining balance",
          partial.amount_due == Decimal('1600.00'), "(got %s)" % partial.amount_due)
    check("fully unpaid order is due its total",
          full.amount_due == Decimal('1800.00'), "(got %s)" % full.amount_due)

    # Rows exist with the partial flag set and no remaining figure behind it.
    orphan = Order(total_amount=Decimal('900.00'), is_partial_payment=True,
                   partial_amount_paid=None, remaining_amount=None)
    check("partial flag with nothing paid and no remaining figure is due the total",
          orphan.amount_due == Decimal('900.00'), "(got %s)" % orphan.amount_due)
    derived = Order(total_amount=Decimal('900.00'), is_partial_payment=True,
                    partial_amount_paid=Decimal('300.00'), remaining_amount=None)
    check("missing remaining figure is derived from total minus paid",
          derived.amount_due == Decimal('600.00'), "(got %s)" % derived.amount_due)
    overpaid = Order(total_amount=Decimal('900.00'), is_partial_payment=True,
                     partial_amount_paid=Decimal('1000.00'), remaining_amount=Decimal('-100.00'))
    check("an overpaid row never quotes a negative amount to collect",
          overpaid.amount_due == Decimal('0.00'), "(got %s)" % overpaid.amount_due)

    print("\n[2] The match list quotes the collectible amount")
    RTVOrder.objects.create(
        order_id=RTV_NCM_ID,
        vendor=user,
        vendor_return=True,
        to_branch=BRANCH,
        # Not 'Pending' — this test exercises the "Use" button's money
        # data-attributes, which only render once the RTV is redirect-eligible
        # (see _rtv_is_redirect_eligible in dashboard/views.py). Status gating
        # itself is covered by test_possible_redirection_status_gate.py.
        last_status='Returned to Warehouse',
        product_description='1x %s' % PRODUCT,
        rtv_marked_at=get_nepali_now(),
        rtv_marked_at_source=RTVOrder.SOURCE_WEBHOOK,
        receiver_name='Returned Customer',
        receiver_phone='9765304656',
    )

    client = Client()
    client.force_login(user)
    resp = client.get('/orders/possible-redirection/')
    check("page renders", resp.status_code == 200, "(got %s)" % resp.status_code)
    if resp.status_code != 200:
        return 1

    entry = next((e for e in resp.context['rtv_entries']
                  if e['ncm_order_id'] == RTV_NCM_ID), None)
    check("the RTV is listed", entry is not None)
    if entry is None:
        return 1
    matched_numbers = [r['order'].order_number for r in entry['matching_rows']]
    check("both candidate orders are offered",
          PARTIAL_ORDER in matched_numbers and FULL_ORDER in matched_numbers,
          "(got %s)" % matched_numbers)

    body = resp.content.decode('utf-8', 'replace')
    check("the collectible Rs.1600.00 is rendered", 'Rs.1600.00' in body)
    check("the partial payment is disclosed on the row",
          'Rs.200.00 paid of Rs.1800.00' in body)

    print("\n[3] The 'Use' button carries the matched order's own money")
    p_attrs = button_attrs(body, PARTIAL_ORDER)
    f_attrs = button_attrs(body, FULL_ORDER)
    check("partially paid button found", bool(p_attrs), "(got %s)" % p_attrs)
    check("fully unpaid button found", bool(f_attrs), "(got %s)" % f_attrs)
    check("partial button carries the matched total",
          p_attrs.get('total') == '1800.00', "(got %s)" % p_attrs.get('total'))
    check("partial button carries the partial flag",
          p_attrs.get('is-partial') == '1', "(got %s)" % p_attrs.get('is-partial'))
    check("partial button carries the amount already paid",
          p_attrs.get('paid') == '200.00', "(got %s)" % p_attrs.get('paid'))
    check("the partial button's figures reconstruct the COD the form will post",
          Decimal(p_attrs.get('total') or '0') - Decimal(p_attrs.get('paid') or '0')
          == Decimal('1600.00'),
          "(got %s - %s)" % (p_attrs.get('total'), p_attrs.get('paid')))
    check("partial button carries the shipping charge for the form",
          p_attrs.get('shipping') == '500.00', "(got %s)" % p_attrs.get('shipping'))
    check("unpaid button is not flagged partial",
          f_attrs.get('is-partial') == '0', "(got %s)" % f_attrs.get('is-partial'))
    check("unpaid button's COD is the full total",
          f_attrs.get('total') == '1800.00', "(got %s)" % f_attrs.get('total'))
    check("the matched order id still rides along for the Redirected stamp",
          p_attrs.get('order-id') == str(partial.id), "(got %s)" % p_attrs.get('order-id'))

    check("the amounts are rendered unlocalized so the number inputs can parse them",
          ',' not in (p_attrs.get('total') or ',') and ',' not in (p_attrs.get('paid') or ','))

    print("\n[4] The redirect form wires those amounts through to NCM's COD box")
    check("the total field recalculates COD as it is edited",
          'id="rf_total_amount"' in body and 'oninput="calcRemaining()"' in body)
    check("calcRemaining pushes the figure into the NCM redirect COD field",
          "ncmCodEl.value = codAmount.toFixed(2)" in body)
    check("one helper defines the COD figure for banner, toast and NCM field",
          'function currentCodAmount()' in body and body.count('currentCodAmount()') >= 3)
    check("the modal explains which order the amounts came from",
          'id="rf_amount_source_note"' in body)

    print("\n[5] Saving the redirect stores a consistent partial-payment state")
    # Toggling partial on with a blank "paid" box used to leave the RETURNED
    # order's old figures in place, so the stored balance disagreed with the COD
    # the form had already sent to NCM.
    returned = Order.objects.create(
        order_number=RETURNED_ORDER,
        customer_name='Returned Customer',
        customer_phone='9765304656',
        shipping_address='Old address',
        branch_city=BRANCH,
        order_status='returned',
        status='returned',
        total_amount=Decimal('1400.00'),
        shipping_charge=Decimal('100.00'),
        is_partial_payment=True,
        partial_amount_paid=Decimal('900.00'),
        remaining_amount=Decimal('500.00'),
    )

    def save_redirect(paid):
        return client.post(
            '/api/orders/%d/redirect-save/' % returned.id,
            {
                'customer_name': partial.customer_name,
                'customer_phone': partial.customer_phone,
                'shipping_address': partial.shipping_address,
                'branch_city': BRANCH,
                'total_amount': '1800.00',
                'shipping_charge': '500.00',
                'discount_amount': '0.00',
                'is_partial_payment': 'true',
                'partial_amount_paid': paid,
                'send_to_logistics': '',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    resp = save_redirect('')          # blank on purpose
    check("save-only redirect succeeds", resp.status_code == 200, "(got %s)" % resp.status_code)
    returned.refresh_from_db()
    check("the new total is stored",
          returned.total_amount == Decimal('1800.00'), "(got %s)" % returned.total_amount)
    check("a blank paid box means nothing paid, not the previous payment",
          returned.partial_amount_paid == Decimal('0.00'),
          "(got %s)" % returned.partial_amount_paid)
    check("the stored balance agrees with the total that was saved",
          returned.remaining_amount == Decimal('1800.00'),
          "(got %s)" % returned.remaining_amount)
    check("amount_due on the saved order matches what NCM would be told",
          returned.amount_due == Decimal('1800.00'), "(got %s)" % returned.amount_due)

    resp = save_redirect('200.00')
    check("second save succeeds", resp.status_code == 200, "(got %s)" % resp.status_code)
    returned.refresh_from_db()
    check("the matched partial payment carries over to the balance",
          returned.remaining_amount == Decimal('1600.00'),
          "(got %s)" % returned.remaining_amount)
    check("the COD that reaches NCM is the remaining balance",
          returned.amount_due == Decimal('1600.00'), "(got %s)" % returned.amount_due)

    save_redirect('2000.00')          # more than the order is worth
    returned.refresh_from_db()
    check("an overpayment never stores a negative balance",
          returned.remaining_amount == Decimal('0.00'),
          "(got %s)" % returned.remaining_amount)

    print("\n[6] No logistics payload or audit row bills the gross total")
    # Pick and Drop was still sending codAmount = total_amount, so a partially
    # paid customer was charged again for what they had already handed over.
    # amount_due is the one definition of that figure; nothing may reintroduce
    # its own.
    cod_files = [
        'dashboard/views.py', 'dashboard/bulk_batch.py',
        'ncm/views.py', 'pick_and_drop/views.py',
    ]
    offenders = []
    for rel in cod_files:
        with io.open(rel, encoding='utf-8') as fh:
            for lineno, line in enumerate(fh, 1):
                code = line.split('#', 1)[0]
                names_cod = any(k in code for k in ('codAmount', 'cod_amount', 'cod_charge'))
                if names_cod and 'total_amount' in code and 'ncm_data.get' not in code:
                    offenders.append('%s:%d %s' % (rel, lineno, code.strip()))
    check("no COD figure is taken straight from the gross total",
          not offenders, "(got %s)" % offenders)
    uses = 0
    for rel in cod_files:
        with io.open(rel, encoding='utf-8') as fh:
            uses += fh.read().count('order.amount_due')
    check("the COD sites route through Order.amount_due",
          uses >= 6, "(got %d)" % uses)

    print()
    if failures:
        print("RESULT: %d check(s) failed" % len(failures))
        for name in failures:
            print("  - %s" % name)
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
        RTVOrder.objects.filter(order_id=RTV_NCM_ID).count()
        + Order.objects.filter(
            order_number__in=[PARTIAL_ORDER, FULL_ORDER, RETURNED_ORDER]).count()
    )
    print("cleanup: %d synthetic row(s) left behind (expected 0)" % leftover)
    raise SystemExit(exit_code or (1 if leftover else 0))
