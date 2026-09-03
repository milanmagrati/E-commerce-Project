import json
from decimal import Decimal

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, Http404
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Avg, Count, F

from dashboard.models import Category, Product
from dashboard.models import Order as DashOrder, OrderItem as DashOrderItem
from dashboard.models import Customer as DashCustomer, Setup
from .models import (ProductReview, Cart, CartItem, Order, OrderItem, Wishlist, Page,
                     DiscountCode, StoreCustomer, BackInStockNotice, ReviewVote)
from .forms import (GuestOrderForm, ReviewForm, OrderTrackForm, CustomerLoginForm,
                    CustomerRegisterForm, CustomerProfileForm, PasswordChangeForm)
from . import bulk_discounts, services, theme2, theme2_checkout
from .customer_auth import (get_customer, login_customer, logout_customer,
                            customer_required, safe_next)

# Order numbers this browser is allowed to open, remembered in the session.
# A signed-in shopper is matched on `customer` instead; this list is what
# lets a guest reopen what they just placed, and the track-order form
# (number + matching phone) grants access from a different device.
SESSION_ORDER_KEY = 'store_order_numbers'

# Failed logins allowed from one session before it is made to wait, so the
# login box cannot be used to grind through passwords.
LOGIN_ATTEMPT_KEY = 'store_login_attempts'
MAX_LOGIN_ATTEMPTS = 8
LOGIN_LOCKOUT_SECONDS = 15 * 60


def _get_next_dash_order_number():
    """Generate next T-format dashboard order number (T001, T002, …)."""
    from django.db.models import Max, IntegerField
    from django.db.models.functions import Substr, Cast
    result = (
        DashOrder.objects
        .filter(order_number__regex=r'^T\d+$')
        .annotate(num=Cast(Substr('order_number', 2), IntegerField()))
        .aggregate(max_num=Max('num'))
    )
    max_num = result['max_num']
    return f"T{(max_num + 1):03d}" if max_num is not None else "T001"


def _create_dashboard_order(full_name, phone, email, address, city, order_type,
                            items_data, total_amount, shipping_charge=0, user=None,
                            district='', branch_code='', branch_name='', note='',
                            discount_amount=0):
    """Create a dashboard Order + OrderItems so admin can see store orders.

    `user` is None for guest orders, which is every storefront order now — the
    dashboard's created_by is nullable and the customer is matched on phone.
    """
    from django.db import IntegrityError, transaction

    display_name = full_name
    if not display_name and user is not None:
        display_name = f"{user.first_name} {user.last_name}".strip() or user.username
    display_name = display_name or 'Website Guest'

    # Find or create a Customer record by phone
    customer = None
    if phone:
        customer = DashCustomer.objects.filter(phone=phone).first()
        if not customer:
            customer = DashCustomer.objects.create(
                name=display_name,
                phone=phone,
                email=email or '',
                city=city or '',
                address=address or '',
            )

    # Status Setup based on order_type
    if order_type == 'inquiry':
        status_setup = Setup.objects.filter(
            setup_type='status', name__iexact='Inquiry', is_active=True
        ).first()
    else:
        status_setup = Setup.objects.filter(
            setup_type='status', name__iexact='Confirmed', is_active=True
        ).first()
    status_name = status_setup.name if status_setup else ('Inquiry' if order_type == 'inquiry' else 'Confirmed')

    # Payment method: Cash on Delivery
    payment_setup = Setup.objects.filter(
        setup_type='payment', name__iexact='Cash on Delivery', is_active=True
    ).first()
    payment_method = payment_setup.name if payment_setup else 'Cash on Delivery'

    # Payment status: Pending
    payment_status_setup = Setup.objects.filter(
        setup_type='payment_status', name__iexact='Pending', is_active=True
    ).first()
    payment_status = payment_status_setup.name if payment_status_setup else 'Pending'

    # Generate T-format order number with race-condition retry
    dash_order_number = _get_next_dash_order_number()
    dash_order = None
    retry = 0
    while dash_order is None and retry < 5:
        try:
            with transaction.atomic():
                dash_order = DashOrder.objects.create(
                    order_number=dash_order_number,
                    created_by=user,
                    customer=customer,
                    customer_name=display_name,
                    customer_phone=phone or '',
                    customer_email=email or '',
                    shipping_address=address or '',
                    branch_city=district or city or '',
                    order_from='Website',
                    in_out='in',
                    status=status_name,
                    order_status=status_name,
                    status_setup=status_setup,
                    payment_method=payment_method,
                    payment_setup=payment_setup,
                    payment_status=payment_status,
                    payment_status_setup=payment_status_setup,
                    total_amount=Decimal(str(total_amount)),
                    delivery_charge=Decimal(str(shipping_charge)),
                    discount_amount=Decimal(str(discount_amount or 0)),
                    ncm_destination_branch=branch_code or '',
                    notes=note or '',
                )
        except IntegrityError as e:
            if 'order_number' in str(e).lower():
                retry += 1
                dash_order_number = _get_next_dash_order_number()
            else:
                raise

    if dash_order is None:
        return None

    for item in items_data:
        price = Decimal(str(item['price']))
        qty = item['quantity']
        variation = item.get('variation')
        DashOrderItem.objects.create(
            order=dash_order,
            product=item['product'],
            product_variation=variation,
            product_name=item['product'].name,
            product_sku=(variation.sku if variation else ''),
            variation_name=(variation.display_label if variation else (item.get('variant') or None)),
            quantity=qty,
            price=price,
            total=price * qty,
        )

    return dash_order


def _session_key(request):
    """The browser's session key, creating the session if it has none yet."""
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def _get_cart(request):
    """This browser's cart: the account's when signed in, otherwise the
    session's. Guest carts are merged into the account cart at sign-in, so
    switching between the two never loses lines."""
    customer = get_customer(request)
    if customer:
        cart, _ = Cart.objects.get_or_create(customer=customer)
        return cart
    if request.user.is_authenticated:
        cart = Cart.objects.filter(user=request.user, customer__isnull=True).first()
        return cart or Cart.objects.create(user=request.user)
    session_key = _session_key(request)
    cart = Cart.objects.filter(session_key=session_key, customer__isnull=True).first()
    return cart or Cart.objects.create(session_key=session_key)


def _wishlist_qs(request):
    """Saved products belonging to the signed-in account, or to this browser."""
    customer = get_customer(request)
    if customer:
        return Wishlist.objects.filter(customer=customer)
    if request.user.is_authenticated:
        return Wishlist.objects.filter(user=request.user, customer__isnull=True)
    session_key = request.session.session_key
    if not session_key:
        return Wishlist.objects.none()
    return Wishlist.objects.filter(
        user__isnull=True, customer__isnull=True, session_key=session_key)


