"""Setup → Bulk Discounts.

Administration for the storefront's quantity breaks: "take 3, save 10%".

A rule (:class:`store.models.BulkDiscount`) points at one target — a single
variation, a single product, a category, or the whole shop — and carries a
ladder of :class:`store.models.BulkDiscountTier` rungs. The storefront resolves
the most specific live rule for each line through :mod:`store.bulk_discounts`,
which reads a cached snapshot, so every write here busts that cache.

Nothing on this page prices anything itself: the preview endpoint calls the
same module the product page and the checkout do, so what an administrator
sees here is what a shopper will be charged.
"""

import csv
import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from accounts.decorators import admin_only
from dashboard.models import Category, Product, ProductVariation
from store import bulk_discounts
from store.models import BulkDiscount, BulkDiscountTier

BULK_ACTIONS = {'activate', 'deactivate', 'delete', 'set_priority', 'clear_schedule'}

MAX_TIERS = 8


# ──────────────────── small helpers ────────────────────

def _decimal(raw, default=None):
    """Money/percentage out of a form field, without letting a typo 500."""
    raw = (raw or '').strip()
    if raw == '':
        return default
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return default
    return value if value >= 0 else default


def _int(raw, default=0):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _datetime(raw):
    """`<input type="datetime-local">` → an aware datetime, or None."""
    raw = (raw or '').strip()
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _saved(request, message, level=messages.SUCCESS):
    """Every mutation ends the same way: bust the storefront cache, say so."""
    bulk_discounts.invalidate_cache()
    messages.add_message(request, level, message)
    return redirect('bulk_discount_setup')


def _rules_qs():
    return (BulkDiscount.objects
            .select_related('product', 'variation', 'variation__product', 'category')
            .prefetch_related(Prefetch('tiers', queryset=BulkDiscountTier.objects.order_by('min_qty'))))


def _selected_rules(request):
    ids = [int(i) for i in request.POST.getlist('selected_ids') if str(i).isdigit()]
    return BulkDiscount.objects.filter(id__in=ids), ids


def _read_tiers(request):
    """The posted ladder as a list of dicts, or (None, error).

    Rungs arrive as parallel arrays so the modal can add and remove rows with
    no server round trip. Blank rows are dropped rather than rejected — an
    admin leaving the last empty row alone is not an error.
    """
    quantities = request.POST.getlist('tier_min_qty')
    types = request.POST.getlist('tier_type')
    values = request.POST.getlist('tier_value')
    labels = request.POST.getlist('tier_label')

    rows = []
    seen = set()
    valid_types = {t[0] for t in BulkDiscountTier.TYPE_CHOICES}

    for index, raw_qty in enumerate(quantities):
        raw_value = values[index] if index < len(values) else ''
        if not str(raw_qty).strip() and not str(raw_value).strip():
            continue  # an untouched empty row

        min_qty = _int(raw_qty, 0)
        if min_qty < 1:
            return None, 'Every tier needs a quantity of 1 or more.'
        if min_qty in seen:
            return None, f'Two tiers both start at {min_qty} units — quantities must differ.'
        seen.add(min_qty)

        value = _decimal(raw_value, None)
        if value is None:
            return None, f'Enter a valid amount for the {min_qty}+ tier.'

        tier_type = (types[index] if index < len(types) else '').strip()
        if tier_type not in valid_types:
            tier_type = BulkDiscountTier.TYPE_PERCENT
        if tier_type == BulkDiscountTier.TYPE_PERCENT and value > 100:
            return None, f'The {min_qty}+ tier is over 100% off.'

        rows.append({
            'min_qty': min_qty,
            'discount_type': tier_type,
            'value': value,
            'label': (labels[index] if index < len(labels) else '').strip()[:60],
        })

    if not rows:
        return None, 'Add at least one tier — a rule with no tiers discounts nothing.'
    if len(rows) > MAX_TIERS:
        return None, f'A rule can hold at most {MAX_TIERS} tiers.'
    rows.sort(key=lambda r: r['min_qty'])
    return rows, ''


def _write_tiers(rule, rows):
    """Replace the rule's ladder wholesale — simpler and safer than diffing,
    and a tier carries nothing worth preserving across an edit."""
    rule.tiers.all().delete()
    BulkDiscountTier.objects.bulk_create([
        BulkDiscountTier(rule=rule, **row) for row in rows
    ])


