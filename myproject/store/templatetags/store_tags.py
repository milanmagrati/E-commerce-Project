import json

from django import template
from django.db.models import Avg

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


# ── Order form helpers ─────────────────────────────────────────────
# The order-form partial needs a list of dicts and a JSON config blob, neither
# of which the template language can build, so they are assembled here.

@register.simple_tag
def order_form_single_item(product, quantity=1, variant=''):
    """Item list for the one-product flows (product page, quick order)."""
    return [{
        'product_id': product.id,
        'name': product.name,
        'image': product_image_url(product),
        'qty': quantity,
        'price': product.price * quantity,
        'variant': variant,
    }]


@register.simple_tag
def order_form_cart_items(cart_items):
    """Item list for cart checkout."""
    return [{
        'product_id': item.product.id,
        'name': item.product.name,
        'image': product_image_url(item.product),
        'qty': item.quantity,
        'price': item.line_total,
        'variant': item.selected_variant,
    } for item in cart_items]


@register.simple_tag
def order_form_config(subtotal, unit_price=0, max_qty=99, qty_editable=False):
    """JSON blob order-form.js reads off the panel's data-config attribute."""
    return json.dumps({
        'subtotal': float(subtotal or 0),
        'unitPrice': float(unit_price or 0),
        'maxQty': int(max_qty or 99),
        'qtyEditable': bool(qty_editable),
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