def _remember_order(request, order):
    """Let this browser reopen the order it just placed."""
    numbers = request.session.get(SESSION_ORDER_KEY, [])
    if order.order_number not in numbers:
        numbers.append(order.order_number)
        request.session[SESSION_ORDER_KEY] = numbers[-30:]
        request.session.modified = True


def _order_form_initial(request):
    """Prefill the order form from the signed-in account, so a returning
    shopper does not retype their name, number and address every time."""
    customer = get_customer(request)
    if not customer:
        return {}
    return {
        'full_name': customer.full_name,
        'phone': customer.phone,
        'email': customer.email,
        'district': customer.district,
        'courier_branch': customer.courier_branch,
        'courier_branch_code': customer.courier_branch_code,
        'address': customer.address,
    }


def _remember_delivery_details(request, form):
    """Keep the address a signed-in shopper just used, for next time."""
    customer = get_customer(request)
    if not customer:
        return
    customer.district = form.cleaned_data.get('district', '') or customer.district
    customer.courier_branch = form.cleaned_data.get('courier_branch', '') or customer.courier_branch
    customer.courier_branch_code = (form.cleaned_data.get('courier_branch_code', '')
                                    or customer.courier_branch_code)
    customer.address = form.cleaned_data.get('address', '') or customer.address
    customer.save(update_fields=['district', 'courier_branch', 'courier_branch_code', 'address'])


def _active_products():
    """Base queryset for active, non-deleted products."""
    return Product.objects.filter(is_active=True, is_deleted=False)


def _get_product_image(product, variation=None):
    """Get the best image URL for a product, preferring the chosen variation's
    own image when it has one."""
    if variation is not None and getattr(variation, 'image', None):
        return variation.image.url
    if product.image:
        return product.image.url
    img = product.images.filter(is_featured=True).first()
    if img:
        return img.image.url
    img = product.images.first()
    if img:
        return img.image.url
    return ''


def _resolve_variation(product, variation_id):
    """Return (variation, error). `variation` is a buyable ProductVariation of
    this product, or None. `error` is a shopper-facing string when a variable
    product was given a missing/invalid variation, else ''."""
    from dashboard.models import ProductVariation

    if variation_id:
        try:
            variation = product.active_variations.get(pk=int(variation_id))
            return variation, ''
        except (ProductVariation.DoesNotExist, ValueError, TypeError):
            if product.is_variable:
                return None, 'That option is no longer available. Please pick another.'
            return None, ''
    if product.has_variations:
        return None, 'Please choose an option first.'
    return None, ''


def _avg_rating(product):
    """Get average rating for a product from store reviews."""
    reviews = product.store_reviews.all()
    if reviews.exists():
        return round(reviews.aggregate(avg=Avg('rating'))['avg'], 1)
    return 0


def _review_count(product):
    """Get review count for a product."""
    return product.store_reviews.count()


def _is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


# ──────────────────── Landing Page ────────────────────

def landing_page(request):
    categories = Category.objects.all()
    products_qs = _active_products()
    featured_products = list(products_qs.order_by('?')[:20])
    flash_sale_products = list(products_qs.order_by('?')[:10])
    new_arrivals = list(products_qs.order_by('-created_at')[:10])
    all_products = list(products_qs.order_by('?')[:20])

    return render(request, 'store/landing.html', {
        'categories': categories,
        'featured_products': featured_products,
        'flash_sale_products': flash_sale_products,
        'new_arrivals': new_arrivals,
        'all_products': all_products,
    })


def load_more_products(request):
    """AJAX endpoint for 'Just For You' load more."""
    offset = int(request.GET.get('offset', 0))
    limit = 20
    # Use deterministic ordering to avoid duplicates with offset pagination
    products = _active_products().order_by('-created_at', 'pk')[offset:offset + limit]
    data = []
    for p in products:
        low, high = p.variation_price_range
        price_display = (f'Rs. {float(low):,.0f} – Rs. {float(high):,.0f}'
                         if low != high else f'Rs. {float(low):,.0f}')
        data.append({
            'id': p.id,
            'name': p.name,
            'slug': p.slug,
            'price': str(p.price),
            'price_display': price_display,
            'product_type': p.product_type,
            'has_variations': p.has_variations,
            'in_stock': p.storefront_available,
            'average_rating': _avg_rating(p),
            'review_count': _review_count(p),
            'image': _get_product_image(p),
            'stock': p.stock,
            'bulk': _bulk_card_payload(p),
        })
    return JsonResponse({'products': data, 'has_more': len(data) == limit})


def _bulk_card_payload(product):
    """The quantity-break rungs a listing card draws, as plain JSON.

    Same shape the server-rendered card gets, so a load-more card and a
    first-page card show the same offer.
    """
    return [
        {
            'min_qty': tier['min_qty'],
            'badge': tier['badge'],
            'lead': tier['badge_lead'],
            'value': tier['badge_value'],
            'each': tier['unit_price_display'],
        }
        for tier in bulk_discounts.card_ladder(product)
    ]


# ──────────────────── Product Views ────────────────────

def product_list(request):
    products = _active_products()
    sort = request.GET.get('sort', '')
    if sort == 'price_low':
        products = products.order_by('price')
    elif sort == 'price_high':
        products = products.order_by('-price')
    elif sort == 'newest':
        products = products.order_by('-created_at')
    elif sort == 'popular':
        products = products.annotate(rc=Count('store_reviews')).order_by('-rc')

    paginator = Paginator(products, 20)
    page = request.GET.get('page')
    products = paginator.get_page(page)
    return render(request, 'store/product_list.html', {'products': products, 'sort': sort})


