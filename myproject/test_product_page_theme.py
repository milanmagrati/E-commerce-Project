"""Verification for the Theme 2 product page (Setup -> Product Page Theme).

Standalone, the way the rest of this repo's checks are written: it sets Django
up by hand, exercises the real models and the real views against the real
database, and cleans up after itself.

What it is actually guarding:

* The shipping-fee parser. `Rs. 100` must be a hundred rupees. Stripping
  non-digits leaves `.100` behind, which is ten paisa, and nothing about that
  failure is visible until a customer is charged.
* `theme2_checkout.quote()` as the single pricing authority: quantity clamped
  to stock, the quantity break applied, a foreign variation refused, a Theme 1
  product refused, and a forged total ignored.
* The router: Theme 1 renders when Theme 1 is selected, and only the design
  changes when it is not.
* An order placed through the modal matching, to the rupee, what the modal
  showed — including the delivery line.
* The landing-page panel on the product add/edit form: that it draws itself,
  that saving the product saves the page with it, that emptying every box
  removes the override rather than leaving a hollow one, and — the one that
  matters — that a post which never carried the panel leaves the product's
  page completely alone.
* The list formats. A clip row grew a title and a description on the end, so
  every three-cell row written before that must still parse to the same card.

Run:  python test_product_page_theme.py
"""

import os
import sys
from decimal import Decimal

import django

# The Nepali rupee sign and the Devanagari fee string below are the point of
# one of these checks, and a cp1252 console would abort on printing them.
try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, ValueError):
    pass

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.core.cache import cache  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

# Allows `testserver` as a host, and — the reason it is here — connects the
# signal that records which templates a response actually rendered. Without it
# `response.templates` is silently empty and the router checks below would
# pass by never looking at anything.
setup_test_environment()

from dashboard.models import (Category, Product,  # noqa: E402
                              ProductVariation)
from store import theme2, theme2_checkout  # noqa: E402
from store.models import (BulkDiscount, BulkDiscountTier, CartItem, Order,
                          ProductPageTheme, ProductThemeOverride)  # noqa: E402

PASSED = []
FAILED = []


def check(label, condition, detail=''):
    if condition:
        PASSED.append(label)
        print('  PASS  %s' % label)
    else:
        FAILED.append(label)
        print('  FAIL  %s%s' % (label, ('  -> %s' % detail) if detail else ''))


def section(title):
    print('\n== %s ==' % title)


# ── fixtures ────────────────────────────────────────────────────────────

def build_fixtures():
    from django.contrib.auth import get_user_model
    from django.utils.text import slugify

    User = get_user_model()
    owner = User.objects.order_by('pk').first()
    if owner is None:
        raise SystemExit('No users in the database — create one before running this.')

    # The product form requires a category, and these fixtures are posted
    # through it further down.
    category = Category.objects.first()

    simple = Product.objects.create(
        user=owner, name='ZZ Theme2 Probe Simple', slug=slugify('zz-theme2-probe-simple'),
        category=category,
        description='A probe product.', product_type='simple',
        price=Decimal('1000.00'), cost_price=Decimal('500.00'),
        stock=25, is_active=True)

    variable = Product.objects.create(
        user=owner, name='ZZ Theme2 Probe Variable', slug=slugify('zz-theme2-probe-variable'),
        description='A probe product with options.', product_type='variable',
        price=Decimal('900.00'), cost_price=Decimal('400.00'),
        stock=0, is_active=True)
    var_a = ProductVariation.objects.create(
        product=variable, variation_name='Red', sku='ZZ-T2-RED',
        price=Decimal('800.00'), stock=6, status='active', is_active=True)
    var_b = ProductVariation.objects.create(
        product=variable, variation_name='Blue', sku='ZZ-T2-BLUE',
        price=Decimal('850.00'), stock=0, status='out_of_stock', is_active=True)

    # A quantity break on the simple product: 3+ takes 10% off.
    rule = BulkDiscount.objects.create(
        name='ZZ Theme2 probe rule', scope=BulkDiscount.SCOPE_PRODUCT,
        product=simple, is_active=True)
    BulkDiscountTier.objects.create(rule=rule, min_qty=3, discount_type='percent',
                                    value=Decimal('10'))

    return {'owner': owner, 'simple': simple, 'variable': variable,
            'var_a': var_a, 'var_b': var_b, 'rule': rule,
            'category': category}


