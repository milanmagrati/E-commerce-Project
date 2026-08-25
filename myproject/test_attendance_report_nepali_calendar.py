"""Standalone verification for the Bikram Sambat (BS) calendar support added to
the Attendance Report page.

Covers the three things that can silently drift:

  1. The Attendance Report list defaults to 50 rows per page.
  2. employee_period_attendance() honours an explicit ref_date/ref_date_to pair
     for period=month — that is how a BS month (29-32 days, straddling two AD
     months) reaches the server — while still falling back to the AD calendar
     month when only ref_date is given.
  3. The BS<->AD month table embedded in attendance_report.html converts
     correctly. The conversion itself runs in the browser, so this re-implements
     the same walk over the same table and checks it against known anchors plus
     a full round trip.

Follows this repo's convention (see CLAUDE.md): a root-level script that calls
django.setup() and exercises the real views/DB.

Run with:  python test_attendance_report_nepali_calendar.py

The employee it creates uses the 9903xx code range and is removed again in
cleanup(), so it is safe to run against a working database.
"""
import os
import re
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

from hrm.models import AttendanceRecord, Employee

CODE = '990301'
TEMPLATE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'hrm', 'templates', 'hrm', 'attendance_report.html',
)

# BS 2000/01/01 == AD 1943-04-14 — the anchor the whole month table hangs off.
BS_EPOCH_AD = date(1943, 4, 14)
BS_MIN_YEAR = 2000

failures = []


def check(label, condition, detail=''):
    status = 'PASS' if condition else 'FAIL'
    if not condition:
        failures.append(label)
    print(f'  [{status}] {label}' + (f'  -> {detail}' if detail else ''))


def cleanup():
    AttendanceRecord.objects.filter(employee__employee_code=CODE).delete()
    Employee.objects.filter(employee_code=CODE).delete()


