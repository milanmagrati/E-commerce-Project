"""Verification for the order VAT / PAN field (order form -> invoice).

Standalone, the way the rest of this repo's checks are written: it sets Django
up by hand, drives the real views through a logged-in test client against the
real database, and cleans up after itself.

The Landmark box left the order create/edit form and a VAT / PAN box took its
place, so what this guards is the whole path that number travels:

* The order form draws a `vat_pan` box and no longer draws a landmark one.
* Creating an order through `order_create` stores what was typed; leaving the
  box empty stores nothing rather than a stray space.
* Editing an order rewrites it, and - the one that matters - an edit post that
  never carried a `landmark` key leaves the order's stored landmark alone.
  NCM payloads, the dispatch sheet and the CSV export all still print it, and
  a form that stopped *collecting* a field must not start *erasing* it.
* The phone lookup that prefills the form hands back `vat_pan`, so a repeat
  buyer does not retype their tax number.
* The invoice prints it through the customizer's whitelist rather than a
  hardcoded template line, and prints nothing at all when the field is blank.
* The migration swapped the seeded Bill To landmark line for a VAT / PAN one,
  so an install seeded before this change prints the new line too.

Run:  python test_order_vat_pan.py
"""

import os
import sys

import django

# The invoice prints a rupee sign; a cp1252 console would abort on it.
try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, ValueError):
    pass

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()

from dashboard import invoice_config  # noqa: E402
from dashboard.models import Customer, InvoiceTemplate, Order  # noqa: E402

PASSED = []
FAILED = []

PROBE_PHONE = '9800000777'


def check(label, condition, detail=''):
    if condition:
        PASSED.append(label)
        print('  PASS  %s' % label)
    else:
        FAILED.append(label)
        print('  FAIL  %s%s' % (label, ('  -> %s' % detail) if detail else ''))


def section(title):
    print('\n== %s ==' % title)


def cleanup():
    Order.objects.filter(customer_phone=PROBE_PHONE).delete()
    Customer.objects.filter(phone=PROBE_PHONE).delete()


def login_client():
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = (User.objects.filter(is_superuser=True).order_by('pk').first()
            or User.objects.filter(role='administrator').order_by('pk').first())
    if user is None:
        raise SystemExit('No administrator in the database - create one before running this.')
    client = Client()
    client.force_login(user)
    return client, user


# -- the model + the token whitelist ------------------------------------

def test_model_and_tokens():
    section('Model field and invoice token')

    field = Order._meta.get_field('vat_pan')
    check('Order.vat_pan exists and is optional', field.blank and field.default == '')
    check('Order.vat_pan holds a full registration number (60 chars)',
          field.max_length == 60, field.max_length)

    check('customer.vat_pan is a whitelisted invoice token',
          'customer.vat_pan' in invoice_config.TOKENS)
    check('the token is offered under Customer in the customizer picker',
          any(o['token'] == 'customer.vat_pan'
              for g in invoice_config.token_groups() if g['group'] == 'Customer'
              for o in g['options']))

    # Landmark data still exists on old orders and on imported ones, so anyone
    # who kept that line on their invoice keeps a token that resolves.
    check('landmark stays available to anyone who kept that line',
          'customer.landmark' in invoice_config.TOKENS)

    # Staff read these off a photographed bill; the spacing they type varies.
    fmt = invoice_config._vat
    check('a tax number loses its internal spacing', fmt(' 601  234 567 ') == '601 234 567',
          repr(fmt(' 601  234 567 ')))
    check('a lettered registration number is upper-cased',
          fmt('pan-601234567') == 'PAN-601234567')
    check('a blank number formats to nothing, not to "None"', fmt('') == '')

    check('the VAT/PAN token prints in the reference-number face',
          'customer.vat_pan' in invoice_config.MONO_TOKENS)


# -- the seeded invoice layout ------------------------------------------

