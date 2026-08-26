"""Standalone verification for the HRM Salary Report page.

Follows this repo's convention for verification scripts (manual django.setup(),
real models, real DB) rather than a pytest/TestCase harness.

Run:  python test_salary_report.py

It exercises the report the way the page does — filters, dataset totals, the
grouped view, the CSV export and the detail drawer — and asserts the arithmetic
identities the payroll model guarantees, so a regression in either this report
or the payslip engine it reads from shows up here.
"""

import csv as csv_module
import os
import sys
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client, RequestFactory  # noqa: E402

from hrm import salary_report as sr  # noqa: E402
from hrm.models import Bonus, Employee, Payslip  # noqa: E402

PASSED = []
FAILED = []


def check(label, condition, detail=''):
    if condition:
        PASSED.append(label)
        print(f'  PASS  {label}')
    else:
        FAILED.append(f'{label} — {detail}')
        print(f'  FAIL  {label}{(" — " + detail) if detail else ""}')


def section(title):
    print(f'\n{title}\n' + '-' * len(title))


def make_filters(**overrides):
    """A filters dict shaped exactly like parse_filters() output."""
    base = dict(sr.NO_FILTERS)
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────────────
section('1. Filter parsing is total — no input can raise')

rf = RequestFactory()
hostile = [
    '',
    '?per_page=999&page=abc&view=hack&status=nonsense',
    '?date_from=not-a-date&date_to=2026-13-45',
    '?department=abc&branch=-1&run=99999999999999999999',
    '?date_from=2026-12-31&date_to=2026-01-01',   # reversed on purpose
    '?search=' + 'x' * 500,
]
for query in hostile:
    try:
        parsed = sr.parse_filters(rf.get('/hrm/reports/salary/' + query))
        ok = (
            parsed['view'] in sr.VIEW_CHOICES
            and parsed['per_page'] in sr.PER_PAGE_CHOICES
            and parsed['status'] in dict(Payslip.STATUS_CHOICES) or parsed['status'] == ''
        )
        check(f'parse_filters survives {query[:44] or "(no query)"!r}', ok, str(parsed))
    except Exception as exc:                                  # noqa: BLE001
        check(f'parse_filters survives {query[:44]!r}', False, repr(exc))

reversed_range = sr.parse_filters(rf.get('/?date_from=2026-12-31&date_to=2026-01-01'))
check(
    'reversed date range is swapped, not silently empty',
    reversed_range['date_from'] < reversed_range['date_to'],
    f"{reversed_range['date_from']} → {reversed_range['date_to']}",
)

check(
    'NO_FILTERS has the same keys parse_filters produces',
    set(sr.NO_FILTERS) == set(sr.parse_filters(rf.get('/'))),
    f'{sorted(set(sr.NO_FILTERS) ^ set(sr.parse_filters(rf.get("/"))))}',
)


# ──────────────────────────────────────────────────────────────────────────────
section('2. Dataset builds and every row is internally consistent')

dataset = sr.build_dataset(make_filters())
rows = dataset['rows']
totals = dataset['totals']
print(f'  ({len(rows)} payslip row(s), {len(dataset["employee_rows"])} employee row(s))')

live_payslips = Payslip.objects.filter(is_deleted=False).count()
check(
    'dataset covers every non-trashed payslip',
    len(rows) == live_payslips,
    f'{len(rows)} rows vs {live_payslips} payslips',
)

check(
    'trashed payslips are excluded',
    not (Payslip.objects.filter(is_deleted=True).exists()
         and {p.pk for p in Payslip.objects.filter(is_deleted=True)} & {r['id'] for r in rows}),
)

for row in rows:
    if row['period_start'] is None:
        check(f"row {row['payslip_number']} resolved a pay period", False, 'period unresolved')
        break
else:
    check('every row resolved a pay period', True)

bad_types = [
    r['payslip_number'] for r in rows
    if not all(isinstance(r[f], Decimal) for f in
               ('basic', 'bonus', 'gross', 'deductions', 'advance', 'net'))
]
check('all money fields are Decimal', not bad_types, str(bad_types[:5]))

