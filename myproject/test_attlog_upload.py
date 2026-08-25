"""Standalone verification for the attlog.dat import + double-tap safeguard.

Follows this repo's convention (see CLAUDE.md): a root-level script that calls
django.setup() and exercises the real models/DB, rather than a pytest suite.

Run with:  python test_attlog_upload.py

Everything it creates is namespaced under the TEST_PIN_PREFIX employee codes and
removed again in cleanup(), so it is safe to run against a working database.
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

import pytz
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from hrm.models import AttendanceRecord, BiometricAttendance, Employee, ZKDevice
from hrm.views import (
    DUPLICATE_PUNCH_WINDOW_SECONDS,
    _derive_clock_times,
    _ingest_attlog_lines,
    _parse_attlog_line,
)

NPT = pytz.timezone('Asia/Kathmandu')
TAB = '\t'

# Distinctive codes so nothing here can collide with real employees.
PIN_FULL = '990101'      # normal in + out
PIN_DOUBLE = '990102'    # double-tap at check-in only
TEST_PINS = [PIN_FULL, PIN_DOUBLE]
TEST_SN = 'TEST-ATTLOG-SN'
TEST_DATE = datetime(2019, 3, 11).date()   # deliberately old: no real data there

failures = []


def check(label, condition, detail=''):
    status = 'PASS' if condition else 'FAIL'
    if not condition:
        failures.append(label)
    print(f'  [{status}] {label}' + (f'  -> {detail}' if detail else ''))


def cleanup():
    BiometricAttendance.objects.filter(pin__in=TEST_PINS).delete()
    AttendanceRecord.objects.filter(employee__employee_code__in=TEST_PINS).delete()
    Employee.objects.filter(employee_code__in=TEST_PINS).delete()
    ZKDevice.objects.filter(serial_number=TEST_SN).delete()


def make_employees():
    made = []
    for pin in TEST_PINS:
        emp, _ = Employee.objects.get_or_create(
            employee_code=pin,
            defaults={
                'full_name': f'Attlog Test {pin}',
                'employee_id': f'ATTLOG-{pin}',
                'email': f'attlog{pin}@example.invalid',
                'phone': '0000000000',
                'date_of_birth': date(1990, 1, 1),
                'gender': 'male',
                'date_of_joining': date(2019, 1, 1),
            },
        )
        made.append(emp)
    return made


def attlog_bytes():
    """A file mixing every wrinkle a real attlog.dat throws at the parser."""
    rows = [
        'No' + TAB + 'UserID' + TAB + 'DateTime' + TAB + 'Status',   # header, skipped
        TAB.join([PIN_FULL, '2019-03-11 09:01:22', '0', '1', '0', '0']),
        TAB.join(['0' + PIN_FULL, '2019-03-11 18:30:05', '1', '1']),  # zero-padded PIN
        TAB.join([PIN_FULL, '2019-03-11 09:01:22', '0', '1']),        # duplicate in-file
        TAB.join([PIN_DOUBLE, '2019-03-11 09:00:00', '0', '1']),
        TAB.join([PIN_DOUBLE, '2019-03-11 09:02:30', '0', '1']),      # double tap
        '',
        'FIRMWARE v1.2.3',                                            # junk, skipped
    ]
    return ('\r\n'.join(rows) + '\r\n').encode('utf-8')


def test_parser():
    print('\n1. Line parser handles the format variants')
    check('tab-delimited ADMS row',
          _parse_attlog_line(TAB.join(['1', '2026-08-20 09:01:22', '0', '1']))[:2]
          == ('1', datetime(2026, 8, 20, 9, 1, 22)))
    check('zero-padded PIN normalizes',
          _parse_attlog_line(TAB.join(['0007', '2026-08-20 09:00:00', '0', '1']))[0] == '7')
    check('date and time in separate columns',
          _parse_attlog_line(TAB.join(['7', '2026-08-20', '09:05:00', '0']))[1]
          == datetime(2026, 8, 20, 9, 5))
    check('comma-delimited export',
          _parse_attlog_line('12,2026-08-20 18:30:11,1,1')[0] == '12')
    check('slash date separator',
          _parse_attlog_line(TAB.join(['3', '2026/08/20 08:59:00']))[1]
          == datetime(2026, 8, 20, 8, 59))
    check('header row rejected', _parse_attlog_line('No\tUserID\tDateTime') is None)
    check('blank line rejected', _parse_attlog_line('   ') is None)
    check('impossible date rejected',
          _parse_attlog_line(TAB.join(['5', '2019-02-30 09:00:00'])) is None)
    check('out-of-range status clamped',
          _parse_attlog_line(TAB.join(['8', '2026-08-20 09:00:00', '99']))[2] == 0)


def test_double_tap():
    print('\n2. Double-tap safeguard')

    def ts(h, m, s=0):
        return NPT.localize(datetime(2019, 3, 11, h, m, s))

    cin, cout = _derive_clock_times([ts(9, 0), ts(9, 2)], NPT)
    check('two taps 2 min apart -> no clock_out', cout is None, f'clock_in={cin}')

    cin, cout = _derive_clock_times([ts(9, 0), ts(9, 5)], NPT)
    check(f'exactly {DUPLICATE_PUNCH_WINDOW_SECONDS}s apart -> no clock_out', cout is None)

    cin, cout = _derive_clock_times([ts(9, 0), ts(9, 5, 1)], NPT)
    check('just past the window -> real clock_out', cout is not None, str(cout))

    cin, cout = _derive_clock_times([ts(9, 0), ts(9, 1), ts(18, 30)], NPT)
    check('duplicate check-in + real check-out',
          cin.hour == 9 and cout.hour == 18, f'{cin} -> {cout}')

    check('empty punch list', _derive_clock_times([], NPT) == (None, None))


def test_upload_endpoint():
    print('\n3. Upload endpoint (Django test client)')

    User = get_user_model()
    admin = User.objects.filter(is_superuser=True).first()
    if not admin:
        print('  [SKIP] no superuser in this database to authenticate as')
        return

    device = ZKDevice.objects.create(serial_number=TEST_SN, name='Attlog Test Device')
    client = Client()
    client.force_login(admin)

    payload = attlog_bytes()

    # -- rejections --------------------------------------------------------
    r = client.post('/hrm/biometric-attendance/upload/', {})
    check('missing file rejected', r.json()['success'] is False, r.json().get('error'))

    r = client.post('/hrm/biometric-attendance/upload/', {
        'attlog_file': SimpleUploadedFile('photo.jpg', b'\xff\xd8\xff', content_type='image/jpeg')})
    check('wrong extension rejected', r.json()['success'] is False, r.json().get('error'))

    r = client.post('/hrm/biometric-attendance/upload/', {
        'attlog_file': SimpleUploadedFile('attlog.dat', b'', content_type='application/octet-stream')})
    check('empty file rejected', r.json()['success'] is False, r.json().get('error'))

    r = client.post('/hrm/biometric-attendance/upload/', {
        'attlog_file': SimpleUploadedFile('user.dat', b'\x00\x01\x02' * 2000,
                                          content_type='application/octet-stream')})
    check('binary .dat rejected', r.json()['success'] is False, r.json().get('error'))

    r = client.post('/hrm/biometric-attendance/upload/', {
        'attlog_file': SimpleUploadedFile('notes.txt', b'just some notes\nnothing here\n',
                                          content_type='text/plain')})
    check('text file with no punches rejected', r.json()['success'] is False, r.json().get('error'))

    r = client.get('/hrm/biometric-attendance/upload/')
    check('GET rejected', r.json()['success'] is False)

    # -- the real import ---------------------------------------------------
    r = client.post('/hrm/biometric-attendance/upload/', {
        'attlog_file': SimpleUploadedFile('attlog.dat', payload,
                                          content_type='application/octet-stream'),
        'device_id': str(device.pk),
    })
    data = r.json()
    check('import succeeds', data.get('success') is True, data.get('error', ''))
    if not data.get('success'):
        return

    st = data['stats']
    check('4 punches imported', st['created'] == 4, str(st))
    check('in-file duplicate collapsed', st['duplicates'] == 1, str(st))
    check('header + junk skipped', st['skipped'] == 2, str(st))
    check('both employees matched', st['employees'] == 2 and not st['unmatched_pins'], str(st))
    check('date range reported', st['date_range'] == '2019-03-11', st['date_range'])

    stored = BiometricAttendance.objects.filter(pin__in=TEST_PINS)
    check('punches tagged with the chosen device',
          stored.exclude(device=device).count() == 0)
    check('padded PIN stored normalized',
          set(stored.values_list('pin', flat=True)) == set(TEST_PINS),
          str(sorted(set(stored.values_list('pin', flat=True)))))

    # -- aggregation -------------------------------------------------------
    full = AttendanceRecord.objects.get(employee__employee_code=PIN_FULL, date=TEST_DATE)
    check('normal day gets clock_in and clock_out',
          full.clock_in is not None and full.clock_out is not None,
          f'{full.clock_in} -> {full.clock_out}')
    check('padded evening punch paired with morning punch',
          full.clock_out.hour == 18, str(full.clock_out))
    check('working hours computed', float(full.working_hours) > 8, str(full.working_hours))

    dbl = AttendanceRecord.objects.get(employee__employee_code=PIN_DOUBLE, date=TEST_DATE)
    check('double-tap day has no clock_out', dbl.clock_out is None, str(dbl.clock_out))
    check('double-tap day flagged incomplete', dbl.status == 'incomplete', dbl.status)
    check('double-tap day not booked as 0-hour shift',
          float(dbl.working_hours) == 0 and dbl.status != 'absent',
          f'{dbl.working_hours}h / {dbl.status}')

    # -- re-upload is a no-op ---------------------------------------------
    before = BiometricAttendance.objects.filter(pin__in=TEST_PINS).count()
    r2 = client.post('/hrm/biometric-attendance/upload/', {
        'attlog_file': SimpleUploadedFile('attlog.dat', payload,
                                          content_type='application/octet-stream')})
    d2 = r2.json()
    after = BiometricAttendance.objects.filter(pin__in=TEST_PINS).count()
    check('re-upload creates nothing', d2['stats']['created'] == 0, str(d2['stats']))
    check('re-upload reports all as duplicates', d2['stats']['duplicates'] == 5, str(d2['stats']))
    check('punch count unchanged', before == after, f'{before} -> {after}')


def test_unmatched_pins_are_reported():
    print('\n4. Punches for an unknown PIN are surfaced, not silently dropped')
    User = get_user_model()
    admin = User.objects.filter(is_superuser=True).first()
    if not admin:
        print('  [SKIP] no superuser in this database to authenticate as')
        return

    client = Client()
    client.force_login(admin)
    unknown = '990199'
    body = (TAB.join([unknown, '2019-03-11 09:00:00', '0', '1']) + '\r\n' +
            TAB.join([unknown, '2019-03-11 18:00:00', '1', '1']) + '\r\n')
    try:
        r = client.post('/hrm/biometric-attendance/upload/', {
            'attlog_file': SimpleUploadedFile('attlog.dat', body.encode(),
                                              content_type='application/octet-stream')})
        data = r.json()
        check('import still succeeds', data.get('success') is True, data.get('error', ''))
        check('unknown PIN listed', unknown in data['stats']['unmatched_pins'],
              str(data['stats']['unmatched_pins']))
        check('message warns the admin', 'No employee matches PIN' in data['message'])
    finally:
        BiometricAttendance.objects.filter(pin=unknown).delete()


def test_force_resync_flag():
    print('\n5. Sync Device queues a device-side history pull')
    User = get_user_model()
    admin = User.objects.filter(is_superuser=True).first()
    if not admin:
        print('  [SKIP] no superuser in this database to authenticate as')
        return

    device = ZKDevice.objects.create(serial_number=TEST_SN + '-RESYNC')
    try:
        client = Client()
        client.force_login(admin)
        r = client.post(f'/hrm/settings/zekto/device/{device.pk}/sync/')
        check('sync endpoint succeeds', r.json().get('success') is True, r.json().get('error', ''))

        device.refresh_from_db()
        check('resync flag set on the device row', device.force_resync_requested_at is not None)

        # The device's next heartbeat should claim the flag and get the pull command.
        resp = Client().get('/iclock/getrequest', {'SN': device.serial_number})
        body = resp.content.decode()
        check('heartbeat issues DATA QUERY ATTLOG', 'DATA QUERY ATTLOG' in body, body.replace('\r\n', ' | '))
        check('heartbeat still sets the clock', 'SET TIME' in body)

        device.refresh_from_db()
        check('flag cleared after being claimed', device.force_resync_requested_at is None)

        # A second heartbeat must not repeat the expensive pull.
        body2 = Client().get('/iclock/getrequest', {'SN': device.serial_number}).content.decode()
        check('next heartbeat is a plain time sync', 'DATA QUERY ATTLOG' not in body2)
        check('plain heartbeat still issues CHECK', 'CHECK' in body2)
    finally:
        ZKDevice.objects.filter(serial_number=TEST_SN + '-RESYNC').delete()


def test_device_push_path():
    print('\n6. Live ADMS push uses the same ingest path')
    body = (TAB.join([PIN_FULL, '2019-03-12 09:15:00', '0', '1']) + '\r\n').encode()
    try:
        resp = Client().post(
            '/iclock/cdata?SN=' + TEST_SN + '&table=ATTLOG',
            data=body, content_type='application/octet-stream')
        check('device gets OK back', resp.content.decode().strip() == 'OK')
        # NOTE: filter on an explicit Nepal-local day range, never
        # timestamp__month/__day — those compile to CONVERT_TZ() on MySQL and
        # match zero rows on this server (see the comments in hrm/views.py).
        day_start = NPT.localize(datetime(2019, 3, 12, 0, 0))
        day_end = NPT.localize(datetime(2019, 3, 13, 0, 0))
        check('pushed punch stored',
              BiometricAttendance.objects.filter(
                  pin=PIN_FULL, timestamp__gte=day_start, timestamp__lt=day_end).exists())

        handshake = Client().get('/iclock/cdata', {'SN': TEST_SN}).content.decode()
        check('handshake asks for the full buffer', 'ATTLOGStamp=0' in handshake)
        check('handshake keeps the Nepal offset', 'TimeZone=345' in handshake)
    finally:
        BiometricAttendance.objects.filter(pin=PIN_FULL).delete()


def main():
    print('=' * 70)
    print('attlog.dat import + biometric sync verification')
    print('=' * 70)

    cleanup()
    make_employees()
    try:
        test_parser()
        test_double_tap()
        test_upload_endpoint()
        test_unmatched_pins_are_reported()
        test_force_resync_flag()
        test_device_push_path()
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
