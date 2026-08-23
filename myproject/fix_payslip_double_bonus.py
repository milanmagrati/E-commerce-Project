#!/usr/bin/env python3
"""
Repair payslips whose gross_salary/net_salary double-counted an already-
approved bonus.

Root cause (fixed in hrm/views.py generate_payslips): when a payslip was
generated with a bonus already approved for that employee/month, the bonus
amount was folded into gross_salary/net_salary, but salary_structure never
recorded `bonus_total_included`. The next time payslip_download ran,
_sync_bonus_to_payslip() saw included_bonus == 0 and re-added the full
bonus amount as a "new" delta, inflating gross_salary/net_salary by exactly
one bonus amount.

This script recomputes the correct gross_salary for every non-finalized
payslip using the same breakdown engine + bonus/adjustment totals, and
fixes any payslip where the stored gross is inflated by exactly the
bonus_total_included amount recorded in its snapshot (the fingerprint of
this specific bug — anything else is left untouched).

Usage:
    python fix_payslip_double_bonus.py            # dry run, prints what would change
    python fix_payslip_double_bonus.py --apply     # actually applies the fix
"""
import os
import sys
import calendar as _cal
from datetime import date as dt_date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()

from hrm.models import Payslip, PayrollSetting, EmployeeSalary, Bonus
from hrm.views import _calculate_payroll_breakdown

apply_fix = '--apply' in sys.argv

_ps = PayrollSetting.get_settings()

fixed = []
checked = 0

for slip in Payslip.objects.select_related('employee', 'payroll_run').filter(is_finalized=False):
    struct = slip.salary_structure or {}
    if not struct.get('earnings_list') and not struct.get('deductions_list'):
        continue  # old-format slip, nothing to recompute against

    run = slip.payroll_run
    if run.pay_period_start and run.pay_period_end:
        cycle_start, cycle_end = run.pay_period_start, run.pay_period_end
    else:
        _month = run.month
        _year = run.year
        if not (_month and _year):
            continue
        cycle_start = dt_date(_year, _month, 1)
        cycle_end = dt_date(_year, _month, _cal.monthrange(_year, _month)[1])

    checked += 1

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

    bonus_total = sum(
        b.amount for b in Bonus.objects.filter(
            employee=slip.employee, month=cycle_start.month, year=cycle_start.year,
            status__in=['approved', 'paid'],
        )
    ) or Decimal('0')
    bonus_total = Decimal(str(bonus_total)).quantize(Decimal('0.01'))

    adj_earnings_included = Decimal(str(struct.get('adj_earnings_included', '0')))
    bonus_total_included = Decimal(str(struct.get('bonus_total_included', '0')))

    correct_gross = (bd['total_earnings'] + bonus_total + adj_earnings_included).quantize(Decimal('0.01'))
    diff = (slip.gross_salary - correct_gross).quantize(Decimal('0.01'))

    if diff != 0 and diff == bonus_total_included and bonus_total_included > 0:
        new_gross = correct_gross
        new_net = max(slip.net_salary - diff, Decimal('0')).quantize(Decimal('0.01'))
        print(
            f"{'FIX' if apply_fix else 'WOULD FIX'}: {slip.payslip_number} "
            f"({slip.employee.full_name}, {cycle_start.strftime('%b %Y')}): "
            f"gross {slip.gross_salary} -> {new_gross}, net {slip.net_salary} -> {new_net}"
        )
        if apply_fix:
            slip.gross_salary = new_gross
            slip.net_salary = new_net
            slip.save(update_fields=['gross_salary', 'net_salary', 'updated_at'])
        fixed.append(slip.payslip_number)

print(f"\nChecked {checked} payslip(s). {'Fixed' if apply_fix else 'Would fix'} {len(fixed)}: {fixed}")
if not apply_fix and fixed:
    print("Re-run with --apply to persist these fixes.")
