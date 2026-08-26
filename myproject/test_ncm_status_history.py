"""Verify the order detail page's NCM "Status History" panel.

The bug: NCM scopes an order to the account that created it, and answers 404
"Not found" when asked with any other API key. Any order whose stored
api_config_id was missing or pointed at the wrong LogisticsAPIConfig therefore
rendered "No status history found" while NCM's own portal listed the full
timeline.

Run:  python test_ncm_status_history.py
(Sections marked LIVE hit the real NCM API and are skipped without network.)
"""
import os
import sys
import json

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings as dj_settings
from django.test import Client

from accounts.models import CustomUser
from dashboard.models import LogisticsAPIConfig, Order
from services.ncm_service import (
    candidate_ncm_config_ids,
    fetch_order_status_history,
    fetch_order_status_raw,
    normalize_status_entries,
)

failures = []


def check(label, condition, detail=''):
    if condition:
        print('  PASS  ' + label)
    else:
        print('  FAIL  ' + label + '  ' + str(detail))
        failures.append(label)


print('\n1. normalize_status_entries() payload shapes')

live_shape = [
    {"orderid": 25098287, "status": "Delivered",
     "added_time": "2026-08-26T11:41:50.311091+05:45", "vendor_return": "False"},
    {"orderid": 25098287, "status": "Sent for Delivery",
     "added_time": "2026-08-26T10:44:21.812524+05:45", "vendor_return": "False"},
]
rows = normalize_status_entries(live_shape)
check('bare list parsed', len(rows) == 2, rows)
check('status carried through', rows[0]['status'] == 'Delivered', rows[0])
check('added_time formatted to Nepal time',
      rows[0]['timestamp_display'] == 'Aug 26, 2026 11:41 AM', rows[0])
check('raw timestamp kept', rows[0]['timestamp'].startswith('2026-08-26T11:41'), rows[0])

check('{"data": [...]} wrapper parsed',
      len(normalize_status_entries({'data': live_shape})) == 2)
check('{"results": {...}} single-dict wrapper parsed',
      len(normalize_status_entries({'results': live_shape[0]})) == 1)
check('None/garbage yields []',
      normalize_status_entries(None) == [] and normalize_status_entries('x') == [])
check('non-dict rows skipped',
      len(normalize_status_entries([live_shape[0], 'junk', None])) == 1)

# The UI paints row 0 as the current status, so ordering is not NCM's to decide.
oldest_first = list(reversed(live_shape))
check('rows sorted newest-first regardless of input order',
      normalize_status_entries(oldest_first)[0]['status'] == 'Delivered')
undated = normalize_status_entries(live_shape + [{'status': 'Unknown step'}])
check('undated row kept, sorted last',
      len(undated) == 3 and undated[-1]['status'] == 'Unknown step', undated)


print('\n2. candidate_ncm_config_ids() ordering and de-duplication')

active = list(LogisticsAPIConfig.objects.filter(logistics_provider='ncm', is_active=True))
distinct_keys = set(c.api_key.strip() for c in active if c.api_key.strip())
distinct_keys.add((getattr(dj_settings, 'NCM_API_KEY', '') or '').strip())
distinct_keys.discard('')

default_candidates = candidate_ncm_config_ids(None)
check('one attempt per distinct API key, not per config row',
      len(default_candidates) == len(distinct_keys),
      str(default_candidates) + ' vs ' + str(len(distinct_keys)) + ' keys')
check('default account tried first when no preference',
      bool(default_candidates) and default_candidates[0] is None, default_candidates)

if active:
    preferred = active[0].id
    ordered = candidate_ncm_config_ids(preferred)
    check('preferred account tried first', ordered[0] == preferred, ordered)
    check('no duplicate attempts', len(ordered) == len(set(ordered)), ordered)
    check('skip_config_ids drops an account the caller already tried',
          preferred not in candidate_ncm_config_ids(preferred)[1:])


print('\n3. LIVE: an order queried with the wrong account still resolves')

# 25098287 belongs to a non-default NCM account (the one whose portal listed a
# full 8-step timeline while our page showed "No status history found").
PROBE_ORDER_ID = 25098287
entries, resolved, error = fetch_order_status_history(PROBE_ORDER_ID, api_config_id=None)