# The identity the payroll engine guarantees. Rows that break it must be
# flagged, not silently rendered.
unflagged_mismatch = [
    r['payslip_number'] for r in rows
    if r['net_variance'] != Decimal('0.00') and not r['has_warning']
]
check(
    'net != gross - deductions - advance is always flagged',
    not unflagged_mismatch,
    str(unflagged_mismatch[:5]),
)

flagged = [r for r in rows if r['has_warning']]
check(
    'flagged rows carry an explanation',
    all(r['warnings'] for r in flagged),
    f'{len(flagged)} flagged',
)
if flagged:
    print(f'    note: {len(flagged)} row(s) flagged for review — first reason:')
    print(f'          {flagged[0]["warnings"][0]}')


# ──────────────────────────────────────────────────────────────────────────────
section('3. KPI totals equal the sum of the rows they summarise')

for field, key in (
    ('basic', 'basic'), ('bonus', 'bonus'), ('gross', 'gross'),
    ('deductions', 'deductions'), ('advance', 'advance'), ('net', 'net'),
):
    expected = sum((r[field] for r in rows), Decimal('0.00'))
    check(f'total {key} matches row sum', totals[key] == expected,
          f'{totals[key]} vs {expected}')

check(
    'paid + pending net equals total net',
    totals['net_paid'] + totals['net_unpaid'] == totals['net'],
    f"{totals['net_paid']} + {totals['net_unpaid']} != {totals['net']}",
)
check(
    'paid + pending counts equal the payslip count',
    totals['paid_count'] + totals['unpaid_count'] == totals['payslips'],
)
check(
    'distinct employee count is right',
    totals['employees'] == len({r['employee_pk'] for r in rows}),
)


# ──────────────────────────────────────────────────────────────────────────────
section('4. Employee summary reconciles with the payslip register')

emp_rows = dataset['employee_rows']
for field in ('basic', 'bonus', 'gross', 'deductions', 'advance', 'net'):
    grouped_total = sum((e[field] for e in emp_rows), Decimal('0.00'))
    check(
        f'grouped {field} equals ungrouped {field}',
        grouped_total == totals[field],
        f'{grouped_total} vs {totals[field]}',
    )

check(
    'grouped payslip counts equal the register size',
    sum(e['payslips'] for e in emp_rows) == len(rows),
)
check(
    'no employee appears twice in the summary',
    len({e['employee_pk'] for e in emp_rows}) == len(emp_rows),
)

zero_rows = [e for e in emp_rows if e['payslips'] == 0]
check(
    'unpaid employees are padded in with zero money',
    all(e['net'] == Decimal('0.00') and e['gross'] == Decimal('0.00') for e in zero_rows),
    f'{len(zero_rows)} unpaid employee row(s)',
)

# Padding must switch off when it would be nonsense.
scoped = sr.build_dataset(make_filters(status='paid'))
check(
    'a payslip-status filter turns padding off',
    not scoped['include_unpaid']
    and all(e['payslips'] > 0 for e in scoped['employee_rows']),
)


# ──────────────────────────────────────────────────────────────────────────────
section('5. Filters actually narrow, and never widen, the result set')

first = rows[0] if rows else None
if first is None:
    print('  (no payslips in this database — filter checks skipped)')
else:
    by_name = sr.build_dataset(make_filters(search=first['employee_name']))
    check(
        'search narrows to the searched employee',
        by_name['rows'] and all(
            first['employee_name'].lower() in r['employee_name'].lower()
            or first['employee_name'].lower() in r['payslip_number'].lower()
            for r in by_name['rows']
        ),
        f"{len(by_name['rows'])} row(s)",
    )
    check('search result is a subset of the full set', len(by_name['rows']) <= len(rows))

    by_run = sr.build_dataset(make_filters(run=first['run_id']))
    check(
        'payroll-run filter keeps only that run',
        all(r['run_id'] == first['run_id'] for r in by_run['rows']),
    )

    day = first['effective_date']
    windowed = sr.build_dataset(make_filters(date_from=day, date_to=day))
    check(
        'a single-day window keeps only that day',
        all(r['effective_date'] == day for r in windowed['rows']),
        f"{len(windowed['rows'])} row(s) on {day}",
    )
    check(
        'the row that defined the window is inside it',
        first['id'] in {r['id'] for r in windowed['rows']},
    )

    impossible = sr.build_dataset(
        make_filters(date_from=day.replace(year=day.year + 50),
                     date_to=day.replace(year=day.year + 50))
    )
    check('an empty window yields zero rows and zero totals',
          not impossible['rows'] and impossible['totals']['net'] == Decimal('0.00'))


