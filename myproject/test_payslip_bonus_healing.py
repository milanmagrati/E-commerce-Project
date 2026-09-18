#!/usr/bin/env python3
"""
Regression test for the self-healing of pre-existing payslips.

The bonus_total_included fix stops NEW payslips double-counting a bonus, but
payslips already in a database were written without that marker. Two shapes
exist there:

  AT RISK    gross_salary is correct but carries no marker, so the next
             download reads it as "no bonus included" and adds the bonus a
             second time.

  CORRUPTED  that already happened -- gross_salary and net_salary are inflated
             by exactly one bonus amount (the reported case: 22,490 -> 29,940
             and 21,450 -> 28,900).

heal_payslip_bonus_snapshot() fixes both without a manual production script,
and _sync_bonus_to_payslip() calls it before trusting the marker. This test
builds both shapes and checks they heal, that healing is idempotent, and that
a payslip matching neither shape is left strictly alone.

Everything runs inside a transaction that is rolled back.

Usage:
    python test_payslip_bonus_healing.py
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
from hrm.models import (
    Employee, EmployeeSalary, PayrollRun, Payslip, Bonus, PayrollSetting,
)
from hrm.views import (
    heal_payslip_bonus_snapshot, _sync_bonus_to_payslip,
    _calculate_payroll_breakdown,
)

MONTH, YEAR = 7, 2026
CYCLE_START, CYCLE_END = date(YEAR, MONTH, 1), date(YEAR, MONTH, 31)
BONUS = Decimal('7450.00')

failures = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {actual}, expected {expected}")
    if not ok:
        failures.append(name)


try:
    with transaction.atomic():
        run = PayrollRun.objects.create(
            title='ZZ Heal Run', frequency='monthly',
            pay_period_start=CYCLE_START, pay_period_end=CYCLE_END,
            pay_date=CYCLE_END, month=MONTH, year=YEAR, status='completed',
        )

        def make_employee(tag):
            emp = Employee.objects.create(
                full_name=f'ZZ Heal {tag}', employee_id=f'ZZHEAL{tag}',
                email=f'zzheal{tag}@example.invalid', phone=f'98330000{tag}',
                date_of_birth=date(1990, 1, 1), gender='male',
                date_of_joining=date(2020, 1, 1), employee_status='active',
                base_salary=Decimal('15000.00'),
            )
            EmployeeSalary.objects.create(
                employee=emp, effective_date=date(2020, 1, 1),
                basic_salary=Decimal('15000.00'), is_active=True,
            )
            Bonus.objects.create(
                employee=emp, bonus_type='monthly', amount=BONUS,
                month=MONTH, year=YEAR, status='paid',
            )
            return emp

        def baseline_for(emp):
            bd = _calculate_payroll_breakdown(
                employee=emp, cycle_start=CYCLE_START, cycle_end=CYCLE_END,
                salary_record=EmployeeSalary.objects.filter(
                    employee=emp, is_active=True).first(),
                payroll_settings=PayrollSetting.get_settings(),
            )
            return bd['total_earnings'].quantize(Decimal('0.01'))

        def make_legacy_slip(emp, gross, net):
            """A payslip as written before the bookkeeping existed: snapshot
            present, but no gross_before_bonus and no marker."""
            return Payslip.objects.create(
                payroll_run=run, employee=emp,
                gross_salary=gross, total_deductions=Decimal('0'),
                advance_deduction=Decimal('0'), absent_deduction=Decimal('0'),
                net_salary=net, basic_salary=Decimal('15000.00'),
                salary_structure={
                    'earnings_list': [{'name': 'Basic', 'amount': 15000.0}],
                    'deductions_list': [],
                },
                status='generated', generated_on=date.today(),
            )

        # ── 1. AT RISK: correct gross, no marker ──
        print("\n1. Legacy payslip with correct totals gets immunised")
        e1 = make_employee('01')
        base1 = baseline_for(e1)
        good_gross = (base1 + BONUS).quantize(Decimal('0.01'))
        s1 = make_legacy_slip(e1, good_gross, good_gross)
        check('heal reports immunised', heal_payslip_bonus_snapshot(s1), 'immunised')
        s1.refresh_from_db()
        check('gross untouched', s1.gross_salary, good_gross)
        check('marker recorded', s1.salary_structure['bonus_total_included'], str(BONUS))
        check('baseline stamped', s1.salary_structure['gross_before_bonus'], str(base1))

        # ...and a download after healing must not inflate it
        _sync_bonus_to_payslip(e1, MONTH, YEAR)
        s1.refresh_from_db()
        check('gross still correct after sync', s1.gross_salary, good_gross)
        check('net still correct after sync', s1.net_salary, good_gross)

        # ── 2. CORRUPTED: gross already inflated by one bonus ──
        print("\n2. Already-corrupted payslip is repaired")
        e2 = make_employee('02')
        base2 = baseline_for(e2)
        correct2 = (base2 + BONUS).quantize(Decimal('0.01'))
        inflated = (correct2 + BONUS).quantize(Decimal('0.01'))
        s2 = make_legacy_slip(e2, inflated, inflated)
        # the buggy sync left the marker behind on its way through
        s2.salary_structure['bonus_total_included'] = str(BONUS)
        s2.save(update_fields=['salary_structure'])
        print(f"   corrupted: gross={inflated} (correct is {correct2})")

        check('heal reports repaired', heal_payslip_bonus_snapshot(s2), 'repaired')
        s2.refresh_from_db()
        check('gross corrected', s2.gross_salary, correct2)
        check('net corrected', s2.net_salary, correct2)

        # ── 3. Healing is idempotent ──
        print("\n3. Healing twice changes nothing further")
        check('second heal is a no-op', heal_payslip_bonus_snapshot(s2), 'healthy')
        s2.refresh_from_db()
        check('gross stable', s2.gross_salary, correct2)
        _sync_bonus_to_payslip(e2, MONTH, YEAR)
        s2.refresh_from_db()
        check('gross stable after sync', s2.gross_salary, correct2)

        # ── 4. A payslip matching neither shape is left strictly alone ──
        print("\n4. An unexplained payslip is reported, not guessed at")
        e4 = make_employee('04')
        base4 = baseline_for(e4)
        odd = (base4 + BONUS + Decimal('1234.56')).quantize(Decimal('0.01'))
        s4 = make_legacy_slip(e4, odd, odd)
        check('heal reports unexplained', heal_payslip_bonus_snapshot(s4), 'unexplained')
        s4.refresh_from_db()
        check('gross untouched', s4.gross_salary, odd)
        check('no marker invented', 'gross_before_bonus' in s4.salary_structure, False)

        # ── 5. The migration's pass heals a whole database ──
        print("\n5. The migration pass heals every affected payslip")
        migration_0057 = __import__(
            'hrm.migrations.0057_heal_payslip_bonus_snapshot',
            fromlist=['heal_existing_payslips'],
        )
        e5 = make_employee('05')
        base5 = baseline_for(e5)
        correct5 = (base5 + BONUS).quantize(Decimal('0.01'))
        s5 = make_legacy_slip(e5, (correct5 + BONUS).quantize(Decimal('0.01')),
                              (correct5 + BONUS).quantize(Decimal('0.01')))
        s5.salary_structure['bonus_total_included'] = str(BONUS)
        s5.save(update_fields=['salary_structure'])
        migration_0057.heal_existing_payslips(None, None)
        s5.refresh_from_db()
        check('migration repaired the slip', s5.gross_salary, correct5)

        # ── 6. A declined verdict is remembered, not re-derived every time ──
        print("\n6. An unexplained payslip is assessed once, not on every download")
        s4.refresh_from_db()
        check('verdict recorded', 'bonus_heal_declined' in s4.salary_structure, True)
        check('recorded against its gross',
              s4.salary_structure['bonus_heal_declined']['gross'], str(odd))
        check('second call short-circuits', heal_payslip_bonus_snapshot(s4), 'unexplained')
        s4.refresh_from_db()
        check('still untouched', s4.gross_salary, odd)

        # ...but a change in gross forces a fresh assessment
        s4.gross_salary = (base4 + BONUS).quantize(Decimal('0.01'))
        s4.net_salary = s4.gross_salary
        s4.save(update_fields=['gross_salary', 'net_salary'])
        check('reassessed once gross moves',
              heal_payslip_bonus_snapshot(s4), 'immunised')
        s4.refresh_from_db()
        check('stale verdict cleared',
              'bonus_heal_declined' in s4.salary_structure, False)

        raise transaction.TransactionManagementError('__ROLLBACK__')

except transaction.TransactionManagementError as e:
    if '__ROLLBACK__' not in str(e):
        raise

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("All checks passed. Test data rolled back.")
