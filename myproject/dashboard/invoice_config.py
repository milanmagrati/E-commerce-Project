"""Invoice rendering configuration.

Everything the printable invoice needs to draw itself is assembled here, so
both the real invoice view (`order_invoice`) and the customizer's live preview
render from exactly the same context. Nothing in `order_invoice.html` reaches
into the ORM any more — it walks the structures this module returns.

Three pieces:

* :data:`TOKENS` — the whitelist of data a custom line may print. A saved
  element stores a token string, never an attribute path, so nothing on the
  page can dereference arbitrary model internals.
* :data:`DEFAULT_ELEMENTS` — the stock invoice lines, seeded as real rows so
  staff can rename, reorder or delete them like any line they add.
* :func:`build_invoice_context` — resolves a template + an order into the flat
  dictionaries the invoice template iterates over.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.utils import timezone

# ---------------------------------------------------------------------------
#  Money / number helpers
# ---------------------------------------------------------------------------

_ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
         'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
         'Seventeen', 'Eighteen', 'Nineteen']
_TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _dec(value, default='0'):
    """Best-effort Decimal — an unparseable field must never 500 an invoice."""
    if value is None or value == '':
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _two_digits(n):
    if n < 20:
        return _ONES[n]
    tens, rest = divmod(n, 10)
    return _TENS[tens] + (f" {_ONES[rest]}" if rest else '')


def _three_digits(n):
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_two_digits(rest))
    return ' '.join(parts)


def amount_in_words(value):
    """Nepali/Indian grouping (crore · lakh · thousand) for the total line."""
    amount = _dec(value)
    negative = amount < 0
    amount = abs(amount)
    rupees = int(amount)
    paisa = int((amount - rupees) * 100)

    if rupees == 0:
        words = 'Zero'
    else:
        chunks = []
        crore, rest = divmod(rupees, 10_000_000)
        lakh, rest = divmod(rest, 100_000)
        thousand, rest = divmod(rest, 1_000)
        if crore:
            chunks.append(f"{_three_digits(crore) if crore < 1000 else str(crore)} Crore")
        if lakh:
            chunks.append(f"{_two_digits(lakh)} Lakh")
        if thousand:
            chunks.append(f"{_three_digits(thousand)} Thousand")
        if rest:
            chunks.append(_three_digits(rest))
        words = ' '.join(chunks)

    out = f"{'Minus ' if negative else ''}{words} Rupees"
    if paisa:
        out += f" and {_two_digits(paisa)} Paisa"
    return out + ' Only'


def format_money(value, cfg):
    """Format an amount using the template's currency symbol/position/grouping."""
    amount = _dec(value)
    quantized = f"{amount:,.2f}" if cfg.thousand_separator else f"{amount:.2f}"
    symbol = (cfg.currency_symbol or '').strip()
    if not symbol:
        return quantized
    if cfg.currency_position == 'after':
        return f"{quantized} {symbol}"
    return f"{symbol} {quantized}"


# ---------------------------------------------------------------------------
#  Token whitelist
# ---------------------------------------------------------------------------
#  Each entry: token -> (group, human label, resolver(data) -> str)
#  `data` is the dict assembled in build_invoice_context(); resolvers must be
#  total — return '' rather than raising when something is missing.

def _g(obj, name, default=''):
    value = getattr(obj, name, None)
    return default if value in (None, '') else value


def _fmt_dt(value, fmt):
    if not value:
        return ''
    try:
        return timezone.localtime(value).strftime(fmt)
    except (ValueError, TypeError):
        try:
            return value.strftime(fmt)
        except Exception:
            return ''


