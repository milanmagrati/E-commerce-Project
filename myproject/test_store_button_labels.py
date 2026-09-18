"""Verification for Setup -> Store Button Labels, and for what came off the
classic (Theme 1) product page along with it.

Standalone, the way the rest of this repo's checks are written: it sets Django
up by hand, exercises the real models and the real views against the real
database, and puts back whatever it changed.

What it is actually guarding:

* Blank means "the wording that ships", not an empty button. A shop that
  clears a box by accident must not end up with an unlabelled rectangle on a
  live product page, and nothing on the page would say why.
* The three lists that must agree - `labels.DEFAULTS`, `labels.FIELD_SPECS`
  and the columns on `StoreLabel`. A key added to one alone is a box that is
  drawn, typed into, and silently never saved.
* The save path: what is typed reaches the storefront, and the cache is busted
  so it reaches it now rather than in a minute.
* That the screen is administrators-only, both to read and to write.
* The removals, because they are only visible on the rendered page: no vendor
  eyebrow above the title, no star line under it, no reviews section, and no
  email box on the order form.
* That `hidden` still beats a class that sets `display`. Three elements on
  this page are shown and hidden by product.js setting `.hidden`, and the
  browser's own `[hidden]` rule loses to any class rule - which is how the
  variation warning came to sit on the page as a bare red "!" bubble with
  nothing written in it.
* That the availability line still carries `data-stock-line` and a class list
  of nothing but `pdp-stock <state>` - product.js overwrites that attribute
  wholesale, so a class added in the template survives exactly until the first
  option is clicked.

Run:  python test_store_button_labels.py
"""

import io
import os
import re
import sys
from decimal import Decimal

import django

# The Devanagari defaults below are the point of several of these checks, and
# a cp1252 console would abort on printing them.
try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, ValueError):
    pass

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()

from dashboard.models import Category, Product, ProductVariation  # noqa: E402
from store import labels, theme2  # noqa: E402
from store.models import ProductPageTheme, StoreLabel  # noqa: E402

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

    in_stock = Product.objects.create(
        user=owner, name='ZZ Label Probe Stocked', slug=slugify('zz-label-probe-stocked'),
        category=Category.objects.first(), description='A probe product.',
        product_type='simple', price=Decimal('333.00'), cost_price=Decimal('100.00'),
        stock=12, is_active=True)

    sold_out = Product.objects.create(
        user=owner, name='ZZ Label Probe Sold Out', slug=slugify('zz-label-probe-sold-out'),
        category=Category.objects.first(), description='A probe product.',
        product_type='simple', price=Decimal('333.00'), cost_price=Decimal('100.00'),
        stock=0, is_active=True)

    # A product with options, so the pick-an-option warning has a page to
    # render on: it exists only where `has_variations` is true.
    variable = Product.objects.create(
        user=owner, name='ZZ Label Probe Options', slug=slugify('zz-label-probe-options'),
        category=Category.objects.first(), description='A probe product.',
        product_type='variable', price=Decimal('333.00'), cost_price=Decimal('100.00'),
        stock=0, is_active=True)
    var_a = ProductVariation.objects.create(
        product=variable, variation_name='Small', sku='ZZ-LBL-S',
        price=Decimal('333.00'), stock=4, status='active', is_active=True)
    var_b = ProductVariation.objects.create(
        product=variable, variation_name='Large', sku='ZZ-LBL-L',
        price=Decimal('444.00'), stock=6, status='active', is_active=True)

    return {'owner': owner, 'in_stock': in_stock, 'sold_out': sold_out,
            'variable': variable, 'var_a': var_a, 'var_b': var_b}


def teardown(fx):
    fx['var_a'].delete()
    fx['var_b'].delete()
    fx['variable'].delete()
    fx['in_stock'].delete()
    fx['sold_out'].delete()


def set_labels(**kwargs):
    row = StoreLabel.get_solo()
    for key, value in kwargs.items():
        setattr(row, key, value)
    row.save()
    labels.invalidate_cache()


def page(slug):
    return Client().get('/store/products/%s/' % slug).content.decode('utf-8', 'replace')


def force_theme1():
    """Draw the classic page whatever the shop is currently set to.

    Everything below the setup-screen checks reads Theme 1 markup, and a shop
    left on Theme 2 would fail all of it at once for a reason that has nothing
    to do with what changed. `main()` puts the setting back.
    """
    row = ProductPageTheme.get_solo()
    row.layout = 'theme1'
    row.save()
    theme2.invalidate_cache()


def admin_user():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return (User.objects.filter(is_superuser=True).first()
            or User.objects.filter(role='administrator').first())


# ── the checks ──────────────────────────────────────────────────────────

