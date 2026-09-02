"""Setup -> Invoice Customizer.

Admin screen for the printable order invoice: business identity (VAT/PAN,
phone, address...), which sections and columns print, the totals wording,
the paper/typography/colour styling, and a free-form list of extra lines that
can pull any whitelisted order field or print fixed text.

Everything saved here is what `dashboard.views.order_invoice` renders, and the
live preview on this page goes through the exact same context builder, so what
is previewed is what prints.
"""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from accounts.decorators import admin_only
from dashboard import invoice_config
from dashboard.models import InvoiceElement, InvoiceTemplate, Order


# ─────────────────────────── form helpers ───────────────────────────

def _flag(post, name):
    """An unchecked checkbox posts nothing — absence means False."""
    return post.get(name) in ('on', 'true', '1', 'yes')


def _text(post, name, default='', limit=None):
    value = (post.get(name) or '').strip()
    if not value:
        value = default
    return value[:limit] if limit else value


def _int(post, name, default, low, high):
    raw = (post.get(name) or '').strip()
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)


def _color(post, name, default):
    value = (post.get(name) or '').strip()
    if len(value) == 7 and value.startswith('#'):
        try:
            int(value[1:], 16)
            return value.lower()
        except ValueError:
            pass
    return default


def _choice(post, name, choices, default):
    value = (post.get(name) or '').strip()
    return value if value in {key for key, _ in choices} else default


BOOLEAN_FIELDS = [
    'show_logo', 'show_header', 'show_meta', 'show_bill_to', 'show_ship_to',
    'show_items', 'show_totals', 'show_footer',
    'col_index', 'col_sku', 'col_type_tag', 'col_variant', 'col_bundle_components',
    'col_qty', 'col_price', 'col_total',
    'show_subtotal', 'show_discount', 'show_shipping', 'show_delivery', 'show_tax',
    'hide_zero_totals', 'show_amount_in_words', 'thousand_separator',
    'show_payment_method', 'show_partial_payment', 'show_admin_notes',
    'show_customer_notes', 'show_terms', 'show_printed_by', 'show_signature',
    'compact_mode', 'show_outer_border', 'show_watermark',
]

# field -> (default, max length)
TEXT_FIELDS = {
    'business_name': ('', 200),
    'business_tagline': ('', 300),
    'vat_number': ('', 60),
    'registration_number': ('', 60),
    'business_phone': ('', 60),
    'business_alt_phone': ('', 60),
    'business_email': ('', 120),
    'business_website': ('', 160),
    'document_title': ('INVOICE', 60),
    'invoice_number_prefix': ('', 20),
    'bill_to_title': ('Bill To', 60),
    'ship_to_title': ('Ship To', 60),
    'col_index_label': ('#', 40),
    'col_product_label': ('Product', 40),
    'col_qty_label': ('Qty', 40),
    'col_price_label': ('Unit Price', 40),
    'col_total_label': ('Total', 40),
    'empty_items_text': ('No items found.', 120),
    'label_subtotal': ('Subtotal', 40),
    'label_discount': ('Discount', 40),
    'label_shipping': ('Shipping', 40),
    'label_delivery': ('Delivery Charge', 40),
    'label_tax': ('Tax', 40),
    'label_grand_total': ('Grand Total', 40),
    'label_amount_in_words': ('In words', 60),
    'currency_symbol': ('रू', 10),
    'admin_notes_title': ('Admin Notes', 60),
    'customer_notes_title': ('Order Notes', 60),
    'terms_title': ('Terms & Conditions', 60),
    'thank_you_text': ('Thank you for shopping with {company}!', 200),
    'footer_note': ('', 200),
    'signature_left_label': ('Customer Signature', 60),
    'signature_right_label': ('Authorised Signature', 60),
    'watermark_text': ('', 60),
}

COLOR_FIELDS = {
    'accent_color': '#111827',
    'text_color': '#000000',
    'muted_color': '#4b5563',
    'border_color': '#000000',
    'page_background': '#ffffff',
}


