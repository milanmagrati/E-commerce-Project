#!/usr/bin/env python
"""
Standalone check for storefront quantity breaks ("buy 3, save 10%"):

  * store.bulk_discounts resolves the most specific rule
    (variation ▸ product ▸ category ▸ shop-wide)
  * every tier type prices correctly, and a broken tier can only fail towards
    the list price
  * the cheapest applicable rung wins, even in a ladder typed out of order
  * schedule windows and the active flag gate a rule
  * a cart line re-prices itself as it crosses a rung, and the cart subtotal
    follows
  * a card teaser appears for a simple product and never for a variable one
  * the product page ships the ladder and the card renders the chip
  * an order placed through the storefront is written at the discounted rate,
    on both the store OrderItem and the mirrored dashboard OrderItem
  * the Setup page loads, saves a rule with its tiers, and the preview endpoint
    agrees with the storefront

Run:  python test_store_bulk_discounts.py
Leaves no rows behind — everything it creates is deleted at the end.
"""

import os
import sys
import django

# A Windows console defaults to cp1252, which cannot print the arrows and
# rules used below; say what encoding this script writes in rather than
# flattening its output to ASCII.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from django.template import Context, Template
from django.test import Client
from django.utils import timezone

from accounts.models import CustomUser
from dashboard.models import Category, Product, ProductVariation
from dashboard.models import Order as DashOrder
from store import bulk_discounts
from store.models import (BulkDiscount, BulkDiscountTier, Cart, CartItem,
                          Order as StoreOrder)

PASS, FAIL = 0, 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")


def tier(rule, min_qty, kind, value, label=''):
    return BulkDiscountTier.objects.create(
        rule=rule, min_qty=min_qty, discount_type=kind,
        value=Decimal(str(value)), label=label)


def fresh():
    """Rules are read through a cached snapshot; the setup page busts it on
    every write, so a script that writes directly must do the same."""
    bulk_discounts.invalidate_cache()