offline = error and ('Connection' in str(error) or 'timeout' in str(error).lower())
if offline:
    print('  SKIP  no network to NCM (' + str(error) + ')')
else:
    check('history found by sweeping accounts', len(entries) > 0, 'error=' + str(error))
    if entries:
        check('resolved to a non-default account', resolved is not None, resolved)
        check('every row has a status', all(e['status'] for e in entries), entries[:2])
        check('every row has a formatted timestamp',
              all(e['timestamp_display'] and e['timestamp_display'] != '—' for e in entries),
              entries[:2])
        print('        -> ' + str(len(entries)) + ' entries via config ' + str(resolved)
              + '; latest = ' + entries[0]['status'] + ' @ ' + entries[0]['timestamp_display'])

    # An id NCM has never heard of must not come back as a bogus success.
    missing, _, missing_error = fetch_order_status_history(1, api_config_id=None)
    check('unknown order id reports an error, not an empty timeline',
          missing == [] and bool(missing_error), str(missing) + ' / ' + str(missing_error))


print('\n4. LIVE: end-to-end through the detail endpoint the page calls')

user = CustomUser.objects.filter(is_superuser=True).first()
if not user:
    print('  SKIP  no superuser in this database')
elif offline:
    print('  SKIP  no network to NCM')
else:
    client = Client(SERVER_NAME='localhost')
    client.force_login(user)

    probe_ids = [PROBE_ORDER_ID]
    local = Order.objects.exclude(ncm_order_id__isnull=True).order_by('-id').first()
    if local:
        probe_ids.append(local.ncm_order_id)

    for ncm_id in probe_ids:
        resp = client.get('/api/ncm-rtv/' + str(ncm_id) + '/detail/')
        check('order ' + str(ncm_id) + ': HTTP 200', resp.status_code == 200, resp.status_code)
        if resp.status_code != 200:
            continue
        payload = json.loads(resp.content)
        history = payload.get('status_history') or []
        check('order ' + str(ncm_id) + ': status_history populated',
              len(history) > 0,
              'error=' + str(payload.get('status_history_error')))
        check('order ' + str(ncm_id) + ': no error alongside a populated history',
              not (history and payload.get('status_history_error')))
        if history:
            print('        -> ' + str(len(history)) + ' entries; latest = '
                  + history[0]['status'] + ' @ ' + history[0]['timestamp_display'])

    # The endpoint must stay a well-formed JSON 200 for an id NCM disowns,
    # since the page's fetch().then(r => r.json()) has no other error path.
    resp = client.get('/api/ncm-rtv/1/detail/')
    check('unknown order id: still JSON 200', resp.status_code == 200, resp.status_code)
    payload = json.loads(resp.content)
    check('unknown order id: reported as an error state',
          payload.get('status_history') == [] and bool(payload.get('status_history_error')),
          payload.get('status_history_error'))


print("\n5. LIVE: the raw variant keeps NCM's own row shape for the sync path")

if offline:
    print('  SKIP  no network to NCM')
else:
    raw, raw_config = fetch_order_status_raw(PROBE_ORDER_ID, api_config_id=None)
    check('raw fetch succeeded via the sweep', raw.get('success'), raw.get('error'))
    check('raw fetch resolved the same account', raw_config == resolved, raw_config)
    rows = raw.get('data') or []
    check('raw rows are NCM dicts, not normalized ones',
          bool(rows) and 'added_time' in rows[0] and 'timestamp_display' not in rows[0],
          rows[:1])

    # The sync endpoint reads added_time and vendor_return off these rows, so a
    # normalized row here would silently break status/date mapping.
    check('raw rows carry vendor_return', 'vendor_return' in rows[0], rows[:1])

    bad_raw, bad_config = fetch_order_status_raw(1, api_config_id=None)
    check('unknown order id: raw fetch reports failure',
          not bad_raw.get('success') and bad_config is None, bad_raw)


print('')
print('ALL CHECKS PASSED' if not failures else str(len(failures)) + ' FAILED: ' + str(failures))
sys.exit(1 if failures else 0)
