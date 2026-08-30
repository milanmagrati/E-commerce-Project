"""Storefront helpers that sit between the guest order form and the rest of
the project: the NCM district/branch catalogue the form's two cascading
selects are built from, and delivery/discount pricing."""

import logging
from decimal import Decimal

from django.core.cache import cache

from .models import DeliveryCharge, DeliverySetting, DiscountCode

logger = logging.getLogger(__name__)

# NCM's branch list is ~630 rows and changes rarely, so it is fetched once and
# cached; every product page would otherwise hit the courier's API.
BRANCH_CACHE_KEY = 'store:ncm_locations:v1'
BRANCH_CACHE_TTL = 60 * 60 * 12  # 12 hours

# Delivery rules + settings are read on every quote (which the order form fires
# as the shopper types), so they are cached and busted explicitly whenever the
# Delivery Charge setup page writes.
DELIVERY_CACHE_KEY = 'store:delivery_rules:v1'
DELIVERY_CACHE_TTL = 60 * 30

# Last-resort fallbacks, used only before an administrator has saved any
# delivery settings at all.
KATHMANDU_VALLEY_DISTRICTS = {'KATHMANDU', 'LALITPUR', 'BHAKTAPUR'}

FREE_DELIVERY_THRESHOLD = Decimal('500')
INSIDE_VALLEY_DELIVERY = Decimal('0')
OUTSIDE_VALLEY_DELIVERY = Decimal('100')


def _fallback_locations():
    """Used when NCM is unreachable so the form still submits — the shopper can
    still pick a district and type an address, we just cannot offer branches."""
    return {
        'districts': [
            {'name': d, 'province': '', 'branches': []}
            for d in sorted(KATHMANDU_VALLEY_DISTRICTS)
        ],
        'source': 'fallback',
    }


def get_locations(force_refresh=False):
    """District -> courier-branch catalogue for the order form.

    Shape: {'districts': [{'name', 'province', 'branches': [{'code','name',
    'address','phone','areas'}]}], 'source': 'ncm'|'fallback'}
    """
    if not force_refresh:
        cached = cache.get(BRANCH_CACHE_KEY)
        if cached:
            return cached

    try:
        from services.ncm_service import NCMService
        result = NCMService().get_branches()
    except Exception as exc:  # network, config, import — never break the page
        logger.warning("Store: could not load NCM branches: %s", exc)
        return _fallback_locations()

    if not result.get('success') or not result.get('data'):
        logger.warning("Store: NCM branch fetch failed: %s", result.get('error'))
        return _fallback_locations()

    by_district = {}
    for row in result['data']:
        district = (row.get('district_name') or '').strip().upper()
        if not district:
            continue
        entry = by_district.setdefault(district, {
            'name': district.title(),
            'province': (row.get('province_name') or '').strip().title(),
            'branches': [],
        })
        entry['branches'].append({
            'code': (row.get('code') or '').strip().upper(),
            'name': (row.get('name') or row.get('code') or '').strip().upper(),
            'address': (row.get('address') or '').strip(),
            'phone': (row.get('phone') or '').strip(),
            'areas': (row.get('areas_covered') or '').strip(),
        })

    for entry in by_district.values():
        entry['branches'].sort(key=lambda b: b['name'])

    data = {
        'districts': sorted(by_district.values(), key=lambda d: d['name']),
        'source': 'ncm',
    }
    cache.set(BRANCH_CACHE_KEY, data, BRANCH_CACHE_TTL)
    return data


def resolve_branch(district, branch_code):
    """Look a submitted branch code up in the catalogue.

    Returns the branch dict, or None when the code is unknown / the catalogue is
    unavailable. Callers treat None as "keep what the shopper typed" rather than
    as a validation failure, so a courier API outage cannot block an order.
    """
    if not branch_code:
        return None
    wanted_district = (district or '').strip().upper()
    branch_code = branch_code.strip().upper()
    for entry in get_locations()['districts']:
        if wanted_district and entry['name'].upper() != wanted_district:
            continue
        for branch in entry['branches']:
            if branch['code'] == branch_code:
                return branch
    return None


