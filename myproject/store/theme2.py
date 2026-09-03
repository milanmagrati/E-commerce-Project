"""Theme 2 — the conversion product landing page.

The storefront ships two product-page designs and this module is the whole of
the second one's *resolution* layer: which design a product page uses, what
copy each block gets, and how the admin's free-text fields become the lists the
template loops over. Nothing here renders HTML — every helper returns plain
data, and returns an empty one when the admin has filled nothing in, so a store
that configures none of this gets the page it had rather than ten empty cards.

Precedence, once, for every field::

    ProductThemeOverride.<field>   (blank means "inherit")
    ProductPageTheme.<field>       (the site-wide setting)
    DEFAULTS[<field>]              (what ships out of the box)

`layout_for()` is asked by the router, and `is_theme2()` by anything that needs
to know which page is being drawn; both read one cached snapshot of the
singleton, which `invalidate_cache()` drops after every write from
Setup → Product Page Theme.
"""

import json
import logging
import re
from decimal import Decimal, InvalidOperation

from django.core.cache import cache

from .models import (LAYOUT_CHOICES, LAYOUT_THEME1, LAYOUT_THEME2,
                     ProductPageTheme, ProductThemeOverride)

logger = logging.getLogger(__name__)

# The settings row is read on every product page, so it is cached. LocMemCache
# is per process, so a change made in one worker reaches the others only when
# this expires — kept short because it decides which *design* renders, and an
# admin switching themes expects to see it immediately.
CACHE_KEY = 'store:page_theme:v1'
CACHE_TTL = 60

# What ships before an administrator has typed anything. The proof and how-to
# blocks are deliberately blank: an empty "How to use" card is worse than no
# card, and `steps_html`-style helpers below return [] for them.
DEFAULTS = {
    'layout': LAYOUT_THEME1,

    'highlights': "High Precision Components\nEco-friendly Materials\nAdvanced Interaction Design",
    'pay_chips': 'Prepaid, COD',
    'pay_bullets': "Prepaid available\nCash on delivery available",

    'return_label': 'Return policy',
    'return_value': '7 Days Replacement',
    'warranty_label': 'Warranty info',
    'warranty_value': '1 Year Warranty',
    'shipping_label': 'Shipping',
    'shipping_value': 'Delivery charged by district',

    'trust_json': ('[{"icon": "delivery", "text": "Nationwide delivery"},'
                   ' {"icon": "payment", "text": "Cash on delivery"},'
                   ' {"icon": "secure", "text": "Genuine products"}]'),

    'buy_label': 'Buy now',
    'info_title': 'Product information',
    'video_title': 'See it in action',
    'videos': '',

    'buy_action': 'modal',
    'ship_fee': '',
    'checkout_title': 'Checkout',
    'place_label': 'Place order',
    'cod_label': 'Cash on delivery',
    'phone_prefix': '',
    'phone_hint': '',

    'stat_text': '',
    'stat_image': '',
    'benefit_text': '',
    'benefit_image': '',
    'steps_title': 'How to use',
    'steps': '',
    'ingredients_title': 'Key ingredients',
    'ingredients': '',

    'footer_address': '',
    'footer_phone': '',
    'footer_credit': '',
}

# Fields a single product may override. Anything outside this set is global
# only — the per-product form and the save loop both walk this list, so the two
# cannot drift.
OVERRIDABLE = (
    'highlights', 'pay_chips', 'pay_bullets',
    'return_value', 'warranty_value', 'shipping_value',
    'videos', 'stat_text', 'stat_image', 'benefit_text', 'benefit_image',
    'steps', 'ingredients', 'ship_fee',
)

# Icon keys the trust row and the rail are allowed to name. An unknown key
# falls back to `secure` rather than rendering a hole.
TRUST_ICONS = ('delivery', 'payment', 'secure', 'return', 'warranty', 'support')


# ── cache + settings ────────────────────────────────────────────────────

def invalidate_cache():
    """Called by Setup → Product Page Theme after any write."""
    cache.delete(CACHE_KEY)


def settings_snapshot():
    """The singleton flattened to plain strings, cached.

    Never raises: an unmigrated database or a DB blip falls back to DEFAULTS,
    which means Theme 1 — the design that was already there.
    """
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    snapshot = dict(DEFAULTS)
    try:
        row = ProductPageTheme.objects.first()
        if row is not None:
            for key in DEFAULTS:
                value = getattr(row, key, '')
                snapshot[key] = '' if value is None else str(value)
    except Exception as exc:  # unmigrated DB, DB blip — never break a page
        logger.warning('Store: product page theme unreadable, using Theme 1: %s', exc)
        return dict(DEFAULTS)

    cache.set(CACHE_KEY, snapshot, CACHE_TTL)
    return snapshot


