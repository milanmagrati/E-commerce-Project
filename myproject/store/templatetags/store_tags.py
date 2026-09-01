import json

from django import template
from django.db.models import Avg

from store import bulk_discounts
from store.models import ProductReview

register = template.Library()


@register.simple_tag
def product_avg_rating(product):
    reviews = ProductReview.objects.filter(product=product)
    if reviews.exists():
        return round(reviews.aggregate(avg=Avg('rating'))['avg'], 1)
    return 0


@register.simple_tag
def product_review_count(product):
    return ProductReview.objects.filter(product=product).count()


@register.simple_tag
def product_image_url(product):
    """Get the best image URL for a product."""
    if product.image:
        return product.image.url
    img = product.images.filter(is_featured=True).first()
    if img:
        return img.image.url
    img = product.images.first()
    if img:
        return img.image.url
    return ''


def _fmt_price(value):
    """'Rs. 450' — no trailing .00, thousands grouped like the rest of the store."""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0
    return 'Rs. ' + f'{n:,.0f}'


@register.simple_tag
def product_price_display(product):
    """Price line for a product card: a single price, or a 'Rs. X – Rs. Y'
    range when a variable product's variations are priced differently."""
    low, high = product.variation_price_range
    if low != high:
        return f'{_fmt_price(low)} – {_fmt_price(high)}'
    return _fmt_price(low)


@register.simple_tag
def product_bulk_teaser(product):
    """Deepest quantity break a listing card should tease, or None.

    Resolved entirely from a cached snapshot, so putting this on every card of
    a 20-product grid costs no extra queries. Variable products return None —
    their breaks are per option and belong on the product page.
    """
    return bulk_discounts.card_teaser(product)


# ── Order form helpers ─────────────────────────────────────────────
# The order-form partial needs a list of dicts and a JSON config blob, neither
# of which the template language can build, so they are assembled here.

@register.simple_tag
def order_form_single_item(product, quantity=1, variant='', unit_price=None, variation_id=''):
    """Item list for the one-product flows (product page, quick order)."""
    price = product.price if unit_price is None else unit_price
    return [{
        'product_id': product.id,
        'name': product.name,
        'image': product_image_url(product),
        'qty': quantity,
        'price': price * quantity,
        'variant': variant,
        'variation_id': variation_id or '',
    }]


@register.simple_tag
def order_form_cart_items(cart_items):
    """Item list for cart checkout."""
    return [{
        'product_id': item.product.id,
        'name': item.product.name,
        'image': (item.variation.image.url if item.variation and item.variation.image
                  else product_image_url(item.product)),
        'qty': item.quantity,
        'price': item.line_total,
        'variant': item.variant_label,
        'variation_id': item.variation_id or '',
    } for item in cart_items]


@register.simple_tag
def order_form_config(subtotal, unit_price=0, max_qty=99, qty_editable=False,
                      product=None, variation=None, list_price=None):
    """JSON blob order-form.js reads off the panel's data-config attribute.

    Pass `product` (and `variation`, for a variable one) on a panel whose
    quantity the shopper can change, and the ladder rides along so the panel
    re-prices itself as the quantity crosses a break.
    """
    tiers = []
    # A variable product with no option picked has no price to discount, so it
    # ships no ladder — the product page installs one per option instead.
    if product is not None and not (getattr(product, 'has_variations', False) and variation is None):
        tiers = [
            {
                'minQty': t['min_qty'],
                'unitPrice': float(t['unit_price']),
                'badge': t['badge'],
            }
            for t in bulk_discounts.tiers_for(product, variation)
        ]
    return json.dumps({
        'subtotal': float(subtotal or 0),
        'unitPrice': float(unit_price or 0),
        # The undiscounted rate the ladder discounts from — without it the
        # panel would treat an already-discounted opening price as the list.
        'listPrice': float(list_price if list_price is not None else (unit_price or 0)),
        'maxQty': int(max_qty or 99),
        'qtyEditable': bool(qty_editable),
        'tiers': tiers,
    })


@register.simple_tag
def of_value(form, name, default=''):
    """Submitted value for `name`, so a server-rendered re-display keeps what
    the shopper typed. Safe when `form` is unbound or missing the field."""
    if not form or name not in getattr(form, 'fields', {}):
        return default
    value = form[name].value()
    return default if value is None else value


@register.simple_tag
def of_error(form, name):
    """First error message for `name`, or '' when there is none."""
    if not form or name not in getattr(form, 'fields', {}):
        return ''
    errors = form[name].errors
    return errors[0] if errors else ''
