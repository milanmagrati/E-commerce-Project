"""Verify the Misc Days / Absent Days split on the Attendance Report.

An incomplete-punch day (clocked in but never clocked out) used to fall through
into Absent Days, because it is neither 'present' nor 'on_leave' and Absent was
derived by subtraction. Misc Days meanwhile just duplicated the Half Day column,
so incomplete punches were never reported anywhere. This asserts that:

  * misc_days  == number of incomplete-punch days
  * absent_days excludes them
  * misc_days is no longer a copy of half_day
  * present + absent + leave + misc reconciles against duty days

Covers both report endpoints: the per-employee period view and the
multi-employee Attendance Detail Report.

All fixtures are created inside a transaction that is always rolled back, so
this leaves the database untouched.

Run: python test_attendance_misc_days.py
"""
import os
import sys
import json
from datetime import date, time, timedelta

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.db import transaction  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from hrm.models import AttendanceRecord, Employee, EmployeeWeekend  # noqa: E402
from hrm.views import (  # noqa: E402
    employee_period_attendance,
    employee_summary_report_ajax,
)

FAILURES = []


class Rollback(Exception):
    """Raised at the end of the fixture block to undo every write."""


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got!r}, expected {want!r}")
    if not ok:
        FAILURES.append(f"{label}: got {got!r}, expected {want!r}")


# ── The scenario from the bug report ─────────────────────────────────────────
# Range 15 Jun .. 16 Jul (32 calendar days), employee's weekend is Wednesday.
#   32 total - 5 Wednesdays          = 27 duty days
#   22 present, 4 absent, 1 incomplete punch  → 22 + 4 + 1 = 27
# Before the fix the report showed absent=5 / misc=0. It must now read 4 / 1.
DATE_FROM = date(2025, 6, 15)
DATE_TO = date(2025, 7, 16)
WEEKEND_WEEKDAY = 2  # Wednesday
ABSENT_DAYS = [date(2025, 6, 19), date(2025, 6, 20),
               date(2025, 6, 21), date(2025, 6, 22)]
INCOMPLETE_DAY = date(2025, 7, 7)

EXPECT_DUTY = 27
EXPECT_PRESENT = 22
EXPECT_ABSENT = 4
EXPECT_MISC = 1


def seed(employee, half_days=()):
    """Create the attendance window described above.

    `half_days` turns those dates into half-day records instead of full present
    ones — used to prove Misc Days no longer mirrors the Half Day column.
    """
    # Guard the fixture itself: a chosen day that happens to land on the
    # employee's weekend would silently change the expected counts.
    for d in list(ABSENT_DAYS) + [INCOMPLETE_DAY] + list(half_days):
        assert d.weekday() != WEEKEND_WEEKDAY, f'fixture day {d} is a weekend'
        assert DATE_FROM <= d <= DATE_TO, f'fixture day {d} is outside the window'
    assert not (set(half_days) & set(ABSENT_DAYS)), 'half-day overlaps an absent day'
    assert INCOMPLETE_DAY not in set(half_days), 'half-day overlaps the incomplete day'

    EmployeeWeekend.objects.filter(employee=employee).delete()
    EmployeeWeekend.objects.create(
        employee=employee,
        weekend_type='weekend',
        weekend_days=['wednesday'],
        effective_from=DATE_FROM - timedelta(days=365),
    )
    AttendanceRecord.objects.filter(
        employee=employee, date__gte=DATE_FROM, date__lte=DATE_TO).delete()

    half_days = set(half_days)
    made = {'present': 0, 'half_day': 0, 'incomplete': 0,
            'weekend': 0, 'no_record': 0}
    d = DATE_FROM
    while d <= DATE_TO:
        if d.weekday() == WEEKEND_WEEKDAY:
            made['weekend'] += 1
        elif d in ABSENT_DAYS:
            # Left with no record at all — the report must still read this as
            # Absent, which is how untracked past duty days behave in practice.
            made['no_record'] += 1
        elif d == INCOMPLETE_DAY:
            AttendanceRecord.objects.create(
                employee=employee, date=d, status='incomplete',
                clock_in=time(8, 48), clock_out=None, working_hours=0,
            )
            made['incomplete'] += 1
        elif d in half_days:
            AttendanceRecord.objects.create(
                employee=employee, date=d, status='half_day',
                clock_in=time(8, 45), clock_out=time(12, 30), working_hours=4,
            )
            made['half_day'] += 1
        else:
            AttendanceRecord.objects.create(
                employee=employee, date=d, status='present',
                clock_in=time(8, 45), clock_out=time(17, 32), working_hours=8,
            )
            made['present'] += 1
        d += timedelta(days=1)
    return made


def admin_user():
    user = CustomUser.objects.filter(is_superuser=True).first()
    if user is None:
        print('!! No superuser found — cannot exercise the @login_required views.')
        sys.exit(1)
    return user


def period_summary(employee, user, df=None, dt=None):
    req = RequestFactory().get('/hrm/employee-period-attendance/', {
        'employee_id': str(employee.id),
        'period': 'custom',
        'ref_date': (df or DATE_FROM).isoformat(),
        'ref_date_to': (dt or DATE_TO).isoformat(),
    })
    req.user = user
    resp = employee_period_attendance(req)
    assert resp.status_code == 200, resp.status_code
    return json.loads(resp.content)['detail_summary']


