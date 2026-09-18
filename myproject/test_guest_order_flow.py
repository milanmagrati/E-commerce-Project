"""Standalone check for the guest (no-login) storefront order flow.

Run with: python test_guest_order_flow.py

Exercises the whole path a shopper now takes: product page renders the inline
order form, the district/branch catalogue and quote endpoints answer, a guest
Confirm Order and a guest Inquiry both persist a store Order plus a mirrored
dashboard Order, a discount code applies, and the old login/register routes are
gone. Everything it creates is rolled back at the end.
"""

import os
import sys
from decimal import Decimal

# The Windows console defaults to cp1252 and this script prints box-drawing and
# Nepali-facing characters.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings                                   # noqa: E402
# Django's test Client talks to host "testserver"; this script runs against the
# real settings module rather than the test runner, so allow it explicitly.
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from django.test import Client                                    # noqa: E402
from django.urls import reverse, NoReverseMatch                    # noqa: E402
from django.db import transaction                                  # noqa: E402

from dashboard.models import Product, Category                     # noqa: E402
from dashboard.models import Order as DashOrder                     # noqa: E402
from store.models import Order, DiscountCode, ProductReview, Wishlist  # noqa: E402
from store import services                                          # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    mark = 'ok  ' if condition else 'FAIL'
    print(f'  [{mark}] {label}' + (f'  — {detail}' if detail and not condition else ''))