# ── Delivery charges ────────────────────────────────────────────────────

def invalidate_delivery_cache():
    """Called by the Delivery Charge setup page after any write."""
    cache.delete(DELIVERY_CACHE_KEY)


def _delivery_snapshot():
    """{'settings': {...}, 'rules': {(DISTRICT, BRANCH_CODE): {...}}} — the whole
    delivery setup flattened into plain data so it survives the cache."""
    cached = cache.get(DELIVERY_CACHE_KEY)
    if cached:
        return cached

    try:
        setting = DeliverySetting.get_solo()
        settings_data = {
            'inside_valley_charge': str(setting.inside_valley_charge),
            'default_charge': str(setting.default_charge),
            'free_delivery_threshold': str(setting.free_delivery_threshold),
            'valley_districts': sorted(setting.valley_district_set),
            'default_delivery_time': setting.default_delivery_time,
            'default_delivery_time_np': setting.default_delivery_time_np,
            'show_covered_areas': setting.show_covered_areas,
            'show_delivery_time': setting.show_delivery_time,
        }
        rules = {}
        for rule in DeliveryCharge.objects.filter(is_active=True):
            rules[(rule.district.upper(), rule.branch_code.upper())] = {
                'charge': str(rule.charge),
                'free_above': None if rule.free_above is None else str(rule.free_above),
                'delivery_time': rule.delivery_time,
                'delivery_time_np': rule.delivery_time_np,
                'covered_areas': rule.covered_area_list,
                'note': rule.note,
                'branch_name': rule.branch_name,
            }
    except Exception as exc:  # unmigrated DB, DB blip — never break checkout
        logger.warning("Store: delivery setup unreadable, using fallbacks: %s", exc)
        return {
            'settings': {
                'inside_valley_charge': str(INSIDE_VALLEY_DELIVERY),
                'default_charge': str(OUTSIDE_VALLEY_DELIVERY),
                'free_delivery_threshold': str(FREE_DELIVERY_THRESHOLD),
                'valley_districts': sorted(KATHMANDU_VALLEY_DISTRICTS),
                'default_delivery_time': '',
                'default_delivery_time_np': '',
                'show_covered_areas': True,
                'show_delivery_time': True,
            },
            'rules': {},
        }

    snapshot = {'settings': settings_data, 'rules': rules}
    cache.set(DELIVERY_CACHE_KEY, snapshot, DELIVERY_CACHE_TTL)
    return snapshot


def delivery_settings():
    """The site-wide delivery defaults, as plain strings/bools."""
    return _delivery_snapshot()['settings']


def is_inside_valley(district):
    valley = set(_delivery_snapshot()['settings']['valley_districts']) or KATHMANDU_VALLEY_DISTRICTS
    return (district or '').strip().upper() in valley


def free_delivery_threshold():
    """Site-wide 'free over Rs. X' figure, for the copy on the product page."""
    return Decimal(_delivery_snapshot()['settings']['free_delivery_threshold'])