def test_seeded_line():
    section('Seeded Bill To line')

    cfg = InvoiceTemplate.get_solo()
    lines = list(cfg.elements.filter(section='bill_to'))
    tokens = [e.token for e in lines]

    check('the Bill To box carries a VAT / PAN line', 'customer.vat_pan' in tokens, tokens)
    check('the dead landmark line is gone from Bill To',
          'customer.landmark' not in tokens, tokens)

    vat_line = next((e for e in lines if e.token == 'customer.vat_pan'), None)
    check('it is a labelled line, not a bare value',
          vat_line is not None and vat_line.label.strip() != '',
          getattr(vat_line, 'label', None))
    check('it hides itself when the order has no VAT number',
          vat_line is not None and vat_line.hide_if_empty)
    check('it is active out of the box', vat_line is not None and vat_line.is_active)

    # DEFAULT_ELEMENTS is what a fresh install seeds; the migration is what an
    # existing one gets. They have to agree or the two diverge silently.
    default_tokens = [spec.get('token') for spec in invoice_config.DEFAULT_ELEMENTS
                      if spec.get('section') == 'bill_to']
    check('a fresh install seeds the same line',
          'customer.vat_pan' in default_tokens and 'customer.landmark' not in default_tokens,
          default_tokens)


# -- create --------------------------------------------------------------

def _order_post(user, vat_pan, total='800'):
    return {
        'customer_name': 'ZZ VAT Probe',
        'customer_phone': PROBE_PHONE,
        'customer_email': '',
        'branch_city': 'Chabahil',
        'shipping_address': 'Chuchepati',
        'vat_pan': vat_pan,
        'in_out': 'in',
        'created_by': str(user.pk),
        'order_from': 'Call',
        'order_status': 'processing',
        'payment_method': 'Cod',
        'payment_status': 'pending',
        'discount': '0',
        'shipping': '0',
        'tax_percent': '0',
        'total_amount': total,
        'order_items': ('[{"name": "ZZ probe item", "price": %s, '
                        '"quantity": 1, "total": %s}]' % (total, total)),
    }