def main():
    client = Client()

    product = (Product.objects
               .filter(is_active=True, is_deleted=False, price__gt=0)
               .exclude(slug='').first())
    if product is None:
        print('No active product in the local DB — nothing to test against.')
        return 1

    print(f'\nProduct under test: {product.name} (Rs. {product.price}, slug={product.slug})\n')

    # ── 1. Auth routes are gone ──────────────────────────────────────
    print('1. Login / register removal')
    for name in ('login', 'register', 'profile', 'logout', 'order_list'):
        try:
            reverse(f'store:{name}')
            check(f'store:{name} no longer routed', False, 'url still resolves')
        except NoReverseMatch:
            check(f'store:{name} no longer routed', True)
    check('/store/login/ returns 404', client.get('/store/login/').status_code == 404)

    # ── 2. Product page carries the inline order form ────────────────
    print('\n2. Product page')
    resp = client.get(reverse('store:product_detail', kwargs={'slug': product.slug}))
    body = resp.content.decode('utf-8', 'replace')
    check('product page renders', resp.status_code == 200, f'status {resp.status_code}')
    check('order form panel present', 'data-order-form' in body)
    check('no login redirect on the buttons', '/store/login/' not in body)
    check('district combobox present', 'data-combo="district"' in body)
    check('discount box present', 'data-discount-apply' in body)

    # ── 3. Location + quote endpoints ────────────────────────────────
    print('\n3. Order-form data endpoints')
    resp = client.get(reverse('store:locations'))
    data = resp.json()
    districts = data.get('districts', [])
    check('locations endpoint answers', resp.status_code == 200)
    check('districts returned', len(districts) > 0, f'{len(districts)} districts')
    kathmandu = next((d for d in districts if d['name'].upper() == 'KATHMANDU'), None)
    check('Kathmandu present with branches', bool(kathmandu and kathmandu['branches']))

    resp = client.get(reverse('store:quote'), {'subtotal': '300', 'district': 'KATHMANDU'})
    quote = resp.json()
    check('valley delivery is free', quote['totals']['delivery'] in ('0.00', '0'),
          quote['totals']['delivery'])
    check('inside_valley flag set', quote['inside_valley'] is True)

    resp = client.get(reverse('store:quote'), {'subtotal': '300', 'district': 'GORKHA'})
    quote = resp.json()
    check('small outside-valley order is charged', Decimal(quote['totals']['delivery']) > 0,
          quote['totals']['delivery'])

    resp = client.get(reverse('store:quote'), {'subtotal': '900', 'district': 'GORKHA'})
    check('outside valley over threshold ships free',
          Decimal(resp.json()['totals']['delivery']) == 0)

    created = {'orders': [], 'dash': [], 'discount': None}
    try:
        # ── 4. Discount code ─────────────────────────────────────────
        print('\n4. Discount code')
        code = DiscountCode.objects.create(
            code='TESTGUEST10', discount_type='percent', value=Decimal('10'),
            min_order_amount=Decimal('100'), is_active=True,
        )
        created['discount'] = code
        resp = client.post(reverse('store:apply_discount'),
                           data='{"code": "TESTGUEST10", "subtotal": "1000", "district": "KATHMANDU"}',
                           content_type='application/json')
        payload = resp.json()
        check('valid code applies', payload['success'] is True, payload.get('message'))
        check('10% off 1000 = 100', payload['totals']['discount'] == '100.00',
              payload['totals']['discount'])

        resp = client.post(reverse('store:apply_discount'),
                           data='{"code": "NOPE", "subtotal": "1000"}',
                           content_type='application/json')
        check('unknown code rejected', resp.json()['success'] is False)

        resp = client.post(reverse('store:apply_discount'),
                           data='{"code": "TESTGUEST10", "subtotal": "50"}',
                           content_type='application/json')
        check('below minimum rejected', resp.json()['success'] is False,
              resp.json().get('message'))

        # ── 5. Guest confirmed order ─────────────────────────────────
        print('\n5. Guest Confirm Order (no login)')
        branch = kathmandu['branches'][0] if kathmandu and kathmandu['branches'] else {'code': '', 'name': ''}
        form = {
            'full_name': 'Guest Test Shopper',
            'phone': '+977 9812345678',          # deliberately messy input
            'email': 'guest@example.com',
            'district': 'KATHMANDU',
            'courier_branch': branch['name'],
            'courier_branch_code': branch['code'],
            'address': 'Baneshwor, Ward 10, near the chowk',
            'note': 'Please call before delivery',
            'discount_code': 'TESTGUEST10',
            'quantity': '2',
            'order_type': 'confirmed',
            'selected_variant': '',
        }
        resp = client.post(reverse('store:quick_order', args=[product.id]), form,
                           HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check('AJAX order accepted', resp.status_code == 200, f'status {resp.status_code}: {resp.content[:200]}')
        payload = resp.json() if resp.status_code == 200 else {}
        check('order reports success', payload.get('success') is True, str(payload)[:200])

        order = Order.objects.filter(order_number=payload.get('order_number')).first()
        created['orders'].append(order)
        check('store Order persisted', order is not None)
        if order:
            check('no user attached (guest)', order.user_id is None)
            check('phone normalised to 10 digits', order.phone == '9812345678', order.phone)
            check('district saved', order.district == 'KATHMANDU', order.district)
            check('courier branch saved', order.courier_branch == branch['name'], order.courier_branch)
            check('note saved', order.note == 'Please call before delivery')
            expected_sub = product.price * 2
            check('subtotal = price × qty', order.subtotal == expected_sub,
                  f'{order.subtotal} vs {expected_sub}')
            check('discount recorded', order.discount_code == 'TESTGUEST10' and order.discount_amount > 0,
                  f'{order.discount_code} / {order.discount_amount}')
            check('valley delivery free', order.delivery_charge == 0, str(order.delivery_charge))
            check('total = subtotal − discount', order.total_price == expected_sub - order.discount_amount,
                  f'{order.total_price}')
            check('order item created', order.items.count() == 1)
            check('discount usage incremented',
                  DiscountCode.objects.get(pk=code.pk).used_count == 1)

            dash = DashOrder.objects.filter(customer_phone='9812345678').order_by('-id').first()
            created['dash'].append(dash)
            check('mirrored to dashboard', dash is not None)
            if dash:
                check('dashboard order has no created_by', dash.created_by_id is None)
                check('dashboard customer name kept', dash.customer_name == 'Guest Test Shopper')
                check('dashboard branch stamped', dash.ncm_destination_branch == branch['code'],
                      dash.ncm_destination_branch)

            # Session grants access to the order page
            resp = client.get(reverse('store:order_detail', args=[order.order_number]))
            check('placing browser can open the order', resp.status_code == 200)

        # ── 6. Guest inquiry ─────────────────────────────────────────
        print('\n6. Guest Inquiry Only')
        form_inq = dict(form, order_type='inquiry', discount_code='', quantity='1')
        resp = client.post(reverse('store:quick_order', args=[product.id]), form_inq,
                           HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        payload = resp.json()
        check('inquiry accepted', payload.get('success') is True, str(payload)[:200])
        inquiry = Order.objects.filter(order_number=payload.get('order_number')).first()
        created['orders'].append(inquiry)
        check('inquiry stored as inquiry', inquiry is not None and inquiry.order_type == 'inquiry')
        if inquiry:
            created['dash'].append(
                DashOrder.objects.filter(customer_phone='9812345678').order_by('-id').first())
            check('inquiry reserved no stock',
                  all(i.reserved_qty == 0 for i in inquiry.items.all()))

        # ── 7. Validation ────────────────────────────────────────────
        print('\n7. Validation')
        bad = dict(form, phone='12345', discount_code='')
        resp = client.post(reverse('store:quick_order', args=[product.id]), bad,
                           HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check('bad phone rejected', resp.status_code == 400)
        check('phone error reported', 'phone' in resp.json().get('errors', {}))

        missing = dict(form, district='', discount_code='')
        resp = client.post(reverse('store:quick_order', args=[product.id]), missing,
                           HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check('missing district rejected', resp.status_code == 400)

        # ── 8. Order lookup ──────────────────────────────────────────
        print('\n8. Track order (no account)')
        stranger = Client()
        if order:
            resp = stranger.get(reverse('store:order_detail', args=[order.order_number]))
            check('stranger is redirected to lookup', resp.status_code == 302)
            resp = stranger.post(reverse('store:order_track'), {
                'order_number': order.order_number, 'phone': '9812345678',
            })
            check('correct number + phone finds it', resp.status_code == 302
                  and order.order_number in resp['Location'], resp.get('Location', ''))
            resp = stranger.post(reverse('store:order_track'), {
                'order_number': order.order_number, 'phone': '9800000000',
            })
            check('wrong phone does not', resp.status_code == 200)

        # ── 9. Guest wishlist + review ───────────────────────────────
        print('\n9. Guest wishlist and reviews')
        fresh = Client()
        resp = fresh.post(reverse('store:toggle_wishlist', args=[product.id]))
        payload = resp.json()
        check('guest can add to wishlist', payload.get('success') and payload.get('added'))
        resp = fresh.post(reverse('store:toggle_wishlist', args=[product.id]))
        check('guest can remove from wishlist', resp.json().get('added') is False)

        resp = fresh.post(reverse('store:add_review', args=[product.id]), {
            'guest_name': 'Guest Reviewer', 'rating': '5', 'comment': 'Great product',
        })
        review = ProductReview.objects.filter(guest_name='Guest Reviewer').first()
        check('guest review saved', review is not None)
        if review:
            check('review has no user', review.user_id is None)
            check('display name is the typed name', review.display_name == 'Guest Reviewer')
            review.delete()

    finally:
        print('\nCleaning up test records…')
        with transaction.atomic():
            for o in created['orders']:
                if o:
                    o.delete()
            for d in created['dash']:
                if d and DashOrder.objects.filter(pk=d.pk).exists():
                    d.delete()
            if created['discount']:
                created['discount'].delete()
        ProductReview.objects.filter(guest_name='Guest Reviewer').delete()
        Wishlist.objects.filter(user__isnull=True, product=product,
                                session_key__isnull=False).exclude(session_key='').delete()

    print(f'\n{"=" * 56}')
    print(f'  {len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        print('\n  Failures:')
        for f in FAIL:
            print(f'    - {f}')
    print(f'{"=" * 56}\n')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
