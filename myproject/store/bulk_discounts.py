"""Quantity-break pricing for the storefront.

One module, one truth. The product card, the product page, the cart line, the
order form and the order that finally gets written all ask the same two
questions here:

* :func:`tiers_for` — what breaks exist for this product / variation?
* :func:`price_for` — what does *this many* units actually cost?

Rules live in :class:`store.models.BulkDiscount` and are administered from
Setup → Bulk Discounts. They are read on every product card, so the whole set
is flattened into one cached snapshot and resolved in memory; the setup page
calls :func:`invalidate_cache` after every write.
"""

import logging
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY = 'store:bulk_discounts:v1'
# Short on purpose. `invalidate_cache` only reaches the process that handled
# the write, and the configured cache is LocMemCache — per process — so any
# other worker keeps its snapshot until this expires. These rules decide what
# a shopper is *charged*, so the ceiling on that staleness is a minute, not
# the half hour the delivery rules can afford. Rebuilding is two queries over
# a small table.
CACHE_TTL = 60

# How many rungs a listing card has room for without crowding out the price
# and the buy button.
CARD_RUNGS = 3

_PENNY = Decimal('0.01')


def invalidate_cache():
    """Called by the Bulk Discount setup page after any write."""
    cache.delete(CACHE_KEY)


# ── the snapshot ────────────────────────────────────────────────────────

def _tier_payload(tier):
    return {
        'id': tier.pk,
        'min_qty': int(tier.min_qty or 1),
        'type': tier.discount_type,
        'value': str(tier.value),
        'label': tier.label or '',
        'offer_label': tier.offer_label,
    }


def _rule_payload(rule):
    return {
        'id': rule.pk,
        'name': rule.name or '',
        'scope': rule.scope,
        'priority': int(rule.priority or 0),
        'badge_text': rule.badge_text or '',
        'show_on_cards': bool(rule.show_on_cards),
        'note': rule.note or '',
        'tiers': [_tier_payload(t) for t in rule.tiers.all()],
    }


def _better(candidate, incumbent):
    """Two rules point at the same target — keep the one that should win.

    Higher priority first, then the more recently created row, so an admin who
    adds a replacement rule without deleting the old one gets the new one.
    """
    if incumbent is None:
        return True
    if candidate['priority'] != incumbent['priority']:
        return candidate['priority'] > incumbent['priority']
    return candidate['id'] > incumbent['id']


def _build_snapshot():
    """Every live rule, indexed by what it targets."""
    from .models import BulkDiscount

    snapshot = {'variation': {}, 'product': {}, 'category': {}, 'all': None}
    now = timezone.now()

    rules = (BulkDiscount.objects
             .filter(is_active=True)
             .prefetch_related('tiers'))
    for rule in rules:
        if rule.starts_at and now < rule.starts_at:
            continue
        if rule.ends_at and now > rule.ends_at:
            continue
        payload = _rule_payload(rule)
        if not payload['tiers']:
            continue  # a rule with no rungs discounts nothing

        if rule.scope == BulkDiscount.SCOPE_VARIATION and rule.variation_id:
            bucket, key = snapshot['variation'], rule.variation_id
        elif rule.scope == BulkDiscount.SCOPE_PRODUCT and rule.product_id:
            bucket, key = snapshot['product'], rule.product_id
        elif rule.scope == BulkDiscount.SCOPE_CATEGORY and rule.category_id:
            bucket, key = snapshot['category'], rule.category_id
        elif rule.scope == BulkDiscount.SCOPE_ALL:
            if _better(payload, snapshot['all']):
                snapshot['all'] = payload
            continue
        else:
            continue  # scope names a target that has since been deleted

        if _better(payload, bucket.get(key)):
            bucket[key] = payload
    return snapshot


def _snapshot():
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    try:
        snapshot = _build_snapshot()
    except Exception as exc:  # unmigrated DB, DB blip — never break a page
        logger.warning('Store: bulk discounts unreadable, pricing at list price: %s', exc)
        return {'variation': {}, 'product': {}, 'category': {}, 'all': None}
    cache.set(CACHE_KEY, snapshot, CACHE_TTL)
    return snapshot


# ── resolution ──────────────────────────────────────────────────────────

def rule_for(product, variation=None):
    """The one rule that governs this line, or None.

    Most specific target wins: the variation's own rule, then the product's,
    then its category's, then a shop-wide rule.
    """
    if product is None:
        return None
    snapshot = _snapshot()

    # Callers hand over a ProductVariation, or just its id from a cart row.
    variation_id = variation if isinstance(variation, int) else getattr(variation, 'pk', None)
    if variation_id:
        found = snapshot['variation'].get(variation_id)
        if found:
            return found

    found = snapshot['product'].get(getattr(product, 'pk', None))
    if found:
        return found

    category_id = getattr(product, 'category_id', None)
    if category_id:
        found = snapshot['category'].get(category_id)
        if found:
            return found

    return snapshot['all']