# ──────────────────────────────────────────────────────────────────────────────
section('6. Bonus accounting is not double-counted')

# `bonus` must be a constituent of gross, never an addend on top of it.
overstated = [
    r['payslip_number'] for r in rows
    if r['bonus'] > Decimal('0.00') and r['bonus'] > r['gross']
]
check('no row reports more bonus than gross', not overstated, str(overstated[:5]))

if rows:
    sample = rows[0]
    live = sum(
        (b.amount for b in Bonus.objects.filter(
            employee_id=sample['employee_pk'],
            year=sample['period_start'].year,
            month=sample['period_start'].month,
            status__in=sr.COUNTED_BONUS_STATUSES)),
        Decimal('0'),
    )
    check(
        'live bonus lookup matches the row\'s bonus_live',
        sample['bonus_live'] == sr.money(live),
        f"{sample['bonus_live']} vs {live}",
    )


# ──────────────────────────────────────────────────────────────────────────────
section('7. Read-only: rendering the report mutates nothing')

before = {
    'payslips': list(Payslip.objects.values_list(
        'pk', 'gross_salary', 'net_salary', 'total_deductions',
        'advance_deduction', 'salary_structure', 'status').order_by('pk')),
    'bonuses': list(Bonus.objects.values_list('pk', 'status', 'amount').order_by('pk')),
}

sr.build_dataset(make_filters())
if rows:
    sr._payslip_detail_payload(rows[0]['id'])
    sr._employee_detail_payload(rows[0]['employee_pk'], make_filters())

after = {
    'payslips': list(Payslip.objects.values_list(
        'pk', 'gross_salary', 'net_salary', 'total_deductions',
        'advance_deduction', 'salary_structure', 'status').order_by('pk')),
    'bonuses': list(Bonus.objects.values_list('pk', 'status', 'amount').order_by('pk')),
}
check('payslip rows are untouched by the report', before['payslips'] == after['payslips'])
check('bonus rows are untouched by the report', before['bonuses'] == after['bonuses'])


# ──────────────────────────────────────────────────────────────────────────────
section('8. Detail payloads build for every row')

if rows:
    failures = []
    for row in rows[:40]:
        try:
            payload = sr._payslip_detail_payload(row['id'])
            if not payload or payload['kind'] != 'payslip':
                failures.append(f"{row['payslip_number']}: empty payload")
        except Exception as exc:                              # noqa: BLE001
            failures.append(f"{row['payslip_number']}: {exc!r}")
    check('payslip drawer payload builds for every sampled row', not failures, str(failures[:3]))

    emp_failures = []
    for emp_pk in list({r['employee_pk'] for r in rows})[:20]:
        try:
            payload = sr._employee_detail_payload(emp_pk, make_filters())
            if not payload or payload['kind'] != 'employee':
                emp_failures.append(f'{emp_pk}: empty payload')
        except Exception as exc:                              # noqa: BLE001
            emp_failures.append(f'{emp_pk}: {exc!r}')
    check('employee drawer payload builds for every sampled employee',
          not emp_failures, str(emp_failures[:3]))

check('missing payslip id yields None, not an exception',
      sr._payslip_detail_payload(987654321) is None)
check('missing employee id yields None, not an exception',
      sr._employee_detail_payload(987654321, make_filters()) is None)


# ──────────────────────────────────────────────────────────────────────────────
section('9. CSV export matches the on-screen dataset')

stamp = '00000000-0000'
csv_payslips = sr._export_payslips(dataset, dataset['rows'], stamp)
body = csv_payslips.content.decode('utf-8-sig')
lines = [ln for ln in body.splitlines() if ln.strip()]
# header + one line per row + TOTAL line
check(
    'payslip CSV has a line per row plus header and total',
    len(lines) == len(rows) + 2,
    f'{len(lines)} lines for {len(rows)} rows',
)
check('payslip CSV is an attachment',
      'attachment;' in csv_payslips['Content-Disposition'])
