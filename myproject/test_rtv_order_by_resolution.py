"""Verify the RTV list's "Order By" attribution.

An RTV row carries only NCM's order id, so dashboard.views.resolve_rtv_order_by
reconstructs who took the order from three sources. This script builds one RTV
per source inside a transaction that is always rolled back, and asserts each
resolves to the right staff member with the right confidence label.

    python test_rtv_order_by_resolution.py
"""
import os
import re
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.db import transaction
from django.test import RequestFactory
from django.utils import timezone

from accounts.models import CustomUser
from dashboard.models import Order, RTVOrder
from dashboard.views import resolve_rtv_order_by, ncm_rtvs_list, _normalize_np_phone
from ncm.models import NCMBulkLog, NCMBulkLogOrder


class Rollback(Exception):
    pass


def make_order(staff, suffix, phone, created_at=None, **extra):
    order = Order.objects.create(
        order_number=f'RTVTEST-{suffix}',
        customer_name=f'Test Customer {suffix}',
        customer_phone=phone,
        shipping_address='Kathmandu',
        order_from='test',
        created_by=staff,
        **extra,
    )
    if created_at:
        Order.objects.filter(pk=order.pk).update(created_at=created_at)
        order.refresh_from_db()
    return order


def make_rtv(vendor, ncm_id, phone='', name='', marked_at=None, ncm_created=None):
    return RTVOrder.objects.create(
        order_id=ncm_id,
        comment='test',
        vendor=vendor,
        receiver_phone=phone,
        receiver_name=name,
        rtv_marked_at=marked_at,
        ncm_created_date=ncm_created,
    )