# ──────────────────── Page ────────────────────

@login_required
@admin_only
def bulk_discount_setup(request):
    search = (request.GET.get('q') or '').strip()
    scope = (request.GET.get('scope') or '').strip()
    status = (request.GET.get('status') or '').strip()
    sort = (request.GET.get('sort') or 'priority').strip()

    rules = _rules_qs()
    if search:
        rules = rules.filter(
            Q(name__icontains=search)
            | Q(product__name__icontains=search)
            | Q(variation__variation_name__icontains=search)
            | Q(variation__sku__icontains=search)
            | Q(category__name__icontains=search)
            | Q(badge_text__icontains=search)
            | Q(note__icontains=search)
        )
    if scope in dict(BulkDiscount.SCOPE_CHOICES):
        rules = rules.filter(scope=scope)

    now = timezone.now()
    if status == 'live':
        rules = rules.filter(is_active=True).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now)
        ).filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
    elif status == 'scheduled':
        rules = rules.filter(is_active=True, starts_at__gt=now)
    elif status == 'expired':
        rules = rules.filter(is_active=True, ends_at__lt=now)
    elif status == 'off':
        rules = rules.filter(is_active=False)

    sort_map = {
        'priority': ['-priority', '-updated_at'],
        'name': ['name', 'id'],
        'scope': ['scope', '-priority'],
        'updated': ['-updated_at'],
        'created': ['-created_at'],
    }
    rules = list(rules.order_by(*sort_map.get(sort, sort_map['priority'])))

    # The template cannot call `tier.unit_price_from(base)`, so each rung is
    # worked out here against its own rule's list price. Category and
    # shop-wide rules have no single base price, so they show the offer only.
    for rule in rules:
        base = rule.base_price
        rule.ladder = [{
            'min_qty': tier.min_qty,
            'offer': tier.offer_label,
            'each': '' if base is None else f'Rs. {tier.unit_price_from(base):,.0f}',
        } for tier in rule.tiers.all()]

    everything = BulkDiscount.objects.all()
    stats = {
        'total': everything.count(),
        'live': sum(1 for r in everything if r.is_live),
        'scheduled': everything.filter(is_active=True, starts_at__gt=now).count(),
        'off': everything.filter(is_active=False).count(),
        'tiers': BulkDiscountTier.objects.count(),
    }

    # The rule editor's target pickers. Variations are shipped inline with
    # their product so switching the product select repopulates the variation
    # select with no request — the catalogue is small enough to send whole.
    products = list(
        Product.objects.filter(is_deleted=False)
        .only('id', 'name', 'price', 'product_type', 'category_id')
        .order_by('name')
    )
    variations_by_product = {}
    for variation in (ProductVariation.objects
                      .exclude(is_active=False)
                      .exclude(status='inactive')
                      .select_related('product')
                      .order_by('product__name', 'created_at')):
        variations_by_product.setdefault(variation.product_id, []).append({
            'id': variation.pk,
            'label': variation.display_label,
            'sku': variation.sku,
            'price': float(variation.price or 0),
        })

    # The editor reopens an existing rule entirely client-side, so every rule
    # on the page ships with the exact values its form fields need — including
    # the local-time strings a `datetime-local` input expects.
    def _local(value):
        return timezone.localtime(value).strftime('%Y-%m-%dT%H:%M') if value else ''

    rules_json = json.dumps([{
        'id': r.pk,
        'name': r.name,
        'scope': r.scope,
        'product': r.product_id or '',
        'variation': r.variation_id or '',
        'category': r.category_id or '',
        'is_active': r.is_active,
        'priority': r.priority,
        'starts_at': _local(r.starts_at),
        'ends_at': _local(r.ends_at),
        'show_on_cards': r.show_on_cards,
        'badge_text': r.badge_text,
        'note': r.note,
        'tiers': [
            {'min_qty': t.min_qty, 'type': t.discount_type,
             'value': f'{t.value:.2f}', 'label': t.label}
            for t in r.tiers.all()
        ],
    } for r in rules])

    return render(request, 'dashboard/bulk_discount_setup.html', {
        'page_title': 'Bulk Discount Setup',
        'rules': rules,
        'rules_json': rules_json,
        'stats': stats,
        'search': search,
        'scope': scope,
        'status': status,
        'sort': sort,
        'scope_choices': BulkDiscount.SCOPE_CHOICES,
        'type_choices': BulkDiscountTier.TYPE_CHOICES,
        'max_tiers': MAX_TIERS,
        'categories': Category.objects.order_by('name'),
        'products_json': json.dumps([
            {
                'id': p.pk,
                'name': p.name,
                'price': float(p.price or 0),
                'type': p.product_type,
                # Lets the preview offer a sensible product to price against
                # for a category rule, which has no single target of its own.
                'category': p.category_id or 0,
                'variations': variations_by_product.get(p.pk, []),
            }
            for p in products
        ]),
    })


