"""Standalone verification for the manual attendance duplicate/validation guards.

Covers the two pages a person (rather than a device) can create duplicates on:
Attendance Records and Attendance Regularizations. Biometric de-duplication is
covered separately by test_attlog_upload.py.

Follows this repo's convention (see CLAUDE.md): a root-level script that calls
django.setup() and exercises the real models/DB.

Run with:  python test_attendance_duplicates.py

Everything it creates uses the 9902xx employee codes and is removed again in
cleanup(), so it is safe to run against a working database.
"""
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings as dj_settings

# The Django test client speaks to the host 'testserver'; this project's
# ALLOWED_HOSTS is production-shaped, so allow it just for this script.
if 'testserver' not in dj_settings.ALLOWED_HOSTS:
    dj_settings.ALLOWED_HOSTS = list(dj_settings.ALLOWED_HOSTS) + ['testserver']

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import Client

from dashboard.timezone_utils import get_nepali_now
from hrm.models import (
    AttendanceRecord,
    AttendanceRegularization,
    BiometricAttendance,
    Employee,
)

CODE_A = '990201'
CODE_B = '990202'
TEST_CODES = [CODE_A, CODE_B]

TODAY = get_nepali_now().date()
WORKDAY = TODAY - timedelta(days=3)

failures = []


def check(label, condition, detail=''):
    status = 'PASS' if condition else 'FAIL'
    if not condition:
        failures.append(label)
    print(f'  [{status}] {label}' + (f'  -> {detail}' if detail else ''))


def cleanup():
    BiometricAttendance.objects.filter(pin__in=TEST_CODES).delete()
    AttendanceRegularization.objects.filter(employee__employee_code__in=TEST_CODES).delete()
    AttendanceRecord.objects.filter(employee__employee_code__in=TEST_CODES).delete()
    Employee.objects.filter(employee_code__in=TEST_CODES).delete()


def make_employee(code):
    emp, _ = Employee.objects.get_or_create(
        employee_code=code,
        defaults={
            'full_name': f'Dup Test {code}',
            'employee_id': f'DUP-{code}',
            'email': f'dup{code}@example.invalid',
            'phone': '0000000000',
            'date_of_birth': date(1990, 1, 1),
            'gender': 'male',
            'date_of_joining': date(2019, 1, 1),
        },
    )
    return emp


def admin_client():
    admin = get_user_model().objects.filter(is_superuser=True).first()
    if not admin:
        return None
    c = Client()
    c.force_login(admin)
    return c


def post_attendance(client, emp, day, clock_in='09:00', clock_out='18:00'):
    payload = {'employee': str(emp.pk), 'date': str(day), 'status': 'present'}
    if clock_in is not None:
        payload['clock_in'] = clock_in
    if clock_out is not None:
        payload['clock_out'] = clock_out
    return client.post('/hrm/attendance/create/', payload).json()