def product_detail(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True, is_deleted=False)
    images = product.images.all().order_by('order')
    # Vote tallies ride along on the review rows so Theme 2's helpful /
    # not-helpful counters cost no extra query; Theme 1 simply ignores them.
    reviews = product.store_reviews.select_related('user', 'customer').annotate(
        up_votes=Count('votes', filter=Q(votes__value=ReviewVote.UP)),
        down_votes=Count('votes', filter=Q(votes__value=ReviewVote.DOWN)),
    )
    related_products = _active_products().filter(
        category=product.category
    ).exclude(pk=product.pk)[:6]

    # Rating distribution as a list for template rendering
    review_count = reviews.count()
    rating_counts = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    for r in reviews:
        rating_counts[r.rating] = rating_counts.get(r.rating, 0) + 1
    # `percent` is the share of all reviews; `bar` is the width relative to the
    # tallest row, which is what makes a histogram readable when four of the
    # five rows are single digits.
    busiest = max(rating_counts.values()) if review_count else 0
    rating_dist = [
        {'star': s, 'count': rating_counts[s],
         'percent': round(rating_counts[s] / review_count * 100) if review_count else 0,
         'bar': round(rating_counts[s] / busiest * 100) if busiest else 0}
        for s in [5, 4, 3, 2, 1]
    ]

    in_wishlist = _wishlist_qs(request).filter(product=product).exists()
    avg_rating = _avg_rating(product)
    customer = get_customer(request)

    # How many of this product are already sitting in the cart — the quantity
    # label reads "(N in cart)". A variable product holds one cart line per
    # variation, so the figure is tracked per variation and product.js swaps it
    # as the shopper picks. `in_cart_qty` is the opening figure: the plain
    # product's line, or 0 while no variation has been chosen.
    cart = _get_cart(request)
    in_cart_by_variation = {}
    in_cart_qty = 0
    for line in cart.items.all():
        if line.product_id != product.pk:
            continue
        if line.variation_id:
            in_cart_by_variation[str(line.variation_id)] = line.quantity
        else:
            in_cart_qty = line.quantity

    # Bundle / variable product extras
    available_stock = product.available_stock
    variations = []
    bundle_components = []
    has_variations = product.has_variations
    preselect_variation_id = ''
    variation_rows = list(product.active_variations) if has_variations else []
    if has_variations:
        for v in variation_rows:
            avail = v.available_stock
            # The card only flags that this option has a break, and how deep.
            # The rungs themselves are drawn once, in the ladder beside the
            # quantity stepper, for whichever option is chosen.
            v_badge = bulk_discounts.summary_badge(product, v)
            variations.append({
                'id': v.id,
                'label': v.display_label,
                'price': v.price,
                'price_display': f'Rs. {float(v.price):,.0f}',
                'stock': avail,
                'in_stock': v.is_in_stock and avail > 0,
                # "Only N left" copy uses the variation's own threshold, not the
                # product's; 5 is the fallback when none is set.
                'low_stock': v.low_stock_threshold or 5,
                'image': v.image.url if v.image else '',
                # Shown under the name only when it adds something the label
                # does not already say (display_label is variation_name or sku).
                'sku': (v.sku or '').strip(),
                # One line flagging this option's quantity break, or ''.
                'bulk_badge': v_badge,
            })
        # The buy block renders when the product can be ordered at all; the qty
        # ceiling starts at the biggest variation so the stepper is usable, and
        # product.js narrows it to the picked variation. Buttons stay disabled
        # until a pick.
        available_stock = max((v['stock'] for v in variations), default=0)
        # A shared / "edit from cart" link may name a variation to open on.
        want = (request.GET.get('variation') or '').strip()
        if want and any(str(v['id']) == want and v['in_stock'] for v in variations):
            preselect_variation_id = want
    elif product.product_type == 'bundle':
        bundle_components = list(
            product.bundle_components.select_related('component_product').all()
        )

    # A quantity-break chip on a listing card links in as `?qty=4`, so the
    # stepper opens on the quantity that earns the offer.
    preselect_qty = 1
    try:
        wanted_qty = int(request.GET.get('qty') or 1)
    except (TypeError, ValueError):
        wanted_qty = 1
    if wanted_qty > 1:
        ceiling = available_stock if available_stock > 0 else wanted_qty
        preselect_qty = max(1, min(wanted_qty, ceiling))

    context = {
        'product': product,
        'images': images,
        'reviews': reviews,
        'related_products': related_products,
        'rating_dist': rating_dist,
        'review_form': ReviewForm(name_required=customer is None),
        'order_form': GuestOrderForm(initial=_order_form_initial(request)),
        'in_wishlist': in_wishlist,
        'avg_rating': avg_rating,
        'review_count': review_count,
        'available_stock': available_stock,
        'in_cart_qty': in_cart_qty,
        'customer': customer,
        'has_variations': has_variations,
        'variations': variations,
        'preselect_variation_id': preselect_variation_id,
        'in_cart_json': json.dumps(in_cart_by_variation),
        'bundle_components': bundle_components,
        # Quantity breaks. `bulk_tiers` is the ladder rendered under the price
        # for a plain product (empty for a variable one, whose ladders live per
        # option); `bulk_tiers_json` carries every ladder so product.js can
        # re-price live as the quantity or the chosen option changes.
        'bulk_tiers': [] if has_variations else bulk_discounts.tiers_for(product),
        'bulk_tiers_json': json.dumps(
            bulk_discounts.tiers_payload(product, variation_rows or None)),
        'preselect_qty': preselect_qty,
        'product_image_url': _get_product_image(product),
        'free_delivery_threshold': services.free_delivery_threshold(),
    }

    # ── The router. Four lines, and Theme 1 below is untouched. ──
    override = theme2.override_for(product)
    if theme2.is_theme2(product, override):
        context.update(_theme2_context(request, product, override, variation_rows))
        return render(request, 'store/product_detail_conversion.html', context)

    return render(request, 'store/product_detail.html', context)


def _theme2_context(request, product, override, variation_rows):
    """Everything the conversion landing page needs that the classic page does not.

    Kept out of `product_detail` so the shared context above stays the one
    both designs are built from — the two pages must never disagree about
    price, stock or what is in the cart.
    """
    blocks = theme2.content_blocks(product, override)

    # One boolean gates the BUY NOW button, the sticky bar and the modal, so
    # they cannot end up disagreeing about what a click does. The modal is off
    # when the administrator asked for the classic add-to-cart path, and when
    # there is nothing to sell.
    # The opening quote must come from a variation a shopper can actually buy:
    # a variable product's first row may well be sold out, and its price is not
    # one anybody is being offered. It is also what the buy box prints, so it
    # is computed whether or not the modal is in play.
    opening_variation = next(
        (v.pk for v in variation_rows if v.available_stock > 0),
        (variation_rows[0].pk if variation_rows else ''))
    opening = theme2_checkout.quote(
        product.pk, variation_id=opening_variation, qty=1)
    if opening.get('error'):
        opening = None

    # One boolean gates the BUY NOW button, the sticky bar and the modal, so
    # they cannot end up disagreeing about what a click does. The modal is off
    # when the administrator asked for the classic add-to-cart path, when there
    # is nothing to sell, and when the opening quote already refuses — a modal
    # that can only ever show an error is not worth rendering.
    use_modal = (blocks['checkout']['action'] == 'modal'
                 and product.storefront_available
                 and opening is not None)

    voted = set()
    customer = get_customer(request)
    session_key = request.session.session_key
    if customer or session_key:
        voted = set(ReviewVote.objects.filter(
            review__product=product,
            voter_key=ReviewVote.key_for(customer, session_key),
        ).values_list('review_id', flat=True))

    return {
        't2': blocks,
        't2_use_modal': use_modal,
        't2_quote': opening,
        't2_voted': voted,
        't2_districts': services.get_locations()['districts'],
    }


