"""Verifies why a bulk NCM batch used to fail every order, and that it no longer does.

The batch that prompted this ("31 orders, 0 sent, 31 failed", and identical
after Resume) failed because the bulk sender posted `Order.branch_city` as
NCM's `branch`. That field is a city or a district; NCM's `branch` is the name
of one of ITS branches. A batch of Kathmandu-valley orders therefore posted the
literal string "KATHMANDU", which is a district and not an NCM branch, and NCM
rejected all 31 - one order at a time, with the reason recorded in the batch and
shown nowhere in the UI.

    python test_ncm_bulk_branch.py

Nothing here touches the network: the branch catalogue is built from a fixture
and `requests.post` is stubbed. Everything created is namespaced ZZBRANCH- and
removed at the end.

Follows this repo's convention (standalone script, manual django.setup(), real
models against the real DB) - see CLAUDE.md.
"""

import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from decimal import Decimal

from django.contrib.auth import get_user_model

import dashboard.views as dv
from dashboard.models import Order
from ncm import branch_resolver

PREFIX = 'ZZBRANCH-'

failures = []


def check(label, ok, detail=''):
    print(('  PASS  ' if ok else '  FAIL  ') + label + (f'  {detail}' if detail and not ok else ''))
    if not ok:
        failures.append(label)


#: A miniature of NCM's real catalogue: a district whose name is not a branch
#: (KATHMANDU), a district with exactly one branch (ZZSOLO), and a code that
#: differs from its branch name (ITH -> ITAHARI), which is the shape the
#: storefront writes into ncm_destination_branch.
ROWS = [
    {'name': 'TINKUNE', 'code': 'TNK', 'district': 'KATHMANDU'},
    {'name': 'CHABAHIL', 'code': 'CHB', 'district': 'KATHMANDU'},
    {'name': 'KALANKI', 'code': 'KLK', 'district': 'KATHMANDU'},
    {'name': 'ITAHARI', 'code': 'ITH', 'district': 'SUNSARI'},
    {'name': 'POKHARA', 'code': 'PKR', 'district': 'KASKI'},
    {'name': 'ZZONLYBRANCH', 'code': 'ZZO', 'district': 'ZZSOLO'},
]

CAT = branch_resolver.Catalogue(ROWS)
NO_CAT = branch_resolver.Catalogue(None, available=False)


def make_order(number, branch_city='', ncm_branch='', phone='9800000000'):
    return Order.objects.create(
        order_number=PREFIX + number,
        customer_name='ZZ Customer',
        customer_phone=phone,
        shipping_address='ZZ Address, Ward 1',
        branch_city=branch_city,
        ncm_destination_branch=ncm_branch,
        total_amount=Decimal('1000.00'),
        order_from='manual',
    )