def test_attendance_record_guards(client, emp):
    print('\n1. Attendance Records — duplicate and input guards')

    r = post_attendance(client, emp, WORKDAY)
    check('first entry for the day succeeds', r.get('success') is True, r.get('error', ''))
    record_id = r.get('id')

    r = post_attendance(client, emp, WORKDAY, clock_in='10:00', clock_out='17:00')
    check('second entry for the same day is refused', r.get('success') is False)
    check('refusal names the employee and existing times',
          'Dup Test' in r.get('error', '') and 'Edit that record' in r.get('error', ''),
          r.get('error', ''))

    r = post_attendance(client, emp, WORKDAY - timedelta(days=1),
                        clock_in='09:00', clock_out='09:00')
    check('identical clock in/out refused', r.get('success') is False, r.get('error', ''))
    check('refusal explains the Absent trap', 'Absent' in r.get('error', ''), r.get('error', ''))

    r = post_attendance(client, emp, TODAY + timedelta(days=5))
    check('future date refused', r.get('success') is False, r.get('error', ''))

    r = post_attendance(client, emp, '31-02-2026')
    check('malformed date refused (not a 500)', r.get('success') is False, r.get('error', ''))

    r = post_attendance(client, emp, WORKDAY - timedelta(days=2),
                        clock_in=None, clock_out=None)
    check('entry with no times refused', r.get('success') is False, r.get('error', ''))

    # Soft-delete the record, then try to re-add the same day.
    AttendanceRecord.objects.filter(pk=record_id).update(is_deleted=True)
    r = post_attendance(client, emp, WORKDAY)
    check('trashed record still blocks re-adding the day', r.get('success') is False)
    check('message points at Attendance Adjustments (Trash)',
          'Trash' in r.get('error', ''), r.get('error', ''))
    AttendanceRecord.objects.filter(pk=record_id).update(is_deleted=False)

    r = client.post(f'/hrm/attendance/{record_id}/update/',
                    {'clock_in': '09:00', 'clock_out': '09:00', 'status': 'present'})
    check('edit to identical clock in/out refused', r.json().get('success') is False,
          r.json().get('error', ''))

    r = client.post(f'/hrm/attendance/{record_id}/update/',
                    {'clock_in': '09:15', 'clock_out': '18:05', 'status': 'present'})
    check('a valid edit still goes through', r.json().get('success') is True,
          r.json().get('error', ''))

    return record_id


def test_race_is_handled(client, emp):
    print('\n2. Concurrent create hits the unique constraint, not a 500')
    from django.db import IntegrityError

    day = WORKDAY - timedelta(days=10)
    r = post_attendance(client, emp, day)
    check('setup entry created', r.get('success') is True, r.get('error', ''))

    # Simulate the double-clicked Save that slips past the .exists() check by
    # inserting the duplicate directly, the way a second worker process would.
    raised = False
    try:
        AttendanceRecord.objects.create(employee=emp, date=day, status='present')
    except IntegrityError:
        raised = True
    check('DB unique constraint is the real backstop', raised,
          'unique_together (employee, date) enforced')

    # And the view surfaces it as JSON rather than an HTML 500.
    r = post_attendance(client, emp, day, clock_in='11:00', clock_out='19:00')
    check('view returns JSON for the clash', r.get('success') is False, r.get('error', ''))