TOKENS = {
    # ── Order ────────────────────────────────────────────────────────────
    'order.number': ('Order', 'Invoice / order number',
                     lambda d: f"{d['cfg'].invoice_number_prefix}{_g(d['order'], 'order_number')}"),
    'order.date': ('Order', 'Order date',
                   lambda d: _fmt_dt(_g(d['order'], 'created_at', None), '%d %b %Y')),
    'order.time': ('Order', 'Order time',
                   lambda d: _fmt_dt(_g(d['order'], 'created_at', None), '%I:%M %p')),
    'order.datetime': ('Order', 'Order date + time',
                       lambda d: _fmt_dt(_g(d['order'], 'created_at', None), '%d %b %Y, %I:%M %p')),
    'order.status': ('Order', 'Order status', lambda d: d['labels']['status']),
    'order.payment_status': ('Order', 'Payment status', lambda d: d['labels']['payment_status']),
    'order.payment_method': ('Order', 'Payment method', lambda d: d['labels']['payment_method']),
    'order.tracking_number': ('Order', 'Tracking number', lambda d: _g(d['order'], 'tracking_number')),
    'order.logistics': ('Order', 'Logistics partner', lambda d: (_g(d['order'], 'logistics') or '').upper()),
    'order.source': ('Order', 'Order source (order from)', lambda d: _g(d['order'], 'order_from')),
    'order.dispatch_date': ('Order', 'Dispatch date',
                            lambda d: _fmt_dt(_g(d['order'], 'dispatch_date', None), '%d %b %Y')),
    'order.delivered_at': ('Order', 'Delivered on',
                           lambda d: _fmt_dt(_g(d['order'], 'delivered_at', None), '%d %b %Y')),
    'order.weight': ('Order', 'Package weight (kg)',
                     lambda d: (f"{_dec(_g(d['order'], 'package_weight', 0)):.2f} kg"
                                if _dec(_g(d['order'], 'package_weight', 0)) else '')),
    'order.item_count': ('Order', 'Number of line items', lambda d: str(d['counts']['lines'] or '')),
    'order.total_qty': ('Order', 'Total quantity', lambda d: str(d['counts']['qty'] or '')),
    'order.delivery_type': ('Order', 'NCM delivery type', lambda d: _g(d['order'], 'ncm_delivery_type')),
    'order.destination_branch': ('Order', 'Destination branch',
                                 lambda d: _g(d['order'], 'ncm_destination_branch')
                                 or _g(d['order'], 'pnd_destination_branch')),
    'order.notes': ('Order', 'Order notes', lambda d: _g(d['order'], 'notes')),
    'order.admin_notes': ('Order', 'Admin notes', lambda d: _g(d['order'], 'admin_notes')),

    # ── Money ────────────────────────────────────────────────────────────
    'money.subtotal': ('Amounts', 'Subtotal', lambda d: format_money(d['amounts']['subtotal'], d['cfg'])),
    'money.discount': ('Amounts', 'Discount', lambda d: format_money(d['amounts']['discount'], d['cfg'])),
    'money.shipping': ('Amounts', 'Shipping charge', lambda d: format_money(d['amounts']['shipping'], d['cfg'])),
    'money.delivery': ('Amounts', 'Delivery charge', lambda d: format_money(d['amounts']['delivery'], d['cfg'])),
    'money.tax': ('Amounts', 'Tax amount', lambda d: format_money(d['amounts']['tax'], d['cfg'])),
    'money.tax_percent': ('Amounts', 'Tax percent',
                          lambda d: f"{_dec(_g(d['order'], 'tax_percent', 0)):.2f}%"),
    'money.grand_total': ('Amounts', 'Grand total', lambda d: format_money(d['amounts']['grand_total'], d['cfg'])),
    'money.paid': ('Amounts', 'Amount paid', lambda d: format_money(d['amounts']['paid'], d['cfg'])),
    'money.due': ('Amounts', 'Amount due', lambda d: format_money(d['amounts']['due'], d['cfg'])),
    'money.cod': ('Amounts', 'COD collected', lambda d: format_money(_g(d['order'], 'cod_collected', 0), d['cfg'])),
    'money.in_words': ('Amounts', 'Grand total in words',
                       lambda d: amount_in_words(d['amounts']['grand_total'])),

    # ── Customer ─────────────────────────────────────────────────────────
    'customer.name': ('Customer', 'Customer name', lambda d: _g(d['order'], 'customer_name')),
    'customer.phone': ('Customer', 'Customer phone', lambda d: _g(d['order'], 'customer_phone')),
    'customer.email': ('Customer', 'Customer email', lambda d: _g(d['order'], 'customer_email')),
    'customer.address': ('Customer', 'Shipping address', lambda d: _g(d['order'], 'shipping_address')),
    'customer.city': ('Customer', 'City / branch city', lambda d: _g(d['order'], 'branch_city')),
    'customer.landmark': ('Customer', 'Landmark', lambda d: _g(d['order'], 'landmark')),

    # ── Business ─────────────────────────────────────────────────────────
    'company.name': ('Business', 'Business name', lambda d: d['brand']['name']),
    'company.tagline': ('Business', 'Tagline', lambda d: d['cfg'].business_tagline),
    'company.vat': ('Business', 'VAT / PAN number', lambda d: d['cfg'].vat_number),
    'company.registration': ('Business', 'Registration number', lambda d: d['cfg'].registration_number),
    'company.phone': ('Business', 'Business phone', lambda d: d['cfg'].business_phone),
    'company.alt_phone': ('Business', 'Alternate phone', lambda d: d['cfg'].business_alt_phone),
    'company.email': ('Business', 'Business email', lambda d: d['cfg'].business_email),
    'company.website': ('Business', 'Website', lambda d: d['cfg'].business_website),
    'company.address': ('Business', 'Business address', lambda d: d['cfg'].business_address),

    # ── System ───────────────────────────────────────────────────────────
    'system.printed_by': ('System', 'Printed by (current user)', lambda d: d['printed_by']),
    'system.printed_at': ('System', 'Printed at',
                          lambda d: _fmt_dt(timezone.now(), '%d %b %Y, %I:%M %p')),
    'system.today': ('System', "Today's date", lambda d: _fmt_dt(timezone.now(), '%d %b %Y')),
}


