"""Verify that a day's Present / Half Day / Absent label always reflects the
*current* Attendance Policy thresholds -- including on manually adjusted rows.

Bug report fixed:
  The Attendance Report showed a 4.85h day as "Present" while shorter days
  showed "Half Day". Status is stored on AttendanceRecord (not derived at
  render time), and two paths never revisited it:

    1. Changing the Half Day / Absent threshold on the Attendance Policies
       page only affected rows written afterwards.
    2. A hand-fixed row (is_regularized=True, i.e. anything corrected from
       Attendance Adjustments or an approved regularization) is deliberately
       skipped by the biometric auto-sync, so it kept the label it was given
       under whatever threshold was in force at fix time -- forever.

Asserted here:
  * saving a policy re-labels the days already recorded under it, hand-fixed
    rows included, without touching their manually corrected clock times;
  * the biometric auto-sync re-labels hand-fixed rows on the next page load
    (the self-heal path on the server) and still refuses to overwrite their
    times;
  * a Shift's fallback half_day_hours does the same for employees with no
    effective policy;
  * reassigning an employee to another policy re-labels their days;
  * 'on_leave' is never overwritten by any of the above.

All fixtures are created inside a transaction that is always rolled back, so
this leaves the database untouched.

Run: python test_attendance_status_refresh.py
"""
import os
import sys
from datetime import date, datetime, time

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

import pytz  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from hrm.models import (  # noqa: E402
    AttendancePolicy, AttendanceRecord, BiometricAttendance, Employee, Shift,
)
from hrm.views import (  # noqa: E402
    _refresh_attendance_statuses, _sync_biometric_to_attendance,
    attendance_policy_update, shift_update,
)

NPT = pytz.timezone('Asia/Kathmandu')
FAILURES = []


class Rollback(Exception):
    pass


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got!r}, expected {want!r}")
    if not ok:
        FAILURES.append(f"{label}: got {got!r}, expected {want!r}")


def npt_utc(day, hh, mm):
    """A Nepal-local wall clock time as the UTC datetime the device stores."""
    return NPT.localize(datetime.combine(day, time(hh, mm))).astimezone(pytz.UTC)


rf = RequestFactory()