def detail_report_row(employee, user, df=None, dt=None):
    req = RequestFactory().get('/hrm/employee-summary-report-ajax/', {
        'date_from': (df or DATE_FROM).isoformat(),
        'date_to': (dt or DATE_TO).isoformat(),
        'employee': str(employee.id),
    })
    req.user = user
    resp = employee_summary_report_ajax(req)
    assert resp.status_code == 200, resp.status_code
    groups = json.loads(resp.content)['dept_groups']
    rows = [r for g in groups for r in g['employees'] if r['code'] == employee.employee_id]
    assert len(rows) == 1, f'expected 1 row for {employee.employee_id}, got {len(rows)}'
    return rows[0]


def assert_summary(name, s, absent_key, misc_key, expect_half=0):
    print(f"\n{name}")
    for k in ('total_days', 'duty_days', 'weekend_days', 'present_days',
              'half_day', absent_key, misc_key):
        print(f"    {k:<14} = {s[k]}")
    print("  Assertions:")
    check(f'{name} duty_days', s['duty_days'], EXPECT_DUTY)
    check(f'{name} present_days', s['present_days'], EXPECT_PRESENT)
    check(f'{name} half_day column shows the half days', s['half_day'], expect_half)
    check(f'{name} misc_days counts the incomplete punch', s[misc_key], EXPECT_MISC)
    check(f'{name} absent_days excludes the incomplete punch', s[absent_key], EXPECT_ABSENT)
    check(f'{name} misc_days is not a copy of half_day',
          (s[misc_key], s['half_day']), (EXPECT_MISC, expect_half))
    check(f'{name} present + absent + misc reconciles to duty days',
          s['present_days'] + s[absent_key] + s[misc_key], s['duty_days'])


def today_boundary_scenarios(employee, user):
    """Absent is derived from elapsed duty days, which stop at yesterday.

    Anything subtracted from that total must stop at yesterday too. A record for
    today (or a future day inside the range) counted against a total that never
    included it silently cancels out a genuine past absence.
    """
    today = date.today()
    df, dt = today - timedelta(days=6), today

    def elapsed_duty(weekend):
        n, d = 0, df
        while d <= dt - timedelta(days=1):
            if d.weekday() not in weekend:
                n += 1
            d += timedelta(days=1)
        return n

    weekend = {5, 6}
    expect_absent = elapsed_duty(weekend)  # nothing attended before today

    for label, status in (
        ('present today only', 'present'),
        ('on leave today only', 'on_leave'),
        ('incomplete today only', 'incomplete'),
    ):
        print(f"\n{'=' * 68}\nSCENARIO: {label} (range {df} .. {dt}, today={today})\n{'=' * 68}")
        if dt.weekday() in weekend:
            print('  today is a weekend for this fixture — skipped')
            continue
        try:
            with transaction.atomic():
                EmployeeWeekend.objects.filter(employee=employee).delete()
                EmployeeWeekend.objects.create(
                    employee=employee, weekend_type='weekend',
                    weekend_days=['saturday', 'sunday'],
                    effective_from=df - timedelta(days=400))
                AttendanceRecord.objects.filter(
                    employee=employee, date__gte=df, date__lte=dt).delete()
                AttendanceRecord.objects.create(
                    employee=employee, date=dt, status=status,
                    clock_in=time(9), working_hours=0,
                    clock_out=None if status == 'incomplete' else time(18))

                for name, s, ak in (
                    ('Period view', period_summary(employee, user, df, dt), 'absent_days'),
                    ('Detail Report', detail_report_row(employee, user, df, dt), 'absent'),
                ):
                    check(f"{name} / {label}: today's record does not cancel a past absence",
                          s[ak], expect_absent)
                raise Rollback
        except Rollback:
            print("  (fixtures rolled back)")


def main():
    employee = Employee.objects.filter(employee_status='active').order_by('id').first()
    if employee is None:
        print('!! No active employee in the DB.')
        return 1
    user = admin_user()

    print(f"Employee : {employee.full_name} ({employee.employee_id})")
    print(f"Window   : {DATE_FROM} .. {DATE_TO}  (weekend = Wednesday)")
    print(f"Today    : {date.today()}  (window fully elapsed)")

    baseline = AttendanceRecord.objects.count()

    for label, half_days in (
        ('no half days', ()),
        ('with 2 half days', (date(2025, 7, 10), date(2025, 7, 14))),
    ):
        print(f"\n{'=' * 68}\nSCENARIO: {label}\n{'=' * 68}")
        try:
            with transaction.atomic():
                made = seed(employee, half_days)
                print(f"Seeded   : {made}")
                assert made['present'] + made['half_day'] == EXPECT_PRESENT, made
                assert made['no_record'] == EXPECT_ABSENT, made
                assert made['incomplete'] == EXPECT_MISC, made

                expect_half = len(half_days)
                assert_summary('Period view (custom range)',
                               period_summary(employee, user),
                               'absent_days', 'misc_days', expect_half)
                assert_summary('Attendance Detail Report',
                               detail_report_row(employee, user),
                               'absent', 'misc_days', expect_half)

                raise Rollback
        except Rollback:
            print("\n(fixtures rolled back)")

    today_boundary_scenarios(employee, user)

    check('database left untouched', AttendanceRecord.objects.count(), baseline)

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