# ── layout resolution ───────────────────────────────────────────────────

def layouts():
    """The choice list, shared by the admin selects and the read-time
    validator, so a stored value can never name a design that does not exist."""
    return dict(LAYOUT_CHOICES)


def override_for(product):
    """This product's override row, or None. Safe on an unmigrated DB."""
    if product is None:
        return None
    try:
        return ProductThemeOverride.objects.filter(product=product).first()
    except Exception as exc:
        logger.warning('Store: product theme override unreadable: %s', exc)
        return None


def layout_for(product, override=None):
    """Which design this product's page uses: 'theme1' or 'theme2'.

    A blank override inherits the global; an unrecognised value anywhere falls
    back to Theme 1, which is the design that was already shipping.
    """
    if override is None:
        override = override_for(product)
    chosen = (getattr(override, 'layout', '') or '').strip()
    if chosen in layouts():
        return chosen
    chosen = (settings_snapshot().get('layout') or '').strip()
    return chosen if chosen in layouts() else LAYOUT_THEME1


def is_theme2(product, override=None):
    return layout_for(product, override) == LAYOUT_THEME2


# ── field accessors ─────────────────────────────────────────────────────

def field(product, key, override=None):
    """One resolved string: the product's, then the site's, then the default."""
    if override is None and product is not None:
        override = override_for(product)
    if key in OVERRIDABLE:
        value = (getattr(override, key, '') or '')
        if str(value).strip() != '':
            return str(value)
    value = settings_snapshot().get(key, '')
    if str(value).strip() != '':
        return str(value)
    return DEFAULTS.get(key, '')


# ── shared parsers ──────────────────────────────────────────────────────

def lines(raw):
    """Newline-separated admin text → a list, blanks dropped."""
    return [line.strip() for line in str(raw or '').splitlines() if line.strip()]


def csv_list(raw):
    """Comma-separated admin text → a list, blanks dropped."""
    return [part.strip() for part in str(raw or '').split(',') if part.strip()]


def cells(line, count):
    """Split one `a | b | c` line into exactly `count` trimmed cells.

    Padding is what lets every trailing field be optional without a single
    length check at the call site.
    """
    parts = [part.strip() for part in str(line or '').split('|')]
    parts = parts[:count]
    parts += [''] * (count - len(parts))
    return parts


def media_url(value):
    """An admin's media reference → a URL the page can use.

    Accepts a Media Library asset id, an absolute URL, a site-absolute path, or
    a path relative to MEDIA_URL — the four things an administrator actually
    pastes into a text field.
    """
    value = str(value or '').strip()
    if not value:
        return ''
    if value.isdigit():
        try:
            from dashboard.models import MediaAsset
            asset = MediaAsset.objects.filter(pk=int(value)).first()
            if asset and asset.image:
                return asset.image.url
        except Exception as exc:
            logger.warning('Store: media asset %s unreadable: %s', value, exc)
        return ''
    if value.startswith(('http://', 'https://', '//', '/')):
        return value
    from django.conf import settings as django_settings
    return (django_settings.MEDIA_URL or '/media/') + value.lstrip('/')


_NUMBER_RE = re.compile(r'\d+(?:\.\d+)?')


def parse_fee(raw):
    """A money figure out of free text, or None when the text holds no number.

    The field is free text so an administrator can type `Rs. 100`. Stripping
    non-digits is the wrong move and fails silently: it leaves the
    abbreviation's full stop behind as `.100`, so a hundred-rupee delivery is
    charged as ten paisa. Match the first *number* instead, after dropping the
    thousands separators that would otherwise cut `1,000` down to `1`.
    """
    text = str(raw or '').replace(',', '')
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    try:
        value = Decimal(match.group(0))
    except InvalidOperation:
        return None
    return max(Decimal('0'), value)


def ship_fee_for(product, override=None):
    """The flat Theme 2 delivery fee, or None to price by district.

    Resolution is per-product fee, then the store-wide fee; a field holding no
    number at all (including a blank one) means "leave it to Setup → Delivery
    Charge Setup", which is the default and the accurate answer for a courier
    priced by destination.
    """
    if override is None and product is not None:
        override = override_for(product)
    for raw in ((getattr(override, 'ship_fee', '') or ''),
                settings_snapshot().get('ship_fee', '')):
        if str(raw).strip() == '':
            continue
        fee = parse_fee(raw)
        if fee is not None:
            return fee
    return None


# ── content blocks ──────────────────────────────────────────────────────
# Each returns plain data and an empty one when there is nothing to say, so
# the template can skip the whole card with a single `{% if %}`.

