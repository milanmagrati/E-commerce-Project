"""The one-step COD checkout behind Theme 2's BUY NOW button.

Three decisions worth stating once, because every one of them is a thing that
goes quietly wrong when it is not:

1. **The modal never touches the cart.** The order is built straight from the
   product, the way `store.views.quick_order` already does it. A shopper may
   already have a cart, so adding to it would either put someone else's items
   on this order or lose what they had; the modal has its own stepper, which
   would fight cart-line merging on every press; and abandoning a dialog must
   cost nothing.

2. **`quote()` is the only thing that decides money.** It is called twice —
   once to render the summary, once inside the place-order handler — and
   *nothing* about price is ever read from the request. A tampered total
   therefore cannot decide what anyone is charged.

3. **Delivery is priced by the real delivery engine.** Setup → Delivery Charge
   Setup already prices per district and per courier branch, and a flat number
   typed into the theme settings only overrides it when an administrator
   deliberately fills that field in.
"""

from decimal import Decimal

from dashboard.models import Product

from . import bulk_discounts, services, theme2

# A ceiling on one line, so a pasted quantity cannot ask the pricing engine to
# multiply by a million. Real stock clamps below this almost always.
MAX_LINE_QTY = 999

_PENNY = Decimal('0.01')


def _money(value):
    return Decimal(str(value or 0)).quantize(_PENNY)


def _error(message):
    return {'error': message}


def resolve_variation(product, variation_id):
    """(variation, error). The variation must exist *and* belong to `product`.

    Checking the parent is the point: without it, a posted id from another
    product would price this order at that product's variation.
    """
    raw = str(variation_id or '').strip()
    if raw:
        if not raw.isdigit():
            return None, 'That option is no longer available. Please pick another.'
        variation = product.active_variations.filter(pk=int(raw)).first()
        if variation is None:
            return None, 'That option is no longer available. Please pick another.'
        return variation, ''
    if product.has_variations:
        return None, 'Please choose an option first.'
    return None, ''


def quote(product_id, variation_id='', qty=1, district='', branch_code=''):
    """Everything the modal shows and the order is written from.

    Returns ``{'error': ...}`` on any refusal, otherwise::

        {'product', 'variation', 'variant_label', 'qty', 'stock',
         'base_unit', 'unit', 'subtotal', 'was', 'saved', 'tier',
         'shipping', 'shipping_is_free', 'shipping_source', 'total', 'nudge'}

    ``qty`` comes back clamped — the caller must use the returned figure, not
    the one it asked for.
    """
    try:
        product = Product.objects.filter(
            pk=int(product_id), is_active=True, is_deleted=False).first()
    except (TypeError, ValueError):
        product = None
    if product is None:
        return _error('This product is no longer available.')

    # Theme 2 only. A Theme 1 product has no modal, so a request naming one is
    # either stale or forged; either way it must not become an order here.
    if not theme2.is_theme2(product):
        return _error('This product cannot be ordered this way.')

    variation, error = resolve_variation(product, variation_id)
    if error:
        return _error(error)

    if not product.storefront_available:
        return _error('This product is out of stock.')

    stock = variation.available_stock if variation else product.available_stock
    backorderable = product.backorders_allowed and variation is None
    if stock <= 0 and not backorderable:
        return _error('This option is out of stock.')

    try:
        qty = int(qty)
    except (TypeError, ValueError):
        qty = 1
    ceiling = MAX_LINE_QTY if backorderable else min(MAX_LINE_QTY, max(stock, 1))
    qty = max(1, min(qty, ceiling))

    # Quantity breaks, priced by the one module that prices them everywhere
    # else — the cart, the classic page and the order that finally gets
    # written all ask `bulk_discounts` the same question.
    bulk = bulk_discounts.price_for(product, variation, qty)
    unit = _money(bulk['unit'])
    subtotal = _money(bulk['line_total'])
    was = _money(bulk['base_line_total'])

    flat_fee = theme2.ship_fee_for(product)
    if flat_fee is not None:
        shipping = _money(flat_fee)
        shipping_source = 'flat'
    else:
        shipping = _money(services.delivery_charge_for(subtotal, district, branch_code))
        shipping_source = 'district'

    nudge = None
    next_tier = bulk.get('next_tier')
    if next_tier and bulk['need_more'] > 0:
        need = int(bulk['need_more'])
        target = int(next_tier['min_qty'])
        nudge = {
            'need': need,
            'min_qty': target,
            'save_total': _money(next_tier['save_each'] * target),
            'badge': next_tier['badge'],
            'progress': int(round(min(qty, target) / target * 100)) if target else 0,
        }

    # The badge on the price row states the saving as a whole percent. It is
    # derived from the two figures already on screen, so it can never claim a
    # discount the struck-through price does not show.
    saved = _money(bulk['saved'])
    discount_percent = int((saved / was * 100).to_integral_value()) if was > 0 and saved > 0 else 0

    return {
        'product': product,
        'variation': variation,
        'variant_label': variation.display_label if variation else '',
        'qty': qty,
        'stock': stock,
        'ceiling': ceiling,
        'base_unit': _money(bulk['base_unit']),
        'unit': unit,
        'subtotal': subtotal,
        'was': was,
        'saved': saved,
        'discount_percent': discount_percent,
        'tier': bulk['tier'],
        'shipping': shipping,
        'shipping_is_free': shipping <= 0,
        'shipping_source': shipping_source,
        'total': _money(subtotal + shipping),
        'nudge': nudge,
    }


def quote_json(result):
    """The quote as the numbers the modal draws, ready for JsonResponse."""
    nudge = result.get('nudge')
    return {
        'qty': result['qty'],
        'ceiling': result['ceiling'],
        'unit': float(result['unit']),
        'baseUnit': float(result['base_unit']),
        'subtotal': float(result['subtotal']),
        'was': float(result['was']),
        'saved': float(result['saved']),
        'discountPercent': result['discount_percent'],
        'shipping': float(result['shipping']),
        'shippingIsFree': result['shipping_is_free'],
        'shippingSource': result['shipping_source'],
        'total': float(result['total']),
        'variantLabel': result['variant_label'],
        'nudge': None if not nudge else {
            'need': nudge['need'],
            'minQty': nudge['min_qty'],
            'saveTotal': float(nudge['save_total']),
            'badge': nudge['badge'],
            'progress': nudge['progress'],
        },
    }