check('payslip CSV TOTAL row carries the net total',
      f"{totals['net']:,.2f}" in lines[-1], lines[-1][:120])

csv_emp = sr._export_employees(dataset, dataset['employee_rows'], stamp)
emp_lines = [ln for ln in csv_emp.content.decode('utf-8-sig').splitlines() if ln.strip()]
check(
    'employee CSV has a line per employee plus header and total',
    len(emp_lines) == len(emp_rows) + 2,
    f'{len(emp_lines)} lines for {len(emp_rows)} employees',
)


# ──────────────────────────────────────────────────────────────────────────────
section('10. The page renders end-to-end over HTTP')

User = get_user_model()
admin = User.objects.filter(is_superuser=True).first()
http_client = None   # set below, once we know a superuser exists
if admin is None:
    print('  (no superuser in this database — HTTP checks skipped)')
else:
    # The Django test runner normally whitelists this host for us; a standalone
    # script has to do it itself or every request comes back 400.
    from django.conf import settings as dj_settings
    if 'testserver' not in dj_settings.ALLOWED_HOSTS:
        dj_settings.ALLOWED_HOSTS = list(dj_settings.ALLOWED_HOSTS) + ['testserver']

    client = Client()
    client.force_login(admin)
    http_client = client

    urls = [
        '/hrm/reports/salary/',
        '/hrm/reports/salary/?view=employees',
        '/hrm/reports/salary/?per_page=10&page=2',
        '/hrm/reports/salary/?status=paid&view=employees',
        '/hrm/reports/salary/?date_from=2020-01-01&date_to=2035-12-31',
        '/hrm/reports/salary/?search=zzz-no-such-employee-zzz',
        '/hrm/reports/salary/?page=99999',            # out-of-range page
        '/hrm/reports/salary/?per_page=abc&view=nope',
    ]
    for url in urls:
        resp = client.get(url)
        check(f'GET {url} -> 200', resp.status_code == 200, f'got {resp.status_code}')

    export = client.get('/hrm/reports/salary/?export=csv')
    check('CSV export responds as text/csv',
          export.status_code == 200 and 'text/csv' in export['Content-Type'],
          f"{export.status_code} {export.get('Content-Type')}")

    export_emp = client.get('/hrm/reports/salary/?export=csv&view=employees')
    check('employee CSV export responds as text/csv',
          export_emp.status_code == 200 and 'text/csv' in export_emp['Content-Type'])

    detail_bad = client.get('/hrm/reports/salary/detail/?type=payslip&id=abc')
    check('detail endpoint rejects a non-numeric id with 400',
          detail_bad.status_code == 400, f'got {detail_bad.status_code}')

    detail_missing = client.get('/hrm/reports/salary/detail/?type=payslip&id=987654321')
    check('detail endpoint answers 404 for an unknown id',
          detail_missing.status_code == 404, f'got {detail_missing.status_code}')

    detail_kind = client.get('/hrm/reports/salary/detail/?type=wat&id=1')
    check('detail endpoint rejects an unknown type with 400',
          detail_kind.status_code == 400, f'got {detail_kind.status_code}')

    if rows:
        ok_detail = client.get(
            f"/hrm/reports/salary/detail/?type=payslip&id={rows[0]['id']}")
        payload = ok_detail.json()
        check('detail endpoint returns a payslip payload',
              ok_detail.status_code == 200 and payload.get('success')
              and payload['data']['kind'] == 'payslip',
              str(payload)[:160])

        ok_emp = client.get(
            f"/hrm/reports/salary/detail/?type=employee&id={rows[0]['employee_pk']}")
        emp_payload = ok_emp.json()
        check('detail endpoint returns an employee payload',
              ok_emp.status_code == 200 and emp_payload.get('success')
              and emp_payload['data']['kind'] == 'employee',
              str(emp_payload)[:160])

    anon = Client()
    login_gate = anon.get('/hrm/reports/salary/')
    check('anonymous access is redirected to login',
          login_gate.status_code in (301, 302), f'got {login_gate.status_code}')


# ──────────────────────────────────────────────────────────────────────────────
section('11. Bonus drift is judged per employee-month, not per payslip')

