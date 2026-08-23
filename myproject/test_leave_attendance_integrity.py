#!/usr/bin/env python3
"""
Regression tests for the leave-management and attendance bugs.

  1. Approved PAID leave was deducted from salary. _calculate_payroll_breakdown
     only counted AttendanceRecord.status == 'on_leave', which nothing but
     holiday_apply() ever writes -- approving a leave request creates no
     attendance row -- so approved paid leave days fell through into
     absent_days and were charged against the employee's pay.

  2. Approved/pending leave booked for a FUTURE date did not consume the leave
     balance: both the LeaveBalance signal and leave_balance_resync() capped the
     count at start_date <= today, so an employee could book well past their
     entitlement and only go negative as the dates arrived.

  3. Rejecting or deleting an already-approved attendance regularization left
     its edit on the attendance record, with is_regularized=True pinning the
     row so the biometric sync could never restore the real punch data.

Everything runs inside a transaction that is rolled back.

Usage:
    python test_leave_attendance_integrity.py
"""
import os
import sys
from datetime import date, time, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()

from django.db import transaction
from django.test import RequestFactory
from accounts.models import CustomUser
from hrm.models import (
    Employee, EmployeeSalary, AttendanceRecord, LeaveType, LeaveRequest,
    LeaveBalance, AttendanceRegularization, PayrollSetting,
)
from hrm.views import (
    _calculate_payroll_breakdown, _apply_regularization_to_record,
    attendance_regularization_update_status, attendance_regularization_delete,
)

# A Mon-Fri working week inside one month. 2026-06-01 is a Monday.
CYCLE_START, CYCLE_END = date(2026, 6, 1), date(2026, 6, 30)

failures = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {actual}, expected {expected}")
    if not ok:
        failures.append(name)


def make_employee(tag):
    emp = Employee.objects.create(
        full_name=f'ZZ LA {tag}', employee_id=f'ZZLA{tag}',
        email=f'zzla{tag}@example.invalid', phone=f'98220000{tag}',
        date_of_birth=date(1990, 1, 1), gender='male',
        date_of_joining=date(2020, 1, 1), employee_status='active',
        base_salary=Decimal('30000.00'),
    )
    EmployeeSalary.objects.create(
        employee=emp, effective_date=date(2020, 1, 1),
        basic_salary=Decimal('30000.00'), is_active=True,
    )
    return emp


def breakdown(emp):
    return _calculate_payroll_breakdown(
        employee=emp, cycle_start=CYCLE_START, cycle_end=CYCLE_END,
        salary_record=EmployeeSalary.objects.filter(employee=emp, is_active=True).first(),
        payroll_settings=PayrollSetting.get_settings(),
    )


def mark_present(emp, days, skip=()):
    """Mark every Mon-Fri duty day present except the given dates."""
    d = CYCLE_START
    while d <= CYCLE_END:
        if d.weekday() < 5 and d not in skip:
            AttendanceRecord.objects.create(employee=emp, date=d, status='present')
        d += timedelta(days=1)