def teardown(fx):
    Order.objects.filter(items__product__in=[fx['simple'], fx['variable']]).distinct().delete()
    CartItem.objects.filter(product__in=[fx['simple'], fx['variable']]).delete()
    fx['rule'].delete()
    ProductThemeOverride.objects.filter(
        product__in=[fx['simple'], fx['variable']]).delete()
    fx['var_a'].delete()
    fx['var_b'].delete()
    fx['simple'].delete()
    fx['variable'].delete()


def set_global(**kwargs):
    row = ProductPageTheme.get_solo()
    for key, value in kwargs.items():
        setattr(row, key, value)
    row.save()
    theme2.invalidate_cache()
    cache.delete('store:bulk_discounts:v1')


# ── the checks ──────────────────────────────────────────────────────────

def test_fee_parsing():
    section('Shipping fee parsing')
    cases = [
        ('Rs. 100', Decimal('100')),   # the bug: naive stripping gives 0.1
        ('रू 250', Decimal('250')),
        ('Rs. 1,000', Decimal('1000')),  # thousands separator must not truncate
        ('100', Decimal('100')),
        ('99.50', Decimal('99.50')),
        ('0', Decimal('0')),
        ('  ', None),
        ('', None),
        ('free', None),
        (None, None),
    ]
    for raw, expected in cases:
        got = theme2.parse_fee(raw)
        check('parse_fee(%r) == %s' % (raw, expected), got == expected, 'got %s' % got)


def test_parsers():
    section('Shared parsers')
    check('lines() drops blanks',
          theme2.lines('one\n\n  two  \n') == ['one', 'two'])
    check('csv_list() trims',
          theme2.csv_list('Prepaid,  COD , ') == ['Prepaid', 'COD'])
    check('cells() pads short lines',
          theme2.cells('only a title', 3) == ['only a title', '', ''])
    check('cells() truncates long lines',
          theme2.cells('a|b|c|d', 3) == ['a', 'b', 'c'])
    check('cells() trims each cell',
          theme2.cells(' a | b ', 2) == ['a', 'b'])


def test_layout_resolution(fx):
    section('Layout resolution')
    simple = fx['simple']

    set_global(layout='theme1')
    check('global theme1 -> theme1', theme2.layout_for(simple) == 'theme1')

    set_global(layout='theme2')
    check('global theme2 -> theme2', theme2.layout_for(simple) == 'theme2')

    row = ProductThemeOverride.objects.create(product=simple, layout='theme1')
    theme2.invalidate_cache()
    check('per-product theme1 beats global theme2',
          theme2.layout_for(simple) == 'theme1')

    row.layout = ''
    row.save()
    theme2.invalidate_cache()
    check('blank override inherits the global',
          theme2.layout_for(simple) == 'theme2')

    row.layout = 'nonsense'
    row.save()
    theme2.invalidate_cache()
    check('an unrecognised override falls back to the global',
          theme2.layout_for(simple) == 'theme2')
    row.delete()
    theme2.invalidate_cache()


def test_field_precedence(fx):
    section('Field precedence')
    simple = fx['simple']
    set_global(layout='theme2', shipping_value='Global shipping line')

    check('global value is used when there is no override',
          theme2.field(simple, 'shipping_value') == 'Global shipping line')

    row = ProductThemeOverride.objects.create(
        product=simple, shipping_value='Product shipping line')
    check('a filled override wins',
          theme2.field(simple, 'shipping_value', row) == 'Product shipping line')

    row.shipping_value = '   '
    row.save()
    check('a whitespace-only override still inherits',
          theme2.field(simple, 'shipping_value', row) == 'Global shipping line')

    set_global(shipping_value='')
    check('a blank global falls through to the shipped default',
          theme2.field(simple, 'shipping_value', row) == theme2.DEFAULTS['shipping_value'])
    row.delete()