# ──────────────────── Single-rule CRUD ────────────────────

@login_required
@admin_only
@require_POST
def bulk_discount_save(request):
    """Add or edit one rule and its whole ladder. Blank `rule_id` means add."""
    rule_id = (request.POST.get('rule_id') or '').strip()
    scope = (request.POST.get('scope') or '').strip()
    if scope not in dict(BulkDiscount.SCOPE_CHOICES):
        return _saved(request, '❌ Pick what this discount applies to.', messages.ERROR)

    fields = {
        'name': (request.POST.get('name') or '').strip()[:140],
        'scope': scope,
        'product': None,
        'variation': None,
        'category': None,
        'is_active': request.POST.get('is_active') == 'on',
        'priority': max(0, _int(request.POST.get('priority'), 0)),
        'starts_at': _datetime(request.POST.get('starts_at')),
        'ends_at': _datetime(request.POST.get('ends_at')),
        'show_on_cards': request.POST.get('show_on_cards') == 'on',
        'badge_text': (request.POST.get('badge_text') or '').strip()[:60],
        'note': (request.POST.get('note') or '').strip()[:200],
    }

    # Resolve the target the scope names, and refuse a rule that points nowhere
    # — a rule with a missing target would silently never match anything.
    if scope == BulkDiscount.SCOPE_VARIATION:
        variation_id = _int(request.POST.get('variation'), 0)
        if not variation_id:
            return _saved(request, '❌ Choose which variation this applies to.', messages.ERROR)
        variation = get_object_or_404(ProductVariation, pk=variation_id)
        fields['variation'] = variation
        fields['product'] = variation.product
    elif scope == BulkDiscount.SCOPE_PRODUCT:
        product_id = _int(request.POST.get('product'), 0)
        if not product_id:
            return _saved(request, '❌ Choose which product this applies to.', messages.ERROR)
        fields['product'] = get_object_or_404(Product, pk=product_id)
    elif scope == BulkDiscount.SCOPE_CATEGORY:
        category_id = _int(request.POST.get('category'), 0)
        if not category_id:
            return _saved(request, '❌ Choose which category this applies to.', messages.ERROR)
        fields['category'] = get_object_or_404(Category, pk=category_id)

    if fields['starts_at'] and fields['ends_at'] and fields['ends_at'] <= fields['starts_at']:
        return _saved(request, '❌ The end of the schedule is before its start.', messages.ERROR)

    rows, error = _read_tiers(request)
    if error:
        return _saved(request, f'❌ {error}', messages.ERROR)

    # Two rules on one target is legal — priority decides — but it is far more
    # often a duplicate nobody meant to make, and the loser is invisible on the
    # storefront. Say so rather than letting it sit there unnoticed.
    rival = BulkDiscount.objects.filter(scope=scope)
    if scope == BulkDiscount.SCOPE_VARIATION:
        rival = rival.filter(variation=fields['variation'])
    elif scope == BulkDiscount.SCOPE_PRODUCT:
        rival = rival.filter(product=fields['product'])
    elif scope == BulkDiscount.SCOPE_CATEGORY:
        rival = rival.filter(category=fields['category'])
    if rule_id:
        rival = rival.exclude(pk=rule_id)
    clash = rival.first()

    if rule_id:
        rule = get_object_or_404(BulkDiscount, pk=rule_id)
        for key, value in fields.items():
            setattr(rule, key, value)
        rule.save()
        _write_tiers(rule, rows)
        verb = 'Updated'
    else:
        rule = BulkDiscount.objects.create(**fields)
        _write_tiers(rule, rows)
        verb = 'Added'

    if clash:
        winner = rule if rule.priority >= clash.priority else clash
        messages.warning(
            request,
            f'⚠️ “{clash}” already targets {rule.target_label}. Only one rule '
            f'can apply — “{winner}” wins on priority. Raise this rule’s '
            f'priority or delete the other.')
    return _saved(request, f'✅ {verb} “{rule}” — {len(rows)} tier(s).')


