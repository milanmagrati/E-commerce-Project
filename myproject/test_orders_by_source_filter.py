"""Verify the page-wide Sources filter on the Orders by Source report.

Checks that `sources_filtered=1&sources=<name>...` narrows both report
endpoints consistently: KPI totals, per-source shares/ranks, and the order
detail table's row count all have to agree with the unfiltered breakdown.

    python test_orders_by_source_filter.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

import json  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from dashboard.views import (  # noqa: E402
    orders_by_source_analytics_data,
    orders_by_source_table_data,
)

DAYS = 90
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


def call(view, **params):
    query = f'days={DAYS}'
    for key, value in params.items():
        if key == 'sources':
            query += ''.join(f'&sources={v}' for v in value)
        else:
            query += f'&{key}={value}'
    request = factory.get('/x/?' + query)
    request.user = admin
    return json.loads(view(request).content)


print(f'\n=== Baseline (last {DAYS} days, no source filter) ===')
base = call(orders_by_source_analytics_data)
base_table = call(orders_by_source_table_data, page_size=100)
universe = {x['source']: x['count'] for x in base['all_sources']}
print(f"  total_orders={base['totals']['total_orders']}  sources={universe}")

if len(universe) < 2:
    raise SystemExit('Need at least 2 distinct sources in this window to test filtering.')

check('all_sources covers every ranked source',
      sorted(universe), sorted(r['source'] for r in base['ranking']))
check('unfiltered table total matches KPI total',
      base_table['total'], base['totals']['total_orders'])

# Keep every source but the largest one.
kept = sorted(universe, key=lambda s: -universe[s])[1:]
dropped = sorted(universe, key=lambda s: -universe[s])[0]
expected_orders = sum(universe[s] for s in kept)
expected_revenue = round(
    sum(r['revenue'] for r in base['ranking'] if r['source'] in kept), 2)

print(f'\n=== Filtered to {kept} (dropping "{dropped}") ===')
filt = call(orders_by_source_analytics_data, sources_filtered=1, sources=kept)
filt_table = call(orders_by_source_table_data, sources_filtered=1, sources=kept, page_size=100)

check('KPI total orders', filt['totals']['total_orders'], expected_orders)
check('KPI active sources', filt['totals']['total_sources'], len(kept))
check('KPI total revenue', filt['totals']['total_revenue'], expected_revenue)
check('dropped source absent from ranking', dropped in [r['source'] for r in filt['ranking']], False)
check('dropped source absent from trend series', dropped in filt['chart']['sources'], False)
check('modal universe still lists every source', sorted(x['source'] for x in filt['all_sources']), sorted(universe))
check('ranks renumbered 1..N', [r['rank'] for r in filt['ranking']], list(range(1, len(kept) + 1)))
check('shares sum to 100%', round(sum(r['share_pct'] for r in filt['ranking'])), 100)
check('detail table row count', filt_table['total'], expected_orders)
check('detail table has no dropped-source rows',
      any(r['source'] == dropped for r in filt_table['rows']), False)

print('\n=== None selected ===')
none_sel = call(orders_by_source_analytics_data, sources_filtered=1)
none_table = call(orders_by_source_table_data, sources_filtered=1, page_size=100)
check('KPI total orders is 0', none_sel['totals']['total_orders'], 0)
check('ranking empty', none_sel['ranking'], [])
check('modal universe still populated', len(none_sel['all_sources']), len(universe))
check('detail table empty', none_table['total'], 0)

print('\n=== Dropdown narrows within the selection ===')
one = kept[0]
inter = call(orders_by_source_table_data, sources_filtered=1, sources=kept, source=one, page_size=100)
check(f'source={one} within selection', inter['total'], universe[one])
outside = call(orders_by_source_table_data, sources_filtered=1, sources=kept, source=dropped, page_size=100)
check(f'source={dropped} outside selection yields nothing', outside['total'], 0)

print('\n' + ('ALL CHECKS PASSED' if not failures else f'{len(failures)} FAILED: {failures}'))
raise SystemExit(1 if failures else 0)