try:
    with transaction.atomic():
        user = CustomUser.objects.filter(is_superuser=True).first()
        if not user:
            user = CustomUser.objects.create_superuser(
                username='__test_admin_refresh__', email='refresh@example.com', password='x'
            )

        # Half Day threshold starts at 4.5h, so a 4.85h day is a full day.
        policy = AttendancePolicy.objects.create(
            name='__test_refresh_policy__', work_hours_per_day=8, late_mark_after=15,
            early_departure_grace=15, overtime_rate=0,
            absent_threshold_hours=2.0, half_day_threshold_hours=4.5, is_active=True,
        )
        shift = Shift.objects.create(
            name='__test_refresh_shift__', start_time=time(10, 0), end_time=time(18, 30),
            break_duration=0, grace_period=15, working_hours=8.5, half_day_hours=4.0,
        )
        emp = Employee.objects.create(
            full_name='__Refresh Employee__', employee_id='TESTREF001',
            employee_code='9901', shift=shift, attendance_policy=policy,
            employee_status='active', email='refresh1@example.com',
            date_of_birth=date(1990, 1, 1), date_of_joining=date(2020, 1, 1),
        )

        # A day corrected by hand on the Attendance Adjustments page:
        # 4.85 worked hours, labelled Present under the 4.5h threshold.
        fixed_day = date(2026, 2, 10)
        fixed_rec = AttendanceRecord.objects.create(
            employee=emp, date=fixed_day, clock_in=time(10, 0), clock_out=time(14, 51),
            shift=shift, status='present', working_hours=4.85, is_regularized=True,
        )
        # A normal (unfixed) full day, plus a leave day that must not move.
        full_rec = AttendanceRecord.objects.create(
            employee=emp, date=date(2026, 2, 11), clock_in=time(10, 0), clock_out=time(18, 30),
            shift=shift, status='present', working_hours=8.5,
        )
        leave_rec = AttendanceRecord.objects.create(
            employee=emp, date=date(2026, 2, 12), shift=shift, status='on_leave',
            working_hours=0,
        )

        # -- Part 1: raising the Half Day threshold re-labels saved days -----
        print('Part 1: Attendance Policy save re-labels already-saved days')
        req = rf.post(f'/hrm/attendance-policies/{policy.id}/update/', {
            'name': policy.name, 'description': '', 'work_hours_per_day': '8',
            'late_mark_after': '15', 'early_departure_grace': '15', 'overtime_rate': '0',
            'absent_threshold_hours': '2', 'half_day_threshold_hours': '5',
            'is_active': 'true',
        })
        req.user = user
        resp = attendance_policy_update(req, policy.id)
        check('policy update succeeded', resp.status_code, 200)

        fixed_rec.refresh_from_db()
        full_rec.refresh_from_db()
        leave_rec.refresh_from_db()
        check('hand-fixed 4.85h day re-labelled to half_day', fixed_rec.status, 'half_day')
        check('hand-fixed clock_in left untouched', fixed_rec.clock_in, time(10, 0))
        check('hand-fixed clock_out left untouched', fixed_rec.clock_out, time(14, 51))
        check('hand-fixed row stays regularized', fixed_rec.is_regularized, True)
        check('8.5h day still present', full_rec.status, 'present')
        check('on_leave day untouched', leave_rec.status, 'on_leave')

        # -- Part 2: the biometric sync self-heals hand-fixed rows -----------
        # This is the path that repairs the server on the next page load: the
        # raw punches say 10:00-19:00, but the human correction (and its
        # 4.85h) must survive while the label follows the current threshold.
        print('Part 2: biometric auto-sync refreshes hand-fixed rows in place')
        AttendanceRecord.objects.filter(pk=fixed_rec.pk).update(status='present')
        BiometricAttendance.objects.create(pin='9901', timestamp=npt_utc(fixed_day, 10, 0), status=0)
        BiometricAttendance.objects.create(pin='9901', timestamp=npt_utc(fixed_day, 19, 0), status=1)

        _sync_biometric_to_attendance()
        fixed_rec.refresh_from_db()
        check('sync re-labelled the hand-fixed day', fixed_rec.status, 'half_day')
        check('sync did not overwrite the corrected clock_out', fixed_rec.clock_out, time(14, 51))
        check('sync did not overwrite the corrected hours', float(fixed_rec.working_hours), 4.85)

        # -- Part 3: shift fallback threshold for policy-less employees ------
        print('Part 3: Shift half_day_hours re-labels employees with no policy')
        # Leave no active policy at all, so the shift's own half_day_hours is
        # what an employee with no assigned policy is judged against.
        AttendancePolicy.objects.update(is_active=False)

        emp2 = Employee.objects.create(
            full_name='__Refresh NoPolicy__', employee_id='TESTREF002',
            employee_code='9902', shift=shift, attendance_policy=None,
            employee_status='active', email='refresh2@example.com',
            date_of_birth=date(1990, 1, 1), date_of_joining=date(2020, 1, 1),
        )
        rec2 = AttendanceRecord.objects.create(
            employee=emp2, date=date(2026, 2, 13), clock_in=time(10, 0),
            clock_out=time(14, 51), shift=shift, status='present', working_hours=4.85,
        )
        req = rf.post(f'/hrm/shifts/{shift.id}/update/', {
            'name': shift.name, 'start_time': '10:00', 'end_time': '18:30',
            'description': '', 'break_duration': '0', 'grace_period': '15',
            'status': 'active', 'working_hours': '8.5', 'half_day_hours': '5',
        })
        req.user = user
        resp = shift_update(req, shift.id)
        check('shift update succeeded', resp.status_code, 200)
        rec2.refresh_from_db()
        check('4.85h day re-labelled via the shift fallback threshold', rec2.status, 'half_day')

        # -- Part 4: reassigning an employee's policy re-labels their days ---
        # employee_edit calls exactly this refresh when the policy/shift
        # field changes on the employee form.
        print('Part 4: employee policy reassignment re-labels their days')
        strict = AttendancePolicy.objects.create(
            name='__test_refresh_strict__', work_hours_per_day=8, late_mark_after=15,
            early_departure_grace=15, overtime_rate=0,
            absent_threshold_hours=2.0, half_day_threshold_hours=4.0, is_active=False,
        )
        emp2.attendance_policy = strict
        emp2.save(update_fields=['attendance_policy'])
        updated = _refresh_attendance_statuses(AttendanceRecord.objects.filter(employee=emp2))
        rec2.refresh_from_db()
        check('one record re-labelled by the reassignment', updated, 1)
        check('4.85h day is a full day under the stricter 4.0h threshold', rec2.status, 'present')

        raise Rollback()
except Rollback:
    pass

print()
if FAILURES:
    print(f'FAILED: {len(FAILURES)} check(s) did not pass.')
    for f in FAILURES:
        print(f'  - {f}')
    sys.exit(1)
else:
    print('All checks passed.')