def main():
    user = get_user_model().objects.filter(is_superuser=True).first()

    print('1. The catalogue indexes names, codes and districts')
    check('a branch name resolves to itself', CAT.by_name.get('TINKUNE') == 'TINKUNE')
    check('a branch code resolves to its name', CAT.by_code.get('ITH') == 'ITAHARI')
    check('a district lists its branches',
          CAT.by_district.get('KATHMANDU') == ['CHABAHIL', 'KALANKI', 'TINKUNE'],
          f'got {CAT.by_district.get("KATHMANDU")}')
    check('an unfetched catalogue is falsy', not NO_CAT)
    check('a fetched one is truthy', bool(CAT))

    print('\n2. An order names its destination best-first')
    o = make_order('CAND', branch_city='Sunsari', ncm_branch='ITH')
    check('the courier branch comes before the city',
          branch_resolver.candidates_for(o) == ['ITH', 'SUNSARI'],
          f'got {branch_resolver.candidates_for(o)}')
    o.ncm_destination_branch = ''
    check('a blank one is skipped, not posted',
          branch_resolver.candidates_for(o) == ['SUNSARI'])

    print('\n3. Resolution')
    website = make_order('WEB', branch_city='Sunsari', ncm_branch='ITH')
    name, err = branch_resolver.resolve(website, CAT)
    check('a storefront order resolves its code to a branch name',
          name == 'ITAHARI' and err is None, f'got {name!r} / {err!r}')

    named = make_order('NAMED', branch_city='Pokhara')
    name, err = branch_resolver.resolve(named, CAT)
    check('a city that IS a branch is accepted', name == 'POKHARA' and err is None,
          f'got {name!r} / {err!r}')

    solo = make_order('SOLO', branch_city='ZZsolo')
    name, err = branch_resolver.resolve(solo, CAT)
    check('a district with exactly one branch is not a guess',
          name == 'ZZONLYBRANCH' and err is None, f'got {name!r} / {err!r}')

    # This is the batch that failed 31 times.
    valley = make_order('VALLEY', branch_city='Kathmandu')
    name, err = branch_resolver.resolve(valley, CAT)
    check('a district with several branches is refused, not guessed', name is None)
    check('the refusal names the value', err and 'KATHMANDU' in err, f'got {err!r}')
    check('the refusal lists the real branches',
          err and 'TINKUNE' in err and 'CHABAHIL' in err, f'got {err!r}')

    unknown = make_order('UNKNOWN', branch_city='Nowhere')
    name, err = branch_resolver.resolve(unknown, CAT)
    check('an unknown value is refused with advice',
          name is None and err and 'not an NCM branch' in err, f'got {name!r} / {err!r}')

    blank = make_order('BLANK')
    name, err = branch_resolver.resolve(blank, CAT)
    check('an order naming nothing is refused', name is None and err, f'got {name!r} / {err!r}')

    print('\n4. A courier outage never blocks a send')
    name, err = branch_resolver.resolve(valley, NO_CAT)
    check('without a catalogue the order value goes out as before',
          name == 'KATHMANDU' and err is None, f'got {name!r} / {err!r}')
    name, err = branch_resolver.resolve(blank, NO_CAT)
    check('and a blank order keeps the old default',
          name == 'KATHMANDU' and err is None, f'got {name!r} / {err!r}')

    print('\n5. The send helper refuses locally instead of spending a call')
    posts = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {'Message': 'Order Successfully Created', 'orderid': 999001}

    real_post = dv.requests.post
    real_details = None
    try:
        dv.requests.post = lambda url, **kw: (posts.append(kw.get('json')) or FakeResponse())

        result = dv.send_single_order_to_ncm(
            None, valley, api_config_id=None, branch_catalogue=CAT)
        check('an unresolvable branch fails the order', result['status'] == 'error',
              f'got {result}')
        check('with the resolver\'s words', 'TINKUNE' in result['message'], result['message'])
        check('and never reaches the courier', not posts, f'got {posts}')

        print('\n6. A resolvable order posts the resolved branch name')
        # Keep the delivery-charge lookup off the network.
        from services import ncm_service
        real_details = ncm_service.NCMService.get_order_details
        ncm_service.NCMService.get_order_details = lambda self, *a, **kw: {'success': False}

        sendable = make_order('SEND', branch_city='Sunsari', ncm_branch='ITH',
                              phone='+977-980-000 1122')
        result = dv.send_single_order_to_ncm(
            None, sendable, api_config_id=None, branch_catalogue=CAT)
        if result['status'] != 'success':
            check('the order was accepted', False, f'got {result}')
        else:
            check('the order was accepted', True)
            payload = posts[-1]
            check('branch is the resolved NCM branch name', payload['branch'] == 'ITAHARI',
                  f'got {payload["branch"]!r}')
            check('phone is digits only, without the country code',
                  payload['phone'] == '9800001122', f'got {payload["phone"]!r}')
            check('vref_id is the order number', payload['vref_id'] == sendable.order_number,
                  f'got {payload["vref_id"]!r}')
            sendable.refresh_from_db()
            check('the order is now an NCM order', sendable.ncm_order_id == 999001)
            check('and is stamped logistics=ncm so Logistics Orders shows it',
                  sendable.logistics == 'ncm', f'got {sendable.logistics!r}')
            check('the destination branch is stored resolved',
                  sendable.ncm_destination_branch == 'ITAHARI',
                  f'got {sendable.ncm_destination_branch!r}')

        print('\n7. An unusable phone is refused before the call')
        posts.clear()
        junk = make_order('PHONE', branch_city='Pokhara', phone='n/a')
        result = dv.send_single_order_to_ncm(
            None, junk, api_config_id=None, branch_catalogue=CAT)
        check('a phone with no digits fails the order', result['status'] == 'error',
              f'got {result}')
        check('and never reaches the courier', not posts, f'got {posts}')
    finally:
        dv.requests.post = real_post
        if real_details is not None:
            from services import ncm_service
            ncm_service.NCMService.get_order_details = real_details

    print('\n8. dashboard.views has one definition of each NCM send entry point')
    import ast
    from collections import Counter
    tree = ast.parse(open('dashboard/views.py', encoding='utf-8').read())
    names = Counter(n.name for n in tree.body
                    if isinstance(n, (ast.FunctionDef, ast.ClassDef)))
    # Two copies used to exist; the live one was whichever came last in the
    # file, so fixes landed in the dead one for months.
    check('one orders_bulk_ncm_send', names['orders_bulk_ncm_send'] == 1,
          f'got {names["orders_bulk_ncm_send"]}')
    check('one send_single_order_to_ncm', names['send_single_order_to_ncm'] == 1,
          f'got {names["send_single_order_to_ncm"]}')

    print('\n9. The batch view is gated like every other NCM send')
    view = dv.orders_bulk_ncm_send
    src = open('dashboard/views.py', encoding='utf-8').read()
    idx = src.index('def orders_bulk_ncm_send')
    head = src[max(0, idx - 200):idx]
    check('bulk send requires can_create_ncm_orders',
          "permission_required('can_create_ncm_orders')" in head, head[-120:])
    check('the view is still callable', callable(view))

    print('\n10. The expanded batch row can show why an order failed')
    tpl = open('dashboard/templates/logistics_bulk_logs.html', encoding='utf-8').read()
    check('the table has a Reason column', '<th>Reason</th>' in tpl)
    check('it renders the recorded message', 'order.message' in tpl)
    check('escaped, because it is the courier\'s text', 'escapeHtml(order.message' in tpl)
    check('the endpoint returns the message', "'message': o.message or ''" in src)

    print('\n' + '=' * 60)
    if failures:
        print(f'{len(failures)} FAILURE(S):')
        for f in failures:
            print('  - ' + f)
    else:
        print('All NCM bulk branch checks passed.')
    return 1 if failures else 0


def cleanup():
    Order.objects.filter(order_number__startswith=PREFIX).delete()


if __name__ == '__main__':
    try:
        code = main()
    finally:
        cleanup()
    sys.exit(code)