# Payslip is unique per (run, employee), NOT per month, so an employee can hold
# several payslips for one month. Bonus is keyed by month and the engine folds
# it into whichever payslip it generates first. A naive per-payslip comparison
# flags every sibling that legitimately carries zero.
from collections import Counter                                    # noqa: E402
from django.db import transaction                                  # noqa: E402

groups = Counter((r['employee_pk'], r['period_key']) for r in rows)
multi = [key for key, n in groups.items() if n > 1]
print(f'  ({len(multi)} employee-month(s) hold more than one payslip)')

if not multi:
    print('  (no sibling payslips in this database - drift checks skipped)')
else:
    emp_pk, period_key = multi[0]
    year, month = int(period_key[:4]), int(period_key[5:7])
    siblings = [r for r in rows if (r['employee_pk'], r['period_key']) == (emp_pk, period_key)]
    folded = sum((r['bonus'] for r in siblings), Decimal('0.00'))

    check(
        "siblings agree with their month's bonus total today, so none is flagged",
        not any('folded into' in w for r in siblings for w in r['warnings']),
        str([w for r in siblings for w in r['warnings']]),
    )

    # Narrowing the window to ONE sibling must not invent a discrepancy: the
    # fold total is read from the database, not from the visible rows.
    one_day = siblings[0]['effective_date']
    narrowed = sr.build_dataset(make_filters(date_from=one_day, date_to=one_day))
    narrowed_rows = [r for r in narrowed['rows'] if r['employee_pk'] == emp_pk]
    check(
        'a date filter showing one sibling does not fake a bonus discrepancy',
        bool(narrowed_rows) and not any(
            'folded into' in w for r in narrowed_rows for w in r['warnings']),
        str([w for r in narrowed_rows for w in r['warnings']]),
    )

    # Now introduce a real discrepancy and confirm it IS caught - then roll back.
    PROBE_REMARK = 'temporary probe - rolled back'
    try:
        with transaction.atomic():
            Bonus.objects.create(
                employee_id=emp_pk, bonus_type='other',
                amount=Decimal('500.00'), month=month, year=year,
                status='approved', remarks=PROBE_REMARK,
            )
            probed = sr.build_dataset(make_filters())
            probe_rows = [
                r for r in probed['rows']
                if (r['employee_pk'], r['period_key']) == (emp_pk, period_key)
            ]
            drift = [
                [w for w in r['warnings'] if 'folded into' in w] for r in probe_rows
            ]
            check('a genuine bonus discrepancy flags every payslip of that month',
                  len(drift) == len(siblings) and all(len(d) == 1 for d in drift),
                  str(drift))
            expected_total = folded + Decimal('500.00')
            check('the discrepancy is reported against the month, not one payslip',
                  all(f'{expected_total:,.2f}' in d[0] and f'{folded:,.2f}' in d[0]
                      for d in drift if d),
                  str(drift[:1]))

            narrowed_probe = sr.build_dataset(
                make_filters(date_from=one_day, date_to=one_day))
            narrowed_probe_rows = [
                r for r in narrowed_probe['rows'] if r['employee_pk'] == emp_pk]
            check('the discrepancy is still caught when filtered to one sibling',
                  any('folded into' in w
                      for r in narrowed_probe_rows for w in r['warnings']))

            # THE regression case. Fold the new bonus into ONE sibling, exactly
            # as the payroll engine does. The month now reconciles, so nothing
            # should be flagged -- a per-payslip check would flag the other
            # siblings for carrying zero, which is precisely correct for them.
            carrier = Payslip.objects.get(pk=siblings[0]['id'])
            structure = dict(carrier.salary_structure or {})
            structure['bonus_total_included'] = str(folded + Decimal('500.00'))
            carrier.salary_structure = structure
            carrier.save(update_fields=['salary_structure', 'updated_at'])

            settled = sr.build_dataset(make_filters())
            settled_rows = [
                r for r in settled['rows']
                if (r['employee_pk'], r['period_key']) == (emp_pk, period_key)
            ]
            stray = [w for r in settled_rows for w in r['warnings'] if 'folded into' in w]
            check('a bonus folded into one sibling clears the whole month',
                  len(settled_rows) == len(siblings) and not stray, str(stray))

            settled_narrow = sr.build_dataset(
                make_filters(date_from=one_day, date_to=one_day))
            stray_narrow = [
                w for r in settled_narrow['rows'] if r['employee_pk'] == emp_pk
                for w in r['warnings'] if 'folded into' in w
            ]
            check("...and stays clear when the filter hides the sibling payslips",
                  not stray_narrow, str(stray_narrow))
            raise RuntimeError('rollback')
    except RuntimeError as exc:
        if str(exc) != 'rollback':
            raise

    check('the probe bonus was rolled back',
          not Bonus.objects.filter(remarks=PROBE_REMARK).exists())


