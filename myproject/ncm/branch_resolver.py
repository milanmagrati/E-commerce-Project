"""Turn whatever an order carries into a destination branch NCM will accept.

Why this exists
---------------
`POST /order/create` wants `branch` to be the **name of a branch in NCM's own
catalogue** - TINKUNE, ITAHARI, NEW ROAD POKHARA. It is not a city, and it is
not a district.

An order does not necessarily carry one:

* A **website order** stores the branch the shopper picked as a *code* in
  `Order.ncm_destination_branch`, and puts the *district* in `Order.branch_city`
  (`store/views.py::_place_order`). The name the shopper saw is kept on the
  StoreCustomer, not on the order.
* A **dashboard order**'s `branch_city` comes from the `NEPAL_CITIES` dropdown
  (`dashboard/forms.py`) - "Kathmandu", "Pokhara", "Other". Some of those happen
  to also be NCM branch names; "Kathmandu" is not one (the valley's branches are
  TINKUNE, CHABAHIL, KALANKI...), and neither is "Other".

The single-send path never had this problem because its form makes the user
pick a real branch and validates it against `get_branches()` before sending
(`ncm/views.py::create_ncm_shipment`). The bulk path posted
`branch_city.upper()` straight through, defaulting to the literal string
`'KATHMANDU'` - so a batch of valley orders was rejected by NCM one order at a
time, all the way to "0 sent, 31 failed", and resuming it failed identically
because nothing about the orders had changed.

What this module does
---------------------
`catalogue()` fetches NCM's branch list once and caches it; `resolve()` maps one
order onto it, preferring the branch the order already names over the city it
sits in. When the catalogue cannot be reached, resolution never blocks - the
send goes out with the order's own value, exactly as it did before.

When the catalogue *is* available and the order matches nothing in it, the send
is refused **locally**, with a message naming the value and the branches that
district does have. NCM would have refused it anyway; refusing here spends no
HTTP call and, unlike NCM's `{"Error": {...}}`, says what to fix.
"""

import logging

from django.core.cache import cache

logger = logging.getLogger('ncm')

#: NCM's branch list is ~630 rows and changes rarely. Keyed by API account
#: because two accounts can be pointed at different NCM deployments.
CACHE_KEY = 'ncm:branch_catalogue:v1:{}'
CACHE_TTL = 60 * 60 * 12  # 12 hours

#: How many branch names an error message lists before it says "and N more".
SUGGEST_LIMIT = 6


class Catalogue:
    """NCM's branches, indexed the three ways an order might name one.

    `available` is False when the branch list could not be fetched. Every
    lookup then answers "keep what the order says", so a courier outage
    degrades bulk sending to its old behaviour instead of blocking it.
    """

    def __init__(self, rows=None, available=True):
        self.available = bool(available) and rows is not None
        self.by_name = {}
        self.by_code = {}
        self.by_district = {}
        for row in rows or []:
            name = (row.get('name') or '').strip().upper()
            code = (row.get('code') or '').strip().upper()
            district = (row.get('district') or '').strip().upper()
            if not name:
                continue
            self.by_name[name] = name
            if code:
                self.by_code.setdefault(code, name)
            if district:
                self.by_district.setdefault(district, []).append(name)
        for names in self.by_district.values():
            names.sort()

    def __bool__(self):
        return self.available and bool(self.by_name)

    def suggestions_for(self, value):
        """Branch names in the district `value` names, for an error message."""
        names = self.by_district.get((value or '').strip().upper(), [])
        if not names:
            return ''
        head = names[:SUGGEST_LIMIT]
        more = len(names) - len(head)
        return ', '.join(head) + (' and {} more'.format(more) if more else '')


def _rows_from_api(api_config_id):
    from services.ncm_service import NCMService

    result = NCMService(api_config_id=api_config_id).get_branches()
    if not result.get('success') or not result.get('data'):
        logger.warning('NCM branch catalogue unavailable: %s', result.get('error'))
        return None
    return [
        {
            'name': row.get('name') or row.get('Name') or '',
            'code': row.get('code') or row.get('Code') or '',
            'district': row.get('district_name') or row.get('district') or '',
        }
        for row in result['data']
        if isinstance(row, dict)
    ]


def catalogue(api_config_id=None, force_refresh=False):
    """The branch catalogue for one API account, fetched at most twice a day.

    Fetched once per batch rather than once per order: 31 orders must not mean
    31 calls to an API that rate-limits at 3 requests a second.
    """
    key = CACHE_KEY.format(api_config_id or 'default')
    if not force_refresh:
        cached = cache.get(key)
        if cached is not None:
            return Catalogue(cached, available=True)

    try:
        rows = _rows_from_api(api_config_id)
    except Exception:
        logger.warning('NCM branch catalogue fetch failed', exc_info=True)
        rows = None

    if rows is None:
        # Deliberately not cached: an outage must not be remembered for 12h.
        return Catalogue(None, available=False)

    cache.set(key, rows, CACHE_TTL)
    return Catalogue(rows, available=True)


def invalidate(api_config_id=None):
    cache.delete(CACHE_KEY.format(api_config_id or 'default'))


def candidates_for(order):
    """What this order offers as a destination, best first.

    `ncm_destination_branch` comes first because it is the only field anything
    ever writes a real branch into - the storefront writes the code the shopper
    picked, and a previous single send writes the validated name. `branch_city`
    is a city or district and is only a guess.
    """
    out = []
    for value in (getattr(order, 'ncm_destination_branch', '') or '',
                  getattr(order, 'branch_city', '') or ''):
        value = str(value).strip().upper()
        if value and value not in out:
            out.append(value)
    return out


def resolve(order, cat):
    """(branch_name, error) for one order against a catalogue.

    Exactly one of the two is ever set. `error` is a sentence naming the value
    that could not be matched and, where the district is known, what it could
    have been instead.
    """
    values = candidates_for(order)

    if not cat:
        # No catalogue: behave as the sender always did rather than block.
        return (values[0] if values else 'KATHMANDU'), None

    if not values:
        return None, ('No destination branch on this order - set its NCM branch '
                      'or branch/city before sending.')

    for value in values:
        if value in cat.by_name:
            return cat.by_name[value], None
        if value in cat.by_code:
            return cat.by_code[value], None

    # A district with exactly one branch is not a guess.
    for value in values:
        names = cat.by_district.get(value) or []
        if len(names) == 1:
            return names[0], None

    shown = ' / '.join(values)
    for value in values:
        hint = cat.suggestions_for(value)
        if hint:
            return None, ("'{}' is a district, not an NCM branch. Set the order's "
                          'NCM branch to one of: {}.'.format(shown, hint))
    return None, ("'{}' is not an NCM branch. Set the order's NCM branch to a "
                  'branch from the courier list before sending.'.format(shown))
