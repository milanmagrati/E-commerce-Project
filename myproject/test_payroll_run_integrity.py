#!/usr/bin/env python3
"""
Regression tests for the payroll-run / advance-payment integrity bugs.

Covers three defects found auditing the payroll module:

  1. Deleting a payslip never credited back the advance repayment that
     generating it had charged, so the ordinary "delete and regenerate to
     correct a mistake" workflow deducted the same installment from the
     employee twice and cleared the advance early.

  2. PayrollRun.employee_count / gross_pay / net_pay -- the money rendered
     on the Payroll Runs page -- were only ever written by
     generate_payslips(), so they went stale after any later change to a
     payslip (deletion, bonus approval, adjustment, advance sync).

  3. payroll_run_delete() cascaded through finalized payslips, destroying
     slips that payslip_delete() explicitly refuses to touch.

Everything runs inside a transaction that is rolled back, so the database
is left untouched.

Usage:
    python test_payroll_run_integrity.py
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
from django.test import RequestFactory
from accounts.models import CustomUser
from hrm.models import (
    Employee, EmployeeSalary, PayrollRun, Payslip, AdvancePayment,
)
from hrm.views import (
    _refresh_payroll_run_totals, _reverse_advance_deductions,
    payslip_delete, payroll_run_delete,
)

MONTH, YEAR = 7, 2026
CYCLE_START, CYCLE_END = date(YEAR, MONTH, 1), date(YEAR, MONTH, 31)

failures = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {actual}, expected {expected}")
    if not ok:
        failures.append(name)


def make_employee(tag):
    emp = Employee.objects.create(
        full_name=f'ZZ Integrity {tag}', employee_id=f'ZZINT{tag}',
        email=f'zzint{tag}@example.invalid', phone=f'98110000{tag}',
        date_of_birth=date(1990, 1, 1), gender='male',
        date_of_joining=date(2020, 1, 1), employee_status='active',
        base_salary=Decimal('20000.00'),
    )
    EmployeeSalary.objects.create(
        employee=emp, effective_date=date(2020, 1, 1),
        basic_salary=Decimal('20000.00'), is_active=True,
    )
    return emp


def make_slip(run, emp, gross, net, adv_breakdown=None):
    return Payslip.objects.create(
        payroll_run=run, employee=emp,
        gross_salary=Decimal(gross), total_deductions=Decimal('0'),
        advance_deduction=Decimal(sum(Decimal(v) for v in (adv_breakdown or {}).values())),
        absent_deduction=Decimal('0'), net_salary=Decimal(net),
        basic_salary=Decimal('20000.00'),
        salary_structure={
            'earnings_list': [{'name': 'Basic', 'amount': 20000.0}],
            'deductions_list': [],
            'bonus_total_included': '0',
            'advance_breakdown': adv_breakdown or {},
        },
        status='generated', generated_on=date.today(),
    )


try:
    with transaction.atomic():
        user = CustomUser.objects.filter(is_superuser=True).first()
        if user is None:
            user = CustomUser.objects.create_superuser(
                username='zz_integrity_admin', email='zz@example.invalid',
                password='x', role='administrator',
            )
        rf = RequestFactory()

        def post(view, pk):
            req = rf.post('/')
            req.user = user
            return view(req, pk)

        # ── 1. Advance repayment is credited back on payslip delete ──
        print("\n1. Deleting a payslip reverses its advance repayment")
        emp1 = make_employee('01')
        adv = AdvancePayment.objects.create(
            employee=emp1, amount=Decimal('5000.00'),
            repayment_mode='installments', installment_amount=Decimal('1000.00'),
            total_installments=5, paid_installments=3,
            amount_repaid=Decimal('3000.00'), status='repaying',
            payment_date=date(YEAR, 1, 1),
        )
        run1 = PayrollRun.objects.create(
            title='ZZ Run 1', frequency='monthly',
            pay_period_start=CYCLE_START, pay_period_end=CYCLE_END,
            pay_date=CYCLE_END, month=MONTH, year=YEAR, status='draft',
        )
        # Simulate what generate_payslips does: charge the installment...
        adv.amount_repaid = Decimal('4000.00')
        adv.paid_installments = 4
        adv.save()
        slip1 = make_slip(run1, emp1, '20000.00', '19000.00',
                          {str(adv.pk): '1000.00'})

        resp = post(payslip_delete, slip1.pk)
        check('delete succeeded', b'"success": true' in resp.content.lower(), True)
        adv.refresh_from_db()
        check('amount_repaid credited back', adv.amount_repaid, Decimal('3000.00'))
        check('paid_installments credited back', adv.paid_installments, 3)

        # ── 2. A cleared advance reopens when its payslip is deleted ──
        print("\n2. A fully-repaid advance reopens if the payslip is deleted")
        emp2 = make_employee('02')
        adv2 = AdvancePayment.objects.create(
            employee=emp2, amount=Decimal('2000.00'),
            repayment_mode='installments', installment_amount=Decimal('2000.00'),
            total_installments=1, paid_installments=1,
            amount_repaid=Decimal('2000.00'), status='cleared',
            payment_date=date(YEAR, 1, 1),
        )
        run2 = PayrollRun.objects.create(
            title='ZZ Run 2', frequency='monthly',
            pay_period_start=CYCLE_START, pay_period_end=CYCLE_END,
            pay_date=CYCLE_END, month=MONTH, year=YEAR, status='draft',
        )
        slip2 = make_slip(run2, emp2, '20000.00', '18000.00',
                          {str(adv2.pk): '2000.00'})
        post(payslip_delete, slip2.pk)
        adv2.refresh_from_db()
        check('amount_repaid back to zero', adv2.amount_repaid, Decimal('0.00'))
        check('status reopened from cleared', adv2.status, 'repaying')

        # ── 3. Run totals follow the payslips ──
        print("\n3. Payroll Run totals stay in step with its payslips")
        emp3, emp4 = make_employee('03'), make_employee('04')
        run3 = PayrollRun.objects.create(
            title='ZZ Run 3', frequency='monthly',
            pay_period_start=CYCLE_START, pay_period_end=CYCLE_END,
            pay_date=CYCLE_END, month=MONTH, year=YEAR, status='draft',
        )
        s3 = make_slip(run3, emp3, '20000.00', '18000.00')
        make_slip(run3, emp4, '10000.00', '9000.00')
        _refresh_payroll_run_totals(run3)
        run3.refresh_from_db()
        check('headcount', run3.employee_count, 2)
        check('gross', run3.gross_pay, Decimal('30000.00'))
        check('net', run3.net_pay, Decimal('27000.00'))

        post(payslip_delete, s3.pk)
        run3.refresh_from_db()
        check('headcount after delete', run3.employee_count, 1)
        check('gross after delete', run3.gross_pay, Decimal('10000.00'))
        check('net after delete', run3.net_pay, Decimal('9000.00'))

        # ── 4. A run holding finalized payslips cannot be deleted ──
        print("\n4. Deleting a run with finalized payslips is refused")
        emp5 = make_employee('05')
        run4 = PayrollRun.objects.create(
            title='ZZ Run 4', frequency='monthly',
            pay_period_start=CYCLE_START, pay_period_end=CYCLE_END,
            pay_date=CYCLE_END, month=MONTH, year=YEAR, status='completed',
        )
        s5 = make_slip(run4, emp5, '20000.00', '20000.00')
        s5.is_finalized = True
        s5.save(update_fields=['is_finalized'])

        resp = post(payroll_run_delete, run4.pk)
        check('run delete refused', b'"success": false' in resp.content.lower(), True)
        check('finalized payslip survived',
              Payslip.objects.filter(pk=s5.pk).exists(), True)
        check('run survived', PayrollRun.objects.filter(pk=run4.pk).exists(), True)

        # ...and is allowed once the slip is unlocked
        s5.is_finalized = False
        s5.save(update_fields=['is_finalized'])
        resp = post(payroll_run_delete, run4.pk)
        check('run delete allowed after unlock',
              b'"success": true' in resp.content.lower(), True)
        check('run removed', PayrollRun.objects.filter(pk=run4.pk).exists(), False)

        # ── 5. Reversal is a no-op for slips with no breakdown recorded ──
        print("\n5. Payslips with no advance breakdown reverse nothing")
        emp6 = make_employee('06')
        run5 = PayrollRun.objects.create(
            title='ZZ Run 5', frequency='monthly',
            pay_period_start=CYCLE_START, pay_period_end=CYCLE_END,
            pay_date=CYCLE_END, month=MONTH, year=YEAR, status='draft',
        )
        old_slip = make_slip(run5, emp6, '20000.00', '20000.00')
        old_slip.salary_structure.pop('advance_breakdown')
        old_slip.save(update_fields=['salary_structure'])
        check('nothing reversed', _reverse_advance_deductions(old_slip), 0)

        raise transaction.TransactionManagementError('__ROLLBACK__')

except transaction.TransactionManagementError as e:
    if '__ROLLBACK__' not in str(e):
        raise

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("All checks passed. Test data rolled back.")