def test_quote(fx):
    section('quote() — the single pricing authority')
    simple, variable, var_a, var_b = fx['simple'], fx['variable'], fx['var_a'], fx['var_b']
    set_global(layout='theme2', ship_fee='')

    q = theme2_checkout.quote(simple.pk, qty=1)
    check('simple product quotes at list price',
          q.get('unit') == Decimal('1000.00'), q.get('error') or q.get('unit'))
    check('subtotal is unit x qty', q.get('subtotal') == Decimal('1000.00'))

    q = theme2_checkout.quote(simple.pk, qty=3)
    check('the 3+ tier applies', q.get('unit') == Decimal('900.00'), q.get('unit'))
    check('was-price is the undiscounted line', q.get('was') == Decimal('3000.00'))
    check('saved is the difference', q.get('saved') == Decimal('300.00'))
    check('discount percent is derived, not asserted', q.get('discount_percent') == 10)

    q = theme2_checkout.quote(simple.pk, qty=2)
    check('below the threshold there is a nudge', bool(q.get('nudge')))
    check('the nudge asks for exactly the shortfall',
          q['nudge']['need'] == 1 and q['nudge']['min_qty'] == 3)

    q = theme2_checkout.quote(simple.pk, qty=9999)
    check('quantity is clamped to available stock',
          q.get('qty') == simple.available_stock, q.get('qty'))

    q = theme2_checkout.quote(simple.pk, qty=0)
    check('quantity is floored at one', q.get('qty') == 1)

    q = theme2_checkout.quote(simple.pk, qty='not a number')
    check('a junk quantity does not raise', q.get('qty') == 1, q.get('error'))

    q = theme2_checkout.quote(variable.pk, qty=1)
    check('a variable product refuses without an option',
          q.get('error') == 'Please choose an option first.', q)

    q = theme2_checkout.quote(variable.pk, variation_id=var_a.pk, qty=1)
    check('a chosen option prices at its own price',
          q.get('unit') == Decimal('800.00'), q.get('error') or q.get('unit'))

    q = theme2_checkout.quote(variable.pk, variation_id=var_b.pk, qty=1)
    check('a sold-out option is refused', bool(q.get('error')), q)

    # The important one: a variation id belonging to another product.
    q = theme2_checkout.quote(simple.pk, variation_id=var_a.pk, qty=1)
    check('a foreign variation is refused, not silently priced',
          bool(q.get('error')), q)

    q = theme2_checkout.quote(999999999, qty=1)
    check('an unknown product is refused', bool(q.get('error')))

    # Theme 1 must not be orderable through the Theme 2 path.
    row = ProductThemeOverride.objects.create(product=simple, layout='theme1')
    theme2.invalidate_cache()
    q = theme2_checkout.quote(simple.pk, qty=1)
    check('a Theme 1 product cannot be quoted here',
          q.get('error') == 'This product cannot be ordered this way.', q)
    row.delete()
    theme2.invalidate_cache()


def test_flat_fee(fx):
    section('Flat delivery fee')
    simple = fx['simple']

    set_global(layout='theme2', ship_fee='Rs. 100')
    q = theme2_checkout.quote(simple.pk, qty=1)
    check('a "Rs. 100" fee is charged as 100, not 0.10',
          q.get('shipping') == Decimal('100.00'), q.get('shipping'))
    check('total = subtotal + delivery',
          q.get('total') == Decimal('1100.00'), q.get('total'))
    check('the source is reported as the flat override',
          q.get('shipping_source') == 'flat')

    row = ProductThemeOverride.objects.create(product=simple, ship_fee='Rs. 250')
    theme2.invalidate_cache()
    q = theme2_checkout.quote(simple.pk, qty=1)
    check('a per-product fee beats the store-wide one',
          q.get('shipping') == Decimal('250.00'), q.get('shipping'))
    row.delete()
    theme2.invalidate_cache()

    set_global(ship_fee='')
    q = theme2_checkout.quote(simple.pk, qty=1, district='KATHMANDU')
    check('a blank fee falls back to the district engine',
          q.get('shipping_source') == 'district', q.get('shipping_source'))


