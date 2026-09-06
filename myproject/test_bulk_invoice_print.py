"""Verify bulk invoice printing from the orders list.

Covers the pieces that would quietly ruin a print run: which ids the sheet
accepts, whose orders a given user is allowed to print, that every selected
order really lands on the sheet, that the shared stylesheet is emitted once
rather than once per order, that the page breaks are tagged so a run does not
end on a blank sheet — and that splitting the invoice into partials left the
single-order invoice and the customizer preview rendering exactly as before.

    python test_bulk_invoice_print.py
"""

import os
import re

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402
from django.template.loader import render_to_string  # noqa: E402
from django.test import Client, RequestFactory  # noqa: E402
from django.urls import reverse  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from accounts.models import CustomUser  # noqa: E402
from dashboard import bulk_invoice_views, invoice_config  # noqa: E402
from dashboard.models import Order  # noqa: E402

PASSED, FAILED = [], []


def check(name, condition, detail=''):
    (PASSED if condition else FAILED).append(name)
    print(f"{'PASS' if condition else 'FAIL'}  {name}"
          f"{(' -> ' + str(detail)) if detail and not condition else ''}")


factory = RequestFactory()


# ── 1. Which ids the sheet accepts ──────────────────────────────────────

def ids_from_post(values):
    return bulk_invoice_views._requested_ids(factory.post('/orders/bulk-invoice/',
                                                          {'order_ids': values}))


def ids_from_get(query):
    return bulk_invoice_views._requested_ids(factory.get('/orders/bulk-invoice/' + query))


check('POSTed ids parse in the order they were ticked',
      ids_from_post(['7', '3', '11']) == [7, 3, 11], ids_from_post(['7', '3', '11']))
check('a duplicated id prints once, not twice',
      ids_from_post(['4', '4', '9', '4']) == [4, 9], ids_from_post(['4', '4', '9', '4']))
check('junk ids are dropped rather than crashing the sheet',
      ids_from_post(['5', '', 'abc', '-2', '6.5', '8']) == [5, 8],
      ids_from_post(['5', '', 'abc', '-2', '6.5', '8']))
check('a ?ids=1,2,3 link parses the same way',
      ids_from_get('?ids=12,5,12,x,7') == [12, 5, 7], ids_from_get('?ids=12,5,12,x,7'))
check('an empty selection yields nothing to print', ids_from_post([]) == [])

# A POST that carries no ids still falls back to the query string, which is
# what the bulk-action form's no-JavaScript redirect relies on.
request = factory.post('/orders/bulk-invoice/?ids=3,4', {})
check('a POST with no ids falls back to the query string',
      bulk_invoice_views._requested_ids(request) == [3, 4])


# ── 2. The route exists and is wired to this view ───────────────────────
try:
    url = reverse('orders_bulk_invoice')
    resolved = url == '/orders/bulk-invoice/'
except Exception as exc:  # pragma: no cover - only when the URL is missing
    url, resolved = None, exc
check('orders_bulk_invoice resolves', resolved is True, resolved)


# ── 3. Real orders on a real sheet ──────────────────────────────────────
orders = list(
    Order.objects.filter(is_deleted=False)
    .select_related('status_setup', 'payment_setup', 'payment_status_setup')
    .order_by('-created_at')[:4]
)
admin = (CustomUser.objects.filter(role='administrator').first()
         or CustomUser.objects.filter(is_superuser=True).first())

if not orders:
    print('SKIP  sheet render (no orders in this database)')
elif admin is None:
    print('SKIP  sheet render (no administrator account in this database)')
else:
    client = Client()
    client.force_login(admin)
    ids = [order.id for order in orders]
    response = client.post(url, {'order_ids': [str(i) for i in ids]})
    check('the sheet renders', response.status_code == 200, response.status_code)

    html = response.content.decode('utf-8', 'replace') if response.status_code == 200 else ''

    check('every selected order is on the sheet',
          html.count('class="bulk-sheet"') == len(orders),
          f"{html.count('class=\"bulk-sheet\"')} sheets for {len(orders)} orders")
    check('each sheet carries a full invoice',
          html.count('class="invoice-container"') == len(orders),
          html.count('class="invoice-container"'))
    check('the stylesheet is emitted once, not once per order',
          html.count('<style>') == 1 and html.count('@page') == 1,
          f"style={html.count('<style>')} page={html.count('@page')}")
    check('the per-invoice action bar is suppressed',
          'btn-action btn-print' not in html)
    check('the sheet has one toolbar of its own', html.count('class="bulk-bar"') == 1)
    check('the last sheet is tagged so a run does not end on a blank page',
          'is-last' in html)
    check('every order number appears on the sheet',
          all((order.order_number or '') in html for order in orders))

    for order in orders:
        for item in order.items.all():
            if item.product_name:
                check(f'{order.order_number} shows its line "{item.product_name[:24]}"',
                      item.product_name in html)
                break

    # Layout is a closed set — a forged value falls back rather than reaching
    # the body's data-layout attribute.
    response = client.post(url, {'order_ids': [str(ids[0])], 'layout': 'flow'})
    check('an asked-for layout is honoured',
          'data-layout="flow"' in response.content.decode('utf-8', 'replace'))
    response = client.post(url, {'order_ids': [str(ids[0])], 'layout': '"><script>'})
    check('a forged layout falls back to one-per-page',
          'data-layout="page"' in response.content.decode('utf-8', 'replace'))

    # An empty or all-invalid selection must bounce back to the list, never 500.
    response = client.post(url, {'order_ids': []})
    check('an empty selection redirects to the order list',
          response.status_code == 302, response.status_code)
    response = client.post(url, {'order_ids': ['99999999']})
    check('a selection of orders that do not exist redirects too',
          response.status_code == 302, response.status_code)

    # ── 4. Selection is not authorisation ───────────────────────────────
    stranger = (CustomUser.objects
                .filter(is_superuser=False, is_staff=False)
                .exclude(role__in=('admin', 'manager', 'administrator'))
                .exclude(id__in=[o.created_by_id for o in orders if o.created_by_id])
                .first())
    if stranger is None:
        print('SKIP  scoping check (no non-admin account without these orders)')
    else:
        request = factory.post(url, {'order_ids': [str(i) for i in ids]})
        request.user = stranger
        visible = bulk_invoice_views._visible_orders(request, ids)
        check("a plain staff user cannot print another user's orders",
              all(order.created_by_id == stranger.id for order in visible),
              [o.order_number for o in visible])

        request.user = admin
        check('an administrator prints every ticked order',
              len(bulk_invoice_views._visible_orders(request, ids)) == len(orders))

    # ── 5. Oversized selections are trimmed, and say so ─────────────────
    check('the sheet caps how much it will build in one go',
          isinstance(bulk_invoice_views.MAX_INVOICES, int)
          and 0 < bulk_invoice_views.MAX_INVOICES <= 500,
          bulk_invoice_views.MAX_INVOICES)

    if len(orders) >= 2:
        real_cap = bulk_invoice_views.MAX_INVOICES
        bulk_invoice_views.MAX_INVOICES = 1
        try:
            response = client.post(url, {'order_ids': [str(i) for i in ids]})
            capped = response.content.decode('utf-8', 'replace')
        finally:
            bulk_invoice_views.MAX_INVOICES = real_cap
        check('a selection past the cap prints only what fits',
              capped.count('class="bulk-sheet"') == 1,
              capped.count('class="bulk-sheet"'))
        check('and the sheet says how many were left out',
              'left out' in capped and f'{len(orders) - 1} more order' in capped)
    else:
        print('SKIP  cap check (needs at least two orders)')


