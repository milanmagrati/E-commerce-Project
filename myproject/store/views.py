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
                     DiscountCode, StoreCustomer)
from .forms import (GuestOrderForm, ReviewForm, OrderTrackForm, CustomerLoginForm,
                    CustomerRegisterForm, CustomerProfileForm, PasswordChangeForm)
from . import services
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
        DashOrderItem.objects.create(
            order=dash_order,
            product=item['product'],
            product_name=item['product'].name,
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


def _get_product_image(product):
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
        data.append({
            'id': p.id,
            'name': p.name,
            'slug': p.slug,
            'price': str(p.price),
            'average_rating': _avg_rating(p),
            'review_count': _review_count(p),
            'image': _get_product_image(p),
            'stock': p.stock,
        })
    return JsonResponse({'products': data, 'has_more': len(data) == limit})


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
    reviews = product.store_reviews.select_related('user').all()
    related_products = _active_products().filter(
        category=product.category
    ).exclude(pk=product.pk)[:6]

    # Rating distribution as a list for template rendering
    review_count = reviews.count()
    rating_counts = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    for r in reviews:
        rating_counts[r.rating] = rating_counts.get(r.rating, 0) + 1
    rating_dist = [
        {'star': s, 'count': rating_counts[s], 'percent': round(rating_counts[s] / review_count * 100) if review_count else 0}
        for s in [5, 4, 3, 2, 1]
    ]

    in_wishlist = _wishlist_qs(request).filter(product=product).exists()
    avg_rating = _avg_rating(product)
    customer = get_customer(request)

    # How many of this product are already sitting in the cart — the
    # quantity label reads "(N in cart)", as on the reference storefront.
    cart = _get_cart(request)
    in_cart_qty = next(
        (i.quantity for i in cart.items.all() if i.product_id == product.pk), 0)

    # Bundle / variable product extras
    available_stock = product.available_stock
    variant_options = []
    bundle_components = []
    if product.product_type == 'variable':
        variant_options = list(product.variant_options.all())
    elif product.product_type == 'bundle':
        bundle_components = list(
            product.bundle_components.select_related('component_product').all()
        )

    return render(request, 'store/product_detail.html', {
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
        'variant_options': variant_options,
        'bundle_components': bundle_components,
        'product_image_url': _get_product_image(product),
        'free_delivery_threshold': services.free_delivery_threshold(),
    })


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
    cart_items = cart.items.select_related('product').all()
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

    avail = product.available_stock
    selected_variant = request.POST.get('selected_variant', '')[:500]

    # Stock validation — skip for backorder-enabled products
    if avail <= 0 and not product.backorders_allowed:
        if _is_ajax(request):
            return JsonResponse({'success': False, 'message': 'This product is out of stock'})
        messages.error(request, 'This product is out of stock.')
        return redirect('store:product_detail', slug=product.slug)

    cart = _get_cart(request)

    # For backorder-enabled products, don't cap quantity to available stock
    if product.backorders_allowed:
        effective_qty = quantity
    else:
        effective_qty = min(quantity, avail)

    item, created = CartItem.objects.get_or_create(
        cart=cart, product=product,
        defaults={'quantity': effective_qty, 'selected_variant': selected_variant}
    )
    if not created:
        if product.backorders_allowed:
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
            'image': _get_product_image(product),
            'variant': item.selected_variant,
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
        if item.product.backorders_allowed:
            item.quantity = quantity
        else:
            item.quantity = min(quantity, item.product.available_stock)
        item.save()

    subtotal = cart.subtotal
    shipping = services.delivery_charge_for(subtotal)
    return JsonResponse({
        'success': True,
        'count': cart.total_items,
        'item_total': str(item.line_total) if quantity > 0 else '0',
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


def _place_order(request, form, items, order_type, delivery_district):
    """Shared tail of every checkout: price it, persist it, mirror it to the
    dashboard, allocate stock. `items` is [{'product', 'quantity', 'price',
    'variant'}]. Returns the store Order."""
    from django.db import transaction

    subtotal = sum(Decimal(str(i['price'])) * i['quantity'] for i in items)
    discount, _msg = services.lookup_discount(form.cleaned_data.get('discount_code'), subtotal)

    branch_code = (form.cleaned_data.get('courier_branch_code') or '').strip().upper()
    totals = services.price_order(subtotal, delivery_district, discount, branch_code)
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
    available_stock = product.available_stock

    if request.method == 'POST':
        form = GuestOrderForm(request.POST)
        order_type = request.POST.get('order_type', 'confirmed')
        if order_type not in ('confirmed', 'inquiry'):
            order_type = 'confirmed'
        selected_variant = request.POST.get('selected_variant', '')[:500]
        try:
            quantity = max(1, int(request.POST.get('quantity', 1)))
        except (ValueError, TypeError):
            quantity = 1

        if form.is_valid():
            district = form.cleaned_data['district']

            # Stock only gates a real commitment; an inquiry may exceed it.
            if order_type == 'confirmed' and not product.backorders_allowed:
                if available_stock <= 0:
                    return _order_error(request, product, 'This product is out of stock.')
                if quantity > available_stock:
                    return _order_error(request, product, f'Only {available_stock} unit(s) available.')

            order = _place_order(
                request, form,
                items=[{
                    'product': product, 'quantity': quantity,
                    'price': product.price, 'variant': selected_variant,
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
        try:
            quantity = max(1, min(int(request.GET.get('qty', 1)), max(available_stock, 1)))
        except (ValueError, TypeError):
            quantity = 1
        order_type = request.GET.get('order_type', 'confirmed')
        if order_type not in ('confirmed', 'inquiry'):
            order_type = 'confirmed'
        selected_variant = request.GET.get('variant', '')[:500]
        form = GuestOrderForm(initial=_order_form_initial(request))

    subtotal = product.price * quantity
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
        'product_image_url': _get_product_image(product),
    })


def _order_error(request, product, message):
    if _is_ajax(request):
        return JsonResponse({'success': False, 'message': message}, status=400)
    messages.error(request, message)
    return redirect('store:product_detail', slug=product.slug)


# ──────────────────── Cart checkout ────────────────────

def checkout_view(request):
    cart = _get_cart(request)
    cart_items = list(cart.items.select_related('product').all())
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
                    avail = item.product.available_stock
                    if item.quantity > avail and not item.product.backorders_allowed:
                        msg = f'"{item.product.name}" only has {avail} in stock.'
                        if _is_ajax(request):
                            return JsonResponse({'success': False, 'message': msg}, status=400)
                        messages.error(request, msg)
                        return redirect('store:cart')

            order = _place_order(
                request, form,
                items=[{
                    'product': ci.product, 'quantity': ci.quantity,
                    'price': ci.product.price, 'variant': ci.selected_variant,
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
        messages.success(request, 'Thanks — your review has been posted.')
    else:
        messages.error(request, 'Please add your name and a rating.')
    return redirect('store:product_detail', slug=product.slug)


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