def make_employee():
    emp, _ = Employee.objects.get_or_create(
        employee_code=CODE,
        defaults={
            'full_name': f'BS Calendar Test {CODE}',
            'employee_id': f'BS-{CODE}',
            'email': f'bs{CODE}@example.invalid',
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


# ── 1. Per-page default ──────────────────────────────────────────────────────

def _selected_per_page(html):
    """Which of the Show-entries options the rendered page marked selected."""
    picked = re.findall(r'<option value="(\d+)"\s+selected>', html)
    return picked[0] if picked else None


def test_per_page_default(client):
    print('\n1. Attendance Report list defaults to 50 rows')
    r = client.get('/hrm/attendance/report/')
    check('report page loads', r.status_code == 200, f'status {r.status_code}')
    if r.status_code != 200:
        return
    picked = _selected_per_page(r.content.decode('utf-8', 'replace'))
    check('the 50 option is pre-selected', picked == '50', str(picked))
    # An explicit choice must still win over the new default.
    html25 = client.get('/hrm/attendance/report/?per_page=25').content.decode('utf-8', 'replace')
    check('explicit per_page=25 still honoured', _selected_per_page(html25) == '25',
          str(_selected_per_page(html25)))
    # An unsupported value falls back to the new default rather than the old one.
    html_bad = client.get('/hrm/attendance/report/?per_page=7').content.decode('utf-8', 'replace')
    check('an unsupported per_page falls back to 50', _selected_per_page(html_bad) == '50',
          str(_selected_per_page(html_bad)))


# ── 2. Explicit month range on the period API ────────────────────────────────

def test_period_api_month_range(client, emp):
    print('\n2. employee_period_attendance honours an explicit month range')
    url = '/hrm/attendance/report/period/'

    # A real BS month: Bhadra 2083 == AD 2026-08-17 .. 2026-09-16 (31 days).
    r = client.get(url, {
        'employee_id': emp.id, 'period': 'month',
        'ref_date': '2026-08-17', 'ref_date_to': '2026-09-16',
    })
    if r.status_code != 200:
        check('BS month request succeeds', False, f'status {r.status_code}')
        return
    d = r.json()
    check('BS month range is used verbatim',
          d['date_from'] == '2026-08-17' and d['date_to'] == '2026-09-16',
          f"{d['date_from']}..{d['date_to']}")
    check('month_days spans the BS month, not the AD one', d['month_days'] == 31,
          str(d['month_days']))
    check('month_first_weekday matches the range start',
          d['month_first_weekday'] == date(2026, 8, 17).weekday(),
          str(d['month_first_weekday']))

    # A 32-day BS month must not be truncated to an AD month length.
    r = client.get(url, {
        'employee_id': emp.id, 'period': 'month',
        'ref_date': '2026-06-15', 'ref_date_to': '2026-07-16',
    })
    d = r.json()
    check('32-day BS month keeps all 32 cells', d['month_days'] == 32, str(d['month_days']))

    # Without ref_date_to the AD calendar month is still used.
    r = client.get(url, {'employee_id': emp.id, 'period': 'month', 'ref_date': '2026-08-11'})
    d = r.json()
    check('AD month fallback starts on the 1st', d['date_from'] == '2026-08-01', d['date_from'])
    check('AD month fallback ends on the last day', d['date_to'] == '2026-08-31', d['date_to'])
    check('AD month fallback reports 31 days', d['month_days'] == 31, str(d['month_days']))

    # A reversed or unparseable end must fall back rather than 500.
    r = client.get(url, {'employee_id': emp.id, 'period': 'month',
                         'ref_date': '2026-08-11', 'ref_date_to': '2026-07-01'})
    check('reversed range falls back to the AD month',
          r.status_code == 200 and r.json()['date_from'] == '2026-08-01',
          f'status {r.status_code}')
    r = client.get(url, {'employee_id': emp.id, 'period': 'month',
                         'ref_date': '2026-08-11', 'ref_date_to': 'not-a-date'})
    check('unparseable end date falls back to the AD month',
          r.status_code == 200 and r.json()['date_from'] == '2026-08-01',
          f'status {r.status_code}')

    # Only a BS month reaches this branch and those run 29-32 days. A longer
    # span must be refused, or a hand-crafted URL could make the browser lay
    # out a calendar grid of arbitrarily many cells.
    r = client.get(url, {'employee_id': emp.id, 'period': 'month',
                         'ref_date': '2026-08-01', 'ref_date_to': '2027-08-01'})
    check('an over-long month range is refused', r.json()['month_days'] == 31,
          str(r.json()['month_days']))
    r = client.get(url, {'employee_id': emp.id, 'period': 'month',
                         'ref_date': '2026-08-01', 'ref_date_to': '2026-08-10'})
    check('a too-short month range is refused', r.json()['month_days'] == 31,
          str(r.json()['month_days']))
    # ...but every real BS month length (29-32) is accepted.
    for span in (29, 30, 31, 32):
        end = date(2026, 8, 1) + timedelta(days=span - 1)
        r = client.get(url, {'employee_id': emp.id, 'period': 'month',
                             'ref_date': '2026-08-01', 'ref_date_to': str(end)})
        check(f'a {span}-day BS month is accepted', r.json()['month_days'] == span,
              str(r.json()['month_days']))

    # The range really does drive the query: a record inside the BS month must
    # be returned, one just outside it must not.
    AttendanceRecord.objects.filter(employee=emp).delete()
    AttendanceRecord.objects.create(employee=emp, date=date(2026, 9, 16), status='present')
    AttendanceRecord.objects.create(employee=emp, date=date(2026, 9, 17), status='present')
    r = client.get(url, {
        'employee_id': emp.id, 'period': 'month',
        'ref_date': '2026-08-17', 'ref_date_to': '2026-09-16',
    })
    dates = [rec['date'] for rec in r.json()['records']]
    check('last day of the BS month is included', '2026-09-16' in dates, str(dates))
    check('the day after the BS month is excluded', '2026-09-17' not in dates, str(dates))


# ── 3. The BS month table shipped in the template ────────────────────────────

def _load_bs_table():
    """Pull the BS month-length table out of the template's inline NP module."""
    src = open(TEMPLATE, encoding='utf-8').read()
    table = {}
    for m in re.finditer(r'^\s*(2[01]\d\d):\[([\d,]+)\],?\s*$', src, re.M):
        months = [int(x) for x in m.group(2).split(',')]
        if len(months) == 12:
            table[int(m.group(1))] = months
    return table


def test_bs_table():
    print('\n3. The BS month table embedded in attendance_report.html')
    table = _load_bs_table()
    check('table found in the template', len(table) >= 100, f'{len(table)} years')
    if len(table) < 100:
        return
    years = sorted(table)
    check('covers BS 2000-2100', years[0] == 2000 and years[-1] == 2100,
          f'{years[0]}..{years[-1]}')
    check('every year length is plausible (355-386 days)',
          all(355 <= sum(v) <= 386 for v in table.values()))
    check('every month length is plausible (29-32 days)',
          all(29 <= d <= 32 for v in table.values() for d in v))

    # Same walk the JS does: day index from the epoch, then back again.
    offsets = {}
    acc = 0
    for y in years:
        offsets[y] = acc
        acc += sum(table[y])

    def bs_to_ad(y, m, d):
        return BS_EPOCH_AD + timedelta(days=offsets[y] + sum(table[y][:m - 1]) + d - 1)

    def ad_to_bs(dt):
        n = (dt - BS_EPOCH_AD).days
        y = BS_MIN_YEAR
        while y < years[-1] and n >= offsets[y + 1]:
            y += 1
        rest, m = n - offsets[y], 1
        while rest >= table[y][m - 1]:
            rest -= table[y][m - 1]
            m += 1
        return (y, m, rest + 1)

    anchors = [
        ((2000, 1, 1), date(1943, 4, 14)),
        ((2050, 1, 1), date(1993, 4, 13)),
        ((2070, 1, 1), date(2013, 4, 14)),
        ((2080, 1, 1), date(2023, 4, 14)),
        ((2081, 1, 1), date(2024, 4, 13)),
        ((2082, 1, 1), date(2025, 4, 14)),
        ((2083, 1, 1), date(2026, 4, 14)),
        ((2083, 5, 1), date(2026, 8, 17)),   # Bhadra 2083
    ]
    for bs, ad in anchors:
        got = bs_to_ad(*bs)
        check(f'BS {bs[0]}/{bs[1]:02d}/{bs[2]:02d} -> {ad}', got == ad, str(got))
        check(f'AD {ad} -> BS {bs[0]}/{bs[1]:02d}/{bs[2]:02d}', ad_to_bs(ad) == bs,
              str(ad_to_bs(ad)))

    bad = 0
    for y in years:
        for m in range(1, 13):
            for d in range(1, table[y][m - 1] + 1):
                if ad_to_bs(bs_to_ad(y, m, d)) != (y, m, d):
                    bad += 1
    check('every BS date in 2000-2100 round-trips through AD', bad == 0, f'{bad} mismatches')


# ── 4. The page still ships the controls the JS reaches for ──────────────────

def test_template_wiring(client):
    print('\n4. Both sections expose the AD/BS switch and its pickers')
    r = client.get('/hrm/attendance/report/')
    if r.status_code != 200:
        check('report page loads', False, f'status {r.status_code}')
        return
    html = r.content.decode('utf-8', 'replace')
    for element_id in ['pvCalAd', 'pvCalBs', 'pvBsDate', 'pvBsMonth', 'pvBsFrom', 'pvBsTo',
                       'esCalAd', 'esCalBs', 'esBsMonth', 'esBsFrom', 'esBsTo',
                       'esBsMonthWrap', 'esBsCustomWrap', 'pvAdWrap', 'pvBsWrap']:
        check(f'#{element_id} is on the page', f'id="{element_id}"' in html)
    for fn in ['function pvSetCalendar', 'function esSetCalendar',
               'function esSyncBsMonth', 'function esSyncBsCustom',
               'function pvDayNumHtml', 'function pvDayCaption', 'window.NP =']:
        check(f'{fn} is defined', fn in html)
    check('the CDN BS converter is gone', 'nepali-date-converter' not in html)
    check('no stale references to the old converter remain', 'NepaliDate' not in html)
    # The month arrows pass a delta now; a leftover 'YYYY-MM' call site would
    # silently navigate to the wrong place.
    check('month nav passes a delta', 'pvNavMonth(-1)' in html and 'pvNavMonth(1)' in html)


def main():
    print('=' * 70)
    print('Attendance Report — Nepali (BS) calendar support')
    print('=' * 70)

    client = admin_client()
    if client is None:
        print('  [SKIP] no superuser in this database to authenticate as')
        return

    test_bs_table()

    cleanup()
    emp = make_employee()
    try:
        test_per_page_default(client)
        test_period_api_month_range(client, emp)
        test_template_wiring(client)
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