def test_create(client, user):
    section('Creating an order with a VAT / PAN number')

    page = client.get('/orders/create/')
    body = page.content.decode('utf-8', 'replace')
    check('the create form draws a VAT / PAN box', 'name="vat_pan"' in body)
    check('the create form no longer draws a Landmark box', 'name="landmark"' not in body)

    post = _order_post(user, ' 601234567 ')
    post['_bypass_js_validation'] = '1'
    post['_ajax'] = '1'
    response = client.post('/orders/create/?_ajax=1', post,
                           HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    order = Order.objects.filter(customer_phone=PROBE_PHONE).order_by('-id').first()
    check('the order was created', order is not None, response.status_code)
    if order is None:
        return None

    check('the typed VAT / PAN reached the order', order.vat_pan == '601234567',
          repr(order.vat_pan))
    return order


def test_create_blank(client, user):
    section('Creating an order without one')

    post = _order_post(user, '   ', total='500')
    post['_bypass_js_validation'] = '1'
    post['_ajax'] = '1'
    client.post('/orders/create/?_ajax=1', post, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    order = Order.objects.filter(customer_phone=PROBE_PHONE).order_by('-id').first()
    check('whitespace stores as empty, not as a space',
          order is not None and order.vat_pan == '',
          repr(getattr(order, 'vat_pan', None)))


# -- edit ----------------------------------------------------------------

def test_edit(client, user, order):
    section('Editing an order')

    # A landmark that predates the change - typed on the old form, or arrived
    # from a Sheets import. Nothing on the new form may clear it by omission.
    order.landmark = 'Near Shiva Mandir'
    order.save(update_fields=['landmark'])

    page = client.get('/orders/%s/edit/' % order.pk)
    body = page.content.decode('utf-8', 'replace')
    check('the edit form draws a VAT / PAN box', 'name="vat_pan"' in body)
    check('the edit form no longer draws a Landmark box', 'name="landmark"' not in body)
    check('the stored number is loaded back into the box', 'value="601234567"' in body)

    post = _order_post(user, '609 876 543')
    client.post('/orders/%s/edit/' % order.pk, post)
    order.refresh_from_db()

    check('the edit rewrote the VAT / PAN number', order.vat_pan == '609 876 543',
          repr(order.vat_pan))
    check('a post carrying no landmark key left the landmark alone',
          order.landmark == 'Near Shiva Mandir', repr(order.landmark))

    # "Leave alone when absent" must not become "can never be emptied".
    post['vat_pan'] = ''
    client.post('/orders/%s/edit/' % order.pk, post)
    order.refresh_from_db()
    check('emptying the box clears the number', order.vat_pan == '', repr(order.vat_pan))

    order.vat_pan = '601234567'
    order.save(update_fields=['vat_pan'])


# -- phone prefill -------------------------------------------------------

def test_phone_lookup(client):
    section('Phone lookup prefill')

    response = client.get('/api/search-customer-by-phone/', {'phone': PROBE_PHONE})
    data = response.json()
    check('the lookup found the probe customer', data.get('success') is True, data)
    # The probe's *latest* order is the blank-VAT one, which is the ordinary
    # case: a returning buyer gave their tax number once, several orders ago.
    check('it hands back the last VAT / PAN this buyer gave, not the last order',
          data.get('customer', {}).get('vat_pan') == '601234567',
          data.get('customer'))


# -- the invoice ---------------------------------------------------------

def test_invoice(client, order):
    section('The printed invoice')

    response = client.get('/orders/%s/invoice/' % order.pk)
    body = response.content.decode('utf-8', 'replace')
    check('the invoice renders', response.status_code == 200, response.status_code)
    check('the VAT / PAN number is printed', '601234567' in body)
    check('it is printed under a label', 'VAT / PAN' in body)
    check('it prints in the reference-number face', 'class="val mono"' in body)

    # Nothing about it is hardcoded into the template: switch the row off and
    # the number stops printing, which is what makes it editable at Setup.
    cfg = InvoiceTemplate.get_solo()
    line = cfg.elements.filter(token='customer.vat_pan').first()
    line.is_active = False
    line.save(update_fields=['is_active'])
    body = client.get('/orders/%s/invoice/' % order.pk).content.decode('utf-8', 'replace')
    check('switching the line off at Setup removes it from the invoice',
          '601234567' not in body)
    line.is_active = True
    line.save(update_fields=['is_active'])

    # A blank number must not leave a dangling "VAT / PAN :-" label behind.
    kept, order.vat_pan = order.vat_pan, ''
    order.save(update_fields=['vat_pan'])
    body = client.get('/orders/%s/invoice/' % order.pk).content.decode('utf-8', 'replace')
    check('an order without a VAT number prints no VAT line at all',
          'VAT / PAN' not in body)
    order.vat_pan = kept
    order.save(update_fields=['vat_pan'])

    # The bulk sheet renders the same partial, so it has to agree.
    bulk = client.get('/orders/bulk-invoice/', {'ids': str(order.pk)})
    check('the bulk print sheet prints it too',
          bulk.status_code == 200
          and '601234567' in bulk.content.decode('utf-8', 'replace'),
          bulk.status_code)

    # The customizer preview draws from the sample order, so its VAT line has
    # to be populated or staff would think they had broken something.
    sample, _sample_items = invoice_config.sample_order()
    check('the customizer preview sample carries a VAT number',
          getattr(sample, 'vat_pan', '') != '')


def main():
    cleanup()
    client, user = login_client()
    try:
        test_model_and_tokens()
        test_seeded_line()
        order = test_create(client, user)
        test_create_blank(client, user)
        if order is not None:
            order.refresh_from_db()
            test_edit(client, user, order)
            test_phone_lookup(client)
            test_invoice(client, order)
    finally:
        cleanup()

    print('\n' + '=' * 60)
    print('  %d passed, %d failed' % (len(PASSED), len(FAILED)))
    for label in FAILED:
        print('    - %s' % label)
    print('=' * 60)
    return 1 if FAILED else 0


if __name__ == '__main__':
    raise SystemExit(main())