def token_groups():
    """Tokens bucketed by group, for the customizer's <optgroup> select."""
    groups = {}
    for token, (group, label, _resolver) in TOKENS.items():
        groups.setdefault(group, []).append({'token': token, 'label': label})
    for options in groups.values():
        options.sort(key=lambda o: o['label'])
    order = ['Order', 'Customer', 'Amounts', 'Business', 'System']
    return [{'group': g, 'options': groups[g]} for g in order if g in groups]


# ---------------------------------------------------------------------------
#  Stock layout
# ---------------------------------------------------------------------------

DEFAULT_ELEMENTS = [
    # Under the business name in the header — each hides itself until filled in.
    dict(section='brand', label='', source='field', token='company.tagline', sort_order=10),
    dict(section='brand', label='VAT/PAN No.:', source='field', token='company.vat', sort_order=20),
    dict(section='brand', label='Phone:', source='field', token='company.phone', sort_order=30),
    dict(section='brand', label='Email:', source='field', token='company.email', sort_order=40),
    dict(section='brand', label='', source='field', token='company.address', sort_order=50),

    # Invoice meta box, top right.
    dict(section='meta', label='Invoice #', source='field', token='order.number',
         sort_order=10, hide_if_empty=False, is_bold=True),
    dict(section='meta', label='Date', source='field', token='order.date',
         sort_order=20, hide_if_empty=False),
    dict(section='meta', label='Time', source='field', token='order.time',
         sort_order=30, hide_if_empty=False),

    # Bill To box.
    dict(section='bill_to', label='Name :-', source='field', token='customer.name',
         sort_order=10, hide_if_empty=False, is_bold=True),
    dict(section='bill_to', label='Phone number :-', source='field', token='customer.phone', sort_order=20),
    dict(section='bill_to', label='Email :-', source='field', token='customer.email', sort_order=30),
    dict(section='bill_to', label='Location :-', source='field', token='customer.city', sort_order=40),
    dict(section='bill_to', label='Landmark :-', source='field', token='customer.landmark', sort_order=50),

    # Ship To box (section is off by default; the lines are ready when it is on).
    dict(section='ship_to', label='Address :-', source='field', token='customer.address', sort_order=10),
    dict(section='ship_to', label='Tracking # :-', source='field', token='order.tracking_number', sort_order=20),
    dict(section='ship_to', label='Courier :-', source='field', token='order.logistics', sort_order=30),
]


# ---------------------------------------------------------------------------
#  Context builder
# ---------------------------------------------------------------------------

