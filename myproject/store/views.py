import json
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Avg, Count

from dashboard.models import Category, Product, ProductImage
from dashboard.models import Order as DashOrder, OrderItem as DashOrderItem
from dashboard.models import Customer as DashCustomer, Setup
from .models import ProductReview, Cart, CartItem, Order, OrderItem, Wishlist
from .forms import LoginForm, RegisterForm, ReviewForm, CheckoutForm


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


def _create_dashboard_order(user, full_name, phone, email,
                            address, city, order_type, items_data,
                            total_amount, shipping_charge=0):
    """Create a dashboard Order + OrderItems so admin can see store orders."""
    from decimal import Decimal
    from django.db import IntegrityError, transaction

    # Find or create a Customer record by phone
    customer = None
    if phone:
        customer = DashCustomer.objects.filter(phone=phone).first()
        if not customer:
            customer = DashCustomer.objects.create(
                name=full_name or f"{user.first_name} {user.last_name}".strip() or user.username,
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
                    customer_name=full_name or f"{user.first_name} {user.last_name}".strip() or user.username,
                    customer_phone=phone or '',
                    customer_email=email or '',
                    shipping_address=address or '',
                    branch_city=city or '',
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


def _get_cart(request):
    """Get or create cart for the current user/session."""
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user)
        return cart
    if not request.session.session_key:
        request.session.create()
    cart, _ = Cart.objects.get_or_create(session_key=request.session.session_key)
    return cart


def _merge_session_cart(request, user):
    """Merge anonymous session cart into user cart on login."""
    session_key = request.session.session_key
    if not session_key:
        return
    try:
        session_cart = Cart.objects.get(session_key=session_key, user__isnull=True)
    except Cart.DoesNotExist:
        return
    user_cart, _ = Cart.objects.get_or_create(user=user)
    for item in session_cart.items.all():
        existing = user_cart.items.filter(product=item.product).first()
        if existing:
            existing.quantity += item.quantity
            existing.save()
        else:
            item.cart = user_cart
            item.save()
    session_cart.delete()


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

    user_has_reviewed = False
    if request.user.is_authenticated:
        user_has_reviewed = reviews.filter(user=request.user).exists()

    in_wishlist = False
    if request.user.is_authenticated:
        in_wishlist = Wishlist.objects.filter(user=request.user, product=product).exists()

    avg_rating = _avg_rating(product)

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
        'user_has_reviewed': user_has_reviewed,
        'review_form': ReviewForm(),
        'in_wishlist': in_wishlist,
        'avg_rating': avg_rating,
        'review_count': review_count,
        'available_stock': available_stock,
        'variant_options': variant_options,
        'bundle_components': bundle_components,
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
    shipping = 0 if subtotal >= 500 else 100
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
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
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

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'message': f'{product.name} added to cart',
            'count': cart.total_items,
        })
    messages.success(request, f'{product.name} added to cart.')
    return redirect('store:cart')


@require_POST
def remove_from_cart(request, item_id):
    cart = _get_cart(request)
    item = get_object_or_404(CartItem, pk=item_id, cart=cart)
    name = item.product.name
    item.delete()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
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
    shipping = 0 if subtotal >= 500 else 100
    return JsonResponse({
        'success': True,
        'count': cart.total_items,
        'item_total': str(item.line_total) if quantity > 0 else '0',
        'subtotal': str(subtotal),
        'shipping': str(shipping),
        'total': str(subtotal + shipping),
    })


# ──────────────────── Wishlist ────────────────────

@login_required(login_url='/store/login/')
def wishlist_view(request):
    wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product')
    return render(request, 'store/wishlist.html', {'wishlist_items': wishlist_items})


@require_POST
def toggle_wishlist(request, product_id):
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'message': 'Login required', 'login_required': True})

    product = get_object_or_404(Product, pk=product_id)
    wishlist_item = Wishlist.objects.filter(user=request.user, product=product)
    if wishlist_item.exists():
        wishlist_item.delete()
        added = False
    else:
        Wishlist.objects.create(user=request.user, product=product)
        added = True

    return JsonResponse({
        'success': True,
        'added': added,
        'message': 'Added to wishlist' if added else 'Removed from wishlist',
        'count': Wishlist.objects.filter(user=request.user).count(),
    })


# ──────────────────── Checkout & Orders ────────────────────