def test_ladder(fx):
    section('Bundle ladder')
    simple, variable, var_a = fx['simple'], fx['variable'], fx['var_a']
    set_global(layout='theme2')

    rows = theme2.ladder(simple)
    check('the ladder opens with a Buy 1 rung',
          bool(rows) and rows[0]['qty'] == 1 and not rows[0]['discounted'], rows[:1])
    check('the Buy 1 rung is the plain price',
          rows[0]['total'] == Decimal('1000.00'), rows[0]['total'])

    tier_row = rows[1]
    check('a discounted rung quotes the line total, not the unit price',
          tier_row['total'] == Decimal('2700.00'), tier_row['total'])
    check('the struck figure is the undiscounted line',
          tier_row['was'] == Decimal('3000.00'), tier_row['was'])
    check('the saving is stated for the whole line',
          tier_row['save'] == 'You save Rs. 300', tier_row['save'])
    check('a single rung is not labelled most popular',
          tier_row['popular'] is False, tier_row['popular'])

    check('a product with no quantity break has no ladder',
          theme2.ladder(variable, var_a) == [])

    # Arriving on ?qty=5 with rungs at 1 and 3 must still open with the rung
    # that is actually in force selected, not with nothing selected.
    rows = theme2.ladder(simple, qty=5)
    chosen = [r for r in rows if r['selected']]
    check('exactly one rung is marked selected', len(chosen) == 1, chosen)
    check('the rung in force is the deepest one reached',
          chosen and chosen[0]['qty'] == 3, chosen)
    rows = theme2.ladder(simple, qty=1)
    check('at quantity one the Buy 1 rung is selected',
          rows[0]['selected'] is True and not rows[1]['selected'])


def test_before_after(fx):
    section('Before / after pairs')
    simple = fx['simple']
    rows = [
        '/media/a.jpg | /media/b.jpg | Four weeks apart',
        '/media/only-before.jpg',
        ' | /media/only-after.jpg | orphan',
    ]
    set_global(layout='theme2', before_after=chr(10).join(rows))
    pairs = theme2.before_after(simple)
    check('a complete pair renders', len(pairs) == 1, pairs)
    check('the caption survives',
          pairs and pairs[0]['caption'] == 'Four weeks apart', pairs)
    check('a half-pair is dropped rather than half-drawn',
          all(p['before'] and p['after'] for p in pairs))
    set_global(before_after='')
    check('a blank field renders nothing', theme2.before_after(simple) == [])