# ──────────────────────────────────────────────────────────────────────────────
section('12. A filtered dropdown never silently drops the filter')

from hrm.models import Branch, Department, PayrollRun                # noqa: E402

# A run older than the dropdown window, or an inactive department, must still
# render as the selected option - otherwise the next "Apply" resets it.
old_run = PayrollRun.objects.order_by('pay_date', 'created_at').first()
if old_run is not None:
    narrow = sr._ensure_selected([], old_run.id, PayrollRun)
    check('a run outside the dropdown window is added back',
          any(o.id == old_run.id for o in narrow))
    already = list(PayrollRun.objects.filter(pk=old_run.pk))
    check('an option already present is not duplicated',
          len(sr._ensure_selected(already, old_run.id, PayrollRun)) == 1)

check('a filter id that no longer exists is ignored, not crashed on',
      sr._ensure_selected([], 987654321, Department) == [])
check('no selection leaves the option list untouched',
      sr._ensure_selected([], None, Branch) == [])

if http_client is not None:
    inactive_dept = Department.objects.exclude(status='active').first()
    probe_dept = inactive_dept or Department.objects.first()
    if probe_dept is not None:
        html = http_client.get(
            f'/hrm/reports/salary/?department={probe_dept.id}').content.decode()
        check('the selected department renders as a chosen <option>',
              f'value="{probe_dept.id}" selected' in html,
              'selected option missing from the department dropdown')

    old_run_page = None
    if old_run is not None:
        old_run_page = http_client.get(
            f'/hrm/reports/salary/?run={old_run.id}').content.decode()
        check('the selected payroll run renders as a chosen <option>',
              f'value="{old_run.id}" selected' in old_run_page,
              'selected run missing from the run dropdown')


# ──────────────────────────────────────────────────────────────────────────────
section('13. Drawer totals match the row the user clicked')

if rows:
    # With a search term active, the employee drawer must aggregate exactly the
    # payslips its summary row aggregated - no wider, no narrower.
    term = rows[0]['employee_name']
    searched = sr.build_dataset(make_filters(search=term))
    mismatch = None
    for summary_row in searched['employee_rows'][:5]:
        payload = sr._employee_detail_payload(
            summary_row['employee_pk'], make_filters(search=term))
        if payload is None:
            mismatch = f"no payload for employee {summary_row['employee_pk']}"
            break
        drawer_net = next(
            (f['value'] for f in payload['figures'] if f['label'] == 'Net salary'), None)
        if drawer_net != f"{summary_row['net']:,.2f}":
            mismatch = f"drawer {drawer_net} vs row {summary_row['net']:,.2f}"
            break
    check('drawer net equals the summary row net under a search filter',
          mismatch is None, mismatch or '')


# ──────────────────────────────────────────────────────────────────────────────
section('14. Sorting reorders rows without changing what they add up to')

