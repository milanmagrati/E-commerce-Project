"""Storefront accounts + rebuilt product page — end-to-end checks.

Follows this repo's convention: a standalone script that calls django.setup()
and exercises the real models and views through Django's test client, rather
than a pytest/TestCase harness (there isn't one).

    python test_store_accounts.py

Everything it creates is removed again at the end, including on failure.
"""

import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

# The test client speaks to host "testserver"; this project runs with an
# explicit ALLOWED_HOSTS, so let it through for the length of this script only.
from django.conf import settings                                  # noqa: E402
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from django.test import Client                                    # noqa: E402
from django.urls import reverse                                   # noqa: E402

from dashboard.models import Category, Product                    # noqa: E402
from store.models import (Cart, CartItem, Order, ProductReview,   # noqa: E402
                          StoreCustomer, Wishlist)

PASS = 0
FAIL = 0
NOTES = []


def check(label, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [ok]   {label}')
    else:
        FAIL += 1
        print(f'  [FAIL] {label}' + (f'  -- {detail}' if detail else ''))


def section(title):
    print(f'\n{title}\n' + '-' * len(title))


# ──────────────────────────── fixtures ────────────────────────────

EMAIL = 'acct-check@example.test'
PHONE = '9812345601'
PASSWORD = 'Str0ng-Pass-9912'

category = None
product = None
created_category = False


def build_fixtures():
    global category, product, created_category
    category = Category.objects.first()
    if category is None:
        category = Category.objects.create(name='Account Check', slug='account-check-tmp')
        created_category = True

    # dashboard.Product.user is not nullable — reuse whichever staff user
    # already owns products rather than creating one.
    from django.contrib.auth import get_user_model
    owner = get_user_model().objects.order_by('pk').first()
    if owner is None:
        print('  [skip] no staff user in the database to own a test product')
        sys.exit(0)

    product = Product.objects.create(
        user=owner,
        name='Account Check Product',
        slug='account-check-product-tmp',
        description='A product used only by test_store_accounts.py.',
        category=category,
        price=1200,
        stock=25,
        is_active=True,
        is_deleted=False,
    )


def teardown():
    ProductReview.objects.filter(product=product).delete() if product else None
    if product:
        CartItem.objects.filter(product=product).delete()
        Wishlist.objects.filter(product=product).delete()
    customer = StoreCustomer.objects.filter(email=EMAIL).first()
    if customer:
        Order.objects.filter(customer=customer).delete()
        Cart.objects.filter(customer=customer).delete()
        Wishlist.objects.filter(customer=customer).delete()
        customer.delete()
    if product:
        Product.objects.filter(pk=product.pk).delete()
    if created_category and category:
        Category.objects.filter(pk=category.pk).delete()


# ──────────────────────────── checks ────────────────────────────

def check_product_page():
    section('Product page renders in the new layout')
    client = Client()
    res = client.get(reverse('store:product_detail', kwargs={'slug': product.slug}))
    html = res.content.decode('utf-8', 'replace')

    check('page returns 200', res.status_code == 200, f'got {res.status_code}')
    check('two-column product grid', 'class="pdp-grid"' in html)
    check('gallery with a scroll track', 'data-media-track' in html and 'data-slide' in html)
    check('image counter', 'data-media-counter' in html)
    check('lightbox modal present', 'data-media-modal' in html)
    check('quantity shows "(N in cart)"', 'data-in-cart' in html and 'in cart)' in html)
    check('add-to-cart form is AJAX-tagged', 'data-add-to-cart' in html)
    check('confirm + inquiry open the order panel',
          'data-open-order="confirmed"' in html and 'data-open-order="inquiry"' in html)
    check('order panel is the compact one', 'is-compact' in html)
    check('accordions rendered', html.count('data-acc-head') >= 3)
    check('share / copy link', 'data-share-copy' in html)
    check('review distribution bars', 'pdp-bar-fill' in html)
    check('sticky mobile buy bar', 'data-sticky-bar' in html)
    check('announcement bar', 'class="announce"' in html)
    check('cart notification markup', 'data-cart-note' in html)
    check('new stylesheet linked', 'store/css/product.css' in html)
    check('new script linked', 'store/js/product.js' in html)
    check('old tab markup is gone', 'tab-header' not in html)
    check('logged-out header offers Log in', 'Log in' in html)


def check_register_and_cart_merge():
    section('Register — and the guest cart comes along')
    client = Client()

    # Fill a cart as a guest first.
    res = client.post(
        reverse('store:add_to_cart', kwargs={'product_id': product.id}),
        {'quantity': 3},
        HTTP_X_REQUESTED_WITH='XMLHttpRequest',
    )
    payload = res.json()
    check('guest add-to-cart succeeds', payload.get('success') is True, str(payload))
    check('response carries the cart count', payload.get('count') == 3, str(payload.get('count')))
    check('response carries the per-product quantity',
          payload.get('item_quantity') == 3, str(payload.get('item_quantity')))
    check('response carries the product name for the notification',
          payload.get('name') == product.name)

    # Save a product as a guest too.
    client.post(reverse('store:toggle_wishlist', kwargs={'product_id': product.id}),
                HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    res = client.post(reverse('store:account_register'), {
        'full_name': 'Account Check',
        'email': EMAIL,
        'phone': PHONE,
        'password': PASSWORD,
        'password_confirm': PASSWORD,
    })
    check('registration redirects', res.status_code == 302, f'got {res.status_code}')

    customer = StoreCustomer.objects.filter(email=EMAIL).first()
    check('account was created', customer is not None)
    if not customer:
        return None, client

    check('password is hashed, not stored raw', customer.password != PASSWORD)
    check('password verifies', customer.check_password(PASSWORD))
    check('phone was normalised', customer.phone == PHONE)

    account_cart = Cart.objects.filter(customer=customer).first()
    check('guest cart was adopted by the account', account_cart is not None)
    if account_cart:
        line = account_cart.items.filter(product=product).first()
        check('the 3 units carried over', line is not None and line.quantity == 3,
              str(line.quantity if line else None))

    check('saved product carried over',
          Wishlist.objects.filter(customer=customer, product=product).exists())

    return customer, client


def check_session_separation(customer):
    section('Shoppers stay out of the staff user table')
    from django.contrib.auth import get_user_model
    User = get_user_model()
    check('no staff user was created for the shopper',
          not User.objects.filter(email=EMAIL).exists())
    check('StoreCustomer is its own model',
          StoreCustomer._meta.db_table != User._meta.db_table)

    client = Client()
    client.post(reverse('store:account_login'),
                {'identifier': EMAIL, 'password': PASSWORD})
    res = client.get(reverse('store:account'))
    check('signed-in shopper reaches the account page', res.status_code == 200)
    check('request.user is still anonymous for a shopper',
          not res.wsgi_request.user.is_authenticated)


def check_login_paths():
    section('Login accepts email or mobile, and rejects the rest')
    for label, identifier in (('email', EMAIL), ('mobile number', PHONE)):
        client = Client()
        res = client.post(reverse('store:account_login'),
                          {'identifier': identifier, 'password': PASSWORD})
        check(f'login with {label} works', res.status_code == 302, f'got {res.status_code}')

    client = Client()
    res = client.post(reverse('store:account_login'),
                      {'identifier': EMAIL, 'password': 'wrong-password'})
    html = res.content.decode('utf-8', 'replace')
    check('wrong password does not sign in', res.status_code == 200)
    check('the error does not reveal whether the account exists',
          'do not match an account' in html and 'no such' not in html.lower())

    client = Client()
    res = client.post(reverse('store:account_login'),
                      {'identifier': 'nobody@example.test', 'password': PASSWORD})
    check('unknown account gets the same message',
          'do not match an account' in res.content.decode('utf-8', 'replace'))


def check_order_form_prefill():
    section('Order form prefills from the account')
    client = Client()
    client.post(reverse('store:account_login'), {'identifier': EMAIL, 'password': PASSWORD})

    customer = StoreCustomer.objects.get(email=EMAIL)
    customer.district = 'KATHMANDU'
    customer.address = 'Kalanki, Ring Road'
    customer.save()

    res = client.get(reverse('store:quick_order', kwargs={'product_id': product.id}))
    html = res.content.decode('utf-8', 'replace')
    check('quick-order page loads', res.status_code == 200)
    check('name is prefilled', 'Account Check' in html)
    check('mobile number is prefilled', PHONE in html)
    check('saved address is prefilled', 'Kalanki, Ring Road' in html)


def check_guest_still_works():
    section('Nothing is gated behind an account')
    client = Client()

    res = client.get(reverse('store:checkout'))
    # Empty cart bounces to the cart page, not to a login page.
    check('checkout never redirects to login',
          '/account/login' not in res.get('Location', ''), res.get('Location', ''))

    res = client.get(reverse('store:order_track'))
    check('order tracking is open to guests', res.status_code == 200)

    res = client.post(reverse('store:add_review', kwargs={'product_id': product.id}),
                      {'rating': 4, 'guest_name': 'Passing Guest', 'comment': 'Fine.'})
    review = ProductReview.objects.filter(product=product, guest_name='Passing Guest').first()
    check('a guest can still review', review is not None)
    check('the guest review has no account attached', review is not None and review.customer_id is None)

    res = client.get(reverse('store:product_detail', kwargs={'slug': product.slug}))
    check('product page is reachable signed out', res.status_code == 200)


def check_signed_in_review():
    section('A signed-in review is attributed to the account')
    client = Client()
    client.post(reverse('store:account_login'), {'identifier': EMAIL, 'password': PASSWORD})
    client.post(reverse('store:add_review', kwargs={'product_id': product.id}),
                {'rating': 5, 'comment': 'Posted while signed in.'})

    review = ProductReview.objects.filter(
        product=product, comment='Posted while signed in.').first()
    check('review was saved without a typed name', review is not None)
    if review:
        check('it is owned by the account', review.customer_id is not None)
        check('it displays the account name', review.display_name == 'Account Check',
              review.display_name)


def check_account_area_is_protected():
    section('Account pages require signing in')
    client = Client()
    for name in ('store:account', 'store:account_profile', 'store:account_password'):
        url = reverse(name)
        res = client.post(url) if name != 'store:account' else client.get(url)
        check(f'{name} redirects a signed-out visitor',
              res.status_code == 302 and '/account/login' in res.get('Location', ''),
              f'{res.status_code} {res.get("Location", "")}')


def check_logout_keeps_cart():
    section('Logging out keeps the browser usable')
    client = Client()
    client.post(reverse('store:account_login'), {'identifier': EMAIL, 'password': PASSWORD})
    res = client.post(reverse('store:account_logout'))
    check('logout redirects to the storefront', res.status_code == 302)

    res = client.get(reverse('store:product_detail', kwargs={'slug': product.slug}))
    html = res.content.decode('utf-8', 'replace')
    check('the header shows Log in again', 'account/login' in html)
    check('still able to browse after logging out', res.status_code == 200)


def check_static_assets():
    section('Static assets are collected and served')
    from django.conf import settings
    for rel in ('store/css/product.css', 'store/js/product.js',
                'store/css/order-form.css', 'store/js/order-form.js'):
        path = os.path.join(settings.STATIC_ROOT, *rel.split('/'))
        check(f'{rel} is in STATIC_ROOT', os.path.exists(path), path)


# ──────────────────────────── run ────────────────────────────

if __name__ == '__main__':
    print('=' * 68)
    print('Storefront accounts + product page — verification')
    print('=' * 68)
    try:
        build_fixtures()
        check_product_page()
        customer, _ = check_register_and_cart_merge()
        if customer:
            check_session_separation(customer)
            check_login_paths()
            check_order_form_prefill()
            check_signed_in_review()
        check_guest_still_works()
        check_account_area_is_protected()
        check_logout_keeps_cart()
        check_static_assets()
    finally:
        teardown()

    print('\n' + '=' * 68)
    print(f'{PASS} passed, {FAIL} failed')
    for note in NOTES:
        print(f'  note: {note}')
    print('=' * 68)
    sys.exit(1 if FAIL else 0)