def category_products(request, slug):
    category = get_object_or_404(Category, slug=slug)
    products = _active_products().filter(category=category)

    sort = request.GET.get('sort', '')
    if sort == 'price_low':
        products = products.order_by('price')
    elif sort == 'price_high':
        products = products.order_by('-price')
    elif sort == 'newest':
        products = products.order_by('-created_at')

    paginator = Paginator(products, 20)
    page = request.GET.get('page')
    products = paginator.get_page(page)
    return render(request, 'store/category_products.html', {
        'category': category,
        'products': products,
        'sort': sort,
    })


# ──────────────────── Search ────────────────────

def search_results(request):
    q = request.GET.get('q', '').strip()
    category_slug = request.GET.get('category', '')
    min_price = request.GET.get('min_price', '')
    max_price = request.GET.get('max_price', '')
    sort = request.GET.get('sort', '')

    products = _active_products()
    if q:
        products = products.filter(
            Q(name__icontains=q) | Q(description__icontains=q) | Q(category__name__icontains=q)
        )
    if category_slug:
        products = products.filter(category__slug=category_slug)
    if min_price:
        products = products.filter(price__gte=min_price)
    if max_price:
        products = products.filter(price__lte=max_price)

    if sort == 'price_low':
        products = products.order_by('price')
    elif sort == 'price_high':
        products = products.order_by('-price')
    elif sort == 'newest':
        products = products.order_by('-created_at')
    elif sort == 'popular':
        products = products.annotate(rc=Count('store_reviews')).order_by('-rc')

    paginator = Paginator(products, 20)
    page = request.GET.get('page')
    products = paginator.get_page(page)

    return render(request, 'store/search_results.html', {
        'products': products,
        'query': q,
        'sort': sort,
        'category_slug': category_slug,
        'min_price': min_price,
        'max_price': max_price,
    })


def search_autocomplete(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})
    products = _active_products().filter(
        name__icontains=q
    ).values('name', 'slug')[:5]
    return JsonResponse({'results': list(products)})


# ──────────────────── Cart Views ────────────────────

def cart_view(request):
    cart = _get_cart(request)
    cart_items = cart.items.select_related('product', 'variation').all()
    subtotal = cart.subtotal
    shipping = services.delivery_charge_for(subtotal)
    total = subtotal + shipping
    return render(request, 'store/cart.html', {
        'cart_items': cart_items,
        'subtotal': subtotal,
        'shipping': shipping,
        'total': total,
    })


@require_POST
def add_to_cart(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_active=True, is_deleted=False)
    try:
        quantity = max(1, int(request.POST.get('quantity', 1)))
    except (ValueError, TypeError):
        quantity = 1

    # Which variation, if this is a variable product
    variation, var_error = _resolve_variation(
        product, request.POST.get('selected_variation', ''))
    if var_error:
        if _is_ajax(request):
            return JsonResponse({'success': False, 'message': var_error})
        messages.error(request, var_error)
        return redirect('store:product_detail', slug=product.slug)

    # A picked variation carries its own stock and price; only a plain product
    # honours the backorder flag.
    if variation is not None:
        avail = variation.available_stock
        selected_variant = variation.display_label[:500]
        backorderable = False
    else:
        avail = product.available_stock
        selected_variant = request.POST.get('selected_variant', '')[:500]
        backorderable = product.backorders_allowed

    # Stock validation — skip for backorder-enabled products
    if avail <= 0 and not backorderable:
        msg = ('This option is out of stock' if variation is not None
               else 'This product is out of stock')
        if _is_ajax(request):
            return JsonResponse({'success': False, 'message': msg})
        messages.error(request, msg + '.')
        return redirect('store:product_detail', slug=product.slug)

    cart = _get_cart(request)

    # For backorder-enabled products, don't cap quantity to available stock
    if backorderable:
        effective_qty = quantity
    else:
        effective_qty = min(quantity, avail)

    item, created = CartItem.objects.get_or_create(
        cart=cart, product=product, variation=variation,
        defaults={'quantity': effective_qty, 'selected_variant': selected_variant}
    )
    if not created:
        if backorderable:
            item.quantity = item.quantity + quantity
        else:
            item.quantity = min(item.quantity + quantity, avail)
        item.selected_variant = selected_variant
        item.save()

    if _is_ajax(request):
        return JsonResponse({
            'success': True,
            'message': f'{product.name} added to cart',
            'count': cart.total_items,
            # The product page shows this next to the quantity stepper, so
            # send the true figure rather than letting the page add up its
            # own — stock caps mean fewer units may have gone in than asked.
            'item_quantity': item.quantity,
            'name': product.name,
            'image': _get_product_image(product, variation),
            'variant': item.variant_label,
            'unit_price': str(item.unit_price),
            'line_total': str(item.line_total),
        })
    messages.success(request, f'{product.name} added to cart.')
    return redirect('store:cart')


@require_POST
def remove_from_cart(request, item_id):
    cart = _get_cart(request)
    item = get_object_or_404(CartItem, pk=item_id, cart=cart)
    name = item.product.name
    item.delete()

    if _is_ajax(request):
        return JsonResponse({
            'success': True,
            'message': f'{name} removed from cart',
            'count': cart.total_items,
            'subtotal': str(cart.subtotal),
        })
    messages.success(request, f'{name} removed from cart.')
    return redirect('store:cart')


@require_POST
def update_cart(request):
    cart = _get_cart(request)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid data'})

    item_id = data.get('item_id')
    try:
        quantity = max(0, int(data.get('quantity', 1)))
    except (ValueError, TypeError):
        quantity = 1

    try:
        item = CartItem.objects.get(pk=item_id, cart=cart)
    except CartItem.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Item not found'})

    if quantity <= 0:
        item.delete()
    else:
        # A variation line is capped at its own stock; a plain product honours
        # the backorder flag.
        if item.variation is None and item.product.backorders_allowed:
            item.quantity = quantity
        else:
            item.quantity = min(quantity, item.available_stock)
        item.save()

    subtotal = cart.subtotal
    shipping = services.delivery_charge_for(subtotal)
    # The line may have crossed a quantity break either way, so the cart page
    # is told the new unit price and which rung (if any) it is now on.
    quote = item.bulk_price if quantity > 0 else None
    return JsonResponse({
        'success': True,
        'count': cart.total_items,
        'item_total': str(item.line_total) if quantity > 0 else '0',
        'unit_price': str(quote['unit']) if quote else '0',
        'base_unit_price': str(quote['base_unit']) if quote else '0',
        'bulk_label': (quote['tier']['offer_label'] if quote and quote['tier'] else ''),
        'bulk_saved': str(quote['saved']) if quote else '0',
        'subtotal': str(subtotal),
        'shipping': str(shipping),
        'total': str(subtotal + shipping),
    })


# ──────────────────── Wishlist ────────────────────

def wishlist_view(request):
    wishlist_items = _wishlist_qs(request).select_related('product')
    return render(request, 'store/wishlist.html', {'wishlist_items': wishlist_items})