def base_price_for(product, variation=None):
    """The undiscounted price of one unit of this line."""
    if variation is not None and getattr(variation, 'price', None) is not None:
        return Decimal(str(variation.price))
    return Decimal(str(getattr(product, 'price', 0) or 0))


def _unit_price(tier, base):
    """Rung price without needing the model instance — the snapshot is plain
    data, so the arithmetic in BulkDiscountTier.unit_price_from lives here too."""
    base = Decimal(str(base or 0))
    if base <= 0:
        return Decimal('0.00')
    value = Decimal(tier['value'])
    if tier['type'] == 'percent':
        pct = max(Decimal('0'), min(value, Decimal('100')))
        price = base * (Decimal('100') - pct) / Decimal('100')
    elif tier['type'] == 'amount':
        price = base - max(Decimal('0'), value)
    else:
        price = max(Decimal('0'), value)
    return min(max(price, Decimal('0')), base).quantize(_PENNY)


def tiers_for(product, variation=None, base_price=None):
    """Every rung of the governing rule, priced against this line.

    Each entry is a plain dict the templates and the JSON blobs both use::

        {'min_qty', 'label', 'offer_label', 'badge', 'badge_lead',
         'badge_value', 'unit_price', 'unit_price_display', 'save_each',
         'save_percent', 'save_total'}

    ``badge`` is the whole line ("Save 10%"). ``badge_lead`` + ``badge_value``
    are the same line split at the verb, so a narrow listing card can drop the
    word and keep the number.

    Empty list when nothing applies. Rungs that save nothing (a 0% rule, or a
    fixed price above the list price) are dropped — a chip promising no saving
    is worse than no chip.
    """
    rule = rule_for(product, variation)
    if not rule:
        return []

    base = Decimal(str(base_price)) if base_price is not None else base_price_for(product, variation)
    if base <= 0:
        return []

    out = []
    for tier in rule['tiers']:
        unit = _unit_price(tier, base)
        save_each = (base - unit).quantize(_PENNY)
        if save_each <= 0:
            continue
        qty = max(1, int(tier['min_qty']))
        save_percent = (save_each / base * Decimal('100')).quantize(Decimal('0.1'))
        # The chip reads "8pcs / Save Rs. 120 / Rs. 380 each", so the middle
        # line always states the saving. `offer_label` describes the *rule*
        # ("Rs. 380 each"), which would just repeat the line below it.
        if tier['type'] == 'percent':
            # 10.0 → "10", 7.5 → "7.5". Decimal's 'g' keeps the exponent, so
            # the trailing zero is trimmed by hand.
            value = f"{save_percent:.1f}".rstrip('0').rstrip('.') + '%'
        else:
            value = f'Rs. {save_each:,.0f}'
        lead = 'Save'
        if rule['badge_text']:
            # An admin's own wording is one phrase — there is no verb to split
            # off, so it survives whole or not at all.
            lead, value = '', rule['badge_text']
        out.append({
            'min_qty': qty,
            'label': tier['label'],
            'offer_label': tier['offer_label'],
            'badge': f'{lead} {value}'.strip(),
            'badge_lead': lead,
            'badge_value': value,
            'type': tier['type'],
            'unit_price': unit,
            'unit_price_display': f'Rs. {unit:,.0f}',
            'save_each': save_each,
            'save_percent': save_percent,
            'save_total': (save_each * qty).quantize(_PENNY),
        })
    out.sort(key=lambda t: t['min_qty'])
    return out


def summary_badge(product, variation=None, base_price=None):
    """One line summing the whole ladder up, or ''.

    Used where the offer only needs *flagging* — the option cards on a product
    page — so the card says "there is a deal here, this deep" and leaves the
    rungs themselves to the ladder beside the quantity stepper. A rule with its
    own badge text keeps it; otherwise a multi-rung ladder reads "up to".
    """
    tiers = tiers_for(product, variation, base_price)
    if not tiers:
        return ''
    rule = rule_for(product, variation)
    if rule and rule['badge_text']:
        return rule['badge_text']

    best = max(tiers, key=lambda t: t['save_each'])
    lead = 'Save up to' if len(tiers) > 1 else 'Save'
    if best['type'] == 'percent':
        pct = f"{best['save_percent']:.1f}".rstrip('0').rstrip('.')
        return f'{lead} {pct}%'
    return f"{lead} Rs. {best['save_each']:,.0f}"


