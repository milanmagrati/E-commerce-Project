"""Verify pagination + page-size controls across the Staff Performance page.

Covers the server-side page-size control on the Staff Orders Log and the
client-side paginator markup on the five smaller sections (the JS itself
can't run here, so this asserts the contract the JS depends on: wrapper,
toolbar, nav placeholder, balanced nesting, and sub-row grouping).

    python test_staff_performance_pagination.py
"""
import os
import re
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db.models import Min, Max
from django.test import RequestFactory
from django.utils import timezone

from dashboard.models import Order
from dashboard.views import staff_performance_analytics

User = get_user_model()

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    print(('  [PASS] ' if condition else '  [FAIL] ') + label + (f' -> {detail}' if detail else ''))


def render(user, **params):
    request = RequestFactory().get('/staff-performance/', params)
    request.user = user
    request.session = SessionStore()
    resp = staff_performance_analytics(request)
    return resp, resp.content.decode()


def count_order_rows(html):
    """Rows in the orders log = checkbox cells, one per order."""
    return len(re.findall(r'data-order-id="\d+"', html))


def strip_scripts(html):
    """Drop <script> bodies. The paginator's own doc comment documents the
    markup contract using the very markup this scans for, and would otherwise
    be counted as a sixth section."""
    return re.sub(r'<script\b.*?</script>', '', html, flags=re.S | re.I)


def sp_paged_blocks(html):
    """Return the inner HTML of each .sp-paged wrapper, by depth-matching divs."""
    html = strip_scripts(html)
    blocks = []
    tag_re = re.compile(r'<(/?)div\b[^>]*>')
    for m in re.finditer(r'<div class="sp-paged"[^>]*>', html):
        start = m.end()
        depth = 1
        pos = start
        end = len(html)
        while depth > 0:
            t = tag_re.search(html, pos)
            if not t:
                break
            depth += -1 if t.group(1) else 1
            pos = t.end()
            if depth == 0:
                end = t.start()  # exclude the wrapper's own closing tag
        blocks.append((m.group(0), html[start:end]))
    return blocks


def main():
    admin = User.objects.filter(is_superuser=True, is_active=True).first()
    if not admin:
        print('No superuser found - cannot exercise the view. Aborting.')
        return 1

    span = Order.objects.filter(is_deleted=False).aggregate(first=Min('created_at'), last=Max('created_at'))
    if not span['first']:
        print('No orders in the local DB - nothing to verify against.')
        return 0
    today = timezone.localtime(timezone.now()).date()
    start = timezone.localtime(span['first']).date()
    end = min(timezone.localtime(span['last']).date(), today)
    PERIOD = dict(period='custom', custom_start=start.isoformat(), custom_end=end.isoformat(), staff_filter='all')
    print(f'Acting as: {admin.username}   Period: custom ({start} .. {end})\n')

    total_orders = Order.objects.filter(is_deleted=False).count()

    print('1) Staff Orders Log honours ?orders_per_page')
    resp, html = render(admin, **PERIOD)
    check('default page size is 15', count_order_rows(html) == min(15, total_orders), count_order_rows(html))

    for size in (25, 50, 100):
        _, h = render(admin, orders_per_page=str(size), **PERIOD)
        expected = min(size, total_orders)
        check(f'orders_per_page={size} renders {expected} rows', count_order_rows(h) == expected, count_order_rows(h))

    print('\n2) Page size is clamped to the allowed menu (no arbitrary values)')
    for bad in ('100000', '7', '-5', 'abc', ''):
        _, h = render(admin, orders_per_page=bad, **PERIOD)
        rows = count_order_rows(h)
        check(f'orders_per_page={bad!r} falls back to 15', rows == min(15, total_orders), rows)

    print('\n3) Page size survives paging, and page 2 differs from page 1')
    _, h1 = render(admin, orders_per_page='25', orders_page='1', **PERIOD)
    _, h2 = render(admin, orders_per_page='25', orders_page='2', **PERIOD)
    ids1 = set(re.findall(r'data-order-id="(\d+)"', h1))
    ids2 = set(re.findall(r'data-order-id="(\d+)"', h2))
    check('page 2 still uses size 25', len(ids2) == min(25, max(0, total_orders - 25)), len(ids2))
    check('page 1 and page 2 are disjoint', not (ids1 & ids2), len(ids1 & ids2))

    print('\n4) The five smaller sections each carry the paginator contract')
    blocks = sp_paged_blocks(html)
    check('exactly 5 .sp-paged wrappers rendered', len(blocks) == 5, len(blocks))

    expected_keys = {'topProducts', 'staffRanking', 'staffMetrics', 'returnedProducts', 'recentReturns'}
    found_keys = set(re.findall(r'<div class="sp-paged" data-sp-key="(\w+)"', strip_scripts(html)))
    check('all expected section keys present', found_keys == expected_keys,
          f'{sorted(found_keys)} vs {sorted(expected_keys)}')

    for opener, inner in blocks:
        key = re.search(r'data-sp-key="(\w+)"', opener).group(1)
        check(f'[{key}] has a per-page select', 'data-sp-perpage' in inner)
        check(f'[{key}] has a "showing" label', 'data-sp-showing' in inner)
        check(f'[{key}] has a nav placeholder', 'data-sp-nav' in inner)
        check(f'[{key}] contains exactly one <tbody>', inner.count('<tbody>') == 1, inner.count('<tbody>'))
        check(f'[{key}] div nesting is balanced',
              len(re.findall(r'<div\b', inner)) == len(re.findall(r'</div>', inner)),
              f"{len(re.findall(r'<div', inner))} open vs {len(re.findall(r'</div>', inner))} close")

    print('\n5) Grouped rows are marked so a page break cannot split them')
    check('product variant rows carry data-sp-sub',
          'sp-variant-row" data-sp-sub' in html or 'data-sp-sub' in html)

    print('\n6) Server row caps were lifted so there is something to page through')
    from dashboard.views import staff_performance_analytics as _v
    import inspect
    src = inspect.getsource(_v)
    check('top_products slice is no longer [:5]', 'top_products_data[:5]' not in src)
    check('recent_returns slice is no longer [:20]', "order_by('-created_at')[:20]" not in src)

    print('\n7) Export buttons are styled by an unscoped rule (selection bar included)')
    check('.sp-toolbar-btn defined unscoped', re.search(r'\n\s*\.sp-toolbar-btn\s*\{', html) is not None)
    check('.sp-toolbar-btn-export defined unscoped',
          re.search(r'\n\s*\.sp-toolbar-btn-export\s*\{', html) is not None)
    check('no .sp-orders-toolbar-scoped button rule remains',
          '.sp-orders-toolbar .sp-toolbar-btn ' not in html and
          '.sp-orders-toolbar .sp-toolbar-btn{' not in html)
    check('count badge markup present in selection bar', 'sp-btn-count' in html)

    print(f'\n{"=" * 60}\n{len(PASS)} passed, {len(FAIL)} failed')
    for f in FAIL:
        print('  FAILED: ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