def test_media_uploads():
    section('Media uploads')
    from django.contrib.auth import get_user_model
    from django.core.files.uploadedfile import SimpleUploadedFile
    from store.models import ThemeMedia

    User = get_user_model()
    admin = (User.objects.filter(is_superuser=True).first()
             or User.objects.filter(role='administrator').first())
    if admin is None:
        check('an administrator exists to upload as', False, 'none found')
        return

    client = Client()
    client.force_login(admin)

    # A one-pixel PNG, so the check is about the endpoint and not about Pillow.
    png = bytes.fromhex(
        '89504e470d0a1a0a0000000d494844520000000100000001080600000'
        '01f15c4890000000a49444154789c6300010000050001'
        '0d0a2db40000000049454e44ae426082')

    before = ThemeMedia.objects.count()
    res = client.post('/setup/product-theme/media/upload/',
                      {'file': SimpleUploadedFile('probe.png', png, content_type='image/png')})
    payload = res.json() if res.status_code < 500 else {}
    check('a png uploads', res.status_code == 200 and payload.get('success'),
          '%s %s' % (res.status_code, payload))
    check('the response hands back a usable URL',
          bool(payload.get('media', {}).get('url', '').startswith('/')), payload.get('media'))
    check('it is filed as a photo', payload.get('media', {}).get('kind') == 'image')

    # A content type is trivially forged, so the extension is what decides.
    res = client.post('/setup/product-theme/media/upload/',
                      {'file': SimpleUploadedFile('payload.svg', b'<svg/>',
                                                  content_type='image/png')})
    check('an unsupported extension is refused even with an image content type',
          res.status_code == 400, res.status_code)

    res = client.post('/setup/product-theme/media/upload/',
                      {'file': SimpleUploadedFile('huge.png', b'x' * (9 * 1024 * 1024),
                                                  content_type='image/png')})
    check('an oversized photo is refused', res.status_code == 400, res.status_code)

    res = client.get('/setup/product-theme/media/?kind=image')
    check('the picker lists photos', res.status_code == 200 and
          any(m['kind'] == 'image' for m in res.json().get('media', [])))
    res = client.get('/setup/product-theme/media/?kind=video')
    check('filtering by kind excludes photos',
          all(m['kind'] == 'video' for m in res.json().get('media', [])))

    media_id = payload.get('media', {}).get('id')
    if media_id:
        res = client.post('/setup/product-theme/media/%s/delete/' % media_id)
        check('deleting removes the row', res.status_code == 200
              and not ThemeMedia.objects.filter(pk=media_id).exists())
    check('nothing was left behind', ThemeMedia.objects.count() == before,
          ThemeMedia.objects.count())

    stranger = Client()
    res = stranger.post('/setup/product-theme/media/upload/',
                        {'file': SimpleUploadedFile('probe.png', png, content_type='image/png')})
    check('an anonymous visitor cannot upload', res.status_code in (302, 403),
          res.status_code)

    # The panel on the product form uploads through this same endpoint, so a
    # shop assistant trusted to write a product page can put a photo in it -
    # and someone with no product permissions at all still cannot. The refusal
    # has to be JSON: the caller is a fetch, and a redirect to the dashboard
    # would reach it as HTML it cannot parse plus a stray toast on the next
    # page the user happens to load.
    editor = User.objects.filter(is_superuser=False, can_edit_products=True).exclude(
        role='administrator').first()
    if editor is not None:
        allowed = Client()
        allowed.force_login(editor)
        res = allowed.post(
            '/setup/product-theme/media/upload/',
            {'file': SimpleUploadedFile('probe2.png', png, content_type='image/png')})
        check('a product editor may upload', res.status_code == 200, res.status_code)
        row_id = res.json().get('media', {}).get('id') if res.status_code == 200 else None
        if row_id:
            allowed.post('/setup/product-theme/media/%s/delete/' % row_id)

    outsider = User.objects.filter(
        is_superuser=False, can_edit_products=False, can_create_products=False
    ).exclude(role='administrator').first()
    if outsider is not None:
        denied = Client()
        denied.force_login(outsider)
        res = denied.post(
            '/setup/product-theme/media/upload/',
            {'file': SimpleUploadedFile('probe3.png', png, content_type='image/png')})
        check('someone with no product permissions is refused in JSON',
              res.status_code == 403 and res['Content-Type'].startswith('application/json'),
              '%s %s' % (res.status_code, res['Content-Type']))

    check('nothing was left behind after the permission checks',
          ThemeMedia.objects.count() == before, ThemeMedia.objects.count())