def test_regularization_guards(client, emp):
    print('\n3. Attendance Regularizations — duplicate guard (was absent entirely)')

    def post_reg(day, clock_in='09:00', clock_out='18:00', draft=False, reason='Forgot to punch out'):
        payload = {'employee': str(emp.pk), 'date': str(day), 'reason': reason}
        if clock_in is not None:
            payload['clock_in'] = clock_in
        if clock_out is not None:
            payload['clock_out'] = clock_out
        if draft:
            payload['is_draft'] = 'true'
        return client.post('/hrm/attendance-regularizations/create/', payload).json()

    day = WORKDAY - timedelta(days=20)

    r = post_reg(day)
    check('first request succeeds', r.get('success') is True, r.get('error', ''))
    first_id = r.get('id')

    r = post_reg(day, clock_in='08:00', clock_out='17:00')
    check('second pending request for the same day refused', r.get('success') is False)
    check('refusal says a pending request exists',
          'pending regularization' in r.get('error', ''), r.get('error', ''))

    r = post_reg(day, reason='No times given', clock_in=None, clock_out=None)
    check('request with no times refused', r.get('success') is False, r.get('error', ''))

    r = post_reg(day - timedelta(days=1), clock_in='09:00', clock_out='09:00')
    check('identical clock in/out refused', r.get('success') is False, r.get('error', ''))

    r = post_reg(TODAY + timedelta(days=3))
    check('future date refused', r.get('success') is False, r.get('error', ''))

    # A draft with no times is legitimate work-in-progress.
    r = post_reg(day - timedelta(days=2), clock_in=None, clock_out=None, draft=True)
    check('draft with no times still allowed', r.get('success') is True, r.get('error', ''))
    draft_id = r.get('id')

    # A rejected request must not block a fresh attempt.
    AttendanceRegularization.objects.filter(pk=first_id).update(status='rejected')
    r = post_reg(day, clock_in='08:30', clock_out='17:30')
    check('re-request after a rejection is allowed', r.get('success') is True, r.get('error', ''))
    second_id = r.get('id')

    # An approved request blocks a new one (pre_regularization_state integrity).
    AttendanceRegularization.objects.filter(pk=second_id).update(status='approved')
    r = post_reg(day, clock_in='07:00', clock_out='16:00')
    check('new request blocked once the day is approved', r.get('success') is False)
    check('refusal explains the restore-on-reject risk',
          'approved regularization' in r.get('error', ''), r.get('error', ''))

    # Editing the draft onto the approved day must hit the same rule. The
    # update path derives the target day from the linked attendance record, so
    # link one on the already-approved day to actually trigger the move.
    taken_day_record = AttendanceRecord.objects.create(
        employee=emp, date=day, clock_in=None, clock_out=None, status='incomplete')
    r = client.post(f'/hrm/attendance-regularizations/{draft_id}/update/', {
        'reason': 'Moving onto a taken day', 'clock_in': '09:00', 'clock_out': '18:00',
        'attendance_record': str(taken_day_record.pk),
    }).json()
    check('editing a request onto an already-approved day is refused',
          r.get('success') is False, r.get('error', ''))
    check('update refusal names the approved conflict',
          'approved regularization' in r.get('error', ''), r.get('error', ''))
    check('the draft was not moved',
          AttendanceRegularization.objects.get(pk=draft_id).date != day)

    # A normal edit that stays on its own day still works.
    r = client.post(f'/hrm/attendance-regularizations/{draft_id}/update/', {
        'reason': 'Updated reason', 'clock_in': '09:30', 'clock_out': '18:30',
    }).json()
    check('a non-conflicting edit still goes through', r.get('success') is True,
          r.get('error', ''))

    # Directly exercise the shared rule the update path uses.
    from hrm.views import _find_conflicting_regularization
    conflict, err = _find_conflicting_regularization(emp, day, exclude_pk=draft_id)
    check('shared rule flags the approved day', conflict is not None, err or '')
    conflict, err = _find_conflicting_regularization(emp, day, exclude_pk=second_id)
    check('excluding the request itself clears the conflict', conflict is None)


def test_biometric_still_deduped(client, emp_b):
    print('\n4. Biometric path still de-duplicates independently')
    from hrm.views import _ingest_attlog_lines

    TAB = '\t'
    lines = [
        TAB.join([CODE_B, '2019-04-01 09:00:00', '0', '1']),
        TAB.join([CODE_B, '2019-04-01 09:00:00', '0', '1']),
        TAB.join(['0' + CODE_B, '2019-04-01 18:00:00', '1', '1']),
    ]
    s1 = _ingest_attlog_lines(lines, device=None, source='dup-test')
    check('in-file duplicate collapsed', s1['created'] == 2 and s1['duplicates'] == 1, str(s1))
    s2 = _ingest_attlog_lines(lines, device=None, source='dup-test')
    check('re-ingest creates nothing', s2['created'] == 0, str(s2))


def main():
    print('=' * 70)
    print('Manual attendance duplicate / validation guards')
    print('=' * 70)

    client = admin_client()
    if client is None:
        print('  [SKIP] no superuser in this database to authenticate as')
        return

    cleanup()
    emp_a = make_employee(CODE_A)
    emp_b = make_employee(CODE_B)
    try:
        test_attendance_record_guards(client, emp_a)
        test_race_is_handled(client, emp_a)
        test_regularization_guards(client, emp_a)
        test_biometric_still_deduped(client, emp_b)
    finally:
        cleanup()

    print('\n' + '=' * 70)
    if failures:
        print(f'{len(failures)} CHECK(S) FAILED:')
        for f in failures:
            print('  - ' + f)
        sys.exit(1)
    print('All checks passed.')
    print('=' * 70)


if __name__ == '__main__':
    main()
