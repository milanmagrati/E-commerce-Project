"""Setup → Delivery Charge Setup.

Administration for the storefront's delivery pricing: the site-wide defaults
(:class:`store.models.DeliverySetting`) plus one rule per district — or per
courier branch inside a district — in :class:`store.models.DeliveryCharge`.
What is saved here is exactly what the guest order form quotes and prints on
the product page, so every write busts the storefront's delivery cache.
"""

import csv
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import admin_only
from store.models import DeliveryCharge, DeliverySetting
from store import services as store_services

BULK_ACTIONS = {
    'activate', 'deactivate', 'delete',
    'set_charge', 'set_delivery_time', 'set_free_above', 'clear_free_above',
}


def _decimal(raw, default=None):
    """Money out of a form field, without letting a typo 500 the page."""
    raw = (raw or '').strip()
    if raw == '':
        return default
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return default
    return value if value >= 0 else default


def _selected_rules(request):
    ids = request.POST.getlist('selected_ids')
    ids = [int(i) for i in ids if str(i).isdigit()]
    return DeliveryCharge.objects.filter(id__in=ids), ids


def _saved(request, message):
    """Every mutation ends the same way: bust the storefront cache, say so."""
    store_services.invalidate_delivery_cache()
    messages.success(request, message)
    return redirect('delivery_charge_setup')


# ──────────────────── Page ────────────────────

@login_required
@admin_only
def delivery_charge_setup(request):
    search = (request.GET.get('q') or '').strip()
    status = (request.GET.get('status') or '').strip()
    scope = (request.GET.get('scope') or '').strip()
    sort = (request.GET.get('sort') or 'district').strip()

    rules = DeliveryCharge.objects.all()
    if search:
        rules = rules.filter(
            Q(district__icontains=search)
            | Q(branch_name__icontains=search)
            | Q(branch_code__icontains=search)
            | Q(covered_areas__icontains=search)
            | Q(delivery_time__icontains=search)
        )
    if status == 'active':
        rules = rules.filter(is_active=True)
    elif status == 'inactive':
        rules = rules.filter(is_active=False)

    if scope == 'district':
        rules = rules.filter(branch_code='')
    elif scope == 'branch':
        rules = rules.exclude(branch_code='')
    elif scope == 'free':
        rules = rules.filter(charge__lte=0)
    elif scope == 'paid':
        rules = rules.filter(charge__gt=0)

    sort_map = {
        'district': ['district', 'branch_name'],
        '-district': ['-district', 'branch_name'],
        'charge': ['charge', 'district'],
        '-charge': ['-charge', 'district'],
        'updated': ['-updated_at'],
    }
    rules = rules.order_by(*sort_map.get(sort, sort_map['district']))

    everything = DeliveryCharge.objects.all()
    stats = everything.aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(is_active=True)),
        districts=Count('district', distinct=True),
        avg_charge=Avg('charge'),
    )
    stats['free'] = everything.filter(charge__lte=0).count()

    # District list for the add/edit modal. Straight from NCM so a rule can
    # never be written against a district the courier does not serve; if the
    # courier API is down the modal falls back to free typing.
    locations = store_services.get_locations()

    return render(request, 'dashboard/delivery_charge_setup.html', {
        'page_title': 'Delivery Charge Setup',
        'rules': rules,
        'setting': DeliverySetting.get_solo(),
        'stats': stats,
        'search': search,
        'status': status,
        'scope': scope,
        'sort': sort,
        'districts': locations['districts'],
        'locations_source': locations['source'],
        # Compact payload for the modal's district combobox — name, province
        # and branch count, nothing the picker doesn't draw.
        'district_options': [
            {'name': d['name'].upper(), 'province': d['province'], 'branches': len(d['branches'])}
            for d in locations['districts']
        ],
    })


@login_required
@admin_only
def delivery_charge_branches(request):
    """Branches for one district — feeds the modal's branch select."""
    district = (request.GET.get('district') or '').strip().upper()
    for entry in store_services.get_locations()['districts']:
        if entry['name'].upper() == district:
            return JsonResponse({'branches': entry['branches']})
    return JsonResponse({'branches': []})


# ──────────────────── Single-rule CRUD ────────────────────

