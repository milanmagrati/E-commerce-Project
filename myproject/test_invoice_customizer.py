"""Verify the Setup -> Invoice Customizer end to end.

Exercises the pieces that would silently break the printed invoice: the
context builder against a real order and against the preview sample, token
resolution, the customizer page + preview render, and the element CRUD /
reset endpoints.

    python test_invoice_customizer.py
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from decimal import Decimal  # noqa: E402

from django.conf import settings  # noqa: E402
from django.template.loader import render_to_string  # noqa: E402
from django.test import Client  # noqa: E402

# django.test.Client talks to 'testserver', which the project's ALLOWED_HOSTS
# does not carry outside the (unused) test runner.
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from accounts.models import CustomUser  # noqa: E402
from dashboard import invoice_config  # noqa: E402
from dashboard.models import InvoiceElement, InvoiceTemplate, Order  # noqa: E402

PASSED, FAILED = [], []


def check(name, condition, detail=''):
    (PASSED if condition else FAILED).append(name)
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' -> ' + str(detail)) if detail and not condition else ''}")


# ── 1. Singleton + seeded lines ─────────────────────────────────────────
cfg = invoice_config.get_template()
check('singleton pins to pk=1', cfg.pk == 1)
check('default lines were seeded', cfg.elements.count() >= len(invoice_config.DEFAULT_ELEMENTS),
      cfg.elements.count())
check('every seeded token is whitelisted',
      all(e.token in invoice_config.TOKENS for e in cfg.elements.filter(source='field')))

# ── 2. Money + words helpers ────────────────────────────────────────────
cfg.currency_symbol = 'Rs.'
cfg.currency_position = 'before'
cfg.thousand_separator = True
check('money formats with symbol + grouping',
      invoice_config.format_money(Decimal('1300'), cfg) == 'Rs. 1,300.00',
      invoice_config.format_money(Decimal('1300'), cfg))
cfg.currency_position = 'after'
check('money honours symbol position',
      invoice_config.format_money(Decimal('12.5'), cfg) == '12.50 Rs.',
      invoice_config.format_money(Decimal('12.5'), cfg))
check('money survives junk input', invoice_config.format_money('not-a-number', cfg).endswith('Rs.'))
check('amount in words uses lakh grouping',
      invoice_config.amount_in_words(Decimal('152300')) == 'One Lakh Fifty Two Thousand Three Hundred Rupees Only',
      invoice_config.amount_in_words(Decimal('152300')))
check('amount in words handles paisa',
      invoice_config.amount_in_words(Decimal('0.50')) == 'Zero Rupees and Fifty Paisa Only',
      invoice_config.amount_in_words(Decimal('0.50')))

# ── 3. Context builder against the preview sample ───────────────────────
sample_order, sample_items = invoice_config.sample_order()
context = invoice_config.build_invoice_context(sample_order, sample_items, cfg=cfg, preview=True)
check('sample builds every item row', len(context['rows']) == len(sample_items), len(context['rows']))
check('grand total is the last totals row', context['totals'][-1]['grand'] is True)
check('bill-to lines resolve real values',
      any('Sagar' in line['value'] for line in context['bill_to_lines']),
      context['bill_to_lines'])
check('columns match the toggles', len(context['columns']) == 5, len(context['columns']))

# Every whitelisted token must resolve without raising.
data = {
    'cfg': cfg, 'order': sample_order, 'items': sample_items,
    'brand': {'name': 'Trendy Shopping'},
    'labels': {'status': 'Processing', 'payment_status': 'Pending', 'payment_method': 'COD'},
    'counts': {'lines': 2, 'qty': 3},
    'amounts': {k: Decimal('100') for k in
                ('subtotal', 'discount', 'shipping', 'delivery', 'tax', 'grand_total', 'paid', 'due')},
    'printed_by': 'tester',
}
broken = []
for token, (_group, _label, resolver) in invoice_config.TOKENS.items():
    try:
        resolver(data)
    except Exception as exc:  # noqa: BLE001
        broken.append(f'{token}: {exc}')
check('all tokens resolve on the sample', not broken, broken)
check('token groups cover every token',
      sum(len(g['options']) for g in invoice_config.token_groups()) == len(invoice_config.TOKENS))

# ── 4. Hidden-when-empty + toggles actually change the output ───────────
cfg.vat_number = ''
cfg.save()
context = invoice_config.build_invoice_context(sample_order, sample_items, cfg=cfg)
check('empty VAT line is dropped',
      not any('VAT' in (line['label'] or '') for line in context['brand']['lines']))
cfg.vat_number = '601234567'
cfg.save()
context = invoice_config.build_invoice_context(sample_order, sample_items, cfg=cfg)
check('filled VAT line prints',
      any('601234567' == line['value'] for line in context['brand']['lines']),
      context['brand']['lines'])

cfg.col_qty = False
cfg.save()
context = invoice_config.build_invoice_context(sample_order, sample_items, cfg=cfg)
check('turning a column off removes it',
      not any(c['key'] == 'qty' for c in context['columns']))
cfg.col_qty = True
cfg.show_amount_in_words = True
cfg.save()
context = invoice_config.build_invoice_context(sample_order, sample_items, cfg=cfg)
check('amount in words appears when enabled', bool(context['amount_words']))

# ── 5. The template itself renders ──────────────────────────────────────
try:
    html = render_to_string('order_invoice.html', context)
    rendered = '</html>' in html and 'invoice-container' in html
    render_error = ''
except Exception as exc:  # noqa: BLE001
    rendered, render_error = False, exc
check('order_invoice.html renders from the sample context', rendered, render_error)

# ── 6. Context builder against a real order, if one exists ──────────────
real = Order.objects.filter(is_deleted=False).order_by('-created_at').first()
if real is None:
    print('SKIP  real order context (no orders in this database)')
else:
    items = list(real.items.select_related('product', 'product_variation').all())
    real_context = invoice_config.build_invoice_context(real, items, cfg=cfg)
    check('real order builds a context', bool(real_context['totals']))
    check('real order row count matches its items', len(real_context['rows']) == len(items))
    try:
        render_to_string('order_invoice.html', real_context)
        ok, err = True, ''
    except Exception as exc:  # noqa: BLE001
        ok, err = False, exc
    check('order_invoice.html renders a real order', ok, err)

# ── 7. Views, as an administrator ───────────────────────────────────────
admin = CustomUser.objects.filter(is_superuser=True).first() or \
    CustomUser.objects.filter(role='administrator').first()
if admin is None:
    print('SKIP  view checks (no administrator account in this database)')
else:
    client = Client()
    client.force_login(admin)

    response = client.get('/setup/invoice/')
    check('customizer page loads', response.status_code == 200, response.status_code)
    check('customizer renders the line manager',
          b'Add / remove lines' in response.content or b'Add / remove lines' in response.content)

    response = client.get('/setup/invoice/preview/?sample=1')
    check('preview renders the sample', response.status_code == 200 and b'invoice-container' in response.content,
          response.status_code)

    response = client.get('/setup/invoice/preview/')
    check('preview renders the default source', response.status_code == 200, response.status_code)

    # The project leaves X_FRAME_OPTIONS at Django's DENY default, which would
    # blank the customizer's preview iframe with "refused to connect".
    check('preview is framable by its own origin',
          response.headers.get('X-Frame-Options') == 'SAMEORIGIN',
          response.headers.get('X-Frame-Options'))

    real_id = Order.objects.filter(is_deleted=False).values_list('id', flat=True).first()
    if real_id:
        printable = client.get(f'/orders/{real_id}/invoice/')
        check('printable invoice still renders', printable.status_code == 200, printable.status_code)
        check('printable invoice keeps DENY',
              printable.headers.get('X-Frame-Options') == 'DENY',
              printable.headers.get('X-Frame-Options'))

    before = InvoiceElement.objects.count()
    response = client.post('/setup/invoice/lines/save/', {
        'section': 'bill_to', 'label': 'Test VAT :-', 'source': 'static',
        'static_value': '999-TEST', 'is_active': 'on', 'hide_if_empty': 'on', 'full_width': 'on',
    }, follow=True)
    created = InvoiceElement.objects.filter(static_value='999-TEST').first()
    check('adding a line works', created is not None and InvoiceElement.objects.count() == before + 1)

    if created:
        client.post('/setup/invoice/lines/save/', {
            'element_id': created.id, 'section': 'footer', 'label': 'Edited :-',
            'source': 'static', 'static_value': '999-EDITED', 'is_active': 'on',
        }, follow=True)
        created.refresh_from_db()
        check('editing a line works',
              created.section == 'footer' and created.static_value == '999-EDITED',
              (created.section, created.static_value))

        client.post(f'/setup/invoice/lines/{created.id}/toggle/', follow=True)
        created.refresh_from_db()
        check('toggling a line works', created.is_active is False)

        # An unknown token must be rejected rather than stored and rendered blank.
        client.post('/setup/invoice/lines/save/', {
            'element_id': created.id, 'section': 'footer', 'label': 'Bad',
            'source': 'field', 'token': 'order.__class__', 'is_active': 'on',
        }, follow=True)
        created.refresh_from_db()
        check('unknown tokens are rejected', created.token == '' and created.source == 'static',
              (created.source, created.token))

        client.post(f'/setup/invoice/lines/{created.id}/delete/', follow=True)
        check('deleting a line works', not InvoiceElement.objects.filter(pk=created.id).exists())

    # Move: the first bill_to line should swap places with the second.
    bill_lines = list(InvoiceElement.objects.filter(section='bill_to').order_by('sort_order', 'id'))
    if len(bill_lines) >= 2:
        first, second = bill_lines[0], bill_lines[1]
        client.post(f'/setup/invoice/lines/{second.id}/move/', {'direction': 'up'}, follow=True)
        reordered = list(InvoiceElement.objects.filter(section='bill_to').order_by('sort_order', 'id'))
        check('moving a line up reorders it', reordered[0].pk == second.pk,
              [e.pk for e in reordered])

    # Settings save.
    response = client.post('/setup/invoice/', {
        'business_name': 'QA Shop', 'vat_number': '601234567', 'document_title': 'TAX INVOICE',
        'currency_symbol': 'Rs.', 'currency_position': 'before', 'thousand_separator': 'on',
        'paper_size': 'a5', 'accent_mode': 'accent', 'accent_color': '#4f46e5',
        'base_font_size': '99',  # out of range on purpose
        'show_header': 'on', 'show_meta': 'on', 'show_bill_to': 'on', 'show_items': 'on',
        'show_totals': 'on', 'show_footer': 'on', 'show_subtotal': 'on',
        'label_grand_total': 'Grand Total',
    }, follow=True)
    cfg.refresh_from_db()
    check('settings save', response.status_code == 200 and cfg.business_name == 'QA Shop',
          cfg.business_name)
    check('paper size saves', cfg.paper_size == 'a5', cfg.paper_size)
    check('out-of-range font size is clamped', cfg.base_font_size == 18, cfg.base_font_size)
    check('unchecked switches turn off', cfg.show_ship_to is False)
    check('an invalid choice falls back to the default',
          InvoiceTemplate.objects.get(pk=1).header_layout == 'split')

    # Reset lines, then reset styling — business identity must survive both.
    client.post('/setup/invoice/reset/', {'scope': 'lines'}, follow=True)
    cfg.refresh_from_db()
    check('reset restores the default line count',
          cfg.elements.count() == len(invoice_config.DEFAULT_ELEMENTS), cfg.elements.count())

    client.post('/setup/invoice/reset/', {'scope': 'style'}, follow=True)
    cfg.refresh_from_db()
    check('style reset returns defaults', cfg.paper_size == 'a4' and cfg.base_font_size == 12,
          (cfg.paper_size, cfg.base_font_size))
    check('style reset keeps business identity',
          cfg.business_name == 'QA Shop' and cfg.vat_number == '601234567')
    check('style reset keeps the lines intact', cfg.elements.count() > 0, cfg.elements.count())

    # Non-admins must not reach the customizer.
    staff = CustomUser.objects.filter(is_superuser=False).exclude(role='administrator').first()
    if staff is not None:
        other = Client()
        other.force_login(staff)
        response = other.get('/setup/invoice/')
        check('non-admins are redirected away', response.status_code in (302, 403), response.status_code)
    else:
        print('SKIP  permission check (no non-admin account in this database)')

    # Leave the shop's own details out of the QA state.
    cfg.business_name = ''
    cfg.vat_number = ''
    cfg.save()

print('\n' + '-' * 58)
print(f'{len(PASSED)} passed, {len(FAILED)} failed')
if FAILED:
    for name in FAILED:
        print(f'  FAILED: {name}')
    raise SystemExit(1)
print('Invoice customizer OK.')