_ALIGN = {'index': 'center', 'qty': 'center', 'price': 'right', 'total': 'right'}


def get_template():
    """The singleton, seeded with the stock lines on first access."""
    from dashboard.models import InvoiceTemplate
    return InvoiceTemplate.get_solo()


def _resolve(element, data):
    if element.source == 'static':
        return element.static_value or ''
    entry = TOKENS.get(element.token)
    if not entry:
        return ''
    try:
        return entry[2](data) or ''
    except Exception:  # a bad row must never take the invoice down
        return ''


def _sections(cfg, elements, data):
    """Resolve every active element into {section: [rendered lines]}."""
    from dashboard.models import InvoiceElement
    out = {key: [] for key, _label in InvoiceElement.SECTION_CHOICES}
    for element in elements:
        if not element.is_active:
            continue
        value = str(_resolve(element, data)).strip()
        if not value and element.hide_if_empty:
            continue
        out.setdefault(element.section, []).append({
            'label': element.label,
            'value': value,
            'bold': element.is_bold,
            'full_width': element.full_width,
        })
    return out


def _build_rows(cfg, order_items):
    rows = []
    total_qty = Decimal('0')
    for index, item in enumerate(order_items, start=1):
        product = getattr(item, 'product', None)
        product_type = getattr(product, 'product_type', None) if product else None
        variation = getattr(item, 'product_variation', None)

        tag = ''
        components = []
        if product_type == 'bundle':
            tag = 'Bundle'
            if cfg.col_bundle_components:
                try:
                    components = [
                        f"{c.component_product.name} x {c.quantity_required}"
                        for c in product.bundle_components.all()
                    ]
                except Exception:
                    components = []
        elif product_type == 'variable' or variation:
            tag = 'Variant'
        elif product is not None:
            tag = 'Simple'

        quantity = _dec(getattr(item, 'quantity', 0))
        total_qty += quantity
        rows.append({
            'index': index,
            'name': getattr(item, 'product_name', '') or '',
            'tag': tag if cfg.col_type_tag else '',
            'sku': (getattr(item, 'product_sku', '') or '') if cfg.col_sku else '',
            'variant': (getattr(item, 'variation_name', '') or '') if cfg.col_variant else '',
            'components': components,
            'qty': int(quantity) if quantity == quantity.to_integral_value() else quantity,
            'price': format_money(getattr(item, 'price', 0), cfg),
            'total': format_money(getattr(item, 'total', 0), cfg),
        })
    return rows, total_qty


def _columns(cfg):
    columns = []
    if cfg.col_index:
        columns.append({'key': 'index', 'label': cfg.col_index_label, 'align': 'center', 'width': '34px'})
    columns.append({'key': 'product', 'label': cfg.col_product_label, 'align': 'left', 'width': ''})
    if cfg.col_qty:
        columns.append({'key': 'qty', 'label': cfg.col_qty_label, 'align': 'center', 'width': '58px'})
    if cfg.col_price:
        columns.append({'key': 'price', 'label': cfg.col_price_label, 'align': 'right', 'width': '110px'})
    if cfg.col_total:
        columns.append({'key': 'total', 'label': cfg.col_total_label, 'align': 'right', 'width': '110px'})
    return columns


PAPER_WIDTHS = {
    'a4': '780px',
    'a5': '560px',
    'letter': '800px',
    'thermal80': '300px',
}
PAPER_CSS = {
    'a4': 'A4',
    'a5': 'A5',
    'letter': 'Letter',
    'thermal80': '80mm auto',
}


def _style(cfg):
    accent = cfg.accent_color or '#111827'
    mono = cfg.accent_mode == 'mono'
    return {
        'paper_width': PAPER_WIDTHS.get(cfg.paper_size, '780px'),
        'paper_css': PAPER_CSS.get(cfg.paper_size, 'A4'),
        'accent': accent,
        'accent_text': cfg.text_color if mono else accent,
        'heading_bg': accent if cfg.accent_mode == 'filled' else 'transparent',
        'heading_fg': '#ffffff' if cfg.accent_mode == 'filled' else (cfg.text_color or '#000'),
        'text': cfg.text_color or '#000000',
        'muted': cfg.muted_color or '#4b5563',
        'border': cfg.border_color or '#000000',
        'background': cfg.page_background or '#ffffff',
        'font': cfg.font_family,
        'size': cfg.base_font_size,
        'radius': cfg.corner_radius if cfg.show_outer_border else 0,
        'pad_y': 4 if cfg.compact_mode else 6,
        'pad_x': 7 if cfg.compact_mode else 10,
        'gap': 8 if cfg.compact_mode else 14,
        'watermark_opacity': cfg.watermark_opacity / 100.0,
    }