def _apply_settings(cfg, post, files, user):
    for name in BOOLEAN_FIELDS:
        setattr(cfg, name, _flag(post, name))

    for name, (default, limit) in TEXT_FIELDS.items():
        setattr(cfg, name, _text(post, name, default, limit))

    cfg.business_address = (post.get('business_address') or '').strip()[:1000]
    cfg.terms_text = (post.get('terms_text') or '').strip()[:4000]
    cfg.custom_css = (post.get('custom_css') or '').strip()[:8000]

    for name, default in COLOR_FIELDS.items():
        setattr(cfg, name, _color(post, name, default))

    cfg.header_layout = _choice(post, 'header_layout', InvoiceTemplate.HEADER_LAYOUT_CHOICES, 'split')
    cfg.paper_size = _choice(post, 'paper_size', InvoiceTemplate.PAPER_CHOICES, 'a4')
    cfg.table_style = _choice(post, 'table_style', InvoiceTemplate.TABLE_STYLE_CHOICES, 'bordered')
    cfg.accent_mode = _choice(post, 'accent_mode', InvoiceTemplate.ACCENT_MODE_CHOICES, 'mono')
    cfg.currency_position = _choice(
        post, 'currency_position', InvoiceTemplate.CURRENCY_POSITION_CHOICES, 'before'
    )
    cfg.font_family = _choice(post, 'font_family', InvoiceTemplate.FONT_CHOICES,
                              InvoiceTemplate.FONT_CHOICES[0][0])

    cfg.logo_height = _int(post, 'logo_height', 50, 16, 160)
    cfg.base_font_size = _int(post, 'base_font_size', 12, 8, 18)
    cfg.corner_radius = _int(post, 'corner_radius', 8, 0, 24)
    cfg.watermark_opacity = _int(post, 'watermark_opacity', 8, 1, 40)

    if files and files.get('logo'):
        cfg.logo = files['logo']
    elif _flag(post, 'remove_logo'):
        cfg.logo = None

    cfg.updated_by = user if getattr(user, 'is_authenticated', False) else None
    cfg.save()
    return cfg


# ─────────────────────────── pages ───────────────────────────

@login_required
@admin_only
def invoice_customizer(request):
    cfg = invoice_config.get_template()

    if request.method == 'POST':
        _apply_settings(cfg, request.POST, request.FILES, request.user)
        messages.success(request, '✅ Invoice design saved.')
        return redirect('invoice_customizer')

    elements = list(cfg.elements.all())
    grouped = []
    for key, label in InvoiceElement.SECTION_CHOICES:
        grouped.append({
            'key': key,
            'label': label,
            'elements': [e for e in elements if e.section == key],
        })

    return render(request, 'dashboard/invoice_customizer.html', {
        'page_title': 'Invoice Customizer',
        'cfg': cfg,
        'grouped_elements': grouped,
        'element_count': len(elements),
        'active_element_count': sum(1 for e in elements if e.is_active),
        'section_choices': InvoiceElement.SECTION_CHOICES,
        'source_choices': InvoiceElement.SOURCE_CHOICES,
        'token_groups': invoice_config.token_groups(),
        'paper_choices': InvoiceTemplate.PAPER_CHOICES,
        'font_choices': InvoiceTemplate.FONT_CHOICES,
        'header_layout_choices': InvoiceTemplate.HEADER_LAYOUT_CHOICES,
        'table_style_choices': InvoiceTemplate.TABLE_STYLE_CHOICES,
        'currency_position_choices': InvoiceTemplate.CURRENCY_POSITION_CHOICES,
        'accent_mode_choices': InvoiceTemplate.ACCENT_MODE_CHOICES,
    })


@login_required
@admin_only
@xframe_options_sameorigin
def invoice_preview(request):
    """The invoice itself, rendered for the customizer's preview pane.

    Uses the newest real order so the preview shows genuine data; falls back to
    a neutral sample when the database has none (or `?sample=1` is asked for).

    `xframe_options_sameorigin` is required: the project leaves
    ``X_FRAME_OPTIONS`` at Django's ``DENY`` default, which otherwise blocks
    even this same-origin preview iframe. The exemption is scoped to this one
    read-only preview — the printable invoice at `order_invoice` stays DENY.
    """
    cfg = invoice_config.get_template()
    order = None
    items = []

    if request.GET.get('sample') != '1':
        order = (
            Order.objects.filter(is_deleted=False)
            .select_related('status_setup', 'payment_setup', 'payment_status_setup')
            .order_by('-created_at')
            .first()
        )
        if order is not None:
            items = list(
                order.items.select_related('product', 'product_variation')
                .prefetch_related('product__bundle_components__component_product')
            )

    if order is None:
        order, items = invoice_config.sample_order()

    context = invoice_config.build_invoice_context(
        order, items, cfg=cfg, user=request.user, preview=True
    )
    context['hide_actions'] = True
    return render(request, 'order_invoice.html', context)


# ─────────────────────────── element CRUD ───────────────────────────

def _element_from_post(element, post):
    section_keys = {key for key, _ in InvoiceElement.SECTION_CHOICES}
    source_keys = {key for key, _ in InvoiceElement.SOURCE_CHOICES}

    section = (post.get('section') or '').strip()
    element.section = section if section in section_keys else 'bill_to'

    source = (post.get('source') or '').strip()
    element.source = source if source in source_keys else 'static'

    token = (post.get('token') or '').strip()
    element.token = token if token in invoice_config.TOKENS else ''
    if element.source == 'field' and not element.token:
        element.source = 'static'

    element.label = (post.get('label') or '').strip()[:80]
    element.static_value = (post.get('static_value') or '').strip()[:400]
    element.is_active = _flag(post, 'is_active')
    element.hide_if_empty = _flag(post, 'hide_if_empty')
    element.is_bold = _flag(post, 'is_bold')
    element.full_width = _flag(post, 'full_width')
    return element