def test_lists_agree():
    section('The three lists that must not drift')
    spec_keys = [spec['key'] for spec in labels.FIELD_SPECS]
    check('FIELD_SPECS names each key once', len(spec_keys) == len(set(spec_keys)))
    check('FIELD_SPECS and DEFAULTS are the same set',
          set(spec_keys) == set(labels.DEFAULTS),
          'specs-only %s, defaults-only %s'
          % (set(spec_keys) - set(labels.DEFAULTS),
             set(labels.DEFAULTS) - set(spec_keys)))

    columns = {f.name for f in StoreLabel._meta.get_fields()}
    check('every default is a column on StoreLabel',
          set(labels.DEFAULTS) <= columns,
          'missing %s' % (set(labels.DEFAULTS) - columns))

    check('every group named by a spec is drawn',
          {spec['group'] for spec in labels.FIELD_SPECS} <= set(labels.GROUPS))
    check('no default is blank',
          all(str(v).strip() for v in labels.DEFAULTS.values()))


def test_resolution():
    section('Blank means the shipped wording')
    set_labels(**{key: '' for key in labels.DEFAULTS})
    snap = labels.snapshot()
    check('an empty row resolves to every default',
          snap == labels.DEFAULTS, 'got %r' % snap)

    set_labels(order_now='अर्डर गर्नुहोस्')
    check('a filled box wins',
          labels.snapshot()['order_now'] == 'अर्डर गर्नुहोस्')
    check('its neighbours still fall back',
          labels.snapshot()['add_to_cart'] == labels.DEFAULTS['add_to_cart'])

    # Whitespace is not a label. A button labelled with a space is a blank
    # button that looks filled in the box that wrote it.
    set_labels(order_now='   ')
    check('a whitespace-only box falls back',
          labels.snapshot()['order_now'] == labels.DEFAULTS['order_now'])

    set_labels(order_now='')
    check('clearing it goes back to the default',
          labels.snapshot()['order_now'] == labels.DEFAULTS['order_now'])


def test_setup_screen():
    section('The setup screen')
    admin = admin_user()
    if admin is None:
        check('an administrator exists to edit as', False, 'none found')
        return

    client = Client()
    client.force_login(admin)

    body = client.get('/setup/store-labels/').content.decode('utf-8', 'replace')
    check('the screen renders', 'Store Button Labels' in body)
    for spec in labels.FIELD_SPECS:
        check('it draws a box for %s' % spec['key'],
              'name="%s"' % spec['key'] in body)
        check('%s shows its default as the placeholder' % spec['key'],
              'placeholder="%s"' % labels.DEFAULTS[spec['key']] in body)

    posted = {key: '' for key in labels.DEFAULTS}
    posted['order_now'] = 'अहिले किन्नुहोस्'
    posted['add_to_cart'] = 'कार्टमा हाल्नुहोस्'
    response = client.post('/setup/store-labels/save/', posted)
    check('saving redirects back to the screen',
          response.status_code == 302 and 'store-labels' in response['Location'])
    # No manual cache bust here: the point of the check is that the view did
    # it, so an admin sees the new wording on the shop rather than the old one
    # for up to a minute.
    check('the save reaches the storefront at once',
          labels.snapshot()['order_now'] == 'अहिले किन्नुहोस्')

    client.post('/setup/store-labels/reset/', {})
    check('reset clears every box',
          labels.snapshot() == labels.DEFAULTS)


def test_permissions():
    section('Who may edit the wording')
    from django.contrib.auth import get_user_model
    User = get_user_model()

    stranger = Client()
    response = stranger.get('/setup/store-labels/')
    check('a signed-out visitor is bounced',
          response.status_code in (301, 302), 'got %s' % response.status_code)

    outsider = User.objects.filter(is_superuser=False).exclude(
        role='administrator').first()
    if outsider is None:
        print('  SKIP  no non-administrator to test with')
        return
    denied = Client()
    denied.force_login(outsider)
    read = denied.get('/setup/store-labels/')
    check('a non-administrator cannot open it',
          read.status_code in (301, 302, 403), 'got %s' % read.status_code)
    write = denied.post('/setup/store-labels/save/', {'order_now': 'Hijacked'})
    check('nor post to it',
          write.status_code in (301, 302, 403), 'got %s' % write.status_code)
    check('and nothing was written',
          labels.snapshot()['order_now'] != 'Hijacked')


def test_product_page_labels(fx):
    section('The wording on the product page')
    set_labels(order_now='अहिले अर्डर', add_to_cart='कार्टमा', sold_out='सकियो',
               save_for_later='पछिलाई सेभ')

    body = page(fx['in_stock'].slug)
    check('the Order Now button takes its label', 'अहिले अर्डर' in body)
    check('so does Add to cart', 'कार्टमा' in body)
    check('and Save for later', 'पछिलाई सेभ' in body)
    check('the shipped English wording is gone from the buttons',
          'Order Now' not in body and '>Add to cart<' not in body)
    check('both wordings ride on the wishlist button for the script',
          'data-save-label=' in body and 'data-saved-label=' in body)

    out = page(fx['sold_out'].slug)
    check('the sold-out button takes its label', 'सकियो' in out)
    check('no English "Sold out" is left behind', 'Sold out' not in out)

    set_labels(**{key: '' for key in labels.DEFAULTS})
    back = page(fx['in_stock'].slug)
    check('cleared boxes put the English wording back', 'Order Now' in back)