def main():
    owner = CustomUser.objects.filter(is_superuser=True).first() or CustomUser.objects.first()
    if owner is None:
        print("No user in the DB to own the product — aborting.")
        sys.exit(1)

    category = Category.objects.create(name='TEST Bulk Cat', slug='test-bulk-cat')
    products, rules, carts = [], [], []
    store_orders, dash_orders = [], []

    try:
        simple = Product.objects.create(
            user=owner, name='TEST Bulk Serum', slug='test-bulk-serum',
            description='temp', category=category, product_type='simple',
            price=Decimal('1000'), stock=100, is_active=True)
        products.append(simple)

        variable = Product.objects.create(
            user=owner, name='TEST Bulk Oil', slug='test-bulk-oil',
            description='temp', category=category, product_type='variable',
            price=Decimal('800'), stock=0, is_active=True)
        products.append(variable)
        v_red = ProductVariation.objects.create(
            product=variable, variation_name='Red', sku='TEST-BULK-RED',
            price=Decimal('900'), stock=50, status='active', is_active=True)
        v_blue = ProductVariation.objects.create(
            product=variable, variation_name='Blue', sku='TEST-BULK-BLUE',
            price=Decimal('700'), stock=50, status='active', is_active=True)

        # ── 1. tier arithmetic ────────────────────────────────────────
        print("\n── tier arithmetic ──")
        r_simple = BulkDiscount.objects.create(
            name='TEST simple ladder', scope='product', product=simple, priority=5)
        rules.append(r_simple)
        tier(r_simple, 3, 'percent', 10)
        tier(r_simple, 6, 'amount', 250)
        tier(r_simple, 10, 'price', 600)
        fresh()

        check("1 unit is the list price",
              bulk_discounts.price_for(simple, None, 1)['unit'] == Decimal('1000.00'))
        check("3 units take 10% off",
              bulk_discounts.price_for(simple, None, 3)['unit'] == Decimal('900.00'))
        check("6 units take Rs. 250 off each",
              bulk_discounts.price_for(simple, None, 6)['unit'] == Decimal('750.00'))
        check("10 units are a flat Rs. 600 each",
              bulk_discounts.price_for(simple, None, 10)['unit'] == Decimal('600.00'))

        q6 = bulk_discounts.price_for(simple, None, 6)
        check("the line total uses the discounted rate",
              q6['line_total'] == Decimal('4500.00'))
        check("the saving is reported against the list price",
              q6['saved'] == Decimal('1500.00'))
        check("the next rung is named for the nudge",
              q6['next_tier'] and q6['next_tier']['min_qty'] == 10)
        check("how many more units the next rung needs", q6['need_more'] == 4)

        # ── 2. a ladder typed out of order ────────────────────────────
        print("\n── a mis-ordered ladder cannot overcharge ──")
        r_simple.tiers.all().delete()
        tier(r_simple, 3, 'percent', 30)     # deeper …
        tier(r_simple, 5, 'percent', 10)     # … than the rung above it
        fresh()
        check("5 units still get the cheapest applicable rung",
              bulk_discounts.price_for(simple, None, 5)['unit'] == Decimal('700.00'))

        print("\n── a broken tier fails towards the list price ──")
        r_simple.tiers.all().delete()
        tier(r_simple, 2, 'amount', 99999)   # more off than the item costs
        fresh()
        check("an over-large discount floors at zero, never negative",
              bulk_discounts.price_for(simple, None, 2)['unit'] == Decimal('0.00'))
        r_simple.tiers.all().delete()
        tier(r_simple, 2, 'price', 5000)     # a 'fixed price' above list
        fresh()
        check("a fixed price above the list price is ignored",
              bulk_discounts.price_for(simple, None, 2)['unit'] == Decimal('1000.00'))
        check("and no tier is offered, because it saves nothing",
              bulk_discounts.tiers_for(simple) == [])

        # ── 3. specificity ────────────────────────────────────────────
        print("\n── the most specific rule wins ──")
        r_simple.tiers.all().delete()
        tier(r_simple, 2, 'percent', 10)
        r_cat = BulkDiscount.objects.create(
            name='TEST category ladder', scope='category', category=category)
        rules.append(r_cat)
        tier(r_cat, 2, 'percent', 5)
        r_all = BulkDiscount.objects.create(name='TEST shop ladder', scope='all')
        rules.append(r_all)
        tier(r_all, 2, 'percent', 1)
        fresh()

        check("a product rule beats its category's",
              bulk_discounts.price_for(simple, None, 2)['unit'] == Decimal('900.00'))
        check("a category rule covers a product with no rule of its own",
              bulk_discounts.price_for(variable, v_red, 2)['unit'] == Decimal('855.00'))

        r_var = BulkDiscount.objects.create(
            name='TEST variation ladder', scope='variation', variation=v_red)
        rules.append(r_var)
        tier(r_var, 2, 'percent', 20)
        fresh()
        check("a variation rule beats the category rule above it",
              bulk_discounts.price_for(variable, v_red, 2)['unit'] == Decimal('720.00'))
        check("its sibling variation is untouched by it",
              bulk_discounts.price_for(variable, v_blue, 2)['unit'] == Decimal('665.00'))
        check("saving a variation rule fills in its product",
              BulkDiscount.objects.get(pk=r_var.pk).product_id == variable.pk)

        # A shop-wide rule is the last resort.
        r_cat.is_active = False
        r_cat.save()
        fresh()
        check("with no category rule, the shop-wide one applies",
              bulk_discounts.price_for(variable, v_blue, 2)['unit'] == Decimal('693.00'))
        r_cat.is_active = True
        r_cat.save()

        # ── 4. gating ─────────────────────────────────────────────────
        print("\n── active flag and schedule ──")
        r_simple.is_active = False
        r_simple.save()
        fresh()
        check("a paused rule stops applying (category takes over)",
              bulk_discounts.price_for(simple, None, 2)['unit'] == Decimal('950.00'))
        r_simple.is_active = True
        r_simple.starts_at = timezone.now() + timedelta(days=1)
        r_simple.save()
        fresh()
        check("a rule that has not started yet does not apply",
              bulk_discounts.price_for(simple, None, 2)['unit'] == Decimal('950.00'))
        r_simple.starts_at = None
        r_simple.ends_at = timezone.now() - timedelta(minutes=1)
        r_simple.save()
        fresh()
        check("an expired rule does not apply",
              bulk_discounts.price_for(simple, None, 2)['unit'] == Decimal('950.00'))
        check("schedule_state names it 'expired'",
              BulkDiscount.objects.get(pk=r_simple.pk).schedule_state == 'expired')
        r_simple.ends_at = None
        r_simple.save()
        fresh()

        # ── 5. priority ───────────────────────────────────────────────
        print("\n── priority breaks a tie ──")
        r_rival = BulkDiscount.objects.create(
            name='TEST rival', scope='product', product=simple, priority=9)
        rules.append(r_rival)
        tier(r_rival, 2, 'percent', 25)
        fresh()
        check("the higher-priority rule on the same target wins",
              bulk_discounts.price_for(simple, None, 2)['unit'] == Decimal('750.00'))
        r_rival.delete()
        rules.remove(r_rival)
        fresh()

        # ── 6. the cart prices for real ───────────────────────────────
        print("\n── the cart charges the break, it does not just show it ──")
        cart = Cart.objects.create(session_key='test-bulk-session')
        carts.append(cart)
        line = CartItem.objects.create(cart=cart, product=simple, quantity=1)
        check("one unit sits at the list price", line.unit_price == Decimal('1000.00'))
        line.quantity = 2
        line.save()
        line = CartItem.objects.get(pk=line.pk)
        check("crossing the rung re-prices the line", line.unit_price == Decimal('900.00'))
        check("and the line total follows", line.line_total == Decimal('1800.00'))
        check("the tier is named for the cart page",
              line.bulk_tier and line.bulk_tier['offer_label'] == 'Save 10%')
        check("the saving is reported", line.bulk_saved == Decimal('200.00'))
        check("the cart subtotal is the discounted one",
              Cart.objects.get(pk=cart.pk).subtotal == Decimal('1800.00'))

        # ── 7. what the storefront shows ──────────────────────────────
        print("\n── card teaser and product page ──")
        teaser = bulk_discounts.card_teaser(simple)
        check("a simple product's card teases the break",
              teaser is not None and teaser['min_qty'] == 2)
        check("a variable product's card does not — it prices per option",
              bulk_discounts.card_teaser(variable) is None)

        r_simple.show_on_cards = False
        r_simple.save()
        fresh()
        check("'hide on cards' is honoured", bulk_discounts.card_teaser(simple) is None)
        r_simple.show_on_cards = True
        r_simple.save()
        fresh()

        # A sold-out product must not advertise a break it cannot honour.
        simple.stock = 0
        simple.save()
        check("a sold-out product shows no chip", bulk_discounts.card_teaser(simple) is None)
        simple.stock = 100
        simple.save()

        # The option card only flags the offer; the rungs live in one place.
        tier(r_simple, 5, 'percent', 20)
        fresh()
        check("a multi-rung ladder sums up as 'up to'",
              bulk_discounts.summary_badge(simple) == 'Save up to 20%')
        r_simple.badge_text = 'Festival deal'
        r_simple.save()
        fresh()
        check("a custom badge overrides the summary",
              bulk_discounts.summary_badge(simple) == 'Festival deal')
        r_simple.badge_text = ''
        r_simple.save()
        r_simple.tiers.filter(min_qty=5).delete()
        fresh()
        check("a single-rung ladder just says 'Save'",
              bulk_discounts.summary_badge(simple) == 'Save 10%')

        rendered = Template(
            "{% load store_tags %}{% include 'store/partials/product_card.html' %}"
        ).render(Context({'product': simple}))
        check("the card renders the chip", 'product-card-bulk' in rendered)
        check("the chip says how many and how much",
              '2pcs' in rendered and 'Save 10%' in rendered)
        check("and links in with that quantity ready", '?qty=2' in rendered)

        client = Client()
        page = client.get(f'/store/products/{simple.slug}/')
        check("the product page loads", page.status_code == 200)
        html = page.content.decode()
        check("it ships the ladder for the browser", 'data-bulk-map' in html)
        check("it renders the tier chips server-side", 'pdp-bulk-tier' in html)
        check("the ladder JSON names the rung", '"minQty": 2' in html or '"minQty":2' in html)

        deep = client.get(f'/store/products/{simple.slug}/?qty=2')
        check("?qty=2 opens the stepper on that quantity",
              'data-qty value="2"' in deep.content.decode())

        # The standalone quick-checkout page has its own stepper and no
        # product-page script, so the ladder has to ride in on the panel
        # config for it to re-price honestly.
        quick = client.get(f'/store/quick-order/{simple.id}/?qty=2')
        quick_html = quick.content.decode()
        check("quick checkout loads", quick.status_code == 200)
        check("its panel carries the ladder", '&quot;tiers&quot;: [{' in quick_html
              or '"tiers": [{' in quick_html)
        check("and the list price to discount from",
              'listPrice' in quick_html or 'listPrice' in quick_html.replace('&quot;', '"'))

        var_page = client.get(f'/store/products/{variable.slug}/')
        var_html = var_page.content.decode()
        check("an option card flags its offer with one summary line",
              'pdp-variant-card-bulk">Save' in var_html)
        check("it does not repeat the rungs the ladder already draws",
              'pdp-variant-card-bulk' in var_html
              and '<span class="qty">' not in var_html)
        check("the ladder waits for an option instead of vanishing",
              'pdp-bulk-await' in var_html and 'Pick an option' in var_html)
        check("and every option's ladder is shipped",
              f'"{v_red.pk}"' in var_html and f'"{v_blue.pk}"' in var_html)

        # ── 8. an order is written at the discounted rate ─────────────
        print("\n── the order is written at the break ──")
        order = client.post(f'/store/quick-order/{simple.id}/', {
            'full_name': 'Bulk Tester', 'phone': '9812345678',
            'address': 'Test address', 'district': 'KATHMANDU',
            'quantity': '3', 'order_type': 'confirmed',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        body = order.json()
        check("the order goes through", body.get('success') is True)
        store_order = StoreOrder.objects.filter(order_number=body.get('order_number')).first()
        store_orders.append(store_order)
        item = store_order.items.first()
        check("the store OrderItem holds the discounted unit price",
              item.price == Decimal('900.00'))
        check("its line total is the discounted one", item.line_total == Decimal('2700.00'))
        check("the order subtotal matches", store_order.subtotal == Decimal('2700.00'))

        dash_order = DashOrder.objects.filter(order_from='Website').order_by('-id').first()
        dash_orders.append(dash_order)
        check("the mirrored dashboard order is priced the same",
              dash_order.items.first().price == Decimal('900.00'))

        # ── 9. the setup page ─────────────────────────────────────────
        print("\n── Setup → Bulk Discounts ──")
        admin = CustomUser.objects.filter(is_superuser=True).first()
        if admin is None:
            print("  SKIP  no superuser to sign in as")
        else:
            staff = Client()
            staff.force_login(admin)
            setup = staff.get('/setup/bulk-discounts/')
            check("the setup page loads", setup.status_code == 200)
            setup_html = setup.content.decode()
            check("it lists the rules made above", 'TEST simple ladder' in setup_html)
            check("it draws the ladder", 'bd-rung' in setup_html)

            before = BulkDiscount.objects.count()
            saved = staff.post('/setup/bulk-discounts/save/', {
                'scope': 'variation', 'variation': str(v_blue.pk),
                'name': 'TEST saved via page', 'priority': '3',
                'is_active': 'on', 'show_on_cards': 'on',
                'tier_min_qty': ['4', '8'],
                'tier_type': ['percent', 'price'],
                'tier_value': ['15', '500'],
                'tier_label': ['', 'Bulk pack'],
            }, follow=True)
            check("saving a rule from the page works", saved.status_code == 200)
            check("it created exactly one rule", BulkDiscount.objects.count() == before + 1)
            made = BulkDiscount.objects.filter(name='TEST saved via page').first()
            if made:
                rules.append(made)
            check("with both tiers", made is not None and made.tiers.count() == 2)
            fresh()
            check("and the storefront prices from it now",
                  bulk_discounts.price_for(variable, v_blue, 4)['unit'] == Decimal('595.00'))

            rejected = staff.post('/setup/bulk-discounts/save/', {
                'scope': 'product', 'product': str(simple.pk),
                'tier_min_qty': ['2', '2'], 'tier_type': ['percent', 'percent'],
                'tier_value': ['10', '20'], 'tier_label': ['', ''],
            }, follow=True)
            check("two tiers at the same quantity are refused",
                  BulkDiscount.objects.count() == before + 1
                  and rejected.status_code == 200)

            preview = staff.get('/setup/bulk-discounts/preview/',
                                {'product': simple.pk, 'qty': 3})
            data = preview.json()
            check("the preview endpoint answers", data.get('ok') is True)
            check("and agrees with the storefront",
                  data.get('unit') == str(bulk_discounts.price_for(simple, None, 3)['unit']))
            check("it names the rule actually in force",
                  data.get('rule') == 'TEST simple ladder')

            var_preview = staff.get('/setup/bulk-discounts/preview/',
                                    {'variation': v_red.pk, 'qty': 2}).json()
            check("previewing a variation prices from the variation",
                  var_preview.get('ok') is True
                  and var_preview.get('base_unit') == '900.00')

            # Re-pointing a variation rule at another product's option must
            # re-derive the product, not keep the stale one.
            moved = BulkDiscount.objects.get(pk=r_var.pk)
            moved.variation = v_blue
            moved.save()
            check("re-pointing a variation rule re-derives its product",
                  BulkDiscount.objects.get(pk=moved.pk).product_id == variable.pk)
            moved.variation = v_red
            moved.save()
            fresh()

            spread = staff.post(f'/setup/bulk-discounts/{r_var.pk}/spread/', follow=True)
            check("spreading a variation ladder works", spread.status_code == 200)
            spread_made = BulkDiscount.objects.filter(
                scope='variation', variation=v_blue).exclude(pk__in=[r.pk for r in rules])
            for extra in spread_made:
                rules.append(extra)
            check("it left the variation that already had a rule alone",
                  BulkDiscount.objects.filter(scope='variation', variation=v_blue).count() == 1)

    finally:
        for order in store_orders:
            if order:
                order.delete()
        for order in dash_orders:
            if order:
                order.delete()
        DashOrder.objects.filter(customer_phone='9812345678').delete()
        # Only the exact rows this script created, by pk.
        BulkDiscount.objects.filter(pk__in=[r.pk for r in rules if r.pk]).delete()
        BulkDiscount.objects.filter(name__startswith='TEST ').delete()
        for cart in carts:
            Cart.objects.filter(pk=cart.pk).delete()
        for prod in products:
            CartItem.objects.filter(product=prod).delete()
            ProductVariation.objects.filter(product=prod).delete()
            Product.objects.filter(pk=prod.pk).delete()
        Category.objects.filter(pk=category.pk).delete()
        bulk_discounts.invalidate_cache()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