@login_required
@admin_only
@require_POST
def invoice_element_save(request):
    cfg = invoice_config.get_template()
    element_id = (request.POST.get('element_id') or '').strip()

    if element_id.isdigit():
        element = get_object_or_404(InvoiceElement, pk=int(element_id), template=cfg)
        verb = 'updated'
    else:
        element = InvoiceElement(template=cfg)
        section = (request.POST.get('section') or 'bill_to').strip()
        highest = (
            InvoiceElement.objects.filter(template=cfg, section=section)
            .aggregate(top=Max('sort_order'))['top'] or 0
        )
        element.sort_order = min(highest + 10, 32000)
        verb = 'added'

    _element_from_post(element, request.POST)
    if not element.label and not element.token and not element.static_value:
        messages.error(request, '⚠️ Give the line a label, a data field, or some text.')
        return redirect('invoice_customizer')

    element.save()
    messages.success(request, f'✅ Invoice line {verb}.')
    return redirect('invoice_customizer')


@login_required
@admin_only
@require_POST
def invoice_element_delete(request, element_id):
    cfg = invoice_config.get_template()
    element = get_object_or_404(InvoiceElement, pk=element_id, template=cfg)
    element.delete()
    messages.success(request, '🗑️ Invoice line removed.')
    return redirect('invoice_customizer')


@login_required
@admin_only
@require_POST
def invoice_element_toggle(request, element_id):
    cfg = invoice_config.get_template()
    element = get_object_or_404(InvoiceElement, pk=element_id, template=cfg)
    element.is_active = not element.is_active
    element.save(update_fields=['is_active'])
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'is_active': element.is_active})
    messages.success(
        request, '✅ Line shown on the invoice.' if element.is_active else '👁️ Line hidden.'
    )
    return redirect('invoice_customizer')


@login_required
@admin_only
@require_POST
def invoice_element_move(request, element_id):
    """Swap sort order with the neighbour above/below inside the same section."""
    cfg = invoice_config.get_template()
    element = get_object_or_404(InvoiceElement, pk=element_id, template=cfg)
    direction = request.POST.get('direction')

    siblings = list(
        InvoiceElement.objects.filter(template=cfg, section=element.section)
        .order_by('sort_order', 'id')
    )
    index = next((i for i, e in enumerate(siblings) if e.pk == element.pk), None)
    target = None
    if index is not None:
        if direction == 'up' and index > 0:
            target = siblings[index - 1]
        elif direction == 'down' and index < len(siblings) - 1:
            target = siblings[index + 1]

    if target is not None:
        # Sort orders can collide after a section change; renumber, then swap.
        for position, sibling in enumerate(siblings, start=1):
            sibling.sort_order = position * 10
        InvoiceElement.objects.bulk_update(siblings, ['sort_order'])
        element.sort_order, target.sort_order = target.sort_order, element.sort_order
        InvoiceElement.objects.bulk_update([element, target], ['sort_order'])

    return redirect('invoice_customizer')


@login_required
@admin_only
@require_POST
def invoice_element_reorder(request):
    """Persist a drag-and-drop reorder: {"section": ..., "ids": [...]}"""
    cfg = invoice_config.get_template()
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'success': False, 'message': 'Bad payload.'}, status=400)

    ids = [int(i) for i in payload.get('ids', []) if str(i).isdigit()]
    if not ids:
        return JsonResponse({'success': False, 'message': 'Nothing to reorder.'}, status=400)

    by_id = {e.pk: e for e in InvoiceElement.objects.filter(template=cfg, pk__in=ids)}
    updates = []
    for position, element_id in enumerate(ids, start=1):
        element = by_id.get(element_id)
        if element:
            element.sort_order = position * 10
            updates.append(element)
    if updates:
        InvoiceElement.objects.bulk_update(updates, ['sort_order'])
    return JsonResponse({'success': True, 'updated': len(updates)})


@login_required
@admin_only
@require_POST
def invoice_customizer_reset(request):
    """Restore stock lines, and optionally the styling, to factory defaults."""
    cfg = invoice_config.get_template()
    scope = request.POST.get('scope') or 'lines'

    if scope in ('all', 'style'):
        # Roll every field back to its model default except the business
        # identity block — those are the shop's own details, not styling.
        keep = {
            'id', 'logo', 'updated_at', 'updated_by',
            'business_name', 'business_tagline', 'vat_number', 'registration_number',
            'business_phone', 'business_alt_phone', 'business_email',
            'business_website', 'business_address',
        }
        for field in InvoiceTemplate._meta.fields:
            if field.name in keep or not field.has_default():
                continue
            setattr(cfg, field.name, field.get_default())
        cfg.save()

    if scope in ('all', 'lines'):
        InvoiceElement.seed_defaults(cfg, wipe=True)
        messages.success(request, '↩️ Invoice lines restored to the default layout.')
    if scope in ('all', 'style'):
        messages.success(request, '↩️ Invoice styling restored to defaults.')

    return redirect('invoice_customizer')