def _card_eligible(product):
    """Whether a listing card may advertise a quantity break at all.

    Only for products a shopper can buy straight from the card — a variable
    product prices per variation, so its offer belongs on the product page
    where an option has actually been picked, and a sold-out product must not
    advertise a quantity break it cannot honour.
    """
    if getattr(product, 'has_variations', False):
        return False
    if not getattr(product, 'storefront_available', True):
        return False
    rule = rule_for(product)
    return bool(rule and rule['show_on_cards'])


def card_ladder(product, limit=CARD_RUNGS):
    """The rungs a listing card shows — the same ladder as the product page.

    Teasing only the deepest rung advertises the *hardest* offer to reach: a
    shopper who would happily take 2 sees "8pcs" and reads the whole thing as
    out of reach, and the card then contradicts the product page it links to.
    Showing the ladder states both the cheapest way in and the best deal going.

    Long ladders are thinned rather than truncated, so the first and last rungs
    always survive — the entry price and the best price are the two a card
    cannot afford to drop.
    """
    if not _card_eligible(product):
        return []
    return thin(tiers_for(product), limit)


def thin(tiers, limit=CARD_RUNGS):
    """At most ``limit`` rungs, evenly spread, both ends kept."""
    if limit < 1 or len(tiers) <= limit:
        return list(tiers)
    if limit == 1:
        return [tiers[-1]]
    last = len(tiers) - 1
    picked, seen = [], set()
    for i in range(limit):
        idx = round(i * last / (limit - 1))
        if idx not in seen:
            seen.add(idx)
            picked.append(tiers[idx])
    return picked


def price_for(product, variation=None, quantity=1, base_price=None):
    """What ``quantity`` units of this line actually cost.

    Returns ``{'base_unit', 'unit', 'line_total', 'base_line_total', 'saved',
    'tier', 'next_tier', 'need_more'}``. ``tier`` is the rung in force (None at
    list price); ``next_tier`` + ``need_more`` drive the "add 2 more to save
    10%" nudge.

    The rung that wins is the cheapest applicable one, not simply the highest
    ``min_qty`` — a mis-ordered ladder can then never charge a shopper *more*
    for taking more.
    """
    qty = max(1, int(quantity or 1))
    base = Decimal(str(base_price)) if base_price is not None else base_price_for(product, variation)
    tiers = tiers_for(product, variation, base)

    applicable = [t for t in tiers if qty >= t['min_qty']]
    tier = min(applicable, key=lambda t: t['unit_price']) if applicable else None
    upcoming = [t for t in tiers if t['min_qty'] > qty]
    next_tier = min(upcoming, key=lambda t: t['min_qty']) if upcoming else None

    unit = tier['unit_price'] if tier else base.quantize(_PENNY)
    base_line = (base * qty).quantize(_PENNY)
    line = (unit * qty).quantize(_PENNY)
    return {
        'base_unit': base.quantize(_PENNY),
        'unit': unit,
        'line_total': line,
        'base_line_total': base_line,
        'saved': (base_line - line).quantize(_PENNY),
        'tier': tier,
        'next_tier': next_tier,
        'need_more': (next_tier['min_qty'] - qty) if next_tier else 0,
        'tiers': tiers,
    }


def unit_price_for(product, variation=None, quantity=1, base_price=None):
    """Just the per-unit figure — the hot path for cart lines."""
    return price_for(product, variation, quantity, base_price)['unit']


# ── JSON for the browser ────────────────────────────────────────────────

def _tier_json(tier):
    # `lineTotal`/`wasTotal`/`saveTotal` are what a rung is actually offering —
    # the price of *that many* units. Theme 2's bundle cards quote them
    # directly; Theme 1 ignores them and keeps using the unit price.
    return {
        'minQty': tier['min_qty'],
        'label': tier['label'],
        'type': tier['type'],
        'offer': tier['offer_label'],
        'badge': tier['badge'],
        'unitPrice': float(tier['unit_price']),
        'saveEach': float(tier['save_each']),
        'savePercent': float(tier['save_percent']),
        'lineTotal': float(tier['unit_price'] * tier['min_qty']),
        'wasTotal': float((tier['unit_price'] + tier['save_each']) * tier['min_qty']),
        'saveTotal': float(tier['save_each'] * tier['min_qty']),
    }


def tiers_payload(product, variations=None):
    """Ladders for the product page, keyed the way product.js looks them up.

    ``{'0': [...]}`` is the plain-product ladder; every other key is a
    variation id. A variable product ships one entry per option so switching
    options re-prices without another request.
    """
    payload = {}
    if variations:
        for variation in variations:
            payload[str(variation.pk)] = [
                _tier_json(t) for t in tiers_for(product, variation)
            ]
    else:
        payload['0'] = [_tier_json(t) for t in tiers_for(product)]
    return payload