# ── 6. The split left the single-order invoice intact ───────────────────
sample_order, sample_items = invoice_config.sample_order()
context = invoice_config.build_invoice_context(sample_order, sample_items)

single_error = ''
try:
    single = render_to_string('order_invoice.html', context)
except Exception as exc:
    single, single_error = '', exc
check('order_invoice.html still renders after the split', bool(single), single_error)
check('the single invoice still carries its own styles and document',
      '<style>' in single and 'invoice-container' in single
      and 'Black Bottle Shampoo' in single)
check('the single invoice keeps its Print button', 'btn-action btn-print' in single)

# The bulk sheet reaches the same partial through an inclusion tag rather than
# a context-inheriting include. If those two ever diverge, a key added to
# build_invoice_context renders on one surface and vanishes from the other.
from django.template import Context, Template  # noqa: E402

direct = render_to_string('invoice/_document.html', context)
tagged, tagged_error = '', ''
try:
    tagged = Template(
        '{% load dashboard_extras %}{% invoice_document inv %}'
    ).render(Context({'inv': context}))
except Exception as exc:
    tagged_error = exc


def squash(html):
    return re.sub(r'\s+', ' ', html).strip()


check('the tag renders exactly what the direct include renders',
      bool(tagged) and squash(tagged) == squash(direct), tagged_error)


# ── 6b. The shop's own CSS is emitted once, and last ────────────────────
# It has to outrank the bulk sheet's own chrome, and a stylesheet pasted twice
# would run any @keyframes or counter in it twice over.
marker = '/*__custom_css_probe__*/'
cfg = invoice_config.get_template()
was = cfg.custom_css
cfg.custom_css = marker
cfg.save(update_fields=['custom_css'])
try:
    single_css = render_to_string('order_invoice.html', dict(context, cfg=cfg))
    check('the single invoice emits custom CSS exactly once',
          single_css.count(marker) == 1, single_css.count(marker))
    if orders and admin is not None:
        sheet = Client()
        sheet.force_login(admin)
        body = sheet.post(url, {'order_ids': [str(orders[0].id)]}).content.decode('utf-8', 'replace')
        check('the bulk sheet emits custom CSS exactly once',
              body.count(marker) == 1, body.count(marker))
        check("and after the sheet's own rules, so the shop still has the last word",
              body.index(marker) > body.index('.bulk-sheet {'))
finally:
    cfg.custom_css = was
    cfg.save(update_fields=['custom_css'])


# ── 7. The customizer preview still works ───────────────────────────────
if admin is not None:
    client = Client()
    client.force_login(admin)
    response = client.get('/setup/invoice/preview/')
    check('the invoice customizer preview still renders',
          response.status_code == 200, response.status_code)


# ── 8. The order list offers the feature ────────────────────────────────
list_html = open(
    os.path.join('dashboard', 'templates', 'orders_list.html'), encoding='utf-8'
).read()
check('the order list has a Print Invoices button', 'printInvoicesButton' in list_html)
check('the button posts to the bulk invoice route',
      "{% url 'orders_bulk_invoice' %}" in list_html)
check('printing opens its own tab', "form.target = '_blank'" in list_html)
check('the bulk-action menu offers printing too',
      'value="print_invoices"' in list_html)
check('choosing it from the menu is intercepted rather than POSTed',
      "action === 'print_invoices'" in list_html)


print('\n' + '-' * 58)
print(f'{len(PASSED)} passed, {len(FAILED)} failed')
if FAILED:
    for name in FAILED:
        print(f'  FAILED: {name}')
    raise SystemExit(1)
print('Bulk invoice printing OK.')
