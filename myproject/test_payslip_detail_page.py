"""Standalone verification for the payslip record page.

Follows this repo's convention for verification scripts (manual django.setup(),
real models, real DB) rather than a pytest/TestCase harness.

Run:  python test_payslip_detail_page.py

`/hrm/payroll/payslips/<pk>/` used to answer every caller with raw JSON, so
following a payslip link out of the Salary Report drawer dumped a wall of JSON
in the browser. It now renders a page for browsers and keeps the JSON for
`?format=json` / XHR callers. This asserts both halves, and — the part most
likely to rot — that the earnings and deductions columns still reconcile to the
payslip's own gross, withheld and net figures for every payslip in the DB,
including legacy rows that stored no component snapshot.
"""

import os
import re
import sys
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402

from hrm.models import Payslip  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

PASSED = []
FAILED = []


def check(label, condition, detail=''):
    if condition:
        PASSED.append(label)
        print(f'  PASS  {label}')
    else:
        FAILED.append(f'{label} — {detail}')
        print(f'  FAIL  {label}{(" — " + detail) if detail else ""}')


def section(title):
    print(f'\n{title}\n' + '-' * len(title))


def as_money(text):
    return Decimal(text.replace(',', ''))


User = get_user_model()
user = User.objects.filter(is_superuser=True).first() or User.objects.first()
if user is None:
    print('No user in the database — cannot exercise a login_required page.')
    sys.exit(1)

client = Client()
client.force_login(user)

slips = list(Payslip.objects.filter(is_deleted=False).order_by('-pk')[:25])
if not slips:
    print('No payslips in the database — nothing to verify.')
    sys.exit(1)

URL = '/hrm/payroll/payslips/{pk}/'

# ──────────────────────────────────────────────────────────────────────────────
section('1. A browser gets a page, not JSON')

for slip in slips[:5]:
    response = client.get(URL.format(pk=slip.pk))
    body = response.content.decode('utf-8', 'replace')
    check(f'payslip #{slip.pk} renders HTML',
          response.status_code == 200 and '<div class="container-fluid py-3 psdet">' in body,
          f'status={response.status_code}')
    check(f'payslip #{slip.pk} does not dump the JSON payload',
          not body.lstrip().startswith('{'),
          body[:80])
    check(f'payslip #{slip.pk} leaks no unrendered template syntax',
          '{{' not in body and '{%' not in body,
          next((m for m in re.findall(r'\{[{%].{0,60}', body)), ''))

# ──────────────────────────────────────────────────────────────────────────────
section('2. JSON callers still get the original payload')

probe = slips[0]
for label, kwargs in (
    ('?format=json', {}),
    ('XHR header', {'headers': {'x-requested-with': 'XMLHttpRequest'}}),
):
    url = URL.format(pk=probe.pk) + ('?format=json' if 'format' in label else '')
    response = client.get(url, **kwargs)
    payload = response.json() if response['Content-Type'].startswith('application/json') else {}
    check(f'{label} returns the payslip JSON',
          payload.get('success') is True
          and payload.get('payslip', {}).get('id') == probe.pk,
          response.content[:120])

# ──────────────────────────────────────────────────────────────────────────────
section('3. Missing payslips answer in the caller\'s own language')

missing = (Payslip.objects.order_by('-pk').first().pk if Payslip.objects.exists() else 0) + 10_000
html = client.get(URL.format(pk=missing))
check('a browser gets a 404 page', html.status_code == 404, str(html.status_code))
api = client.get(URL.format(pk=missing) + '?format=json')
check('a JSON caller gets a 404 payload',
      api.status_code == 404 and api.json().get('success') is False,
      api.content[:120])

# ──────────────────────────────────────────────────────────────────────────────
section('4. Every column reconciles to the payslip it describes')

ROW_RE = re.compile(r'ps-line-value"[^>]*>Rs\. ([\d,\.\-]+)</div>')
SUM_RE = re.compile(r'ps-sum"[^>]*>\s*<span[^>]*>([^<]+)</span>\s*<span[^>]*>Rs\. ([\d,\.\-]+)')
CARD_RE = re.compile(r'<div class="ps-card">(.*?)</div>\s*</div>\s*</div>', re.S)

for slip in slips:
    body = client.get(URL.format(pk=slip.pk)).content.decode('utf-8', 'replace')
    sums = dict((label.strip(), as_money(value)) for label, value in SUM_RE.findall(body))

    check(f'#{slip.pk} states the payslip\'s own gross',
          sums.get('Gross salary') == slip.gross_salary.quantize(Decimal('0.01')),
          f'page={sums.get("Gross salary")} db={slip.gross_salary}')
    check(f'#{slip.pk} states the payslip\'s own net',
          sums.get('Net salary') == slip.net_salary.quantize(Decimal('0.01')),
          f'page={sums.get("Net salary")} db={slip.net_salary}')
    check(f'#{slip.pk} withheld = deductions + advance',
          sums.get('Total withheld')
          == (slip.total_deductions + slip.advance_deduction).quantize(Decimal('0.01')),
          f'page={sums.get("Total withheld")} db='
          f'{slip.total_deductions + slip.advance_deduction}')

    # The earnings card is the first card in the two-column grid; its rows have
    # to add up to the gross printed underneath them, otherwise the page is
    # showing a column that quietly loses money (overtime and weekend pay are
    # never stored as components, which is exactly how that used to happen).
    cards = CARD_RE.findall(body)
    earnings_card = next((c for c in cards if 'Earnings</h2>' in c), '')
    rows = [as_money(v) for v in ROW_RE.findall(earnings_card)]
    check(f'#{slip.pk} earnings rows sum to gross',
          sum(rows, Decimal('0')) == slip.gross_salary.quantize(Decimal('0.01')),
          f'rows={rows} gross={slip.gross_salary}')

    withheld_card = next((c for c in cards if 'Withheld</h2>' in c), '')
    wrows = [as_money(v) for v in ROW_RE.findall(withheld_card)]
    check(f'#{slip.pk} withheld rows sum to deductions + advance',
          sum(wrows, Decimal('0'))
          == (slip.total_deductions + slip.advance_deduction).quantize(Decimal('0.01')),
          f'rows={wrows} expected={slip.total_deductions + slip.advance_deduction}')

# ──────────────────────────────────────────────────────────────────────────────
print('\n' + '=' * 66)
print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
print('=' * 66)
for failure in FAILED:
    print(f'  FAILED: {failure}')
sys.exit(1 if FAILED else 0)
