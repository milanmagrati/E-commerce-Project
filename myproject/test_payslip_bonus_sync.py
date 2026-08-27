"""Standalone verification for the Payslips page's bonus sync.

Follows this repo's convention for verification scripts (manual django.setup(),
real models, real DB) rather than a pytest/TestCase harness.

Run:  python test_payslip_bonus_sync.py

Everything runs inside a transaction that is rolled back at the end, so the
bonus this creates to force a drift never survives the run.

What it covers: a bonus approved *after* a payroll run is generated leaves the
payslip's stored gross behind the live bonus total. The Payslips page has to
show that as a stale bonus (badge + count + banner), the Sync Bonus Data button
has to fold it into gross and net, and after that sync the page must show the
bonus as included with nothing left stale.
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
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402

from hrm.models import Bonus, Payslip  # noqa: E402
from hrm.views import _attach_bonus_state, _payslip_period_key  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

PASSED = []
FAILED = []

LIST_URL = '/hrm/payroll/payslips/?per_page=100'
SYNC_URL = '/hrm/payroll/payslips/sync-bonuses/'


def check(label, condition, detail=''):
    if condition:
        PASSED.append(label)
        print(f'  PASS  {label}')
    else:
        FAILED.append(f'{label} — {detail}')
        print(f'  FAIL  {label}{(" — " + detail) if detail else ""}')


def section(title):
    print(f'\n{title}\n' + '-' * len(title))


User = get_user_model()
user = User.objects.filter(is_superuser=True).first() or User.objects.first()
if user is None:
    print('No user in the database — cannot exercise a login_required page.')
    sys.exit(1)

client = Client()
client.force_login(user)


def page():
    response = client.get(LIST_URL)
    return response, response.content.decode('utf-8', 'replace')


def row_for(body, payslip_number):
    """The one <tr> block for this payslip, so badges from other rows can't
    be mistaken for this payslip's own."""
    match = re.search(
        r'<tr data-pk[^>]*>(?:(?!</tr>).)*?' + re.escape(payslip_number) + r'(?:(?!</tr>).)*?</tr>',
        body, re.S)
    return match.group(0) if match else ''


# ──────────────────────────────────────────────────────────────────────────────
section('1. The page renders with the bonus column')

response, body = page()
check('payslips page loads', response.status_code == 200, str(response.status_code))
check('bonus column header is present', '>Bonus</th>' in body)
check('sync bonus button is present', 'btnSyncBonuses' in body)
check('sync bonus posts to the bonus endpoint', 'sync-bonuses' in body)
check('page leaks no unrendered template syntax',
      '{{' not in body and '{%' not in body,
      next(iter(re.findall(r'\{[{%].{0,60}', body)), ''))

trash = client.get('/hrm/payroll/payslips/?view=trash')
check('trash view still renders', trash.status_code == 200, str(trash.status_code))

# ──────────────────────────────────────────────────────────────────────────────
section('2. A bonus approved after generation reads as stale, and syncs')

target = (
    Payslip.objects.select_related('employee', 'payroll_run')
    .filter(is_deleted=False, is_finalized=False)
    .order_by('-created_at').first()
)
if target is None:
    print('No editable payslip in the database — nothing to verify.')
    sys.exit(1)

month, year = _payslip_period_key(target)
check('the payslip files under a bonus period', bool(month),
      f'run={target.payroll_run_id}')

# The whole scenario is written inside one transaction and rolled back below.
with transaction.atomic():
    sid = transaction.savepoint()

    gross_before = target.gross_salary
    net_before = target.net_salary
    live_before = sum(
        b.amount for b in Bonus.objects.filter(
            employee=target.employee, month=month, year=year,
            status__in=('approved', 'paid'))
    ) or Decimal('0')

    amount = Decimal('1234.00')
    bonus = Bonus.objects.create(
        employee=target.employee, bonus_type='performance',
        amount=amount, month=month, year=year, status='approved',
        remarks='bonus-sync verification (rolled back)',
    )

    fresh = Payslip.objects.select_related('payroll_run').get(pk=target.pk)
    _attach_bonus_state([fresh])
    check('the new bonus makes the payslip stale', fresh.bonus_stale,
          f'included={fresh.bonus_included} live={fresh.bonus_live}')
    check('the live bonus total includes the new bonus',
          fresh.bonus_live == (live_before + amount).quantize(Decimal('0.01')),
          f'{fresh.bonus_live} vs {live_before + amount}')

    response, body = page()
    row = row_for(body, target.payslip_number)
    check('the row shows a Needs Sync badge',
          'bonus-badge-stale' in row, row[:200] or 'row not found')
    check('the page shows the bonus banner', 'bonusStaleBanner' in body)
    check('the sync button carries a stale count',
          re.search(r'btnSyncBonuses.*?btn-sync-count', body, re.S) is not None)

    sync = client.post(SYNC_URL)
    payload = sync.json()
    check('sync returns success', payload.get('success') is True, sync.content[:160])
    check('sync reports at least one updated payslip',
          payload.get('updated', 0) >= 1, str(payload))

    after = Payslip.objects.get(pk=target.pk)
    check('gross grew by exactly the bonus',
          after.gross_salary == (gross_before + amount).quantize(Decimal('0.01')),
          f'{gross_before} -> {after.gross_salary}')
    check('net grew by exactly the bonus',
          after.net_salary == (net_before + amount).quantize(Decimal('0.01')),
          f'{net_before} -> {after.net_salary}')

    _attach_bonus_state([after])
    check('the payslip is no longer stale', not after.bonus_stale,
          f'included={after.bonus_included} live={after.bonus_live}')
    check('the stored bonus marker equals the live total',
          after.bonus_included == after.bonus_live,
          f'{after.bonus_included} vs {after.bonus_live}')

    response, body = page()
    row = row_for(body, target.payslip_number)
    check('the row now shows the bonus as included',
          'bonus-badge-active' in row and 'bonus-badge-stale' not in row,
          row[:200] or 'row not found')

    # Syncing again must be a no-op — the marker is what stops a second run
    # from adding the same bonus twice.
    again = client.post(SYNC_URL).json()
    twice = Payslip.objects.get(pk=target.pk)
    check('a second sync does not double-add the bonus',
          twice.gross_salary == after.gross_salary and twice.net_salary == after.net_salary,
          f'{after.gross_salary}/{after.net_salary} -> {twice.gross_salary}/{twice.net_salary}')
    check('a second sync reports nothing updated',
          again.get('updated', 0) == 0, str(again))

    check('GET is rejected on the sync endpoint',
          client.get(SYNC_URL).status_code == 405)

    transaction.savepoint_rollback(sid)

restored = Payslip.objects.get(pk=target.pk)
check('the verification left no bonus behind',
      not Bonus.objects.filter(remarks='bonus-sync verification (rolled back)').exists())
check('the payslip is back to its original figures',
      restored.gross_salary == gross_before and restored.net_salary == net_before,
      f'{restored.gross_salary}/{restored.net_salary} vs {gross_before}/{net_before}')

# ──────────────────────────────────────────────────────────────────────────────
print('\n' + '=' * 66)
print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
print('=' * 66)
for failure in FAILED:
    print(f'  FAILED: {failure}')
sys.exit(1 if FAILED else 0)
