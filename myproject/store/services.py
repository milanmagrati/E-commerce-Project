"""Storefront helpers that sit between the guest order form and the rest of
the project: the NCM district/branch catalogue the form's two cascading
selects are built from, and delivery/discount pricing."""

import logging
from decimal import Decimal

from django.core.cache import cache

from .models import DiscountCode

logger = logging.getLogger(__name__)

# NCM's branch list is ~630 rows and changes rarely, so it is fetched once and
# cached; every product page would otherwise hit the courier's API.
BRANCH_CACHE_KEY = 'store:ncm_locations:v1'
BRANCH_CACHE_TTL = 60 * 60 * 12  # 12 hours

# Free delivery inside the valley, matching the note printed on the order form.
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


def is_inside_valley(district):
    return (district or '').strip().upper() in KATHMANDU_VALLEY_DISTRICTS


def delivery_charge_for(subtotal, district=''):
    """Delivery fee before any free-delivery coupon is applied."""
    subtotal = Decimal(str(subtotal or 0))
    if is_inside_valley(district):
        return INSIDE_VALLEY_DELIVERY
    if subtotal >= FREE_DELIVERY_THRESHOLD:
        return Decimal('0')
    return OUTSIDE_VALLEY_DELIVERY


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


def price_order(subtotal, district='', discount=None):
    """Final money for an order: {'subtotal', 'delivery', 'discount', 'total'}."""
    subtotal = Decimal(str(subtotal or 0))
    delivery = delivery_charge_for(subtotal, district)
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
