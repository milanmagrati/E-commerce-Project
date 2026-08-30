"""Sign-in for storefront shoppers.

Kept entirely separate from `django.contrib.auth`: `request.user` is the staff
user with the dashboard's RBAC booleans on it, and letting a shopper occupy
that slot would put customers into staff lists, role dropdowns and every
`@permission_required` check. A shopper is instead remembered by a single
session key, `store_customer_id`, and read back through `get_customer()`.

Signing in is always optional — every flow on the storefront still works for a
guest, who is identified by `session_key` alone.
"""

import logging
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger(__name__)

SESSION_CUSTOMER_KEY = 'store_customer_id'

# Attribute the resolved customer is cached on, so a request that touches the
# navbar, the context processor and a view does one query, not three.
_REQUEST_CACHE_ATTR = '_store_customer_cache'


def get_customer(request):
    """The signed-in shopper, or None. Safe to call on every request."""
    cached = getattr(request, _REQUEST_CACHE_ATTR, False)
    if cached is not False:
        return cached

    customer = None
    customer_id = request.session.get(SESSION_CUSTOMER_KEY)
    if customer_id:
        from .models import StoreCustomer
        customer = StoreCustomer.objects.filter(pk=customer_id, is_active=True).first()
        if customer is None:
            # Deactivated or deleted between requests — drop the stale pointer
            # rather than leaving a session that claims to be signed in.
            request.session.pop(SESSION_CUSTOMER_KEY, None)

    setattr(request, _REQUEST_CACHE_ATTR, customer)
    return customer


def login_customer(request, customer):
    """Sign `customer` in and carry this browser's guest data over to them."""
    old_key = request.session.session_key

    # Fresh session id on privilege change, the same defence Django's own
    # login() applies, against session fixation. cycle_key() keeps the session
    # contents (the cart is keyed separately and adopted just below).
    request.session.cycle_key()

    request.session[SESSION_CUSTOMER_KEY] = customer.pk
    request.session.modified = True
    setattr(request, _REQUEST_CACHE_ATTR, customer)

    customer.last_login = timezone.now()
    customer.save(update_fields=['last_login'])

    adopt_guest_data(request, customer, old_key)
    return customer


def logout_customer(request):
    """Sign out without destroying the session: the shopper keeps their cart."""
    request.session.pop(SESSION_CUSTOMER_KEY, None)
    request.session.cycle_key()
    request.session.modified = True
    setattr(request, _REQUEST_CACHE_ATTR, None)


def adopt_guest_data(request, customer, old_session_key):
    """Hand everything this browser did as a guest to the account it just
    signed into: cart lines, wishlist, past orders and reviews.

    Runs on both sign-in and registration, so a shopper who fills a cart and
    only then makes an account does not lose it.
    """
    from .models import Cart, CartItem, Order, ProductReview, Wishlist

    new_key = request.session.session_key
    keys = {k for k in (old_session_key, new_key) if k}
    if not keys:
        return

    # ── cart: merge the guest cart into the account's cart ──
    account_cart, _ = Cart.objects.get_or_create(customer=customer)
    guest_carts = Cart.objects.filter(
        customer__isnull=True, session_key__in=keys
    ).exclude(pk=account_cart.pk)
    for guest_cart in guest_carts:
        for item in guest_cart.items.select_related('product'):
            existing = CartItem.objects.filter(
                cart=account_cart, product=item.product
            ).first()
            if existing:
                # unique_together is (cart, product), so quantities add up
                # instead of the move failing on a duplicate row.
                existing.quantity += item.quantity
                if item.selected_variant and not existing.selected_variant:
                    existing.selected_variant = item.selected_variant
                existing.save(update_fields=['quantity', 'selected_variant'])
            else:
                item.cart = account_cart
                item.save(update_fields=['cart'])
        guest_cart.delete()
    account_cart.session_key = new_key
    account_cart.save(update_fields=['session_key'])

    # ── wishlist: claim the guest rows, dropping products already saved ──
    already_saved = set(
        Wishlist.objects.filter(customer=customer).values_list('product_id', flat=True)
    )
    guest_saved = Wishlist.objects.filter(customer__isnull=True, session_key__in=keys)
    guest_saved.filter(product_id__in=already_saved).delete()
    guest_saved.update(customer=customer, session_key=new_key)

    # ── orders and reviews: claim by session, and by phone for orders placed
    # from another device with the number on this account ──
    Order.objects.filter(customer__isnull=True, session_key__in=keys).update(customer=customer)
    if customer.phone:
        Order.objects.filter(customer__isnull=True, phone=customer.phone).update(customer=customer)
    ProductReview.objects.filter(customer__isnull=True, session_key__in=keys).update(customer=customer)

    # The account now owns this browser; keep the session key on it so a later
    # guest-keyed lookup in the same tab still resolves.
    request.session.modified = True


def customer_required(view_func):
    """Send signed-out visitors to the login page, remembering where they were.

    Used only for the account area. Cart, checkout and order tracking stay open
    to guests, exactly as on the reference storefront.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if get_customer(request) is None:
            messages.info(request, 'Please log in to view your account.')
            login_url = reverse('store:account_login')
            return redirect(f'{login_url}?{urlencode({"next": request.get_full_path()})}')
        return view_func(request, *args, **kwargs)
    return _wrapped


def safe_next(request, fallback='store:account'):
    """The `next=` target, but only when it points back at this site."""
    target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return reverse(fallback)