def build_invoice_context(order, order_items, cfg=None, user=None, labels=None, preview=False):
    """Everything `order_invoice.html` renders, resolved up front.

    `order` may be a real :class:`dashboard.models.Order` or the lightweight
    sample object the customizer preview uses — only attribute reads happen
    here, and every one of them is defensive.
    """
    cfg = cfg or get_template()
    order_items = list(order_items or [])
    elements = list(cfg.elements.all())

    from dashboard.models import CompanySetup
    company = None
    try:
        company = CompanySetup.objects.filter(pk=1).first()
    except Exception:
        company = None

    brand_name = (cfg.business_name or '').strip() or (
        getattr(company, 'company_name', '') or 'Trendy Shopping'
    )

    logo_url = ''
    if cfg.show_logo:
        for candidate in (cfg.logo, getattr(company, 'logo', None)):
            try:
                if candidate and candidate.name and candidate.storage.exists(candidate.name):
                    logo_url = candidate.url
                    break
            except Exception:
                continue

    subtotal = sum((_dec(getattr(i, 'total', 0)) for i in order_items), Decimal('0'))
    discount = _dec(getattr(order, 'discount_amount', 0))
    shipping = _dec(getattr(order, 'shipping_charge', 0))
    delivery = _dec(getattr(order, 'delivery_charge', 0))
    tax_percent = _dec(getattr(order, 'tax_percent', 0))
    tax = ((subtotal - discount) * tax_percent) / Decimal('100')
    grand_total = _dec(getattr(order, 'total_amount', 0))
    paid = _dec(getattr(order, 'partial_amount_paid', 0))
    due = _dec(getattr(order, 'remaining_amount', 0))

    rows, total_qty = _build_rows(cfg, order_items)

    if labels is None:
        labels = {
            'status': (getattr(getattr(order, 'status_setup', None), 'name', None)
                       or getattr(order, 'order_status', '') or getattr(order, 'status', '') or ''),
            'payment_status': (getattr(getattr(order, 'payment_status_setup', None), 'name', None)
                               or getattr(order, 'payment_status', '') or ''),
            'payment_method': (getattr(getattr(order, 'payment_setup', None), 'name', None)
                               or getattr(order, 'payment_method', '') or ''),
        }

    printed_by = ''
    if user is not None and getattr(user, 'is_authenticated', False):
        printed_by = user.get_full_name() or user.get_username()

    data = {
        'cfg': cfg,
        'order': order,
        'items': order_items,
        'brand': {'name': brand_name},
        'labels': labels,
        'counts': {'lines': len(rows), 'qty': int(total_qty) if total_qty else 0},
        'amounts': {
            'subtotal': subtotal, 'discount': discount, 'shipping': shipping,
            'delivery': delivery, 'tax': tax, 'grand_total': grand_total,
            'paid': paid, 'due': due,
        },
        'printed_by': printed_by,
    }

    sections = _sections(cfg, elements, data)

    # ── Totals rows ──────────────────────────────────────────────────────
    totals = []

    def _add(show, label, value, negative=False):
        if not show:
            return
        if cfg.hide_zero_totals and _dec(value) == 0:
            return
        totals.append({
            'label': label,
            'value': ('- ' if negative else '') + format_money(value, cfg),
            'grand': False,
        })

    if cfg.show_subtotal and not (cfg.hide_zero_totals and subtotal == 0):
        totals.append({'label': cfg.label_subtotal, 'value': format_money(subtotal, cfg), 'grand': False})
    _add(cfg.show_discount, cfg.label_discount, discount, negative=True)
    _add(cfg.show_shipping, cfg.label_shipping, shipping)
    _add(cfg.show_delivery, cfg.label_delivery, delivery)
    if cfg.show_tax and not (cfg.hide_zero_totals and tax == 0):
        totals.append({
            'label': f"{cfg.label_tax} ({tax_percent:.2f}%)" if tax_percent else cfg.label_tax,
            'value': format_money(tax, cfg),
            'grand': False,
        })
    for extra in sections.get('totals', []):
        totals.append({'label': extra['label'], 'value': extra['value'], 'grand': False})
    totals.append({'label': cfg.label_grand_total, 'value': format_money(grand_total, cfg), 'grand': True})

    terms = [line.strip() for line in (cfg.terms_text or '').splitlines() if line.strip()]

    return {
        'cfg': cfg,
        'style': _style(cfg),
        'brand': {
            'name': brand_name,
            'logo_url': logo_url,
            'lines': sections.get('brand', []),
        },
        'meta_lines': sections.get('meta', []),
        'bill_to_lines': sections.get('bill_to', []),
        'ship_to_lines': sections.get('ship_to', []),
        'items_note_lines': sections.get('items_note', []),
        'footer_lines': sections.get('footer', []),
        'columns': _columns(cfg),
        'rows': rows,
        'totals': totals,
        'amount_words': amount_in_words(grand_total) if cfg.show_amount_in_words else '',
        'terms': terms,
        'payment_method_label': labels['payment_method'] or 'N/A',
        'partial': {
            'active': bool(getattr(order, 'is_partial_payment', False)) and cfg.show_partial_payment,
            'paid': format_money(paid, cfg),
            'due': format_money(due, cfg),
        },
        'admin_notes': (getattr(order, 'admin_notes', '') or '') if cfg.show_admin_notes else '',
        'customer_notes': (getattr(order, 'notes', '') or '') if cfg.show_customer_notes else '',
        'thank_you': (cfg.thank_you_text or '').replace('{company}', brand_name),
        'printed_by': printed_by,
        'order': order,
        'is_preview': preview,
    }


