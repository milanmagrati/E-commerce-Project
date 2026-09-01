#!/usr/bin/env python
"""
Standalone check for the storefront variable-product flow:

  * Product.has_variations / variation_price_range helpers
  * the product-card price-range tag
  * add_to_cart refuses a variable product with no variation chosen
  * two different variations of one product land as two separate cart lines,
    each priced from its own variation
  * placing an order records the variation on both the store OrderItem and the
    mirrored dashboard OrderItem

Run:  python test_store_variations.py
Leaves no rows behind — everything it creates is deleted at the end.
"""

import json
import os
import re
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from decimal import Decimal

from django.conf import settings
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

from django.core import mail
from django.test import Client
from django.template import Context, Template

from accounts.models import CustomUser
from dashboard.models import Product, ProductVariation, Category
from dashboard.models import Order as DashOrder, OrderItem as DashOrderItem
from store.models import Cart, CartItem, Order as StoreOrder, BackInStockNotice

PASS, FAIL = 0, 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")


def main():
    owner = CustomUser.objects.filter(is_superuser=True).first() \
        or CustomUser.objects.first()
    if owner is None:
        print("No user in the DB to own the product — aborting.")
        sys.exit(1)

    category = Category.objects.first()
    product = Product.objects.create(
        user=owner, name='TEST Variation Tee', slug='test-variation-tee',
        description='temp', category=category, product_type='variable',
        price=Decimal('500'), stock=0, is_active=True,
    )
    v_small = ProductVariation.objects.create(
        product=product, variation_name='Small', sku='TEST-VAR-S',
        price=Decimal('450'), stock=5, status='active', is_active=True,
    )
    v_large = ProductVariation.objects.create(
        product=product, variation_name='Large', sku='TEST-VAR-L',
        price=Decimal('700'), stock=3, status='active', is_active=True,
    )

    created_dash_orders = []
    created_store_orders = []
    created_products = [product]
    created_customers = []
    try:
        product.refresh_from_db()

        print("\nModel helpers")
        check("is_variable", product.is_variable)
        check("has_variations", product.has_variations)
        check("variation_price_range == (450, 700)",
              product.variation_price_range == (Decimal('450'), Decimal('700')))
        check("storefront_available (some variation in stock)",
              product.storefront_available)

        print("\nprice-display template tag")
        rendered = Template(
            "{% load store_tags %}{% product_price_display p %}"
        ).render(Context({'p': product})).strip()
        check(f"shows a range ({rendered!r})", '–' in rendered and '450' in rendered)

        print("\nadd_to_cart guarding")
        c = Client()
        add_url = f'/store/cart/add/{product.id}/'
        r = c.post(add_url, {'quantity': 1},
                   HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check("no variation -> success:false",
              r.json().get('success') is False)

        r = c.post(add_url, {'quantity': 2, 'selected_variation': v_small.id},
                   HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check("variation Small -> success:true", r.json().get('success') is True)
        check("unit_price is the variation price (450)",
              r.json().get('unit_price') in ('450.00', '450'))

        r = c.post(add_url, {'quantity': 1, 'selected_variation': v_large.id},
                   HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check("variation Large -> success:true", r.json().get('success') is True)

        cart = Cart.objects.filter(session_key=c.session.session_key).first()
        lines = list(CartItem.objects.filter(cart=cart, product=product))
        check("two separate cart lines", len(lines) == 2)
        by_var = {li.variation_id: li for li in lines}
        check("Small line total = 450 * 2",
              by_var[v_small.id].line_total == Decimal('900'))
        check("Large line total = 700 * 1",
              by_var[v_large.id].line_total == Decimal('700'))
        check("variant_label surfaces the variation name",
              by_var[v_small.id].variant_label == 'Small')

        print("\nproduct page")
        html = c.get(f'/store/products/{product.slug}/').content.decode()
        check("renders a group of toggle-able variation tiles",
              html.count('class="pdp-variant-card"') == 2
              and 'role="group"' in html
              and html.count('aria-pressed="false"') >= 2)
        check("offers a Clear control to back out of a choice",
              'data-variation-clear' in html)
        check("buy buttons wait on a choice", 'is-awaiting' in html)
        check("no error is shown before the shopper acts",
              'data-variation-hint hidden' in html)
        # The bug this replaced: the label read the first cart line for the
        # product, so both variations reported the same figure.
        cart_map = json.loads(
            re.search(r'data-in-cart-map>(.*?)</script>', html, re.S).group(1))
        check("in-cart figures are tracked per variation",
              cart_map == {str(v_small.id): 2, str(v_large.id): 1})

        print("\nvariation availability nets off unshipped orders")
        avail_p = Product.objects.create(
            user=owner, name='TEST Avail Tee', slug='test-avail-tee',
            description='temp', category=category, product_type='variable',
            price=Decimal('300'), stock=0, is_active=True,
        )
        created_products.append(avail_p)
        av = ProductVariation.objects.create(
            product=avail_p, variation_name='One', sku='TEST-AV-1',
            price=Decimal('300'), stock=10, status='active', is_active=True,
        )
        check("available_stock starts at real stock", av.available_stock == 10)
        d_order = DashOrder.objects.create(
            created_by=owner, customer_name='x', customer_phone='9811111111',
            shipping_address='a', order_from='Website',
            order_status='processing', payment_method='cod',
        )
        DashOrderItem.objects.create(order=d_order, product=avail_p,
                                     product_variation=av, product_name='TEST Avail Tee',
                                     quantity=4, price=Decimal('300'))
        av.refresh_from_db()
        check("an unshipped order claims units", av.available_stock == 6)
        d_order.order_status = 'dispatched'
        d_order.save(update_fields=['order_status'])
        av.refresh_from_db()
        check("a dispatched order no longer double-counts", av.available_stock == 10)
        DashOrderItem.objects.filter(order=d_order).delete()
        d_order.delete()

        print("\nout_of_stock status hides a variation from buying")
        av.status = 'out_of_stock'
        av.save(update_fields=['status'])
        check("is_in_stock is False despite stock on hand", av.is_in_stock is False)
        check("storefront_available follows", avail_p.storefront_available is False)
        oos_html = c.get(f'/store/products/{avail_p.slug}/').content.decode()
        check("the tile shows as sold out",
              'pdp-variant-card is-out' in oos_html)

        print("\ndeep link preselects a variation")
        dl_html = c.get(
            f'/store/products/{product.slug}/?variation={v_large.id}'
        ).content.decode()
        check("the named tile carries data-preselect",
              re.search(
                  r'data-variation-id="%d"[^>]*data-preselect="1"' % v_large.id,
                  dl_html, re.S) is not None)
        check("an unknown ?variation is ignored",
              'data-preselect="1"' not in
              c.get(f'/store/products/{product.slug}/?variation=999999').content.decode())

        print("\nper-variation low-stock threshold drives the copy")
        v_small.low_stock_threshold = 2
        v_small.save(update_fields=['low_stock_threshold'])
        ls_html = c.get(f'/store/products/{product.slug}/').content.decode()
        # v_small has 5 in stock, threshold 2 -> plain "In stock", not "Only N left"
        tile = re.search(
            r'data-variation-id="%d".*?</button>' % v_small.id, ls_html, re.S).group(0)
        check("stock above the variation threshold reads 'In stock'",
              'In stock' in tile and 'Only 5 left' not in tile)
        # v_large has 3, default threshold (5) -> "Only 3 left"
        tile_l = re.search(
            r'data-variation-id="%d".*?</button>' % v_large.id, ls_html, re.S).group(0)
        check("stock at/below the fallback threshold reads 'Only N left'",
              'Only 3 left' in tile_l)
        v_small.low_stock_threshold = 0
        v_small.save(update_fields=['low_stock_threshold'])

        print("\nback-in-stock notice: capture and one-shot send")
        mail.outbox = []
        sold = Product.objects.create(
            user=owner, name='TEST Notify Me', slug='test-notify-me',
            description='temp', category=category, product_type='simple',
            price=Decimal('80'), stock=0, is_active=True,
        )
        created_products.append(sold)
        nc = Client()
        sold_html = nc.get(f'/store/products/{sold.slug}/').content.decode()
        check("a sold-out product offers a notify form, not a buy block",
              'data-restock-form' in sold_html and 'data-add-to-cart' not in sold_html)
        rn = nc.post(f'/store/notify-back-in-stock/{sold.id}/',
                     {'email': 'waiting@test.local'},
                     HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check("notify endpoint accepts an email", rn.json().get('success') is True)
        check("one pending notice stored",
              BackInStockNotice.objects.filter(product=sold, notified_at__isnull=True).count() == 1)
        nc.post(f'/store/notify-back-in-stock/{sold.id}/',
                {'email': 'waiting@test.local'},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check("a repeat request does not duplicate",
              BackInStockNotice.objects.filter(product=sold).count() == 1)
        no_channel = nc.post(f'/store/notify-back-in-stock/{sold.id}/', {},
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check("no email or phone is rejected", no_channel.json().get('success') is False)
        sold.stock = 7
        sold.save(update_fields=['stock', 'stock_status'])
        notice = BackInStockNotice.objects.get(product=sold)
        check("restock stamps notified_at", notice.notified_at is not None)
        check("restock sends exactly one email", len(mail.outbox) == 1)
        mail.outbox = []
        sold.stock = 12
        sold.save(update_fields=['stock'])
        check("a later restock does not re-send", len(mail.outbox) == 0)
        BackInStockNotice.objects.filter(product=sold).delete()

        print("\nguest cart with variations survives sign-in")
        # Changing CartItem's unique_together to (cart, product, variation)
        # means the guest->account cart merge must key on the variation too;
        # keying on (cart, product) alone collides and raises IntegrityError.
        from django.urls import reverse
        from store.models import StoreCustomer
        buyer = StoreCustomer(full_name='Merge Buyer',
                              email='merge-buyer@test.local', phone='9800000011')
        buyer.set_password('pw-test-1234')
        buyer.save()
        created_customers.append(buyer)
        acct_cart = Cart.objects.create(customer=buyer)
        CartItem.objects.create(cart=acct_cart, product=product,
                                variation=v_small, quantity=1)
        login_res = c.post(reverse('store:account_login'), {
            'identifier': 'merge-buyer@test.local', 'password': 'pw-test-1234',
        })
        check("sign-in redirects, no IntegrityError on the cart merge",
              login_res.status_code == 302)
        acct_cart.refresh_from_db()
        merged = {ci.variation_id: ci.quantity for ci in acct_cart.items.all()}
        check("the shared variation's quantities add up (2 + 1)",
              merged.get(v_small.id) == 3)
        check("the guest-only variation moved across as its own line",
              merged.get(v_large.id) == 1)

        print("\nevery option sold out, product allows backorders")
        # A ProductVariation has no backorder flag, so the parent product's
        # must not open a buy block the shopper cannot use: with nothing
        # selectable they would be told to choose an option that isn't there.
        sold_out = Product.objects.create(
            user=owner, name='TEST Sold Out Tee', slug='test-sold-out-tee',
            description='temp', category=category, product_type='variable',
            price=Decimal('500'), stock=0, backorders_allowed=True, is_active=True,
        )
        created_products.append(sold_out)
        ProductVariation.objects.create(
            product=sold_out, variation_name='Only', sku='TEST-VAR-SO',
            price=Decimal('500'), stock=0, status='active', is_active=True,
        )
        check("storefront_available is False", sold_out.storefront_available is False)
        so_html = c.get(f'/store/products/{sold_out.slug}/').content.decode()
        check("no buy block is offered", 'data-add-to-cart' not in so_html)
        check("the option is still listed, marked sold out",
              'pdp-variant-card is-out' in so_html and 'Sold out' in so_html)

        print("\ncheckout records the variation")
        r = c.post('/store/checkout/', {
            'full_name': 'Test Buyer', 'phone': '9812345678',
            'district': 'KATHMANDU', 'address': 'Test tole, ward 5',
            'order_type': 'confirmed',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        body = r.json()
        check("checkout succeeded", body.get('success') is True)

        store_order = StoreOrder.objects.filter(
            order_number=body.get('order_number')).first()
        created_store_orders.append(store_order)
        oi_vars = sorted(
            (oi.variation_id for oi in store_order.items.all()))
        check("store OrderItems carry both variations",
              oi_vars == sorted([v_small.id, v_large.id]))

        dash_order = DashOrder.objects.filter(order_from='Website').order_by('-id').first()
        created_dash_orders.append(dash_order)
        dash_named = [i for i in dash_order.items.all() if i.product_variation_id]
        check("dashboard OrderItems mirror product_variation",
              len(dash_named) == 2)
        check("dashboard OrderItems mirror variation_name",
              {i.variation_name for i in dash_named} == {'Small', 'Large'})

    finally:
        for so in created_store_orders:
            if so:
                so.delete()
        for do in created_dash_orders:
            if do:
                do.delete()
        DashOrder.objects.filter(customer_phone='9812345678').delete()
        # Only the exact products this script created, by pk.
        for prod in created_products:
            Cart.objects.filter(items__product=prod).distinct().delete()
            ProductVariation.objects.filter(product=prod).delete()
            Product.objects.filter(pk=prod.pk).delete()
        for cust in created_customers:
            Cart.objects.filter(customer=cust).delete()
            cust.__class__.objects.filter(pk=cust.pk).delete()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