try:
    with transaction.atomic():
        user = CustomUser.objects.filter(is_superuser=True).first()
        if user is None:
            user = CustomUser.objects.create_superuser(
                username='zz_la_admin', email='zzla@example.invalid',
                password='x', role='administrator',
            )
        rf = RequestFactory()

        def post(view, pk, **data):
            req = rf.post('/', data)
            req.user = user
            return view(req, pk)

        paid_type = LeaveType.objects.create(
            name='ZZ Paid Leave', max_days_per_year=12, is_paid=True, is_active=True)
        unpaid_type = LeaveType.objects.create(
            name='ZZ Unpaid Leave', max_days_per_year=12, is_paid=False, is_active=True)

        # ── 1. Approved PAID leave must not be deducted ──
        print("\n1. Approved paid leave counts as paid leave, not absence")
        emp1 = make_employee('01')
        leave_days = [date(2026, 6, 10), date(2026, 6, 11)]   # Wed, Thu
        mark_present(emp1, None, skip=leave_days)

        bd_before = breakdown(emp1)
        print(f"   without any leave record: absent={bd_before['absent_days']}, "
              f"deduction={bd_before['absent_deduction']}")
        check('unexplained gap counts as absent', bd_before['absent_days'], Decimal('2'))

        LeaveRequest.objects.create(
            employee=emp1, leave_type=paid_type,
            start_date=leave_days[0], end_date=leave_days[1],
            days=2, status='approved', reason='ZZ test',
        )
        bd_after = breakdown(emp1)
        print(f"   with approved paid leave: absent={bd_after['absent_days']}, "
              f"deduction={bd_after['absent_deduction']}")
        check('paid leave days recognised', bd_after['paid_leave_days'], Decimal('2'))
        check('absent days cleared', bd_after['absent_days'], Decimal('0'))
        check('no absent deduction', bd_after['absent_deduction'], Decimal('0.00'))
        check('full basic salary earned', bd_after['net_basic'], Decimal('30000.00'))

        # ── 2. UNPAID leave is still deducted ──
        print("\n2. Approved unpaid leave is still treated as absence")
        emp2 = make_employee('02')
        mark_present(emp2, None, skip=leave_days)
        LeaveRequest.objects.create(
            employee=emp2, leave_type=unpaid_type,
            start_date=leave_days[0], end_date=leave_days[1],
            days=2, status='approved', reason='ZZ test',
        )
        bd2 = breakdown(emp2)
        check('unpaid leave not credited', bd2['paid_leave_days'], Decimal('0'))
        check('unpaid leave still absent', bd2['absent_days'], Decimal('2'))

        # ── 3. Pending leave is not credited ──
        print("\n3. Leave still awaiting approval is not credited")
        emp3 = make_employee('03')
        mark_present(emp3, None, skip=leave_days)
        LeaveRequest.objects.create(
            employee=emp3, leave_type=paid_type,
            start_date=leave_days[0], end_date=leave_days[1],
            days=2, status='pending', reason='ZZ test',
        )
        bd3 = breakdown(emp3)
        check('pending leave not credited', bd3['absent_days'], Decimal('2'))

        # ── 4. Leave is not double-counted against an on_leave row ──
        print("\n4. Leave already recorded on the attendance sheet isn't counted twice")
        emp4 = make_employee('04')
        mark_present(emp4, None, skip=leave_days)
        for d in leave_days:
            AttendanceRecord.objects.create(employee=emp4, date=d, status='on_leave')
        LeaveRequest.objects.create(
            employee=emp4, leave_type=paid_type,
            start_date=leave_days[0], end_date=leave_days[1],
            days=2, status='approved', reason='ZZ test',
        )
        bd4 = breakdown(emp4)
        check('counted exactly once', bd4['paid_leave_days'], Decimal('2'))
        check('absent still zero', bd4['absent_days'], Decimal('0'))

        # ── 5. Future-dated leave consumes the balance ──
        print("\n5. Leave booked for a future date consumes the balance")
        emp5 = make_employee('05')
        future_start = date(2026, 12, 1)
        LeaveBalance.objects.create(
            employee=emp5, leave_type=paid_type, year=2026,
            allocated_days=12, used_days=0, carry_forward_days=0,
        )
        LeaveRequest.objects.create(
            employee=emp5, leave_type=paid_type,
            start_date=future_start, end_date=future_start + timedelta(days=2),
            days=3, status='approved', reason='ZZ test',
        )
        bal = LeaveBalance.objects.get(employee=emp5, leave_type=paid_type, year=2026)
        check('future leave counted as used', bal.used_days, 3)

        # ── 6. Un-approving a regularization takes it back off the record ──
        print("\n6. Rejecting an approved regularization restores the record")
        emp6 = make_employee('06')
        reg_date = date(2026, 6, 3)
        rec = AttendanceRecord.objects.create(
            employee=emp6, date=reg_date, status='incomplete',
            clock_in=time(10, 30), clock_out=None,
        )
        reg = AttendanceRegularization.objects.create(
            employee=emp6, attendance_record=rec, date=reg_date,
            clock_in=time(9, 0), clock_out=time(18, 0),
            reason='ZZ test', status='pending',
        )
        _apply_regularization_to_record(reg)
        rec.refresh_from_db()
        reg.refresh_from_db()
        check('regularization applied', rec.clock_in, time(9, 0))
        check('record pinned from sync', rec.is_regularized, True)
        check('snapshot captured', bool(reg.pre_regularization_state), True)
        reg.status = 'approved'
        reg.save()

        post(attendance_regularization_update_status, reg.pk, status='rejected')
        rec.refresh_from_db()
        check('clock_in restored', rec.clock_in, time(10, 30))
        check('clock_out restored', rec.clock_out, None)
        check('status restored', rec.status, 'incomplete')
        check('unpinned for biometric sync', rec.is_regularized, False)

        # ── 7. Deleting an approved regularization also restores the record ──
        print("\n7. Deleting an approved regularization restores the record")
        emp7 = make_employee('07')
        rec7 = AttendanceRecord.objects.create(
            employee=emp7, date=reg_date, status='incomplete',
            clock_in=time(11, 0), clock_out=None,
        )
        reg7 = AttendanceRegularization.objects.create(
            employee=emp7, attendance_record=rec7, date=reg_date,
            clock_in=time(9, 0), clock_out=time(18, 0),
            reason='ZZ test', status='pending',
        )
        _apply_regularization_to_record(reg7)
        reg7.status = 'approved'
        reg7.save()
        post(attendance_regularization_delete, reg7.pk)
        rec7.refresh_from_db()
        check('clock_in restored on delete', rec7.clock_in, time(11, 0))
        check('unpinned on delete', rec7.is_regularized, False)

        # ── 8. A record the regularization invented is removed again ──
        print("\n8. A record created by the regularization is removed on revert")
        emp8 = make_employee('08')
        reg8 = AttendanceRegularization.objects.create(
            employee=emp8, attendance_record=None, date=reg_date,
            clock_in=time(9, 0), clock_out=time(18, 0),
            reason='ZZ test', status='pending',
        )
        _apply_regularization_to_record(reg8)
        check('record created by approval',
              AttendanceRecord.objects.filter(employee=emp8, date=reg_date).exists(), True)
        reg8.status = 'approved'
        reg8.save()
        post(attendance_regularization_update_status, reg8.pk, status='rejected')
        check('invented record removed',
              AttendanceRecord.objects.filter(employee=emp8, date=reg_date).exists(), False)

        raise transaction.TransactionManagementError('__ROLLBACK__')

except transaction.TransactionManagementError as e:
    if '__ROLLBACK__' not in str(e):
        raise

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("All checks passed. Test data rolled back.")