def test_router(fx):
    section('Router')
    simple = fx['simple']
    client = Client()

    set_global(layout='theme1')
    response = client.get('/store/products/%s/' % simple.slug)
    names = [t.name for t in response.templates if t.name]
    check('Theme 1 renders the classic template',
          'store/product_detail.html' in names, names[:4])
    check('Theme 1 does not load the Theme 2 stylesheet',
          b'pdp-theme2.css' not in response.content)

    set_global(layout='theme2')
    response = client.get('/store/products/%s/' % simple.slug)
    names = [t.name for t in response.templates if t.name]
    check('Theme 2 renders the conversion template',
          'store/product_detail_conversion.html' in names, names[:4])
    check('Theme 2 loads its own stylesheet',
          b'pdp-theme2.css' in response.content)
    check('Theme 2 carries the body class',
          b'class="lx-pdp2"' in response.content)
    check('the classic template is not also rendered',
          'store/product_detail.html' not in names)

    row = ProductThemeOverride.objects.create(product=simple, layout='theme1')
    theme2.invalidate_cache()
    response = client.get('/store/products/%s/' % simple.slug)
    names = [t.name for t in response.templates if t.name]
    check('a per-product override reverts that product alone',
          'store/product_detail.html' in names, names[:4])
    row.delete()
    theme2.invalidate_cache()

    # A variable product on Theme 2 must still draw its bundle ladder — the
    # classic mistake is skipping it for anything variable.
    response = client.get('/store/products/%s/' % fx['variable'].slug)
    check('a variable product renders the Theme 2 page',
          b'lx-pdp2' in response.content)
    check('a variable product still ships its bundle block',
          b'data-p2-bundle' in response.content)