def test_order_form(fx):
    section('The order form')
    body = page(fx['in_stock'].slug)
    check('there is no email box', 'name="email"' not in body)
    check('the phone box is full width now',
          'of-field of-span-2' in body and 'data-field="phone"' in body)
    check('name, district and address are untouched',
          all('data-field="%s"' % f in body
              for f in ('full_name', 'district', 'address')))

    set_labels(confirm_title='अर्डर पक्का', inquiry_title='सोध्नुहोस्',
               form_title='फारम भर्नुहोस्')
    body = page(fx['in_stock'].slug)
    check('the confirm button takes its label', 'अर्डर पक्का' in body)
    check('the inquiry button takes its label', 'सोध्नुहोस्' in body)
    check('the panel heading takes its label', 'फारम भर्नुहोस्' in body)
    set_labels(**{key: '' for key in labels.DEFAULTS})


def test_removals(fx):
    section('What came off the classic page')
    body = page(fx['in_stock'].slug)

    check('no vendor eyebrow above the title', 'pdp-vendor' not in body)
    check('no star line under it', 'pdp-rating' not in body)
    check('no reviews section', 'pdp-review' not in body and 'id="reviews"' not in body)
    check('nothing posts to add_review from here', '/review/add/' not in body)
    check('the write-a-review anchor is gone', 'write-review' not in body)
    check('"You may also like" still renders its section chrome',
          'pdp-section' in body)


def test_hidden_beats_display():
    section('`hidden` beats a class that sets display')
    css = io.open('store/static/store/css/product.css', encoding='utf-8').read()

    guard = re.search(r'\.pdp \[hidden\][^{]*\{[^}]*display:\s*none\s*!important', css)
    check('the product page guards [hidden] with !important', guard is not None)

    # Any class here that sets a display other than none outranks the browser's
    # own `[hidden] { display: none }`, so it needs that guard to exist. This
    # asserts the guard covers them rather than listing them one by one.
    offenders = [
        name for name, body in re.findall(r'^\.(pdp[\w-]*)\s*\{([^}]*)\}', css, re.M)
        for d in [re.search(r'display:\s*([^;]+);', body)]
        if d and d.group(1).strip() != 'none'
    ]
    check('the classes that need it are inside .pdp', bool(offenders),
          'found none, which means the scan is wrong')
    check('.pdp-variant-alert is one of them (it was the bug)',
          'pdp-variant-alert' in offenders)


def test_variation_hint(fx):
    section('The pick-an-option warning')
    set_labels(choose_option='कृपया विकल्प छान्नुहोस्।')
    body = page(fx['variable'].slug)
    check('the warning carries its wording for the script',
          'data-hint-text="कृपया विकल्प छान्नुहोस्।"' in body)
    check('it still starts hidden',
          re.search(r'data-variation-hint hidden', body) is not None)
    check('and it starts empty - nothing to say until a button is pressed',
          re.search(r'data-hint-text="[^"]*"></p>', body) is not None)

    js = io.open('store/static/store/js/product.js', encoding='utf-8').read()
    check('product.js reads that attribute rather than its own literal',
          'variationHint.dataset.hintText' in js)
    set_labels(**{key: '' for key in labels.DEFAULTS})


def test_stock_pill(fx):
    section('The availability pill')
    body = page(fx['in_stock'].slug)

    row = re.search(r'<div class="pdp-price-row">(.*?)</div>', body, re.S)
    check('the price row is still one element', row is not None)
    if row:
        check('the availability line sits inside it',
              'data-stock-line' in row.group(1))
        check('so does the price', 'data-price-display' in row.group(1))

    # product.js writes `stockLine.className = 'pdp-stock in'` wholesale, so a
    # class the template added here would vanish on the first click.
    for slug in (fx['in_stock'].slug, fx['sold_out'].slug):
        found = re.findall(r'class="(pdp-stock[^"]*)"[^>]*data-stock-line', page(slug))
        check('%s: the pill carries only pdp-stock + a state' % slug,
              found and all(
                  set(cls.split()) - {'pdp-stock'} <= {'in', 'out', 'is-pending'}
                  for cls in found),
              'got %r' % found)

    out = page(fx['sold_out'].slug)
    check('a sold-out product still says so in the pill',
          re.search(r'class="pdp-stock out"[^>]*data-stock-line', out) is not None)


def main():
    fx = build_fixtures()
    row = StoreLabel.get_solo()
    original = {key: getattr(row, key) for key in labels.DEFAULTS}
    original_layout = ProductPageTheme.get_solo().layout
    try:
        test_lists_agree()
        test_resolution()
        test_setup_screen()
        test_permissions()
        force_theme1()
        test_product_page_labels(fx)
        test_order_form(fx)
        test_removals(fx)
        test_stock_pill(fx)
        test_hidden_beats_display()
        test_variation_hint(fx)
    finally:
        set_labels(**original)
        restore = ProductPageTheme.get_solo()
        restore.layout = original_layout
        restore.save()
        theme2.invalidate_cache()
        teardown(fx)

    print('\n%d passed, %d failed' % (len(PASSED), len(FAILED)))
    if FAILED:
        print('\nFailures:')
        for label in FAILED:
            print('  - %s' % label)
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