@login_required(login_url='/store/login/')
def checkout_view(request):
    cart = _get_cart(request)
    cart_items = cart.items.select_related('product').all()
    if not cart_items.exists():
        messages.warning(request, 'Your cart is empty.')
        return redirect('store:cart')

    subtotal = cart.subtotal
    shipping = 0 if subtotal >= 500 else 100
    total = subtotal + shipping

    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            order_type = request.POST.get('order_type', 'confirmed')
            if order_type not in ('confirmed', 'inquiry'):
                order_type = 'confirmed'

            # Stock validation only for confirmed orders
            if order_type == 'confirmed':
                for item in cart_items:
                    avail = item.product.available_stock
                    if item.quantity > avail and not item.product.backorders_allowed:
                        messages.error(request, f'"{item.product.name}" only has {avail} in stock.')
                        return redirect('store:cart')

            address_parts = [
                form.cleaned_data['address_line1'],
                form.cleaned_data.get('address_line2', ''),
                form.cleaned_data['city'],
                form.cleaned_data['province'],
            ]
            address = ', '.join(p for p in address_parts if p)

            order = Order.objects.create(
                user=request.user,
                full_name=form.cleaned_data['full_name'],
                order_type=order_type,
                total_price=total,
                shipping_address=address,
                phone=form.cleaned_data['phone'],
                email=form.cleaned_data['email'],
                city=form.cleaned_data['city'],
                province=form.cleaned_data['province'],
            )
            for item in cart_items:
                OrderItem.objects.create(
                    order=order,
                    product=item.product,
                    quantity=item.quantity,
                    price=item.product.price,
                    selected_variant=item.selected_variant,
                )
            # Create dashboard order for admin visibility
            _create_dashboard_order(
                user=request.user,
                full_name=form.cleaned_data['full_name'],
                phone=form.cleaned_data['phone'],
                email=form.cleaned_data['email'],
                address=address,
                city=form.cleaned_data['city'],
                order_type=order_type,
                items_data=[
                    {'product': ci.product, 'quantity': ci.quantity, 'price': ci.product.price}
                    for ci in cart_items
                ],
                total_amount=total,
                shipping_charge=shipping,
            )

            # Allocate stock via inventory service for confirmed orders
            if order_type == 'confirmed':
                from inventory.services import allocate_order
                allocate_order(order)
                cart_items.delete()
                messages.success(request, f'Order confirmed! Order number: {order.order_number}')
            else:
                messages.success(request, f'Inquiry submitted! Reference number: {order.order_number}')
            return redirect('store:order_detail', order_number=order.order_number)
    else:
        form = CheckoutForm(initial={
            'full_name': f'{request.user.first_name} {request.user.last_name}'.strip(),
            'email': request.user.email,
        })

    return render(request, 'store/checkout.html', {
        'form': form,
        'cart_items': cart_items,
        'subtotal': subtotal,
        'shipping': shipping,
        'total': total,
    })


@login_required(login_url='/store/login/')
def order_list(request):
    orders = Order.objects.filter(user=request.user)
    return render(request, 'store/orders.html', {'orders': orders})