def run():
    staff_a, staff_b, staff_c = list(CustomUser.objects.all()[:3])
    vendor = CustomUser.objects.filter(is_superuser=True).first() or staff_a
    now = timezone.now()

    # NCM ids far above anything real so they cannot collide with live data.
    NCM_EXACT, NCM_BULK, NCM_PHONE, NCM_NONE = 990000001, 990000002, 990000003, 990000004

    failures = []

    def check(label, condition, detail=''):
        if condition:
            print(f'  PASS  {label}')
        else:
            print(f'  FAIL  {label} {detail}')
            failures.append(label)

    print('phone normalization')
    check('+977 prefix stripped', _normalize_np_phone('+977-9812345678') == '9812345678')
    check('spaces/dashes ignored', _normalize_np_phone('98 1234-5678') == '9812345678')
    check('977 prefix without plus', _normalize_np_phone('9779812345678') == '9812345678')
    check('too short rejected', _normalize_np_phone('12345') == '')
    check('empty rejected', _normalize_np_phone(None) == '')

    print('\nresolution layers')
    # 1. exact — the NCM id stamped on the order
    o_exact = make_order(staff_a, 'EXACT', '9800000001', ncm_order_id=NCM_EXACT)
    rtv_exact = make_rtv(vendor, NCM_EXACT, '9800000001', 'Exact Guy', marked_at=now)

    # 2. bulk log — order never got the id written back, the send log kept it
    o_bulk = make_order(staff_b, 'BULK', '9800000002')
    batch = NCMBulkLog.objects.create(batch_number=NCMBulkLog.generate_batch_number())
    NCMBulkLogOrder.objects.create(
        batch=batch, order=o_bulk, order_number=o_bulk.order_number,
        customer_phone=o_bulk.customer_phone, ncm_order_id=NCM_BULK,
    )
    rtv_bulk = make_rtv(vendor, NCM_BULK, '9800000002', 'Bulk Guy', marked_at=now)

    # 3. phone — nothing links the ids; two orders share the phone, and the RTV
    #    must attach to the one placed closest before NCM created the order.
    old = now - timezone.timedelta(days=40)
    recent = now - timezone.timedelta(days=2)
    o_phone_old = make_order(staff_c, 'PHONE-OLD', '+977-9800000003', created_at=old)
    o_phone_new = make_order(staff_a, 'PHONE-NEW', '9800000003', created_at=recent)
    rtv_phone = make_rtv(
        vendor, NCM_PHONE, '977 9800000003', 'Phone Guy',
        marked_at=now, ncm_created=now - timezone.timedelta(days=30),
    )

    # 4. nothing at all
    rtv_none = make_rtv(vendor, NCM_NONE, '9899999999', 'Ghost', marked_at=now)

    resolved = resolve_rtv_order_by([rtv_exact, rtv_bulk, rtv_phone, rtv_none])

    r = resolved.get(NCM_EXACT) or {}
    check('exact: right staff', r.get('username') == staff_a.username, f'got {r.get("username")}')
    check('exact: labelled exact', r.get('match') == 'exact', f'got {r.get("match")}')
    check('exact: right order', r.get('order_number') == o_exact.order_number)

    r = resolved.get(NCM_BULK) or {}
    check('bulk log: right staff', r.get('username') == staff_b.username, f'got {r.get("username")}')
    check('bulk log: labelled bulk_log', r.get('match') == 'bulk_log', f'got {r.get("match")}')
    check('bulk log: right order', r.get('order_number') == o_bulk.order_number)

    r = resolved.get(NCM_PHONE) or {}
    check('phone: labelled phone', r.get('match') == 'phone', f'got {r.get("match")}')
    check('phone: picked order closest before the NCM date',
          r.get('order_number') == o_phone_old.order_number,
          f'got {r.get("order_number")}, wanted {o_phone_old.order_number}')
    check('phone: right staff', r.get('username') == staff_c.username, f'got {r.get("username")}')
    check('phone: flagged ambiguous', r.get('ambiguous') is True)
    check('phone: normalizes across formats', bool(r.get('name')))

    check('unmatched RTV stays unresolved', NCM_NONE not in resolved)

    # bulk-log link must not beat an exact link on the same id
    Order.objects.filter(pk=o_bulk.pk).update(ncm_order_id=NCM_BULK, created_by=staff_c)
    o_bulk.refresh_from_db()
    r2 = resolve_rtv_order_by([rtv_bulk]).get(NCM_BULK) or {}
    check('exact link wins over bulk log', r2.get('match') == 'exact', f'got {r2.get("match")}')
    Order.objects.filter(pk=o_bulk.pk).update(ncm_order_id=None, created_by=staff_b)

    print('\nrendered page')
    rf = RequestFactory()
    req = rf.get('/orders/rtvs/', {'search': '99000000'})  # the four test ids
    req.user = CustomUser.objects.filter(is_superuser=True).first() or staff_a
    html = ncm_rtvs_list(req).content.decode('utf-8', 'replace')
    check('Order By header present', '<th>Order By</th>' in html)
    # A row must have exactly as many cells as the table has headers — an
    # extra or missing <td> silently shifts every column after it.
    table = html[html.find('id="rtvTable"'):]
    header_count = len(re.findall(r'<th[\s>]', table[:table.find('</thead>')]))
    first_row = table[table.find('<tbody>'):]
    first_row = first_row[:first_row.find('</tr>')]
    cell_count = len(re.findall(r'<td[\s>]', first_row))
    check('cells line up with headers', header_count == cell_count,
          f'{header_count} headers vs {cell_count} cells')
    check('exact match renders as confirmed',
          'Linked by NCM order id' in html)
    check('phone match renders as probable',
          'order-by-name probable' in html and 'Probable: matched on' in html)
    check('unmatched row renders the fallback', 'Not in system' in html)

    req2 = rf.get('/orders/rtvs/', {'search': staff_a.username})
    req2.user = req.user
    html2 = ncm_rtvs_list(req2).content.decode('utf-8', 'replace')
    check('search by staff username finds the exact-linked RTV',
          str(NCM_EXACT) in html2, 'RTV missing from staff search')

    req3 = rf.get('/orders/rtvs/', {'search': 'Ghost'})
    req3.user = req.user
    html3 = ncm_rtvs_list(req3).content.decode('utf-8', 'replace')
    check('search by receiver name finds the unlinked RTV', str(NCM_NONE) in html3)

    return failures


if __name__ == '__main__':
    failures = []
    try:
        with transaction.atomic():
            failures = run()
            raise Rollback()
    except Rollback:
        print('\n(all test rows rolled back)')

    if failures:
        print(f'\n{len(failures)} FAILED: ' + ', '.join(failures))
        sys.exit(1)
    print('\nAll checks passed.')
