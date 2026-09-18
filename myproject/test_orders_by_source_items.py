"""Verify the Today preset and the Items column on the Orders by Source report.

Two things changed on that report and both are server-side contracts the
template renders straight from:

  * `days=1` is a real preset ("Today"), so the analytics window has to be a
    single day and its growth comparison the day before — not silently fall
    back to the 30-day default.
  * every order row in the detail table carries an `items` list (name,
    variation, quantity, product type) plus `items_count` / `total_qty`, which
    is what replaced the removed Payment column.

    python test_orders_by_source_items.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

import json  # noqa: E402
from datetime import timedelta  # noqa: E402

from django.test import RequestFactory  # noqa: E402
from django.utils import timezone  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from dashboard.models import Order  # noqa: E402
from dashboard.views import (  # noqa: E402
    _orders_by_source_date_range,
    orders_by_source_analytics_data,
    orders_by_source_table_data,
)

factory = RequestFactory()
admin = CustomUser.objects.filter(is_superuser=True).first() or \
    CustomUser.objects.filter(role='administrator').first()
if admin is None:
    raise SystemExit('No administrator user found — cannot exercise the report views.')

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(('  PASS  ' if ok else '  FAIL  ') + f'{label}: {actual!r}' + ('' if ok else f' (expected {expected!r})'))
    if not ok:
        failures.append(label)


def ok(label, condition, detail=''):
    print(('  PASS  ' if condition else '  FAIL  ') + label + (f': {detail}' if detail else ''))
    if not condition:
        failures.append(label)


def call(view, query):
    request = factory.get('/x/?' + query)
    request.user = admin
    return json.loads(view(request).content)


print('\n=== days=1 is the "Today" preset, not a fallback ===')
request = factory.get('/x/?days=1')
dates_list, start_dt, end_dt, prev_start_dt, prev_end_dt = _orders_by_source_date_range(request)
today = timezone.localtime(timezone.now()).date()
check('window length in days', len(dates_list), 1)
check('window is today', dates_list[0], today)
check('comparison window starts yesterday',
      timezone.localtime(prev_start_dt).date(), today - timedelta(days=1))
ok('comparison window ends just before the window opens',
   prev_end_dt < start_dt and (start_dt - prev_end_dt) <= timedelta(seconds=1),
   f'{prev_end_dt} < {start_dt}')

# A junk preset still falls back to the 30-day default — 1 being valid must not
# have turned the whitelist into "anything goes".
_, _, _, _, _ = _orders_by_source_date_range(factory.get('/x/?days=1'))
junk_dates, _, _, _, _ = _orders_by_source_date_range(factory.get('/x/?days=3'))
check('unsupported preset still falls back to 30 days', len(junk_dates), 30)

analytics = call(orders_by_source_analytics_data, 'days=1')
check('analytics chart has one bucket', len(analytics['chart']['dates']), 1)
check('KPI range is a single day',
      analytics['totals']['date_from'], analytics['totals']['date_to'])

today_orders = Order.objects.filter(
    is_deleted=False, created_at__range=(start_dt, end_dt)
).count()
check("today's KPI order count matches the DB", analytics['totals']['total_orders'], today_orders)

table_today = call(orders_by_source_table_data, 'days=1&page_size=100')
check("today's table total matches the DB", table_today['total'], today_orders)

print('\n=== Detail rows carry their line items ===')
# Use a window wide enough to actually contain orders with items.
data = call(orders_by_source_table_data, 'days=90&page_size=50')
rows = data['rows']
ok('table returned rows to inspect', bool(rows), f'{len(rows)} rows')

if rows:
    ok('every row has an items list',
       all(isinstance(r.get('items'), list) for r in rows))
    ok('every row has items_count / total_qty',
       all('items_count' in r and 'total_qty' in r for r in rows))
    ok('items_count matches the length of items',
       all(r['items_count'] == len(r['items']) for r in rows))
    ok('total_qty is the sum of the line quantities',
       all(r['total_qty'] == sum(i['quantity'] for i in r['items']) for r in rows))

    line_keys = {'product_name', 'variation_name', 'product_type', 'quantity'}
    ok('every line carries name, variation, type and quantity',
       all(line_keys <= set(i) for r in rows for i in r['items']))
    ok('product_type is never blank (deleted products read as "simple")',
       all((i['product_type'] or '') != '' for r in rows for i in r['items']))

    with_items = [r for r in rows if r['items']]
    ok('at least one row lists a real product', bool(with_items))
    if with_items:
        sample = with_items[0]
        db_order = Order.objects.get(id=sample['id'])
        db_lines = [(it.product_name or 'Unknown', it.quantity or 0) for it in db_order.items.all()]
        api_lines = [(i['product_name'], i['quantity']) for i in sample['items']]
        check(f"row {sample['order_number']} lists exactly its DB line items", api_lines, db_lines)

    # The Payment column went away from the table, but the field stays in the
    # payload — the CSV export still writes it.
    ok('payment_status is still exported in the payload',
       all('payment_status' in r for r in rows))

print()
if failures:
    print(f'{len(failures)} CHECK(S) FAILED: ' + ', '.join(failures))
    raise SystemExit(1)
print('ALL CHECKS PASSED')
