#!/usr/bin/env python3
"""
Repair + immunise payslips against the bonus double-counting bug.

Root cause (fixed in hrm/views.py generate_payslips): when a payslip was
generated, approved bonuses for that employee/month were folded into
gross_salary/net_salary, but salary_structure never recorded
`bonus_total_included`. The next time payslip_download ran, it called
_sync_bonus_to_payslip(), saw included_bonus == 0, and re-added the
already-included bonus as a fresh delta -- inflating gross_salary and
net_salary by exactly one bonus amount (once; a second download is a no-op
because the sync writes the marker on its way through).

This script handles BOTH halves of the problem:

  CORRUPTED  gross is inflated by exactly the bonus amount recorded in the
             snapshot -> gross and net are corrected back down.

  AT RISK    gross is already correct but the snapshot has no
             `bonus_total_included` marker (every payslip generated before
             the code fix). Left alone, these inflate on their NEXT
             download even with the fixed code deployed. The marker is
             backfilled so the sync's delta comes out to zero.

Anything whose gross does not match either fingerprint is reported and
left untouched -- this script never guesses.

Finalized payslips are skipped (they are locked from edits everywhere else).

Usage:
    python fix_payslip_double_bonus.py            # dry run, shows what would change
    python fix_payslip_double_bonus.py --apply    # persist the changes
"""
import os
import sys
import calendar as _cal
from datetime import date as dt_date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()

from hrm.models import Payslip, PayrollSetting, EmployeeSalary, Bonus
from hrm.views import _calculate_payroll_breakdown

apply_fix = '--apply' in sys.argv
_ps = PayrollSetting.get_settings()

corrupted, backfilled, unexplained = [], [], []
checked = 0

for slip in Payslip.objects.select_related('employee', 'payroll_run').filter(is_finalized=False):
    struct = slip.salary_structure or {}
    if not struct.get('earnings_list') and not struct.get('deductions_list'):
        continue  # pre-snapshot slip; payslip_download recomputes these live

    run = slip.payroll_run
    if run.pay_period_start and run.pay_period_end:
        cycle_start, cycle_end = run.pay_period_start, run.pay_period_end
    elif run.month and run.year:
        cycle_start = dt_date(run.year, run.month, 1)
        cycle_end = dt_date(run.year, run.month, _cal.monthrange(run.year, run.month)[1])
    else:
        continue

    checked += 1
    label = f"{slip.payslip_number} ({slip.employee.full_name}, {cycle_start.strftime('%b %Y')})"

    salary_record = (
        EmployeeSalary.objects.filter(employee=slip.employee, is_active=True)
        .prefetch_related('components')
        .order_by('-effective_date')
        .first()
    )
    bd = _calculate_payroll_breakdown(
        employee=slip.employee, cycle_start=cycle_start, cycle_end=cycle_end,
        salary_record=salary_record, payroll_settings=_ps,
    )

    # Same scope the fixed generate_payslips / _sync_bonus_to_payslip use.
    bonus_total = sum(
        b.amount for b in Bonus.objects.filter(
            employee=slip.employee, month=cycle_start.month, year=cycle_start.year,
            status__in=['approved', 'paid'],
        )
    ) or Decimal('0')
    bonus_total = Decimal(str(bonus_total)).quantize(Decimal('0.01'))

    adj_earnings = Decimal(str(struct.get('adj_earnings_included', '0')))
    marker = Decimal(str(struct.get('bonus_total_included', '0')))
    has_marker = 'bonus_total_included' in struct

    correct_gross = (bd['total_earnings'] + bonus_total + adj_earnings).quantize(Decimal('0.01'))
    diff = (slip.gross_salary - correct_gross).quantize(Decimal('0.01'))

    if diff == 0:
        if (has_marker and marker == bonus_total) or bonus_total == 0:
            # Already immunised, or there is no bonus at all for this period --
            # the sync's delta is 0 either way, so leave the row untouched.
            continue
        # AT RISK: correct totals, but no (or stale) marker -> next download inflates it.
        print(f"{'BACKFILL' if apply_fix else 'WOULD BACKFILL'}: {label}: "
              f"gross {slip.gross_salary} is correct; recording bonus_total_included={bonus_total}")
        if apply_fix:
            struct['bonus_total_included'] = str(bonus_total)
            slip.salary_structure = struct
            slip.save(update_fields=['salary_structure', 'updated_at'])
        backfilled.append(slip.payslip_number)

    elif diff == bonus_total and bonus_total > 0:
        # CORRUPTED: inflated by exactly one bonus amount.
        new_gross = correct_gross
        new_net = max(slip.net_salary - diff, Decimal('0')).quantize(Decimal('0.01'))
        print(f"{'FIX' if apply_fix else 'WOULD FIX'}: {label}: "
              f"gross {slip.gross_salary} -> {new_gross}, net {slip.net_salary} -> {new_net}")
        if apply_fix:
            struct['bonus_total_included'] = str(bonus_total)
            slip.salary_structure = struct
            slip.gross_salary = new_gross
            slip.net_salary = new_net
            slip.save(update_fields=['salary_structure', 'gross_salary', 'net_salary', 'updated_at'])
        corrupted.append(slip.payslip_number)

    else:
        # Doesn't match either fingerprint -- could be a legitimate manual
        # edit. Report it so a human can look, but change nothing.
        print(f"SKIP (unexplained {diff:+}): {label}: "
              f"stored gross {slip.gross_salary}, recomputed {correct_gross}, "
              f"bonus={bonus_total}, marker={marker if has_marker else 'none'}")
        unexplained.append(slip.payslip_number)

verb = 'Fixed' if apply_fix else 'Would fix'
print(f"\nChecked {checked} payslip(s).")
print(f"  {verb} (double-counted): {len(corrupted)} {corrupted}")
print(f"  {verb.replace('fix', 'backfill').replace('Fixed', 'Backfilled')} (at risk): "
      f"{len(backfilled)} {backfilled}")
print(f"  Skipped as unexplained: {len(unexplained)} {unexplained}")
if not apply_fix and (corrupted or backfilled):
    print("\nRe-run with --apply to persist these changes.")