for view, table in (('payslips', sr.PAYSLIP_SORTS), ('employees', sr.EMPLOYEE_SORTS)):
    listing_source = 'rows' if view == 'payslips' else 'employee_rows'
    base = sr.build_dataset(make_filters(view=view))
    unsorted_rows = base[listing_source]

    for key in table:
        for direction in ('asc', 'desc'):
            ordered = sr.sort_listing(list(unsorted_rows), view, key, direction)
            _label, keyfunc, _default = table[key]
            keys = [keyfunc(r) for r in ordered]
            if keys != sorted(keys, reverse=(direction == 'desc')):
                check(f'{view}: sort by {key} {direction} is ordered', False, str(keys[:4]))
                break
            if len(ordered) != len(unsorted_rows):
                check(f'{view}: sort by {key} {direction} keeps every row', False,
                      f'{len(ordered)} vs {len(unsorted_rows)}')
                break
        else:
            continue
        break
    else:
        check(f'{view}: every sort key orders correctly in both directions', True)

    # Sorting must be a permutation — totals cannot move.
    shuffled = sr.sort_listing(list(unsorted_rows), view, 'employee', 'asc')
    check(f'{view}: sorting preserves the row set exactly',
          sorted(id(r) for r in shuffled) == sorted(id(r) for r in unsorted_rows))
    if view == 'payslips':
        check('sorting does not change the totals',
              sr._summarise(shuffled)['net'] == base['totals']['net'])

check('an unknown sort key falls back to the view default',
      sr.parse_filters(rf.get('/?sort=drop_table&view=payslips'))['sort']
      == sr.DEFAULT_SORT['payslips'])
check('an unknown direction falls back to the column default',
      sr.parse_filters(rf.get('/?sort=employee&dir=sideways'))['dir'] == 'asc')
other_view = sr.parse_filters(rf.get('/?view=payslips&sort=current_salary&dir=asc'))
check("a sort key from the other view resolves to this view's default",
      other_view['sort'] == sr.DEFAULT_SORT['payslips'])
check("...and takes that default column direction, not the stale one",
      other_view['dir'] == sr.PAYSLIP_SORTS[sr.DEFAULT_SORT['payslips']][2])
check('the view switch does not carry sort across views',
      'sort=' not in sr.filter_querystring(
          sr.parse_filters(rf.get('/?view=payslips&sort=net')), view='', sort='', dir=''))
check('sort defaults differ per view',
      sr.parse_filters(rf.get('/?view=employees'))['sort'] == 'net'
      and sr.parse_filters(rf.get('/?view=payslips'))['sort'] == 'period')

# Header links: clicking the active column flips it, others start at their own
# natural direction.
active = sr.parse_filters(rf.get('/?view=payslips&sort=net&dir=desc'))
links = sr.sort_links(active)
check('the active column is marked active', links['net']['active'])
check('clicking the active column flips its direction',
      'dir=asc' in links['net']['href'])
check('clicking another money column starts descending',
      'dir=desc' in links['gross']['href'] and not links['gross']['active'])
check('clicking a text column starts ascending',
      'dir=asc' in links['employee']['href'])
check('sort links carry the other filters along',
      'view=payslips' in links['net']['href'])
check('every sortable column has a link',
      set(links) == set(sr.PAYSLIP_SORTS))

# Paging and exporting must stay in the chosen order.
paged = sr.filter_querystring(active, page=3)
check('the querystring keeps sort and direction for paging',
      'sort=net' in paged and 'dir=desc' in paged)

if http_client is not None:
    for url in ['/hrm/reports/salary/?sort=net&dir=asc',
                '/hrm/reports/salary/?sort=employee&dir=desc',
                '/hrm/reports/salary/?view=employees&sort=payslips&dir=desc',
                '/hrm/reports/salary/?sort=&dir=',
                '/hrm/reports/salary/?sort=../../etc&dir=%00']:
        code = http_client.get(url).status_code
        check(f'GET {url} -> 200', code == 200, f'got {code}')

    export = http_client.get('/hrm/reports/salary/?export=csv&sort=net&dir=asc')
    lines = [ln for ln in export.content.decode('utf-8-sig').splitlines() if ln.strip()]
    body_nets = []
    for line in lines[1:-1]:
        parts = list(csv_module.reader([line]))[0]
        if len(parts) > 15 and parts[15]:
            try:
                body_nets.append(float(parts[15].replace(',', '')))
            except ValueError:
                pass
    check('the CSV export comes out in the sorted order',
          body_nets == sorted(body_nets), str(body_nets[:5]))


# ──────────────────────────────────────────────────────────────────────────────
print('\n' + '=' * 66)
print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
if FAILED:
    print('\n  Failures:')
    for item in FAILED:
        print(f'   - {item}')
print('=' * 66)
sys.exit(1 if FAILED else 0)