@login_required
@admin_only
@require_POST
def delivery_charge_save(request):
    """Add or edit one rule. A blank `rule_id` means add."""
    rule_id = (request.POST.get('rule_id') or '').strip()
    district = (request.POST.get('district') or '').strip().upper()
    branch_code = (request.POST.get('branch_code') or '').strip().upper()

    if not district:
        messages.error(request, '❌ District is required.')
        return redirect('delivery_charge_setup')

    charge = _decimal(request.POST.get('charge'), Decimal('0'))
    if charge is None:
        messages.error(request, '❌ Enter a valid delivery charge.')
        return redirect('delivery_charge_setup')

    fields = {
        'district': district,
        'branch_code': branch_code,
        'branch_name': (request.POST.get('branch_name') or '').strip(),
        'charge': charge,
        'free_above': _decimal(request.POST.get('free_above'), None),
        'delivery_time': (request.POST.get('delivery_time') or '').strip(),
        'delivery_time_np': (request.POST.get('delivery_time_np') or '').strip(),
        'covered_areas': (request.POST.get('covered_areas') or '').strip(),
        'note': (request.POST.get('note') or '').strip(),
        'is_active': request.POST.get('is_active') == 'on',
    }

    # (district, branch_code) is unique — catch it here so the admin gets a
    # message instead of an IntegrityError page.
    clash = DeliveryCharge.objects.filter(district=district, branch_code=branch_code)
    if rule_id:
        clash = clash.exclude(pk=rule_id)
    if clash.exists():
        scope = fields['branch_name'] or branch_code or 'all branches'
        messages.error(request, f'❌ A rule for {district} ({scope}) already exists.')
        return redirect('delivery_charge_setup')

    if rule_id:
        rule = get_object_or_404(DeliveryCharge, pk=rule_id)
        for key, value in fields.items():
            setattr(rule, key, value)
        rule.save()
        return _saved(request, f'✅ Updated the delivery rule for {rule}.')

    rule = DeliveryCharge.objects.create(**fields)
    return _saved(request, f'✅ Added a delivery rule for {rule}.')


@login_required
@admin_only
@require_POST
def delivery_charge_delete(request, rule_id):
    rule = get_object_or_404(DeliveryCharge, pk=rule_id)
    label = str(rule)
    rule.delete()
    return _saved(request, f'🗑️ Deleted the delivery rule for {label}.')


@login_required
@admin_only
@require_POST
def delivery_charge_toggle(request, rule_id):
    rule = get_object_or_404(DeliveryCharge, pk=rule_id)
    rule.is_active = not rule.is_active
    rule.save(update_fields=['is_active', 'updated_at'])
    state = 'activated' if rule.is_active else 'deactivated'
    return _saved(request, f'✅ {rule} {state}.')


# ──────────────────── Bulk actions ────────────────────

@login_required
@admin_only
@require_POST
def delivery_charge_bulk_action(request):
    action = (request.POST.get('action') or '').strip()
    if action not in BULK_ACTIONS:
        messages.error(request, '❌ Unknown bulk action.')
        return redirect('delivery_charge_setup')

    rules, ids = _selected_rules(request)
    count = rules.count()
    if not count:
        messages.warning(request, '⚠️ Select at least one delivery rule first.')
        return redirect('delivery_charge_setup')

    if action == 'activate':
        rules.update(is_active=True, updated_at=timezone.now())
        return _saved(request, f'✅ Activated {count} delivery rule(s).')

    if action == 'deactivate':
        rules.update(is_active=False, updated_at=timezone.now())
        return _saved(request, f'✅ Deactivated {count} delivery rule(s).')

    if action == 'delete':
        rules.delete()
        return _saved(request, f'🗑️ Deleted {count} delivery rule(s).')

    if action == 'set_charge':
        charge = _decimal(request.POST.get('bulk_charge'), None)
        if charge is None:
            messages.error(request, '❌ Enter the charge to apply.')
            return redirect('delivery_charge_setup')
        rules.update(charge=charge, updated_at=timezone.now())
        return _saved(request, f'✅ Set Rs. {charge:.0f} on {count} delivery rule(s).')

    if action == 'set_delivery_time':
        english = (request.POST.get('bulk_delivery_time') or '').strip()
        nepali = (request.POST.get('bulk_delivery_time_np') or '').strip()
        if not english and not nepali:
            messages.error(request, '❌ Enter a delivery time to apply.')
            return redirect('delivery_charge_setup')
        # Blank halves are left alone, so an admin can set just the Nepali
        # line across a batch without wiping the English one.
        updates = {}
        if english:
            updates['delivery_time'] = english
        if nepali:
            updates['delivery_time_np'] = nepali
        updates['updated_at'] = timezone.now()
        rules.update(**updates)
        return _saved(request, f'✅ Updated the delivery time on {count} rule(s).')

    if action == 'set_free_above':
        threshold = _decimal(request.POST.get('bulk_free_above'), None)
        if threshold is None:
            messages.error(request, '❌ Enter a free-delivery threshold.')
            return redirect('delivery_charge_setup')
        rules.update(free_above=threshold, updated_at=timezone.now())
        return _saved(request, f'✅ Free above Rs. {threshold:.0f} on {count} rule(s).')

    # clear_free_above
    rules.update(free_above=None, updated_at=timezone.now())
    return _saved(request, f'✅ {count} rule(s) now always charge their delivery fee.')


