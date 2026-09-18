"""Verify the new Attendance Policy Absent/Half Day threshold feature and the
Attendance Adjustments status-recompute fix.

Bug reports fixed:
  1. Attendance Adjustments page: editing an incomplete record's clock-out
     (adding the missing time) could leave status stuck on 'incomplete', or
     leave a stale 'present'/'half_day' status that no longer matched the
     newly edited hours, because _compute_attendance_metrics only recomputed
     status starting from a bare 'present'/'late'.
  2. There was no way to configure an "Absent" threshold, or to centrally
     configure the Half Day threshold on the Attendance Policy page (it only
     lived on individual Shifts). Both are now on AttendancePolicy and used
     by _compute_attendance_metrics, the biometric sync, and regularization
     approval consistently.

This asserts, using the real AttendancePolicy-driven thresholds:
  * hours <= absent_threshold_hours -> 'absent'
  * absent_threshold_hours < hours <= half_day_threshold_hours -> 'half_day'
  * hours > half_day_threshold_hours -> 'present' (or 'late')
  * editing an already-'incomplete' record's times via attendance_update
    (the Attendance Adjustments quick-edit path) correctly recomputes status
    instead of getting stuck.
  * editing an already-'half_day' record's times recomputes to 'present' or
    'absent' as appropriate (not left stale).
  * 'on_leave' is never overwritten by the auto-recompute.

All fixtures are created inside a transaction that is always rolled back, so
this leaves the database untouched.

Run: python test_attendance_thresholds.py
"""
import os
import sys
from datetime import date, time

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.db import transaction  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from hrm.models import AttendancePolicy, AttendanceRecord, Employee, Shift  # noqa: E402
from hrm.views import _compute_attendance_metrics, attendance_update  # noqa: E402

FAILURES = []


class Rollback(Exception):
    pass


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got!r}, expected {want!r}")
    if not ok:
        FAILURES.append(f"{label}: got {got!r}, expected {want!r}")


try:
    with transaction.atomic():
        policy = AttendancePolicy.objects.create(
            name='__test_policy__', work_hours_per_day=8, late_mark_after=15,
            early_departure_grace=15, overtime_rate=0,
            absent_threshold_hours=2.0, half_day_threshold_hours=4.0,
            is_active=True,
        )
        shift = Shift.objects.create(
            name='__test_shift__', start_time=time(9, 0), end_time=time(17, 30),
            break_duration=60, working_hours=8.5, half_day_hours=4.0,
        )

        # ── Part 1: _compute_attendance_metrics threshold bands ────────────
        print("Part 1: threshold bands via _compute_attendance_metrics")
        m = _compute_attendance_metrics('09:00', '10:30', shift, policy, 'present')  # 1.5h worked
        check('1.5h worked -> absent', m['status'], 'absent')

        m = _compute_attendance_metrics('09:01', '13:52', shift, policy, 'incomplete')  # ~3.85h after break
        check('~3.85h worked, starting from incomplete -> half_day', m['status'], 'half_day')

        m = _compute_attendance_metrics('09:00', '17:30', shift, policy, 'present')  # 7.5h worked
        check('7.5h worked -> present', m['status'], 'present')

        m = _compute_attendance_metrics('10:00', '17:30', shift, policy, 'present')  # late arrival, full hours
        check('late arrival with full hours -> late', m['status'], 'late')

        m = _compute_attendance_metrics('09:00', '09:30', shift, policy, 'on_leave')
        check('on_leave is never overwritten even with clock times', m['status'], 'on_leave')

        # ── Part 2: real attendance_update call (Attendance Adjustments path) ──
        print("Part 2: attendance_update recompute (simulates Adjustments quick-edit)")
        user = CustomUser.objects.filter(is_superuser=True).first()
        if not user:
            user = CustomUser.objects.create_superuser(
                username='__test_admin__', email='test@example.com', password='x'
            )

        emp = Employee.objects.create(
            full_name='__Test Employee__', employee_id='TESTEMP001',
            shift=shift, attendance_policy=policy, employee_status='active',
            date_of_birth=date(1990, 1, 1), date_of_joining=date(2020, 1, 1),
        )

        rec = AttendanceRecord.objects.create(
            employee=emp, date=date(2026, 1, 5), clock_in=time(9, 1),
            clock_out=None, shift=shift, status='incomplete',
        )

        rf = RequestFactory()
        req = rf.post(f'/hrm/attendance/{rec.id}/update/', {
            'clock_in': '09:01', 'clock_out': '13:52', 'shift': str(shift.id),
            'is_holiday': 'false', 'notes': '', 'remarks': 'test fix',
            'status': 'incomplete', 'source': 'adjustment_edit',
        })
        req.user = user
        attendance_update(req, rec.id)
        rec.refresh_from_db()
        check('incomplete -> both times filled -> half_day (not stuck)', rec.status, 'half_day')
        check('working_hours recomputed', float(rec.working_hours), 3.85)

        # Now edit again: shorten clock_out so hours drop into absent band.
        req2 = rf.post(f'/hrm/attendance/{rec.id}/update/', {
            'clock_in': '09:01', 'clock_out': '10:30', 'shift': str(shift.id),
            'is_holiday': 'false', 'notes': '', 'remarks': 'shortened',
            'status': rec.status, 'source': 'adjustment_edit',
        })
        req2.user = user
        attendance_update(req2, rec.id)
        rec.refresh_from_db()
        check('editing an existing half_day record down to ~0.5h -> absent (not stuck on half_day)', rec.status, 'absent')

        # Now edit again: lengthen clock_out so hours cross into present.
        req3 = rf.post(f'/hrm/attendance/{rec.id}/update/', {
            'clock_in': '09:01', 'clock_out': '17:30', 'shift': str(shift.id),
            'is_holiday': 'false', 'notes': '', 'remarks': 'corrected fully',
            'status': rec.status, 'source': 'adjustment_edit',
        })
        req3.user = user
        attendance_update(req3, rec.id)
        rec.refresh_from_db()
        check('editing up to a full day -> present (not stuck on absent)', rec.status, 'present')

        raise Rollback()
except Rollback:
    pass

print()
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s) did not pass.")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("All checks passed.")