@require_POST
def toggle_wishlist(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    customer = get_customer(request)
    if customer:
        defaults = {'customer': customer, 'session_key': _session_key(request)}
    elif request.user.is_authenticated:
        defaults = {'user': request.user}
    else:
        defaults = {'session_key': _session_key(request)}

    existing = _wishlist_qs(request).filter(product=product)
    if existing.exists():
        existing.delete()
        added = False
    else:
        Wishlist.objects.create(product=product, **defaults)
        added = True

    return JsonResponse({
        'success': True,
        'added': added,
        'message': 'Added to wishlist' if added else 'Removed from wishlist',
        'count': _wishlist_qs(request).count(),
    })


@require_POST
def notify_back_in_stock(request, product_id):
    """Register a shopper to hear when a sold-out product — or one specific
    variation — is buyable again. Works for guests and signed-in shoppers."""
    product = get_object_or_404(Product, pk=product_id, is_active=True, is_deleted=False)
    variation, var_error = _resolve_variation(
        product, request.POST.get('selected_variation', ''))
    if var_error:
        variation = None  # fall back to a product-level notice

    email = (request.POST.get('email') or '').strip()
    phone = (request.POST.get('phone') or '').strip()
    customer = get_customer(request)
    if customer:
        email = email or (customer.email or '')
        phone = phone or (customer.phone or '')

    if not email and not phone:
        msg = 'Add an email or phone number so we can reach you.'
        if _is_ajax(request):
            return JsonResponse({'success': False, 'message': msg})
        messages.error(request, msg)
        return redirect('store:product_detail', slug=product.slug)

    match = {'product': product, 'variation': variation, 'notified_at__isnull': True}
    if email:
        match['email'] = email
    else:
        match['phone'] = phone
    BackInStockNotice.objects.get_or_create(
        defaults={
            'session_key': _session_key(request),
            'customer': customer,
            'email': email,
            'phone': phone,
        },
        **match,
    )

    msg = "You're on the list — we'll message you the moment it's back."
    if _is_ajax(request):
        return JsonResponse({'success': True, 'message': msg})
    messages.success(request, msg)
    return redirect('store:product_detail', slug=product.slug)


# ──────────────────── Order form plumbing ────────────────────

def locations_json(request):
    """District → courier-branch catalogue driving the order form's two selects."""
    return JsonResponse(services.get_locations())


@require_POST
def apply_discount(request):
    """Validate a discount code against a subtotal and return the new totals."""
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        payload = {}

    code = (payload.get('code') or '').strip().upper()
    district = (payload.get('district') or '').strip()
    branch = (payload.get('branch') or '').strip()
    try:
        subtotal = Decimal(str(payload.get('subtotal') or '0'))
    except Exception:
        subtotal = Decimal('0')

    discount, message = services.lookup_discount(code, subtotal)
    totals = services.price_order(subtotal, district, discount, branch)
    return JsonResponse({
        'success': discount is not None,
        'message': message or ('' if discount else 'Enter a discount code.'),
        'code': discount.code if discount else '',
        'totals': {k: str(v) for k, v in totals.items()},
    })


def quote_json(request):
    """Live delivery/total quote as the shopper picks a district or changes qty."""
    district = request.GET.get('district', '')
    branch = (request.GET.get('branch') or '').strip()
    code = (request.GET.get('code') or '').strip().upper()
    try:
        subtotal = Decimal(str(request.GET.get('subtotal') or '0'))
    except Exception:
        subtotal = Decimal('0')

    discount, _msg = services.lookup_discount(code, subtotal) if code else (None, '')
    totals = services.price_order(subtotal, district, discount, branch)
    quote = services.quote_delivery(subtotal, district, branch)
    coupon_free = bool(discount and discount.free_delivery)
    return JsonResponse({
        'district_known': bool(district.strip()),
        'inside_valley': quote['inside_valley'],
        'totals': {k: str(v) for k, v in totals.items()},
        'delivery': {
            'charge': str(quote['charge'] if not coupon_free else Decimal('0')),
            'base_charge': str(quote['base_charge']),
            'is_free': coupon_free or quote['is_free'],
            # 'coupon' outranks the zone/threshold reasons so the shopper can
            # see *why* they are not being charged.
            'free_reason': 'coupon' if coupon_free else quote['free_reason'],
            'free_above': str(quote['free_above']),
            'delivery_time': quote['delivery_time'],
            'delivery_time_np': quote['delivery_time_np'],
            'covered_areas': quote['covered_areas'],
            'note': quote['note'],
            'matched': quote['matched'],
        },
    })


def _place_order(request, form, items, order_type, delivery_district,
                 delivery_override=None):
    """Shared tail of every checkout: price it, persist it, mirror it to the
    dashboard, allocate stock. `items` is [{'product', 'quantity', 'price',
    'variant', 'variation'}] ('variation' optional). Returns the store Order.

    `delivery_override` is the delivery figure a caller was already quoted;
    Theme 2's one-step checkout passes the one its modal displayed so the order
    cannot be written at a different delivery charge than the shopper agreed
    to. Left None — every other flow — delivery is priced by district here as
    it always was.
    """
    from django.db import transaction

    subtotal = sum(Decimal(str(i['price'])) * i['quantity'] for i in items)
    discount, _msg = services.lookup_discount(form.cleaned_data.get('discount_code'), subtotal)

    branch_code = (form.cleaned_data.get('courier_branch_code') or '').strip().upper()
    totals = services.price_order(subtotal, delivery_district, discount, branch_code,
                                  delivery_override=delivery_override)
    branch = services.resolve_branch(delivery_district, branch_code)
    branch_name = branch['name'] if branch else (form.cleaned_data.get('courier_branch') or '')

    address_parts = [
        form.cleaned_data['address'],
        branch_name,
        delivery_district,
    ]
    address = ', '.join(p for p in address_parts if p)

    with transaction.atomic():
        order = Order.objects.create(
            user=request.user if request.user.is_authenticated else None,
            customer=get_customer(request),
            session_key=_session_key(request),
            full_name=form.cleaned_data['full_name'],
            order_type=order_type,
            subtotal=totals['subtotal'],
            delivery_charge=totals['delivery'],
            discount_code=discount.code if discount else '',
            discount_amount=totals['discount'],
            total_price=totals['total'],
            shipping_address=address,
            phone=form.cleaned_data['phone'],
            email=form.cleaned_data.get('email', ''),
            city=delivery_district,
            district=delivery_district,
            courier_branch=branch_name,
            courier_branch_code=branch_code,
            note=form.cleaned_data.get('note', ''),
        )
        for item in items:
            OrderItem.objects.create(
                order=order,
                product=item['product'],
                variation=item.get('variation'),
                quantity=item['quantity'],
                price=item['price'],
                selected_variant=item.get('variant', ''),
            )
        if discount:
            DiscountCode.objects.filter(pk=discount.pk).update(used_count=F('used_count') + 1)

    _create_dashboard_order(
        full_name=form.cleaned_data['full_name'],
        phone=form.cleaned_data['phone'],
        email=form.cleaned_data.get('email', ''),
        address=address,
        city=delivery_district,
        order_type=order_type,
        items_data=items,
        total_amount=totals['total'],
        shipping_charge=totals['delivery'],
        user=request.user if request.user.is_authenticated else None,
        district=delivery_district,
        branch_code=branch_code,
        branch_name=branch_name,
        note=form.cleaned_data.get('note', ''),
        discount_amount=totals['discount'],
    )

    # Inquiries are not commitments — they must not consume stock.
    if order_type == 'confirmed':
        from inventory.services import allocate_order
        allocate_order(order)

    _remember_order(request, order)
    _remember_delivery_details(request, form)
    return order


# ──────────────────── Quick order (product page) ────────────────────

def quick_order(request, product_id):
    """Guest order form for a single product.

    POSTed by the inline panel on the product page (as JSON-answering AJAX) and
    rendered as a standalone page when JavaScript is unavailable.
    """
    product = get_object_or_404(Product, pk=product_id, is_active=True, is_deleted=False)

    if request.method == 'POST':
        form = GuestOrderForm(request.POST)
        order_type = request.POST.get('order_type', 'confirmed')
        if order_type not in ('confirmed', 'inquiry'):
            order_type = 'confirmed'
        variation, var_error = _resolve_variation(
            product, request.POST.get('selected_variation', ''))
        if var_error:
            return _order_error(request, product, var_error)
        selected_variant = (variation.display_label if variation
                            else request.POST.get('selected_variant', ''))[:500]
        available_stock = variation.available_stock if variation else product.available_stock
        backorderable = product.backorders_allowed and variation is None
        try:
            quantity = max(1, int(request.POST.get('quantity', 1)))
        except (ValueError, TypeError):
            quantity = 1
        # Quantity break, priced server-side: whatever the page showed, the
        # order is written at the rate this quantity actually earns.
        bulk = bulk_discounts.price_for(product, variation, quantity)
        unit_price = bulk['unit']

        if form.is_valid():
            district = form.cleaned_data['district']

            # Stock only gates a real commitment; an inquiry may exceed it.
            if order_type == 'confirmed' and not backorderable:
                if available_stock <= 0:
                    return _order_error(request, product, 'This product is out of stock.')
                if quantity > available_stock:
                    return _order_error(request, product, f'Only {available_stock} unit(s) available.')

            order = _place_order(
                request, form,
                items=[{
                    'product': product, 'quantity': quantity,
                    'price': unit_price, 'variant': selected_variant,
                    'variation': variation,
                }],
                order_type=order_type,
                delivery_district=district,
            )

            if _is_ajax(request):
                return JsonResponse({
                    'success': True,
                    'order_number': order.order_number,
                    'order_type': order.order_type,
                    'total': str(order.total_price),
                    'redirect': f'/store/orders/{order.order_number}/',
                })

            if order_type == 'confirmed':
                messages.success(request, f'Order confirmed! Order #{order.order_number}')
            else:
                messages.success(request, f'Inquiry submitted! Reference #{order.order_number}')
            return redirect('store:order_detail', order_number=order.order_number)

        if _is_ajax(request):
            return JsonResponse({
                'success': False,
                'errors': {f: [str(e) for e in errs] for f, errs in form.errors.items()},
                'message': 'Please correct the highlighted fields.',
            }, status=400)
    else:
        variation, _var_error = _resolve_variation(product, request.GET.get('variation', ''))
        available_stock = variation.available_stock if variation else product.available_stock
        try:
            quantity = max(1, min(int(request.GET.get('qty', 1)), max(available_stock, 1)))
        except (ValueError, TypeError):
            quantity = 1
        bulk = bulk_discounts.price_for(product, variation, quantity)
        unit_price = bulk['unit']
        order_type = request.GET.get('order_type', 'confirmed')
        if order_type not in ('confirmed', 'inquiry'):
            order_type = 'confirmed'
        selected_variant = (variation.display_label if variation
                            else request.GET.get('variant', ''))[:500]
        form = GuestOrderForm(initial=_order_form_initial(request))

    subtotal = unit_price * quantity
    totals = services.price_order(subtotal)

    return render(request, 'store/quick_checkout.html', {
        'product': product,
        'quantity': quantity,
        'order_type': order_type,
        'form': form,
        'subtotal': totals['subtotal'],
        'shipping': totals['delivery'],
        'total': totals['total'],
        'available_stock': available_stock,
        'selected_variant': selected_variant,
        'unit_price': unit_price,
        'list_unit_price': bulk['base_unit'],
        'bulk_tier': bulk['tier'],
        'bulk_saved': bulk['saved'],
        'variation': variation,
        'variation_id': variation.id if variation else '',
        'product_image_url': _get_product_image(product, variation),
    })


def _order_error(request, product, message):
    if _is_ajax(request):
        return JsonResponse({'success': False, 'message': message}, status=400)
    messages.error(request, message)
    return redirect('store:product_detail', slug=product.slug)


# ──────────────────── Cart checkout ────────────────────

def checkout_view(request):
    cart = _get_cart(request)
    cart_items = list(cart.items.select_related('product', 'variation').all())
    if not cart_items:
        messages.warning(request, 'Your cart is empty.')
        return redirect('store:cart')

    subtotal = cart.subtotal

    if request.method == 'POST':
        form = GuestOrderForm(request.POST)
        order_type = request.POST.get('order_type', 'confirmed')
        if order_type not in ('confirmed', 'inquiry'):
            order_type = 'confirmed'

        if form.is_valid():
            if order_type == 'confirmed':
                for item in cart_items:
                    avail = item.available_stock
                    backorderable = item.variation is None and item.product.backorders_allowed
                    if item.quantity > avail and not backorderable:
                        label = item.product.name
                        if item.variation:
                            label += f' ({item.variation.display_label})'
                        msg = f'"{label}" only has {avail} in stock.'
                        if _is_ajax(request):
                            return JsonResponse({'success': False, 'message': msg}, status=400)
                        messages.error(request, msg)
                        return redirect('store:cart')

            order = _place_order(
                request, form,
                items=[{
                    'product': ci.product, 'quantity': ci.quantity,
                    'price': ci.unit_price, 'variant': ci.variant_label,
                    'variation': ci.variation,
                } for ci in cart_items],
                order_type=order_type,
                delivery_district=form.cleaned_data['district'],
            )

            if order_type == 'confirmed':
                cart.items.all().delete()

            if _is_ajax(request):
                return JsonResponse({
                    'success': True,
                    'order_number': order.order_number,
                    'order_type': order.order_type,
                    'total': str(order.total_price),
                    'redirect': f'/store/orders/{order.order_number}/',
                })

            if order_type == 'confirmed':
                messages.success(request, f'Order confirmed! Order number: {order.order_number}')
            else:
                messages.success(request, f'Inquiry submitted! Reference number: {order.order_number}')
            return redirect('store:order_detail', order_number=order.order_number)

        if _is_ajax(request):
            return JsonResponse({
                'success': False,
                'errors': {f: [str(e) for e in errs] for f, errs in form.errors.items()},
                'message': 'Please correct the highlighted fields.',
            }, status=400)
    else:
        form = GuestOrderForm(initial=_order_form_initial(request))

    totals = services.price_order(subtotal)
    return render(request, 'store/checkout.html', {
        'form': form,
        'cart_items': cart_items,
        'subtotal': totals['subtotal'],
        'shipping': totals['delivery'],
        'total': totals['total'],
    })


# ──────────────────── Order lookup ────────────────────

def order_track(request):
    """Replaces "My Orders": find an order by number + the phone on it."""
    form = OrderTrackForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        order = Order.objects.filter(
            order_number=form.cleaned_data['order_number'],
            phone=form.cleaned_data['phone'],
        ).first()
        if order:
            _remember_order(request, order)
            return redirect('store:order_detail', order_number=order.order_number)
        messages.error(request, 'No order matches that number and mobile number.')

    recent = _visible_orders(request)[:10]
    return render(request, 'store/orders.html', {
        'form': form,
        'recent_orders': recent,
        'customer': get_customer(request),
    })


def _visible_orders(request):
    """Orders this visitor may open: everything on the account when signed
    in, plus whatever this browser placed as a guest."""
    customer = get_customer(request)
    lookup = Q(order_number__in=request.session.get(SESSION_ORDER_KEY, []))
    session_key = request.session.session_key
    if session_key:
        lookup |= Q(session_key=session_key)
    if customer:
        lookup |= Q(customer=customer)
        if customer.phone:
            lookup |= Q(phone=customer.phone)
    return Order.objects.filter(lookup).distinct()


def order_detail(request, order_number):
    order = Order.objects.filter(order_number=order_number).first()
    if order is None:
        raise Http404('Order not found')

    customer = get_customer(request)
    allowed = (
        order.order_number in request.session.get(SESSION_ORDER_KEY, [])
        or (order.session_key and order.session_key == request.session.session_key)
        or (customer is not None and order.customer_id == customer.pk)
        or (customer is not None and order.phone and order.phone == customer.phone)
        or (request.user.is_authenticated and order.user_id == request.user.id)
    )
    if not allowed:
        messages.info(request, 'Enter your mobile number to open this order.')
        return redirect('store:order_track')

    return render(request, 'store/order_detail.html', {'order': order})


# ──────────────────── Reviews ────────────────────

@require_POST
def add_review(request, product_id):
    """Anyone may review, signed in or not — matching the reference store,
    where an account is never demanded to leave feedback."""
    product = get_object_or_404(Product, pk=product_id)
    customer = get_customer(request)
    form = ReviewForm(request.POST, name_required=customer is None)
    if form.is_valid():
        ProductReview.objects.create(
            product=product,
            user=request.user if request.user.is_authenticated else None,
            customer=customer,
            session_key=_session_key(request),
            guest_name=form.cleaned_data['guest_name'],
            rating=form.cleaned_data['rating'],
            comment=form.cleaned_data['comment'],
        )
        if _is_ajax(request):
            return JsonResponse({
                'success': True,
                'message': 'Thanks — your review has been posted.',
            })
        messages.success(request, 'Thanks — your review has been posted.')
    else:
        if _is_ajax(request):
            return JsonResponse({
                'success': False,
                'message': 'Please add your name and a rating.',
                'errors': {f: [str(e) for e in errs] for f, errs in form.errors.items()},
            }, status=400)
        messages.error(request, 'Please add your name and a rating.')
    return redirect('store:product_detail', slug=product.slug)


@require_POST
def review_vote(request, review_id):
    """One helpful / not-helpful vote on one review.

    A second click from the same visitor is refused rather than counted again:
    a signed-in shopper is matched on their account, a guest on the session, so
    the vote survives a reload without demanding one.
    """
    review = get_object_or_404(ProductReview, pk=review_id)
    value = (request.POST.get('value') or '').strip().lower()
    if value not in (ReviewVote.UP, ReviewVote.DOWN):
        return JsonResponse({'success': False, 'message': 'Unknown vote.'}, status=400)

    from django.db import IntegrityError

    customer = get_customer(request)
    session_key = _session_key(request)
    voter_key = ReviewVote.key_for(customer, session_key)

    try:
        _vote, created = ReviewVote.objects.get_or_create(
            review=review, voter_key=voter_key,
            defaults={'customer': customer,
                      'session_key': '' if customer else session_key,
                      'value': value})
    except IntegrityError:
        created = False
    if not created:
        return JsonResponse({
            'success': False,
            'message': 'You have already voted on this review.',
        }, status=409)

    counts = ReviewVote.objects.filter(review=review)
    return JsonResponse({
        'success': True,
        'up': counts.filter(value=ReviewVote.UP).count(),
        'down': counts.filter(value=ReviewVote.DOWN).count(),
    })


# ──────────────────── Theme 2: the one-step COD checkout ────────────────────
# Both endpoints are open to guests on purpose — the whole point of a
# cash-on-delivery funnel is buying without an account.


@require_POST
def theme2_quote(request, product_id):
    """Re-price on a quantity, option or district change; returns the summary."""
    result = theme2_checkout.quote(
        product_id,
        variation_id=request.POST.get('variation', ''),
        qty=request.POST.get('quantity', 1),
        district=request.POST.get('district', ''),
        branch_code=request.POST.get('courier_branch_code', ''),
    )
    if result.get('error'):
        return JsonResponse({'success': False, 'message': result['error']}, status=400)
    return JsonResponse({'success': True, 'quote': theme2_checkout.quote_json(result)})


@require_POST
def theme2_place_order(request, product_id):
    """Place the order the modal is showing.

    Every figure is re-quoted here and nothing about price is read from the
    request, so a forged total changes nothing: the order is written at what
    `theme2_checkout.quote()` says it costs.
    """
    result = theme2_checkout.quote(
        product_id,
        variation_id=request.POST.get('variation', ''),
        qty=request.POST.get('quantity', 1),
        district=request.POST.get('district', ''),
        branch_code=request.POST.get('courier_branch_code', ''),
    )
    if result.get('error'):
        if _is_ajax(request):
            return JsonResponse({'success': False, 'message': result['error']}, status=400)
        return _order_error(request, get_object_or_404(Product, pk=product_id),
                            result['error'])

    form = GuestOrderForm(request.POST)
    if not form.is_valid():
        if _is_ajax(request):
            return JsonResponse({
                'success': False,
                'message': 'Please correct the highlighted fields.',
                'errors': {f: [str(e) for e in errs] for f, errs in form.errors.items()},
            }, status=400)
        # Without scripting the modal is a plain form; re-rendering the whole
        # landing page for one bad field would lose the shopper's place, so the
        # first error is reported and they come back to the page they were on.
        first = next(iter(form.errors.values()))[0]
        return _order_error(request, result['product'], str(first))

    product = result['product']
    order = _place_order(
        request, form,
        items=[{
            'product': product,
            'quantity': result['qty'],
            'price': result['unit'],
            'variant': result['variant_label'],
            'variation': result['variation'],
        }],
        order_type='confirmed',
        delivery_district=form.cleaned_data['district'],
        # The delivery figure the modal displayed, so the order cannot be
        # written at a charge the shopper never saw.
        delivery_override=result['shipping'],
    )

    if not _is_ajax(request):
        messages.success(request, f'Order confirmed! Order #{order.order_number}')
        return redirect('store:order_detail', order_number=order.order_number)

    return JsonResponse({
        'success': True,
        'order_number': order.order_number,
        'total': str(order.total_price),
        'redirect': f'/store/orders/{order.order_number}/',
    })


# ──────────────────── Static pages ────────────────────

def dynamic_page(request, slug):
    page = get_object_or_404(Page, slug=slug, is_published=True)
    return render(request, 'store/page_detail.html', {'page': page})


# ──────────────────── Customer accounts ────────────────────
# Optional throughout: nothing below is required to shop, place an order or
# track one. Signing in only saves the shopper retyping their details and
# keeps their order history in one place.

def _login_locked(request):
    """True while this session is serving a cool-off after repeated failures."""
    import time
    state = request.session.get(LOGIN_ATTEMPT_KEY) or {}
    if state.get('count', 0) < MAX_LOGIN_ATTEMPTS:
        return False
    if time.time() - state.get('at', 0) > LOGIN_LOCKOUT_SECONDS:
        request.session.pop(LOGIN_ATTEMPT_KEY, None)
        return False
    return True


def _note_login_failure(request):
    import time
    state = request.session.get(LOGIN_ATTEMPT_KEY) or {'count': 0}
    state['count'] = state.get('count', 0) + 1
    state['at'] = time.time()
    request.session[LOGIN_ATTEMPT_KEY] = state
    request.session.modified = True


def account_login(request):
    if get_customer(request):
        return redirect(safe_next(request))

    form = CustomerLoginForm(request.POST or None)
    if request.method == 'POST':
        if _login_locked(request):
            messages.error(request, 'Too many failed attempts. Please try again in a few minutes.')
        elif form.is_valid():
            customer = form.get_customer()
            if customer:
                # `next` is read before login_customer() cycles the session.
                target = safe_next(request)
                request.session.pop(LOGIN_ATTEMPT_KEY, None)
                login_customer(request, customer)
                messages.success(request, f'Welcome back, {customer.first_name}.')
                return redirect(target)
            _note_login_failure(request)
            # One message for both wrong-password and no-such-account, so the
            # form cannot be used to discover which numbers are registered.
            form.add_error(None, 'Those details do not match an account.')

    return render(request, 'store/account_login.html', {
        'form': form,
        'next': request.POST.get('next') or request.GET.get('next', ''),
    })


def account_register(request):
    if get_customer(request):
        return redirect(safe_next(request))

    form = CustomerRegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        customer = StoreCustomer(
            full_name=form.cleaned_data['full_name'],
            email=form.cleaned_data['email'],
            phone=form.cleaned_data['phone'],
        )
        customer.set_password(form.cleaned_data['password'])
        customer.save()

        target = safe_next(request)
        login_customer(request, customer)
        messages.success(request, f'Account created. Welcome, {customer.first_name}.')
        return redirect(target)

    return render(request, 'store/account_register.html', {
        'form': form,
        'next': request.POST.get('next') or request.GET.get('next', ''),
    })


@require_POST
def account_logout(request):
    logout_customer(request)
    messages.success(request, 'You have been logged out.')
    return redirect('store:landing')


@customer_required
def account_home(request):
    customer = get_customer(request)
    orders = _visible_orders(request).prefetch_related('items__product')[:25]
    return render(request, 'store/account.html', {
        'customer': customer,
        'orders': orders,
        'profile_form': CustomerProfileForm(customer=customer, initial={
            'full_name': customer.full_name,
            'email': customer.email,
            'phone': customer.phone,
            'district': customer.district,
            'address': customer.address,
        }),
        'password_form': PasswordChangeForm(customer=customer),
        'saved_products': _wishlist_qs(request).select_related('product')[:8],
    })


@customer_required
@require_POST
def account_profile(request):
    customer = get_customer(request)
    form = CustomerProfileForm(request.POST, customer=customer)
    if form.is_valid():
        customer.full_name = form.cleaned_data['full_name']
        customer.email = form.cleaned_data['email']
        customer.phone = form.cleaned_data['phone']
        customer.district = form.cleaned_data['district']
        customer.address = form.cleaned_data['address']
        customer.save(update_fields=['full_name', 'email', 'phone', 'district', 'address'])
        messages.success(request, 'Your details have been saved.')
        return redirect('store:account')

    return render(request, 'store/account.html', {
        'customer': customer,
        'orders': _visible_orders(request).prefetch_related('items__product')[:25],
        'profile_form': form,
        'password_form': PasswordChangeForm(customer=customer),
        'saved_products': _wishlist_qs(request).select_related('product')[:8],
        'open_panel': 'details',
    })


@customer_required
@require_POST
def account_password(request):
    customer = get_customer(request)
    form = PasswordChangeForm(request.POST, customer=customer)
    if form.is_valid():
        customer.set_password(form.cleaned_data['new_password'])
        customer.save(update_fields=['password'])
        # A password change invalidates other sessions elsewhere in Django; do
        # the same here by re-establishing this one on a fresh key.
        login_customer(request, customer)
        messages.success(request, 'Your password has been changed.')
        return redirect('store:account')

    return render(request, 'store/account.html', {
        'customer': customer,
        'orders': _visible_orders(request).prefetch_related('items__product')[:25],
        'profile_form': CustomerProfileForm(customer=customer, initial={
            'full_name': customer.full_name,
            'email': customer.email,
            'phone': customer.phone,
            'district': customer.district,
            'address': customer.address,
        }),
        'password_form': form,
        'saved_products': _wishlist_qs(request).select_related('product')[:8],
        'open_panel': 'password',
    })