def test_place_order(fx):
    section('Placing an order through the modal')
    simple = fx['simple']
    set_global(layout='theme2', ship_fee='Rs. 100')

    client = Client()
    # A cart the shopper already had: the modal must not touch it.
    client.get('/store/products/%s/' % simple.slug)
    client.post('/store/cart/add/%s/' % simple.pk, {'quantity': 2},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    before = CartItem.objects.filter(product=simple).count()
    stock_before = Product.objects.get(pk=simple.pk).available_stock

    quoted = theme2_checkout.quote(simple.pk, qty=3)

    response = client.post(
        '/store/api/theme2/%s/order/' % simple.pk,
        {
            'quantity': 3,
            'variation': '',
            'full_name': 'Probe Shopper',
            'phone': '9812345678',
            'district': 'KATHMANDU',
            'address': 'Probe tole, probe street',
            # A forged total, which must change nothing.
            'total': '1',
            'subtotal': '1',
            'shipping': '0',
        },
        HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    payload = response.json() if response.status_code < 500 else {}
    check('the order endpoint accepts the post',
          response.status_code == 200 and payload.get('success'),
          '%s %s' % (response.status_code, payload))

    if not payload.get('success'):
        return

    order = Order.objects.get(order_number=payload['order_number'])
    check('the order total matches the quote',
          order.total_price == quoted['total'],
          'order %s vs quote %s' % (order.total_price, quoted['total']))
    check('the forged total was ignored', order.total_price != Decimal('1'))
    check('the delivery line is the fee that was shown',
          order.delivery_charge == Decimal('100.00'), order.delivery_charge)
    check('the line is written at the tier price',
          order.items.first().price == Decimal('900.00'), order.items.first().price)
    check('the quantity is the one quoted', order.items.first().quantity == 3)

    check('the pre-existing cart is untouched',
          CartItem.objects.filter(product=simple).count() == before)

    simple.refresh_from_db()
    check('stock was allocated once',
          simple.available_stock == stock_before - 3,
          'before %s after %s' % (stock_before, simple.available_stock))


def test_order_rejections(fx):
    section('Order refusals')
    simple = fx['simple']
    set_global(layout='theme2')
    client = Client()

    response = client.post(
        '/store/api/theme2/%s/order/' % simple.pk,
        {'quantity': 1, 'full_name': 'X', 'phone': '123', 'district': '', 'address': ''},
        HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    check('a bad form is refused with field errors',
          response.status_code == 400 and response.json().get('errors'),
          response.status_code)

    row = ProductThemeOverride.objects.create(product=simple, layout='theme1')
    theme2.invalidate_cache()
    response = client.post(
        '/store/api/theme2/%s/order/' % simple.pk,
        {'quantity': 1, 'full_name': 'Probe Shopper', 'phone': '9812345678',
         'district': 'KATHMANDU', 'address': 'Probe tole'},
        HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    check('a Theme 1 product cannot be ordered through this endpoint',
          response.status_code == 400, response.status_code)
    row.delete()
    theme2.invalidate_cache()


def test_content_lists(fx):
    section('Clips, gallery and features')
    simple = fx['simple']

    set_global(layout='theme2',
               videos=('/media/one.mp4 | /media/one.jpg | creator | On camera | '
                       'Shot in a single take' + chr(10) +
                       '/media/two.mp4 | /media/two.jpg | creator'),
               info_media=('/media/pack.jpg | Tried and trusted by | 15 lakh+ women'
                           + chr(10) +
                           ' |  | A line with no picture' + chr(10) +
                           ' | | '),
               features='Made in Nepal | Since 2019' + chr(10) + 'Dermat tested')

    clips = theme2.videos(simple)
    check('both clips parse', len(clips) == 2, clips)
    check('a five-cell clip keeps its caption',
          clips[0]['title'] == 'On camera'
          and clips[0]['note'] == 'Shot in a single take', clips[0])
    # The two trailing cells were added after the format shipped. `cells()`
    # pads, so a row written before they existed must draw exactly the card it
    # always drew — not a card with the word "creator" as its title.
    check('a three-cell clip still parses to the same card',
          clips[1]['is_creator'] and clips[1]['title'] == ''
          and clips[1]['note'] == '', clips[1])

    blocks = theme2.info_media(simple)
    check('the gallery drops the wholly empty row', len(blocks) == 2, blocks)
    check('a gallery block keeps its words',
          blocks[0]['heading'] == 'Tried and trusted by'
          and blocks[0]['text'] == '15 lakh+ women', blocks[0])
    check('a gallery block with only words is kept',
          blocks[1]['image'] == '' and blocks[1]['text'] == 'A line with no picture',
          blocks[1])

    rows = theme2.features(simple)
    check('both features parse', len(rows) == 2, rows)
    check('a feature keeps its description',
          rows[0]['title'] == 'Made in Nepal' and rows[0]['text'] == 'Since 2019',
          rows[0])
    check('a feature with no description is still a feature',
          rows[1]['title'] == 'Dermat tested' and rows[1]['text'] == '', rows[1])

    body = Client().get('/store/products/%s/' % simple.slug).content.decode(
        'utf-8', 'replace')
    check('the clip caption reaches the page', 'Shot in a single take' in body)
    check('the gallery reaches the page', 'lx-p2-shot' in body and '15 lakh+ women' in body)
    check('the features block reaches the page',
          'lx-p2-feats' in body and 'Dermat tested' in body)

    set_global(layout='theme1', videos='', info_media='', features='')
    body = Client().get('/store/products/%s/' % simple.slug).content.decode(
        'utf-8', 'replace')
    check('none of it leaks into Theme 1',
          'lx-p2-feats' not in body and 'lx-p2-shot' not in body)


def _product_post(fx, **extra):
    """The minimum a valid product-form post needs, plus whatever is asked."""
    simple = fx['simple']
    payload = {
        'name': simple.name, 'slug': simple.slug, 'description': simple.description,
        'category': str(fx['category'].pk),
        'product_type': 'simple', 'price': '1000', 'cost_price': '500',
        'cost_price_type': 'fixed', 'stock': '25', 'stock_status': 'in_stock',
        'is_active': 'on',
        'variations-TOTAL_FORMS': '0', 'variations-INITIAL_FORMS': '0',
        'variations-MIN_NUM_FORMS': '0', 'variations-MAX_NUM_FORMS': '1000',
    }
    payload.update(extra)
    return payload


def test_product_form_panel(fx):
    section('The landing-page panel on the product form')
    from django.contrib.auth import get_user_model

    simple = fx['simple']
    User = get_user_model()
    # `product_edit` turns away anyone without `can_edit_prices` unless their
    # role is administrator, so a superuser with a sales role is not enough.
    admin = (User.objects.filter(role='administrator').first()
             or User.objects.filter(is_superuser=True, can_edit_prices=True).first())
    if admin is None or fx['category'] is None:
        check('an administrator and a category exist to post the form with',
              False, 'skipped the panel checks')
        return

    client = Client()
    client.force_login(admin)

    body = client.get('/products/%s/edit/' % simple.pk).content.decode('utf-8', 'replace')
    for needle in ('name="pt_present"', 'name="pt_layout"', 'name="pt_videos"',
                   'data-pt-repeater="info_media"', 'data-pt-repeater="features"',
                   'data-pt-media="image"', 'window.PT_SETUP',
                   'product-theme-setup.js', 'product-theme-editor.css'):
        check('the edit form carries %s' % needle, needle in body)

    body = client.get('/products/add/').content.decode('utf-8', 'replace')
    check('the add form carries the panel too', 'name="pt_present"' in body)

    ProductThemeOverride.objects.filter(product=simple).delete()
    theme2.invalidate_cache()

    filled = _product_post(
        fx, pt_present='1', pt_layout='theme2',
        pt_videos='/media/a.mp4 | /media/a.jpg | creator | Title | Note',
        pt_info_media='/media/b.jpg | Heading | Words',
        pt_features='Feature | Detail',
        pt_video_title='Watch it work')
    response = client.post('/products/%s/edit/' % simple.pk, filled)
    check('saving the product saves the page with it', response.status_code == 302,
          response.status_code)
    row = ProductThemeOverride.objects.filter(product=simple).first()
    check('the override row is written', row is not None)
    if row is not None:
        check('the design choice is stored', row.layout == 'theme2', row.layout)
        check('the clip row is stored whole', row.videos.endswith('| Title | Note'),
              row.videos)
        check('the gallery row is stored', row.info_media == '/media/b.jpg | Heading | Words',
              row.info_media)
        check('the feature row is stored', row.features == 'Feature | Detail', row.features)
        check('the section heading is stored', row.video_title == 'Watch it work',
              row.video_title)

    # A post that never carried the panel must not touch the page. This is the
    # check that matters: every other product-form path in the dashboard posts
    # without these boxes, and any of them silently blanking a landing page
    # would be discovered by a customer, not by us.
    response = client.post('/products/%s/edit/' % simple.pk, _product_post(fx))
    row = ProductThemeOverride.objects.filter(product=simple).first()
    check('a post without the panel leaves the page alone',
          row is not None and row.video_title == 'Watch it work',
          row and row.video_title)

    # Every box empty says nothing at all, and an empty row would only make the
    # "products with their own settings" count lie.
    emptied = _product_post(fx, pt_present='1')
    response = client.post('/products/%s/edit/' % simple.pk, emptied)
    check('emptying every box removes the override',
          not ProductThemeOverride.objects.filter(product=simple).exists())

    # A product form rejected for an unrelated reason must hand the landing
    # page back, not throw away everything that was typed into it.
    rejected = _product_post(fx, price='', pt_present='1', pt_layout='theme2',
                             pt_features='Typed but not saved | yet')
    body = client.post('/products/%s/edit/' % simple.pk, rejected).content.decode(
        'utf-8', 'replace')
    check('a rejected product form keeps what was typed into the panel',
          'Typed but not saved | yet' in body)

    ProductThemeOverride.objects.filter(product=simple).delete()
    theme2.invalidate_cache()


def main():
    fx = build_fixtures()
    original = ProductPageTheme.get_solo()
    original_layout, original_fee = original.layout, original.ship_fee
    original_ba = original.before_after
    original_videos = original.videos
    original_gallery = original.info_media
    original_features = original.features
    try:
        test_fee_parsing()
        test_parsers()
        test_layout_resolution(fx)
        test_field_precedence(fx)
        test_quote(fx)
        test_flat_fee(fx)
        test_ladder(fx)
        test_before_after(fx)
        test_content_lists(fx)
        test_product_form_panel(fx)
        test_media_uploads()
        test_router(fx)
        test_place_order(fx)
        test_order_rejections(fx)
    finally:
        set_global(layout=original_layout, ship_fee=original_fee,
                   before_after=original_ba, videos=original_videos,
                   info_media=original_gallery, features=original_features)
        teardown(fx)

    print('\n%d passed, %d failed' % (len(PASSED), len(FAILED)))
    if FAILED:
        print('\nFailures:')
        for label in FAILED:
            print('  - %s' % label)
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
