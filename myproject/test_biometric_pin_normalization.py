"""
Regression test for the PIN zero-padding bug fixed in hrm/views.py.

Simulates the real-world failure mode: one employee's clock-in and
clock-out for the same day arrive under differently-padded PIN strings
(e.g. '7' vs '07'), as ZKTeco devices sometimes do between the realtime
push and the buffered ATTLOG push. Before the fix, _sync_biometric_to_attendance
grouped raw punches by the exact pin string, so this split into two
one-punch groups and the day was wrongly marked 'incomplete' with a missing
clock-out. After the fix (grouping by _normalize_pin), both punches should
pair up into one complete AttendanceRecord.

Creates and cleans up its own temporary Employee/BiometricAttendance rows —
safe to run against a real DB.

Usage: python test_biometric_pin_normalization.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from datetime import datetime, date, time as dt_time
import pytz

from hrm.models import Employee, BiometricAttendance, AttendanceRecord
from hrm.views import _sync_biometric_to_attendance, _normalize_pin

NPT = pytz.timezone('Asia/Kathmandu')
TEST_CODE = '7'
TEST_DATE = date(2099, 1, 15)  # far-future date so it can't collide with real data


def cleanup():
    Employee.objects.filter(employee_code__in=[TEST_CODE, '07', '007']).delete()
    BiometricAttendance.objects.filter(pin__in=[TEST_CODE, '07', '007']).delete()


def main():
    cleanup()
    try:
        assert _normalize_pin('07') == '7'
        assert _normalize_pin('007') == '7'
        assert _normalize_pin('7') == '7'
        assert _normalize_pin('0') == '0'
        assert _normalize_pin('EMP007') == 'EMP007'  # non-numeric prefix left alone
        print('_normalize_pin unit checks: PASS')

        emp = Employee.objects.create(
            full_name='PIN Padding Test Employee',
            employee_id='TESTPINPAD001',
            employee_code=TEST_CODE,
            email='pin-padding-test@example.invalid',
            phone='0000000000',
            date_of_birth=date(1990, 1, 1),
            gender='other',
            date_of_joining=date(2020, 1, 1),
        )

        checkin_dt = NPT.localize(datetime.combine(TEST_DATE, dt_time(9, 0, 0))).astimezone(pytz.UTC)
        checkout_dt = NPT.localize(datetime.combine(TEST_DATE, dt_time(18, 0, 0))).astimezone(pytz.UTC)

        # Check-in sent unpadded, check-out sent zero-padded — the real-world
        # mismatch that used to split this into two incomplete groups.
        BiometricAttendance.objects.create(pin=TEST_CODE, timestamp=checkin_dt, status=0)
        BiometricAttendance.objects.create(pin='07', timestamp=checkout_dt, status=1)

        _sync_biometric_to_attendance()

        record = AttendanceRecord.objects.filter(employee=emp, date=TEST_DATE).first()
        assert record is not None, 'No AttendanceRecord was created at all'
        assert record.clock_in is not None, f'clock_in missing: {record.clock_in}'
        assert record.clock_out is not None, f'clock_out missing (this is the bug): {record.clock_out}'
        assert record.status != 'incomplete', f'status still incomplete: {record.status}'
        print(f'AttendanceRecord: clock_in={record.clock_in} clock_out={record.clock_out} status={record.status}')
        print('End-to-end sync check: PASS')

    finally:
        cleanup()


if __name__ == '__main__':
    main()