# ──────────────────── Site-wide defaults ────────────────────

@login_required
@admin_only
@require_POST
def delivery_charge_settings(request):
    setting = DeliverySetting.get_solo()
    setting.inside_valley_charge = _decimal(request.POST.get('inside_valley_charge'), setting.inside_valley_charge)
    setting.default_charge = _decimal(request.POST.get('default_charge'), setting.default_charge)
    setting.free_delivery_threshold = _decimal(
        request.POST.get('free_delivery_threshold'), setting.free_delivery_threshold)
    setting.valley_districts = (request.POST.get('valley_districts') or '').strip() or setting.valley_districts
    setting.default_delivery_time = (request.POST.get('default_delivery_time') or '').strip()
    setting.default_delivery_time_np = (request.POST.get('default_delivery_time_np') or '').strip()
    setting.show_covered_areas = request.POST.get('show_covered_areas') == 'on'
    setting.show_delivery_time = request.POST.get('show_delivery_time') == 'on'
    setting.save()
    return _saved(request, '✅ Delivery defaults saved.')


# ──────────────────── Smart helpers ────────────────────

@login_required
@admin_only
@require_POST
def delivery_charge_sync(request):
    """Create a district-level rule for every NCM district that has none.

    The tedious part of setting this up is typing 70-odd districts; this fills
    them in at the site default charge (or a charge given on the form) and
    leaves existing rules untouched.
    """
    locations = store_services.get_locations()
    if locations['source'] != 'ncm':
        messages.error(request, '❌ Could not reach NCM for the district list. Try again shortly.')
        return redirect('delivery_charge_setup')

    setting = DeliverySetting.get_solo()
    charge = _decimal(request.POST.get('sync_charge'), None)
    # A rule does not inherit the site-wide free threshold, so offer to stamp it
    # onto the rules being created — otherwise syncing every district would
    # silently switch free-delivery-over-Rs.-X off across the whole shop.
    free_above = _decimal(request.POST.get('sync_free_above'), None)
    valley = setting.valley_district_set
    existing = set(
        DeliveryCharge.objects.filter(branch_code='').values_list('district', flat=True))

    new_rules = []
    for entry in locations['districts']:
        district = entry['name'].strip().upper()
        if district in existing:
            continue
        if charge is not None:
            amount = charge
        else:
            amount = setting.inside_valley_charge if district in valley else setting.default_charge
        new_rules.append(DeliveryCharge(
            district=district,
            branch_code='',
            branch_name='',
            charge=amount,
            free_above=free_above,
            delivery_time=setting.default_delivery_time,
            delivery_time_np=setting.default_delivery_time_np,
            is_active=True,
        ))

    if not new_rules:
        messages.info(request, 'ℹ️ Every NCM district already has a delivery rule.')
        return redirect('delivery_charge_setup')

    DeliveryCharge.objects.bulk_create(new_rules)
    return _saved(request, f'✅ Added {len(new_rules)} district rule(s) from NCM.')


@login_required
@admin_only
def delivery_charge_export(request):
    """CSV of every rule — the same columns the import-free workflow expects."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="delivery_charges.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'District', 'Branch Code', 'Branch Name', 'Charge', 'Free Above',
        'Delivery Time', 'Delivery Time (Nepali)', 'Covered Areas', 'Note',
        'Active', 'Updated',
    ])
    for rule in DeliveryCharge.objects.all():
        writer.writerow([
            rule.district, rule.branch_code, rule.branch_name, rule.charge,
            '' if rule.free_above is None else rule.free_above,
            rule.delivery_time, rule.delivery_time_np, rule.covered_areas,
            rule.note, 'Yes' if rule.is_active else 'No',
            rule.updated_at.strftime('%Y-%m-%d %H:%M'),
        ])
    return response
