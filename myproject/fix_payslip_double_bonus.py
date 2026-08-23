#!/usr/bin/env python3
"""
READ-ONLY audit of payslip bonus accounting.

This script no longer repairs anything, and you do not need to run it as part
of a deploy. The repair lives in the code now:

  * migration hrm/0057_heal_payslip_bonus_snapshot walks every non-finalized
    payslip during `manage.py migrate`, so deploying is enough;
  * hrm.views.heal_payslip_bonus_snapshot() also heals each payslip lazily,
    the first time _sync_bonus_to_payslip() touches it, as a safety net for
    anything created between deploys.

What remains useful is checking the result, which is all this does: it
reports each payslip as healed, still at risk, or unexplained, and writes
nothing. Use it to confirm a deploy landed, or to look into whatever the
migration declined to guess at.

Usage:
    python fix_payslip_double_bonus.py
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

_ps = PayrollSetting.get_settings()
healed, at_risk, unexplained, skipped = [], [], [], []

for slip in Payslip.objects.select_related('employee', 'payroll_run').filter(
    is_finalized=False
):
    struct = slip.salary_structure or {}
    if not struct.get('earnings_list') and not struct.get('deductions_list'):
        continue

    run = slip.payroll_run
    if run.pay_period_start and run.pay_period_end:
        cycle_start, cycle_end = run.pay_period_start, run.pay_period_end
    elif run.month and run.year:
        cycle_start = dt_date(run.year, run.month, 1)
        cycle_end = dt_date(run.year, run.month, _cal.monthrange(run.year, run.month)[1])
    else:
        skipped.append(slip.payslip_number)
        continue

    label = f"{slip.payslip_number} ({slip.employee.full_name}, {cycle_start.strftime('%b %Y')})"

    if struct.get('gross_before_bonus') is not None:
        healed.append(slip.payslip_number)
        continue

    try:
        bd = _calculate_payroll_breakdown(
            employee=slip.employee, cycle_start=cycle_start, cycle_end=cycle_end,
            salary_record=(
                EmployeeSalary.objects.filter(employee=slip.employee, is_active=True)
                .prefetch_related('components').order_by('-effective_date').first()
            ),
            payroll_settings=_ps,
        )
    except Exception as exc:
        print(f"SKIP  {label}: could not recompute — {exc}")
        skipped.append(slip.payslip_number)
        continue

    bonus_total = sum(
        b.amount for b in Bonus.objects.filter(
            employee=slip.employee, month=cycle_start.month, year=cycle_start.year,
            status__in=['approved', 'paid'],
        )
    ) or Decimal('0')
    bonus_total = Decimal(str(bonus_total)).quantize(Decimal('0.01'))

    baseline = bd['total_earnings'].quantize(Decimal('0.01'))
    adj = Decimal(str(struct.get('adj_earnings_included', '0')))
    correct_gross = (baseline + adj + bonus_total).quantize(Decimal('0.01'))
    drift = (slip.gross_salary - correct_gross).quantize(Decimal('0.01'))
    marker = struct.get('bonus_total_included')

    if drift == 0 or (marker is not None and drift == Decimal(str(marker)) and drift > 0):
        print(f"AT RISK  {label}: not yet healed (drift {drift:+}) — "
              f"run `manage.py migrate`, or open the payslip once")
        at_risk.append(slip.payslip_number)
    else:
        print(f"UNEXPLAINED  {label}: stored gross {slip.gross_salary}, "
              f"recomputed {correct_gross} (drift {drift:+}), bonus {bonus_total}, "
              f"marker {marker or 'none'}")
        unexplained.append(slip.payslip_number)

print(f"\nHealed: {len(healed)} | Still at risk: {len(at_risk)} | "
      f"Unexplained: {len(unexplained)} | Skipped: {len(skipped)}")
if at_risk:
    print(f"  at risk: {at_risk}")
if unexplained:
    print(f"  unexplained (left alone deliberately — check these by hand): {unexplained}")