def videos(product, override=None):
    """`video | poster | creator` lines → the 9:16 video cards.

    Self-hosted files only: the play/pause, mute and expand controls are bound
    to a real <video> element, which a YouTube or Vimeo iframe is not.
    """
    out = []
    for line in lines(field(product, 'videos', override)):
        src, poster, creator = cells(line, 3)
        src = media_url(src)
        if not src:
            continue
        out.append({
            'src': src,
            'poster': media_url(poster),
            'is_creator': creator.lower() == 'creator',
        })
    return out


def trust_stat(product, override=None):
    """{'figure', 'body', 'image'} or None. First line is the big number."""
    rows = lines(field(product, 'stat_text', override))
    image = media_url(field(product, 'stat_image', override))
    if not rows:
        return None
    return {'figure': rows[0], 'body': rows[1:], 'image': image}


def benefit(product, override=None):
    """{'text', 'image'} or None."""
    text = field(product, 'benefit_text', override).strip()
    image = media_url(field(product, 'benefit_image', override))
    if not text and not image:
        return None
    return {'text': text, 'image': image}


def steps(product, override=None):
    """`title | instruction | image` lines → numbered how-to steps.

    The numbers come from the line order, never from the admin's typing, so
    deleting the second step renumbers the rest instead of leaving a gap.
    """
    out = []
    for index, line in enumerate(lines(field(product, 'steps', override)), start=1):
        title, instruction, image = cells(line, 3)
        if not title and not instruction:
            continue
        out.append({
            'number': index,
            'title': title,
            'instruction': instruction,
            'image': media_url(image),
        })
    return out


def ingredients(product, override=None):
    """`name | image | note` lines → the key-ingredients grid.

    A grid, not tabs: the reference shows one at a time only because it has
    nowhere to put six, and a grid says the same thing without asking anyone
    to click.
    """
    out = []
    for line in lines(field(product, 'ingredients', override)):
        name, image, note = cells(line, 3)
        if not name and not image:
            continue
        out.append({'name': name, 'image': media_url(image), 'note': note})
    return out


def trust_row(product, override=None):
    """The three icon + label columns under BUY NOW, from the JSON field."""
    raw = field(product, 'trust_json', override)
    try:
        parsed = json.loads(raw) if raw.strip() else []
    except (ValueError, TypeError):
        logger.warning('Store: product page trust row is not valid JSON, ignoring it.')
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for entry in parsed[:4]:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get('text') or '').strip()
        if not text:
            continue
        icon = str(entry.get('icon') or '').strip().lower()
        out.append({'icon': icon if icon in TRUST_ICONS else 'secure', 'text': text})
    return out


def rail(product, override=None):
    """Column C: highlights, payment chips and bullets, and the three rows."""
    return {
        'highlights': lines(field(product, 'highlights', override)),
        'pay_chips': csv_list(field(product, 'pay_chips', override)),
        'pay_bullets': lines(field(product, 'pay_bullets', override)),
        'rows': [
            {'icon': 'return',
             'label': field(product, 'return_label', override),
             'value': field(product, 'return_value', override)},
            {'icon': 'warranty',
             'label': field(product, 'warranty_label', override),
             'value': field(product, 'warranty_value', override)},
            {'icon': 'delivery',
             'label': field(product, 'shipping_label', override),
             'value': field(product, 'shipping_value', override),
             'accent': True},
        ],
    }


def content_blocks(product, override=None):
    """Everything Theme 2 needs that Theme 1 does not, in one call.

    Column A's blocks are returned in funnel order — show it, prove it, teach
    it, describe it — which is not the order the settings screen lists them in.
    """
    if override is None:
        override = override_for(product)
    return {
        'videos': videos(product, override),
        'video_title': field(product, 'video_title', override),
        'stat': trust_stat(product, override),
        'benefit': benefit(product, override),
        'steps': steps(product, override),
        'steps_title': field(product, 'steps_title', override),
        'ingredients': ingredients(product, override),
        'ingredients_title': field(product, 'ingredients_title', override),
        'info_title': field(product, 'info_title', override),
        'trust_row': trust_row(product, override),
        'buy_label': field(product, 'buy_label', override),
        'rail': rail(product, override),
        'shell': {
            'address': field(product, 'footer_address', override),
            'phone': field(product, 'footer_phone', override),
            'credit': field(product, 'footer_credit', override),
        },
        'checkout': {
            'action': ('checkout'
                       if field(product, 'buy_action', override) == 'checkout'
                       else 'modal'),
            'title': field(product, 'checkout_title', override),
            'place_label': field(product, 'place_label', override),
            'cod_label': field(product, 'cod_label', override),
            'phone_prefix': field(product, 'phone_prefix', override),
            'phone_hint': field(product, 'phone_hint', override),
        },
    }