def quote_delivery(subtotal, district='', branch_code=''):
    """Everything the order form shows about delivery for one destination.

    Returns {'charge', 'base_charge', 'is_free', 'free_reason', 'delivery_time',
    'delivery_time_np', 'covered_areas', 'note', 'matched', 'inside_valley',
    'free_above'}. A branch rule beats its district rule; with neither, the
    site-wide valley/default charge applies, so an unlisted district still
    quotes a price instead of blocking the order.
    """
    snapshot = _delivery_snapshot()
    conf = snapshot['settings']
    subtotal = Decimal(str(subtotal or 0))
    district_key = (district or '').strip().upper()
    branch_key = (branch_code or '').strip().upper()

    rule = None
    if district_key:
        rule = (snapshot['rules'].get((district_key, branch_key))
                or snapshot['rules'].get((district_key, '')))

    inside_valley = district_key in (set(conf['valley_districts']) or KATHMANDU_VALLEY_DISTRICTS)
    site_threshold = Decimal(conf['free_delivery_threshold'])

    if rule:
        base = Decimal(rule['charge'])
        # A rule is authoritative about its own district: the site-wide "free
        # above Rs. X" belongs to the *default* pricing, so it must not quietly
        # zero out a charge somebody set on purpose. A district that should go
        # free on big orders says so in its own `free_above`.
        free_above = Decimal(rule['free_above']) if rule['free_above'] is not None else Decimal('0')
        delivery_time = rule['delivery_time'] or conf['default_delivery_time']
        delivery_time_np = rule['delivery_time_np'] or conf['default_delivery_time_np']
        covered = list(rule['covered_areas'])
        note = rule['note']
    else:
        base = Decimal(conf['inside_valley_charge'] if inside_valley else conf['default_charge'])
        free_above = site_threshold
        delivery_time = conf['default_delivery_time']
        delivery_time_np = conf['default_delivery_time_np']
        covered = []
        note = ''

    # Nothing typed yet is not the same as "free" — leave that to the caller.
    free_reason = ''
    charge = base
    if base <= 0:
        free_reason = 'zone'
        charge = Decimal('0')
    elif free_above > 0 and subtotal >= free_above:
        free_reason = 'threshold'
        charge = Decimal('0')

    if conf['show_covered_areas'] and district_key and not covered:
        branch = resolve_branch(district_key, branch_key) if branch_key else None
        if branch and branch.get('areas'):
            covered = [a.strip() for a in branch['areas'].replace('\n', ',').split(',') if a.strip()]
    if not conf['show_covered_areas']:
        covered = []
    if not conf['show_delivery_time']:
        delivery_time = delivery_time_np = ''

    return {
        'charge': charge.quantize(Decimal('0.01')),
        'base_charge': base.quantize(Decimal('0.01')),
        'is_free': charge <= 0,
        'free_reason': free_reason,
        'free_above': free_above,
        'delivery_time': delivery_time,
        'delivery_time_np': delivery_time_np,
        'covered_areas': covered,
        'note': note,
        'matched': rule is not None,
        'inside_valley': inside_valley,
    }


def delivery_charge_for(subtotal, district='', branch_code=''):
    """Delivery fee before any free-delivery coupon is applied."""
    return quote_delivery(subtotal, district, branch_code)['charge']


def lookup_discount(code, subtotal):
    """Return (discount_code_or_None, message). Message is the error when the
    code is unusable, or a short success line when it is."""
    code = (code or '').strip().upper()
    if not code:
        return None, ''
    try:
        discount = DiscountCode.objects.get(code=code)
    except DiscountCode.DoesNotExist:
        return None, 'That discount code is not valid.'

    ok, message = discount.check_valid(subtotal)
    if not ok:
        return None, message

    amount = discount.discount_for(subtotal)
    label = f'Rs. {amount:.0f} off applied.'
    if discount.free_delivery:
        label += ' Free delivery included.'
    return discount, label


def price_order(subtotal, district='', discount=None, branch_code=''):
    """Final money for an order: {'subtotal', 'delivery', 'discount', 'total'}."""
    subtotal = Decimal(str(subtotal or 0))
    delivery = delivery_charge_for(subtotal, district, branch_code)
    discount_amount = Decimal('0')
    if discount is not None:
        discount_amount = discount.discount_for(subtotal)
        if discount.free_delivery:
            delivery = Decimal('0')
    total = subtotal - discount_amount + delivery
    return {
        'subtotal': subtotal.quantize(Decimal('0.01')),
        'delivery': delivery.quantize(Decimal('0.01')),
        'discount': discount_amount.quantize(Decimal('0.01')),
        'total': max(total, Decimal('0')).quantize(Decimal('0.01')),
    }
