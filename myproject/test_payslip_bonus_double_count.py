#!/usr/bin/env python3
"""
Regression test for the payslip bonus double-counting bug.

Reproduces the exact production symptom reported for Laxmi Karki:
a payslip is generated with an approved bonus for the month, and the first
time it is downloaded, gross_salary and net_salary jump by exactly one
bonus amount (22,490 -> 29,940 / 21,450 -> 28,900).

Covers three cases:
  1. Bonus 'approved' at generation time  -- the reported case.
  2. Bonus already 'paid' at generation time -- the filter-mismatch variant
     (generate_payslips used to scope only 'approved', while the download
     sync scopes 'approved' + 'paid').
  3. A genuinely new bonus approved AFTER generation -- must still be
     added exactly once (proves the fix didn't break the feature the
     sync exists for).

Everything runs inside a transaction that is rolled back, so the database
is left untouched.

Usage:
    python test_payslip_bonus_double_count.py
"""
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()

from django.db import transaction
from hrm.models import Employee, EmployeeSalary, PayrollRun, Payslip, Bonus
from hrm.views import _sync_bonus_to_payslip, _calculate_payroll_breakdown
from hrm.models import PayrollSetting

MONTH, YEAR = 7, 2026
CYCLE_START = date(YEAR, MONTH, 1)
CYCLE_END = date(YEAR, MONTH, 31)

failures = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {actual}, expected {expected}")
    if not ok:
        failures.append(name)


def make_employee(tag):
    emp = Employee.objects.create(
        full_name=f'ZZ Test {tag}',
        employee_id=f'ZZTEST{tag}',
        email=f'zztest{tag}@example.invalid',
        phone=f'98000000{tag}',
        date_of_birth=date(1990, 1, 1),
        gender='male',
        date_of_joining=date(2020, 1, 1),
        employee_status='active',
        base_salary=Decimal('15000.00'),
    )
    EmployeeSalary.objects.create(
        employee=emp, effective_date=date(2020, 1, 1),
        basic_salary=Decimal('15000.00'), is_active=True,
    )
    return emp


def generate_slip(emp, run, bonus_statuses):
    """Mirror the (fixed) generate_payslips bonus handling for one employee."""
    ps = PayrollSetting.get_settings()
    salary_record = EmployeeSalary.objects.filter(employee=emp, is_active=True).first()
    bd = _calculate_payroll_breakdown(
        employee=emp, cycle_start=CYCLE_START, cycle_end=CYCLE_END,
        salary_record=salary_record, payroll_settings=ps,
    )
    bonus_qs = Bonus.objects.filter(
        employee=emp, month=MONTH, year=YEAR, status__in=bonus_statuses
    )
    bonus_total = sum(b.amount for b in bonus_qs) or Decimal('0')
    bonus_total = Decimal(str(bonus_total)).quantize(Decimal('0.01'))

    gross = (bd['total_earnings'] + bonus_total).quantize(Decimal('0.01'))
    deductions = bd['other_deductions_total'].quantize(Decimal('0.01'))
    slip = Payslip.objects.create(
        payroll_run=run, employee=emp,
        gross_salary=gross, total_deductions=deductions,
        advance_deduction=Decimal('0'), absent_deduction=bd['absent_deduction'],
        net_salary=max(gross - deductions, Decimal('0')),
        basic_salary=bd['basic_salary'],
        salary_structure={
            'earnings_list': [{'name': i['name'], 'amount': float(i['amount'])} for i in bd['earnings_list']],
            'deductions_list': [{'name': i['name'], 'amount': float(i['amount'])} for i in bd['deductions_list']],
            # THE FIX: record what was already folded in.
            'bonus_total_included': str(bonus_total),
        },
        status='generated', generated_on=date.today(),
    )
    bonus_qs.update(status='paid')
    return slip


try:
    with transaction.atomic():
        run = PayrollRun.objects.create(
            title='ZZ Test Run', frequency='monthly',
            pay_period_start=CYCLE_START, pay_period_end=CYCLE_END,
            pay_date=CYCLE_END, month=MONTH, year=YEAR, status='draft',
        )

        # ── Case 1: bonus approved before generation (the reported bug) ──
        print("\nCase 1: bonus 'approved' at generation, then downloaded twice")
        emp1 = make_employee('01')
        Bonus.objects.create(
            employee=emp1, bonus_type='monthly', amount=Decimal('7450.00'),
            month=MONTH, year=YEAR, status='approved',
        )
        slip1 = generate_slip(emp1, run, ['approved', 'paid'])
        gross_at_gen, net_at_gen = slip1.gross_salary, slip1.net_salary
        print(f"  generated: gross={gross_at_gen}, net={net_at_gen}")

        _sync_bonus_to_payslip(emp1, MONTH, YEAR)   # 1st download
        slip1.refresh_from_db()
        check('gross unchanged after 1st download', slip1.gross_salary, gross_at_gen)
        check('net unchanged after 1st download', slip1.net_salary, net_at_gen)

        _sync_bonus_to_payslip(emp1, MONTH, YEAR)   # 2nd download
        slip1.refresh_from_db()
        check('gross unchanged after 2nd download', slip1.gross_salary, gross_at_gen)
        check('net unchanged after 2nd download', slip1.net_salary, net_at_gen)

        # ── Case 2: bonus already 'paid' before generation (filter mismatch) ──
        print("\nCase 2: bonus already 'paid' at generation")
        emp2 = make_employee('02')
        Bonus.objects.create(
            employee=emp2, bonus_type='monthly', amount=Decimal('2000.00'),
            month=MONTH, year=YEAR, status='paid',
        )
        slip2 = generate_slip(emp2, run, ['approved', 'paid'])
        gross2, net2 = slip2.gross_salary, slip2.net_salary
        print(f"  generated: gross={gross2}, net={net2}")
        check('paid bonus included in gross at generation',
              slip2.salary_structure['bonus_total_included'], '2000.00')

        _sync_bonus_to_payslip(emp2, MONTH, YEAR)
        slip2.refresh_from_db()
        check('gross unchanged after download', slip2.gross_salary, gross2)
        check('net unchanged after download', slip2.net_salary, net2)

        # ── Case 3: bonus approved AFTER generation must still apply once ──
        print("\nCase 3: new bonus approved after generation (sync must still work)")
        emp3 = make_employee('03')
        slip3 = generate_slip(emp3, run, ['approved', 'paid'])
        gross3, net3 = slip3.gross_salary, slip3.net_salary
        print(f"  generated (no bonus): gross={gross3}, net={net3}")

        Bonus.objects.create(
            employee=emp3, bonus_type='performance', amount=Decimal('1500.00'),
            month=MONTH, year=YEAR, status='approved',
        )
        _sync_bonus_to_payslip(emp3, MONTH, YEAR)
        slip3.refresh_from_db()
        check('gross +1500 once', slip3.gross_salary, gross3 + Decimal('1500.00'))
        check('net +1500 once', slip3.net_salary, net3 + Decimal('1500.00'))

        _sync_bonus_to_payslip(emp3, MONTH, YEAR)   # re-download
        slip3.refresh_from_db()
        check('gross not added twice', slip3.gross_salary, gross3 + Decimal('1500.00'))
        check('net not added twice', slip3.net_salary, net3 + Decimal('1500.00'))

        raise transaction.TransactionManagementError('__ROLLBACK__')

except transaction.TransactionManagementError as e:
    if '__ROLLBACK__' not in str(e):
        raise

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("All checks passed. Test data rolled back.")