# ---------------------------------------------------------------------------
#  Preview sample
# ---------------------------------------------------------------------------

class _Sample:
    """A stand-in object with only the attributes the invoice reads."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getattr__(self, name):  # anything unset reads as empty, never raises
        return ''


def sample_order():
    """A representative order used by the customizer preview when no real
    order is available (or when the user asks for the neutral sample)."""
    order = _Sample(
        order_number='T14714',
        created_at=timezone.now(),
        customer_name='Sagar Shrestha',
        customer_phone='9803111615',
        customer_email='sagar@example.com',
        shipping_address='Sabaila-04, Dhanusha',
        branch_city='Sabaila',
        landmark='Near Shiva Mandir',
        tracking_number='NCM-88213',
        logistics='ncm',
        order_from='Facebook',
        order_status='Processing',
        status='processing',
        payment_status='Pending',
        payment_method='COD',
        ncm_delivery_type='Door2Door',
        ncm_destination_branch='JANAKPUR',
        package_weight=Decimal('1.00'),
        discount_amount=Decimal('100.00'),
        shipping_charge=Decimal('0.00'),
        delivery_charge=Decimal('150.00'),
        tax_percent=Decimal('13.00'),
        total_amount=Decimal('1631.00'),
        cod_collected=Decimal('0.00'),
        is_partial_payment=True,
        partial_amount_paid=Decimal('500.00'),
        remaining_amount=Decimal('1131.00'),
        admin_notes='Call before delivery.',
        notes='Gift wrap requested.',
        status_setup=None, payment_setup=None, payment_status_setup=None,
    )
    items = [
        _Sample(product_name='Black Bottle Shampoo', product_sku='black-bottle-shampoo',
                variation_name='', quantity=1, price=Decimal('1300.00'), total=Decimal('1300.00'),
                product=_Sample(product_type='simple'), product_variation=None),
        _Sample(product_name='Argan Hair Serum', product_sku='argan-serum-50', variation_name='50 ml',
                quantity=2, price=Decimal('180.00'), total=Decimal('360.00'),
                product=_Sample(product_type='variable'), product_variation=_Sample()),
    ]
    return order, items