@login_required
@admin_only
@require_POST
def bulk_discount_delete(request, rule_id):
    rule = get_object_or_404(BulkDiscount, pk=rule_id)
    label = str(rule)
    rule.delete()
    return _saved(request, f'🗑️ Deleted “{label}”.')


@login_required
@admin_only
@require_POST
def bulk_discount_toggle(request, rule_id):
    rule = get_object_or_404(BulkDiscount, pk=rule_id)
    rule.is_active = not rule.is_active
    rule.save(update_fields=['is_active', 'updated_at'])
    return _saved(request, f'✅ “{rule}” {"activated" if rule.is_active else "paused"}.')


@login_required
@admin_only
@require_POST
def bulk_discount_duplicate(request, rule_id):
    """Copy a rule, ladder and all, switched off so it can be retargeted
    before it goes anywhere near a shopper."""
    source = get_object_or_404(BulkDiscount, pk=rule_id)
    rows = [
        {'min_qty': t.min_qty, 'discount_type': t.discount_type,
         'value': t.value, 'label': t.label}
        for t in source.tiers.all()
    ]
    copy = BulkDiscount.objects.create(
        name=(source.name or str(source))[:130] + ' (copy)',
        scope=source.scope,
        product=source.product,
        variation=source.variation,
        category=source.category,
        is_active=False,
        priority=source.priority,
        starts_at=source.starts_at,
        ends_at=source.ends_at,
        show_on_cards=source.show_on_cards,
        badge_text=source.badge_text,
        note=source.note,
    )
    _write_tiers(copy, rows)
    return _saved(request, f'✅ Copied “{source}”. The copy is paused — edit it, then switch it on.')


# ──────────────────── Bulk actions ────────────────────

@login_required
@admin_only
@require_POST
def bulk_discount_bulk_action(request):
    action = (request.POST.get('action') or '').strip()
    if action not in BULK_ACTIONS:
        return _saved(request, '❌ Unknown bulk action.', messages.ERROR)

    rules, _ids = _selected_rules(request)
    count = rules.count()
    if not count:
        return _saved(request, '⚠️ Select at least one rule first.', messages.WARNING)

    if action == 'activate':
        rules.update(is_active=True, updated_at=timezone.now())
        return _saved(request, f'✅ Activated {count} rule(s).')

    if action == 'deactivate':
        rules.update(is_active=False, updated_at=timezone.now())
        return _saved(request, f'✅ Paused {count} rule(s).')

    if action == 'delete':
        rules.delete()
        return _saved(request, f'🗑️ Deleted {count} rule(s).')

    if action == 'set_priority':
        priority = max(0, _int(request.POST.get('bulk_priority'), -1))
        if priority < 0:
            return _saved(request, '❌ Enter the priority to apply.', messages.ERROR)
        rules.update(priority=priority, updated_at=timezone.now())
        return _saved(request, f'✅ Priority {priority} set on {count} rule(s).')

    # clear_schedule
    rules.update(starts_at=None, ends_at=None, updated_at=timezone.now())
    return _saved(request, f'✅ {count} rule(s) now run with no end date.')


# ──────────────────── Smart helpers ────────────────────