@login_required(login_url='/store/login/')
def order_detail(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    return render(request, 'store/order_detail.html', {'order': order})


# ──────────────────── Auth ────────────────────

def customer_login(request):
    if request.user.is_authenticated:
        return redirect('store:landing')
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            _merge_session_cart(request, user)
            messages.success(request, f'Welcome back, {user.first_name or user.username}!')
            next_url = request.GET.get('next', '/store/')
            return redirect(next_url)
    else:
        form = LoginForm()
    return render(request, 'store/login.html', {'form': form})


def customer_register(request):
    if request.user.is_authenticated:
        return redirect('store:landing')
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            _merge_session_cart(request, user)
            messages.success(request, 'Account created successfully!')
            return redirect('store:landing')
    else:
        form = RegisterForm()
    return render(request, 'store/register.html', {'form': form})


@require_POST
def customer_logout(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('store:landing')


@login_required(login_url='/store/login/')
def customer_profile(request):
    orders = Order.objects.filter(user=request.user)[:5]
    wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product')[:5]
    return render(request, 'store/profile.html', {
        'orders': orders,
        'wishlist_items': wishlist_items,
    })


# ──────────────────── Reviews ────────────────────

@login_required(login_url='/store/login/')
@require_POST
def add_review(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    if ProductReview.objects.filter(product=product, user=request.user).exists():
        messages.warning(request, 'You have already reviewed this product.')
        return redirect('store:product_detail', slug=product.slug)

    form = ReviewForm(request.POST)
    if form.is_valid():
        ProductReview.objects.create(
            product=product,
            user=request.user,
            rating=form.cleaned_data['rating'],
            comment=form.cleaned_data['comment'],
        )
        messages.success(request, 'Review submitted successfully!')
    else:
        messages.error(request, 'Invalid review. Please provide a rating between 1-5.')
    return redirect('store:product_detail', slug=product.slug)


# ──────────────────── Quick Order (from product detail) ────────────────────

def quick_order(request, product_id):
    """Direct order from product detail — shows a mini checkout form."""
    product = get_object_or_404(Product, pk=product_id, is_active=True, is_deleted=False)

    # Require login — redirect back to product page after login
    if not request.user.is_authenticated:
        return redirect(f'/store/login/?next=/store/products/{product.slug}/')

    available_stock = product.available_stock

    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            try:
                quantity = max(1, min(int(request.POST.get('quantity', 1)), available_stock))
            except (ValueError, TypeError):
                quantity = 1

            order_type = request.POST.get('order_type', 'confirmed')
            if order_type not in ('confirmed', 'inquiry'):
                order_type = 'confirmed'

            selected_variant = request.POST.get('selected_variant', '')[:500]

            if order_type == 'confirmed' and available_stock <= 0 and not product.backorders_allowed:
                messages.error(request, 'This product is out of stock.')
                return redirect('store:product_detail', slug=product.slug)

            if order_type == 'confirmed' and quantity > available_stock and not product.backorders_allowed:
                messages.error(request, f'Only {available_stock} unit(s) available.')
                return redirect('store:product_detail', slug=product.slug)

            subtotal = product.price * quantity
            shipping = 0 if subtotal >= 500 else 100
            total = subtotal + shipping

            address_parts = [
                form.cleaned_data['address_line1'],
                form.cleaned_data.get('address_line2', ''),
                form.cleaned_data['city'],
                form.cleaned_data['province'],
            ]
            address = ', '.join(p for p in address_parts if p)

            order = Order.objects.create(
                user=request.user,
                full_name=form.cleaned_data['full_name'],
                order_type=order_type,
                total_price=total,
                shipping_address=address,
                phone=form.cleaned_data['phone'],
                email=form.cleaned_data['email'],
                city=form.cleaned_data['city'],
                province=form.cleaned_data['province'],
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=quantity,
                price=product.price,
                selected_variant=selected_variant,
            )

            # Create dashboard order for admin visibility
            _create_dashboard_order(
                user=request.user,
                full_name=form.cleaned_data['full_name'],
                phone=form.cleaned_data['phone'],
                email=form.cleaned_data['email'],
                address=address,
                city=form.cleaned_data['city'],
                order_type=order_type,
                items_data=[
                    {'product': product, 'quantity': quantity, 'price': product.price}
                ],
                total_amount=total,
                shipping_charge=shipping,
            )

            if order_type == 'confirmed':
                from inventory.services import allocate_order
                allocate_order(order)
                messages.success(request, f'Order confirmed! Order #{order.order_number}')
            else:
                messages.success(request, f'Inquiry submitted! Reference #{order.order_number}')

            return redirect('store:order_detail', order_number=order.order_number)

        # Form invalid — re-render with errors
        try:
            quantity = max(1, int(request.POST.get('quantity', 1)))
        except (ValueError, TypeError):
            quantity = 1
        order_type = request.POST.get('order_type', 'confirmed')
        selected_variant = request.POST.get('selected_variant', '')[:500]
    else:
        # GET — pre-fill form and show quick checkout
        try:
            quantity = max(1, min(int(request.GET.get('qty', 1)), max(available_stock, 1)))
        except (ValueError, TypeError):
            quantity = 1
        order_type = request.GET.get('order_type', 'confirmed')
        selected_variant = request.GET.get('variant', '')[:500]
        form = CheckoutForm(initial={
            'full_name': f'{request.user.first_name} {request.user.last_name}'.strip(),
            'email': request.user.email,
        })

    subtotal = product.price * quantity
    shipping = 0 if subtotal >= 500 else 100

    return render(request, 'store/quick_checkout.html', {
        'product': product,
        'quantity': quantity,
        'order_type': order_type,
        'form': form,
        'subtotal': subtotal,
        'shipping': shipping,
        'total': subtotal + shipping,
        'available_stock': available_stock,
        'selected_variant': selected_variant,
    })