@login_required
@admin_only
@require_POST
def bulk_discount_spread(request, rule_id):
    """Copy one variation rule's ladder onto every other variation of the
    same product.

    Setting the same break on eight sizes by hand is the tedious part of this
    page; this does it in one press. Variations that already have a rule of
    their own are left alone — a deliberate exception is never overwritten.
    """
    source = get_object_or_404(BulkDiscount, pk=rule_id)
    if source.scope != BulkDiscount.SCOPE_VARIATION or not source.variation_id:
        return _saved(request, '❌ Only a variation rule can be spread across a product.',
                      messages.ERROR)

    product = source.variation.product
    rows = [
        {'min_qty': t.min_qty, 'discount_type': t.discount_type,
         'value': t.value, 'label': t.label}
        for t in source.tiers.all()
    ]
    if not rows:
        return _saved(request, '❌ That rule has no tiers to copy.', messages.ERROR)

    already = set(
        BulkDiscount.objects
        .filter(scope=BulkDiscount.SCOPE_VARIATION, variation__product=product)
        .values_list('variation_id', flat=True)
    )

    created = 0
    for variation in product.active_variations:
        if variation.pk in already:
            continue
        copy = BulkDiscount.objects.create(
            name=f'{product.name} — {variation.display_label}'[:140],
            scope=BulkDiscount.SCOPE_VARIATION,
            variation=variation,
            product=product,
            is_active=source.is_active,
            priority=source.priority,
            starts_at=source.starts_at,
            ends_at=source.ends_at,
            show_on_cards=source.show_on_cards,
            badge_text=source.badge_text,
            note=source.note,
        )
        _write_tiers(copy, rows)
        created += 1

    if not created:
        return _saved(request, 'ℹ️ Every variation of this product already has its own rule.',
                      messages.INFO)
    return _saved(request, f'✅ Copied this ladder onto {created} more variation(s) of {product.name}.')


@login_required
@admin_only
def bulk_discount_preview(request):
    """Live calculator for the editor: what a shopper pays for N units.

    Deliberately goes through :mod:`store.bulk_discounts` rather than doing
    the arithmetic here, so the preview cannot drift from the storefront. It
    prices the *saved* rules, so it answers "what is live now", which is also
    what makes it useful for spotting a rule being shadowed by a more specific
    one.
    """
    product_id = _int(request.GET.get('product'), 0)
    variation_id = _int(request.GET.get('variation'), 0)
    quantity = max(1, _int(request.GET.get('qty'), 1))

    variation = None
    if variation_id:
        variation = ProductVariation.objects.select_related('product').filter(pk=variation_id).first()
        if variation is None:
            return JsonResponse({'ok': False, 'message': 'That variation no longer exists.'})
        product = variation.product
    else:
        product = Product.objects.filter(pk=product_id).first()
        if product is None:
            return JsonResponse({'ok': False, 'message': 'Pick a product to preview.'})

    quote = bulk_discounts.price_for(product, variation, quantity)
    rule = bulk_discounts.rule_for(product, variation)
    return JsonResponse({
        'ok': True,
        'target': variation.display_label if variation else product.name,
        'rule': (rule or {}).get('name') or '',
        'rule_scope': (rule or {}).get('scope') or '',
        'quantity': quantity,
        'base_unit': str(quote['base_unit']),
        'unit': str(quote['unit']),
        'line_total': str(quote['line_total']),
        'base_line_total': str(quote['base_line_total']),
        'saved': str(quote['saved']),
        'tier': quote['tier']['offer_label'] if quote['tier'] else '',
        'next_tier': (
            f"{quote['next_tier']['min_qty']}+ → {quote['next_tier']['offer_label']}"
            if quote['next_tier'] else ''
        ),
        'tiers': [
            {'min_qty': t['min_qty'], 'offer': t['offer_label'],
             'each': t['unit_price_display']}
            for t in quote['tiers']
        ],
    })


@login_required
@admin_only
def bulk_discount_export(request):
    """CSV of every rule, one row per tier."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="bulk_discounts.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Rule', 'Scope', 'Target', 'Status', 'Priority', 'Starts', 'Ends',
        'Show on cards', 'Badge', 'Tier min qty', 'Tier type', 'Tier value',
        'Tier label', 'Base price', 'Price each at tier',
    ])
    for rule in _rules_qs():
        base = rule.base_price
        for tier in rule.tiers.all():
            writer.writerow([
                str(rule), rule.get_scope_display(), rule.target_label,
                rule.schedule_state, rule.priority,
                rule.starts_at.strftime('%Y-%m-%d %H:%M') if rule.starts_at else '',
                rule.ends_at.strftime('%Y-%m-%d %H:%M') if rule.ends_at else '',
                'Yes' if rule.show_on_cards else 'No', rule.badge_text,
                tier.min_qty, tier.get_discount_type_display(), tier.value, tier.label,
                '' if base is None else base,
                '' if base is None else tier.unit_price_from(base),
            ])
    return response
