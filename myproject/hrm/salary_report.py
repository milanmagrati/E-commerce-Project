"""Salary Report — a read-only payroll analytics page.

Every figure rendered by this module comes from ONE dataset built by
:func:`build_dataset`: the KPI strip, the payslip table, the employee summary,
the charts, the CSV export and the row-detail drawer all read the same list of
rows. That is deliberate — a report whose header totals are computed by a
separate query from its table rows is a report that eventually disagrees with
itself.

Nothing here writes to the database. Payroll healing/sync
(``hrm.views._sync_bonus_to_payslip``, ``heal_payslip_bonus_snapshot``) belongs
to the payslip pages; running it from a report would mutate payroll as a side
effect of *looking* at it. So when a payslip's stored totals disagree with its
own snapshot, this page **flags** the row instead of quietly repairing it.

Money model (mirrors ``hrm.views.generate_payslips`` exactly)::

    net_basic        = basic_salary - absent_deduction
    gross_salary     = net_basic + weekend/holiday/OT pay + allowances
                       + bonus + manual earning adjustments
    total_deductions = salary-component deductions + manual deduction adjustments
    net_salary       = max(gross_salary - total_deductions - advance_deduction, 0)

Consequences worth remembering when adding a column: ``absent_deduction`` and
``bonus`` are *constituents of* gross, never separate addends. Summing
``gross + bonus``, or recomputing ``net`` as
``gross - deductions - advance - absent``, would double-count.
"""

import calendar
import csv
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import DateField, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse

from dashboard.decimal_utils import safe_decimal

from .models import (
    AdvancePayment, Bonus, Branch, Department, Employee, EmployeeSalary,
    PayrollRun, Payslip, PayslipAdjustment, PayslipAuditLog,
)

logger = logging.getLogger('hrm')

ZERO = Decimal('0.00')
CENTS = Decimal('0.01')
PER_PAGE_CHOICES = (10, 25, 50, 100)
DEFAULT_PER_PAGE = 25
VIEW_CHOICES = ('payslips', 'employees')

# How many payroll runs the run dropdown offers. A currently-filtered run is
# always added on top of this, even if it is older — see `_ensure_selected`.
RUN_CHOICE_LIMIT = 60

# Payslip status meaning "the money has actually left the company".
PAID_STATUSES = ('paid',)

# Advance statuses representing a balance the employee still owes.
OUTSTANDING_ADVANCE_STATUSES = ('disbursed', 'repaying')

# Bonus rows that are (or will be) folded into a payslip.
COUNTED_BONUS_STATUSES = ('approved', 'paid')

# An unfiltered filter dict, for lookups addressed by primary key. Keeping the
# shape identical to `parse_filters()` output means `_payslip_queryset` has
# exactly one contract to satisfy.
NO_FILTERS = {
    'search': '', 'date_from': None, 'date_to': None, 'department': None,
    'branch': None, 'run': None, 'status': '', 'view': 'payslips',
    'per_page': DEFAULT_PER_PAGE, 'sort': 'period', 'dir': 'desc',
}


# ──────────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────────

def money(value):
    """Coerce anything (None, str, float, Decimal, corrupt DB value) to money.

    Uses the project-wide ``safe_decimal`` so one corrupted row shows as 0.00
    instead of taking the whole report down with ``decimal.InvalidOperation``.
    ``max_digits=14`` matches the widest payroll column (PayrollRun.total_amount).
    """
    return safe_decimal(value, max_digits=14, decimal_places=2).quantize(CENTS)


def _parse_date(value):
    """Parse a ``YYYY-MM-DD`` GET parameter, returning None when unusable."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), '%Y-%m-%d').date()
    except (ValueError, AttributeError):
        return None


def _int_or_none(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_dict(value):
    """``Payslip.salary_structure`` as a dict, whatever shape it comes back in.

    JSONField normally decodes for us, but rows written by older code paths (or
    a raw SQL fix-up) can surface as a JSON string, and a legacy row can hold a
    list or ``None``. Anything that is not a mapping becomes ``{}``.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _component_list(struct, key):
    """Normalise a snapshot earnings/deductions list into ``[{name, amount}]``."""
    raw = struct.get(key)
    if not isinstance(raw, list):
        return []
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        items.append({
            'name': str(entry.get('name') or 'Component'),
            'amount': money(entry.get('amount')),
        })
    return items


def _period_bounds(pay_period_start, pay_period_end, month, year, fallback):
    """Resolve a payroll run's pay period to a concrete (start, end) pair.

    All four of ``pay_period_start/end/month/year`` are nullable on PayrollRun,
    so resolution walks from most to least specific and finally leans on the
    payslip's own effective date. Period drives month grouping, the bonus
    lookup key and the period label, so ``(None, None)`` is a last resort.
    """
    if pay_period_start and pay_period_end:
        return pay_period_start, pay_period_end
    if year and month and 1 <= month <= 12:
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    anchor = pay_period_start or pay_period_end or fallback
    if anchor:
        last_day = calendar.monthrange(anchor.year, anchor.month)[1]
        return date(anchor.year, anchor.month, 1), date(anchor.year, anchor.month, last_day)
    return None, None


def _period_label(start, end):
    if not start or not end:
        return '—'
    if start.year == end.year and start.month == end.month:
        return start.strftime('%b %Y')
    if start.year == end.year:
        return f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
    return f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"


def _month_label(period_key):
    """``'2026-08'`` → ``'Aug 2026'``. Anything unparseable becomes 'Undated'."""
    try:
        year, month = period_key.split('-')
        return f'{calendar.month_abbr[int(month)]} {int(year)}'
    except (ValueError, AttributeError, IndexError, KeyError):
        return 'Undated'


def _initials(name):
    parts = [p for p in str(name or '').split() if p]
    if not parts:
        return '?'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _fmt_date(value):
    return value.strftime('%d %b %Y') if value else ''


def _fmt_money(value):
    """Thousands-separated 2dp string — used by the CSV and the JSON drawer."""
    return f'{money(value):,.2f}'


# ──────────────────────────────────────────────────────────────────────────────
# Sorting
#
# Sorting happens in Python, over the already-materialised rows, for the same
# reason the dataset is materialised at all: the sorted table, the KPI totals
# and the CSV all come from one list, so ordering can never change what the
# numbers add up to. Each entry is (column label, key function, default
# direction) — the default direction is what a *first* click on that column
# gives you, which for money should be "biggest first".
# ──────────────────────────────────────────────────────────────────────────────

def _text(value):
    return str(value or '').lower()


PAYSLIP_SORTS = {
    'employee':   ('Employee',   lambda r: (_text(r['employee_name']), r['id']), 'asc'),
    'period':     ('Period',     lambda r: (r['effective_date'] or date.min, r['id']), 'desc'),
    'basic':      ('Basic',      lambda r: (r['basic'], r['id']), 'desc'),
    'bonus':      ('Bonus',      lambda r: (r['bonus'], r['id']), 'desc'),
    'gross':      ('Gross',      lambda r: (r['gross'], r['id']), 'desc'),
    'deductions': ('Deductions', lambda r: (r['deductions'], r['id']), 'desc'),
    'advance':    ('Advance',    lambda r: (r['advance'], r['id']), 'desc'),
    'net':        ('Net salary', lambda r: (r['net'], r['id']), 'desc'),
    'status':     ('Status',     lambda r: (_text(r['status_label']), r['id']), 'asc'),
}

EMPLOYEE_SORTS = {
    'employee':       ('Employee',       lambda r: (_text(r['employee_name']), r['employee_pk']), 'asc'),
    'current_salary': ('Current salary', lambda r: (r['current_salary'], r['employee_pk']), 'desc'),
    'payslips':       ('Payslips',       lambda r: (r['payslips'], r['employee_pk']), 'desc'),
    'bonus':          ('Bonus',          lambda r: (r['bonus'], r['employee_pk']), 'desc'),
    'gross':          ('Gross',          lambda r: (r['gross'], r['employee_pk']), 'desc'),
    'deductions':     ('Deductions',     lambda r: (r['deductions'], r['employee_pk']), 'desc'),
    'advance':        ('Advance',        lambda r: (r['advance'], r['employee_pk']), 'desc'),
    'net':            ('Net salary',     lambda r: (r['net'], r['employee_pk']), 'desc'),
    'paid':           ('Paid status',    lambda r: (r['net_paid'], r['employee_pk']), 'desc'),
}

DEFAULT_SORT = {'payslips': 'period', 'employees': 'net'}


def sort_table(view):
    """The sort definitions that apply to a view."""
    return EMPLOYEE_SORTS if view == 'employees' else PAYSLIP_SORTS


def sort_listing(rows, view, sort, direction):
    """Order the listing. Unknown keys fall back to the view's default."""
    table = sort_table(view)
    _label, keyfunc, _default = table.get(sort) or table[DEFAULT_SORT[view]]
    return sorted(rows, key=keyfunc, reverse=(direction == 'desc'))


def sort_links(filters):
    """Per-column header links: where each click goes, and which arrow to draw.

    Clicking the active column flips its direction; clicking any other column
    starts it at that column's natural direction.
    """
    links = {}
    for key, (label, _keyfunc, default_dir) in sort_table(filters['view']).items():
        active = filters['sort'] == key
        if active:
            next_dir = 'asc' if filters['dir'] == 'desc' else 'desc'
        else:
            next_dir = default_dir
        links[key] = {
            'label': label,
            'href': '?' + filter_querystring(filters, sort=key, dir=next_dir),
            'active': active,
            'dir': filters['dir'] if active else '',
        }
    return links


# ──────────────────────────────────────────────────────────────────────────────
# Filters
# ──────────────────────────────────────────────────────────────────────────────

def parse_filters(request):
    """Read every GET parameter this report understands, defensively.

    Bad input never raises — it falls back to the default. The returned dict is
    the only filter representation used downstream, so the page, the export and
    the drawer can never interpret the same URL differently.
    """
    get = request.GET

    view = (get.get('view') or 'payslips').strip().lower()
    if view not in VIEW_CHOICES:
        view = 'payslips'

    per_page = _int_or_none(get.get('per_page'))
    if per_page not in PER_PAGE_CHOICES:
        per_page = DEFAULT_PER_PAGE

    date_from = _parse_date(get.get('date_from'))
    date_to = _parse_date(get.get('date_to'))
    # A reversed range silently returns nothing, which reads as "no data" rather
    # than "you typed the dates backwards". Swap instead.
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    status = (get.get('status') or '').strip()
    if status not in dict(Payslip.STATUS_CHOICES):
        status = ''

    # Sort keys are view-specific, so an unknown key — or one carried over from
    # the other view by the view switch — resolves to that view's default
    # rather than erroring or silently ordering by nothing.
    table = sort_table(view)
    sort = (get.get('sort') or '').strip().lower()
    sort_is_known = sort in table
    if not sort_is_known:
        sort = DEFAULT_SORT[view]
    direction = (get.get('dir') or '').strip().lower()
    if direction not in ('asc', 'desc') or not sort_is_known:
        direction = table[sort][2]

    return {
        'search': (get.get('search') or '').strip(),
        'date_from': date_from,
        'date_to': date_to,
        'department': _int_or_none(get.get('department')),
        'branch': _int_or_none(get.get('branch')),
        'run': _int_or_none(get.get('run')),
        'status': status,
        'view': view,
        'per_page': per_page,
        'sort': sort,
        'dir': direction,
    }


def filter_querystring(filters, **overrides):
    """Rebuild the filter querystring (minus ``page``) for links and exports."""
    parts = {
        'search': filters['search'],
        'date_from': filters['date_from'].isoformat() if filters['date_from'] else '',
        'date_to': filters['date_to'].isoformat() if filters['date_to'] else '',
        'department': filters['department'] or '',
        'branch': filters['branch'] or '',
        'run': filters['run'] or '',
        'status': filters['status'],
        'view': filters['view'],
        'per_page': filters['per_page'],
        'sort': filters['sort'],
        'dir': filters['dir'],
    }
    parts.update(overrides)
    return urlencode({k: v for k, v in parts.items() if v not in ('', None)})


def _ensure_selected(options, selected_id, model):
    """Guarantee the active filter's option is present in its dropdown.

    The dropdowns are deliberately narrow — only *active* departments/branches,
    only the most recent runs — but a bookmarked URL, a shared link, or simply
    an older run can carry an id that falls outside that window. Without this,
    the `<select>` renders with nothing selected and the user's next "Apply
    filters" silently drops a filter they never touched, changing the numbers
    on screen for no visible reason.
    """
    if not selected_id or any(option.id == selected_id for option in options):
        return options
    missing = model.objects.filter(pk=selected_id).first()
    if missing is not None:
        options.append(missing)
    return options


def active_filter_chips(filters, departments, branches, runs):
    """Human-readable descriptions of what is currently narrowing the report."""
    chips = []
    if filters['search']:
        chips.append(('search', f"Search: {filters['search']}"))
    if filters['date_from'] and filters['date_to']:
        chips.append(('date', f"{_fmt_date(filters['date_from'])} → {_fmt_date(filters['date_to'])}"))
    elif filters['date_from']:
        chips.append(('date', f"From {_fmt_date(filters['date_from'])}"))
    elif filters['date_to']:
        chips.append(('date', f"Until {_fmt_date(filters['date_to'])}"))
    if filters['department']:
        name = next((d.name for d in departments if d.id == filters['department']), None)
        chips.append(('department', f"Department: {name or filters['department']}"))
    if filters['branch']:
        name = next((b.name for b in branches if b.id == filters['branch']), None)
        chips.append(('branch', f"Branch: {name or filters['branch']}"))
    if filters['run']:
        name = next((r.title for r in runs if r.id == filters['run']), None)
        chips.append(('run', f"Run: {name or filters['run']}"))
    if filters['status']:
        chips.append(('status', f"Status: {dict(Payslip.STATUS_CHOICES)[filters['status']]}"))
    return chips


# ──────────────────────────────────────────────────────────────────────────────
# Dataset
# ──────────────────────────────────────────────────────────────────────────────

def _payslip_queryset(filters):
    """Filtered, annotated payslip queryset — trashed payslips excluded.

    ``effective_date`` collapses the nullable date sources on a payroll run into
    one sortable/filterable date, so the range filter can't drop a payslip just
    because its run was created without a pay date.
    """
    qs = (
        Payslip.objects
        .filter(is_deleted=False)
        .annotate(
            effective_date=Coalesce(
                'payroll_run__pay_date',
                'payroll_run__pay_period_end',
                'generated_on',
                TruncDate('created_at'),
                output_field=DateField(),
            )
        )
    )

    if filters['search']:
        term = filters['search']
        qs = qs.filter(
            Q(employee__full_name__icontains=term)
            | Q(employee__employee_id__icontains=term)
            | Q(employee__employee_code__icontains=term)
            | Q(payslip_number__icontains=term)
        )
    if filters['department']:
        qs = qs.filter(employee__department_id=filters['department'])
    if filters['branch']:
        qs = qs.filter(employee__branch_id=filters['branch'])
    if filters['run']:
        qs = qs.filter(payroll_run_id=filters['run'])
    if filters['status']:
        qs = qs.filter(status=filters['status'])
    if filters['date_from']:
        qs = qs.filter(effective_date__gte=filters['date_from'])
    if filters['date_to']:
        qs = qs.filter(effective_date__lte=filters['date_to'])

    return qs


def _bonus_map(employee_ids):
    """``{(employee_id, year, month): Decimal}`` of approved/paid bonuses.

    Used only to detect drift against each payslip's own snapshot — the amount
    actually inside gross always comes from the snapshot.
    """
    if not employee_ids:
        return {}
    rows = (
        Bonus.objects
        .filter(employee_id__in=employee_ids, status__in=COUNTED_BONUS_STATUSES)
        .values('employee_id', 'year', 'month')
        .annotate(total=Sum('amount'))
    )
    return {(r['employee_id'], r['year'], r['month']): money(r['total']) for r in rows}


def _advance_map(employee_ids, date_from, date_to):
    """Per-employee advance facts: outstanding balance and amount taken in range."""
    if not employee_ids:
        return {}
    out = {}
    rows = AdvancePayment.objects.filter(employee_id__in=employee_ids).values(
        'employee_id', 'status', 'amount', 'amount_repaid', 'payment_date', 'created_at',
    )
    for row in rows:
        bucket = out.setdefault(row['employee_id'], {
            'outstanding': ZERO, 'taken_in_period': ZERO,
        })
        amount = money(row['amount'])
        repaid = money(row['amount_repaid'])
        if row['status'] in OUTSTANDING_ADVANCE_STATUSES:
            bucket['outstanding'] += max(amount - repaid, ZERO)
        if row['status'] != 'rejected':
            taken_on = row['payment_date'] or (
                row['created_at'].date() if row['created_at'] else None
            )
            in_range = taken_on is not None
            if in_range and date_from and taken_on < date_from:
                in_range = False
            if in_range and date_to and taken_on > date_to:
                in_range = False
            if in_range:
                bucket['taken_in_period'] += amount
    return out


def _current_salary_map(employee_ids):
    """Latest active EmployeeSalary basic pay per employee."""
    if not employee_ids:
        return {}
    out = {}
    rows = (
        EmployeeSalary.objects
        .filter(employee_id__in=employee_ids, is_active=True)
        .order_by('employee_id', '-effective_date', '-id')
        .values('employee_id', 'basic_salary', 'effective_date')
    )
    for row in rows:
        # Ordered newest-first within each employee, so the first one wins.
        out.setdefault(row['employee_id'], {
            'basic': money(row['basic_salary']),
            'effective_date': row['effective_date'],
        })
    return out


# The exact column set `_build_row` reads. Kept in one place so every caller
# (page, export, drawer) hydrates rows from an identical projection — a missing
# key here would surface as a KeyError deep inside `_build_row`.
ROW_FIELDS = (
    'id', 'payslip_number', 'status', 'paid_date', 'generated_on',
    'basic_salary', 'gross_salary', 'total_deductions',
    'advance_deduction', 'absent_deduction', 'net_salary',
    'salary_structure', 'effective_date',
    'employee_id',
    'employee__full_name', 'employee__employee_id', 'employee__employee_code',
    'employee__department__name', 'employee__designation__name',
    'employee__branch__name',
    'payroll_run_id', 'payroll_run__title', 'payroll_run__status',
    'payroll_run__pay_date', 'payroll_run__pay_period_start',
    'payroll_run__pay_period_end', 'payroll_run__month', 'payroll_run__year',
)


def _rows_for(queryset):
    """Hydrate an annotated payslip queryset into computed report rows.

    One bonus lookup covers the whole batch, so this stays two queries no
    matter how many payslips come back.
    """
    records = list(
        queryset.values(*ROW_FIELDS)
        .order_by('-effective_date', 'employee__full_name', '-id')
    )
    employee_ids = {r['employee_id'] for r in records}
    bonus_lookup = _bonus_map(employee_ids)
    rows = [_build_row(r, bonus_lookup) for r in records]
    _flag_bonus_drift(rows, _folded_bonus_map(employee_ids))
    return rows


def _folded_bonus_map(employee_ids):
    """``{(employee_id, 'YYYY-MM'): Decimal}`` of bonus actually folded into pay.

    Sums ``bonus_total_included`` over *every* live payslip of these employees,
    not just the ones the current filters show. The comparison in
    `_flag_bonus_drift` is against a whole month's ``Bonus`` rows, so it has to
    see the whole month's payslips — narrowing the date range must not turn a
    healthy month into a false alarm.
    """
    if not employee_ids:
        return {}
    out = {}
    records = Payslip.objects.filter(
        is_deleted=False, employee_id__in=employee_ids,
    ).values(
        'employee_id', 'salary_structure', 'generated_on', 'created_at',
        'payroll_run__pay_date', 'payroll_run__pay_period_start',
        'payroll_run__pay_period_end', 'payroll_run__month', 'payroll_run__year',
    )
    for record in records:
        # Same precedence as the `effective_date` annotation, so a payslip lands
        # in the same month here as it does everywhere else in the report.
        fallback = (
            record['payroll_run__pay_date']
            or record['payroll_run__pay_period_end']
            or record['generated_on']
            or (record['created_at'].date() if record['created_at'] else None)
        )
        start, _end = _period_bounds(
            record['payroll_run__pay_period_start'],
            record['payroll_run__pay_period_end'],
            record['payroll_run__month'],
            record['payroll_run__year'],
            fallback,
        )
        if not start:
            continue
        key = (record['employee_id'], f'{start.year:04d}-{start.month:02d}')
        folded = money(_as_dict(record['salary_structure']).get('bonus_total_included'))
        out[key] = out.get(key, ZERO) + folded
    return out


def _flag_bonus_drift(rows, folded_map):
    """Flag employee-months whose payslips no longer carry the bonuses on record.

    Deliberately a second pass rather than a check inside ``_build_row``.
    ``Payslip`` is unique per (run, employee), not per month, so an employee can
    hold several payslips for one month — weekly and bi-weekly runs exist in
    this data, and re-runs produce them too. ``Bonus`` is keyed by month, and
    the payroll engine folds a month's bonus into whichever payslip it generates
    first. Checking each payslip on its own against the month's total would
    therefore flag every sibling that legitimately carries zero.

    Comparing the month's folded total against the month's ``Bonus`` total is
    the check that actually holds, for one payslip or five. Mutates ``rows`` in
    place, keeping ``has_warning`` in step.
    """
    seen = {}
    for row in rows:
        if not row['period_key']:
            continue
        seen.setdefault((row['employee_pk'], row['period_key']), []).append(row)

    for key, group in seen.items():
        folded = folded_map.get(key, ZERO)
        # Every row in a group shares the same employee+month, so any row's
        # `bonus_live` is that month's total from the Bonus table.
        on_record = group[0]['bonus_live']
        if folded == on_record:
            continue
        message = (
            f'Approved bonuses for this period total {on_record:,.2f}, but '
            f'{folded:,.2f} is folded into this month\'s payslip(s). '
            f'Open the payslip to re-sync.'
        )
        for row in group:
            row['warnings'].append(message)
            row['has_warning'] = True


def _build_row(record, bonus_lookup):
    """Turn one payslip ``values()`` dict into a fully computed report row."""
    struct = _as_dict(record['salary_structure'])

    effective = record['effective_date']
    period_start, period_end = _period_bounds(
        record['payroll_run__pay_period_start'],
        record['payroll_run__pay_period_end'],
        record['payroll_run__month'],
        record['payroll_run__year'],
        effective,
    )

    basic = money(record['basic_salary'])
    absent = money(record['absent_deduction'])
    gross = money(record['gross_salary'])
    deductions = money(record['total_deductions'])
    advance = money(record['advance_deduction'])
    net = money(record['net_salary'])

    # Bonus is already inside `gross`, per the snapshot written at generation.
    bonus = money(struct.get('bonus_total_included'))

    # Live bonus total for this employee+month, to spot slips generated before a
    # later bonus approval. Reported, never auto-applied (see module docstring).
    bonus_live = ZERO
    if period_start:
        bonus_live = bonus_lookup.get(
            (record['employee_id'], period_start.year, period_start.month), ZERO
        )

    # Three quantities, deliberately distinct:
    #   raw          what the arithmetic says, which can go negative
    #   expected_net what the engine stores, since it floors net at zero
    #   unwithheld   the part of the deductions that floor swallowed
    # Without `unwithheld` the KPI strip cannot reconcile: a payslip whose
    # deductions exceed its gross contributes its full deductions to the
    # Deductions total but nothing to Net, so Gross − Deductions − Advance
    # silently undershoots the Net total.
    raw_net = gross - deductions - advance
    expected_net = max(raw_net, ZERO)
    unwithheld = (expected_net - raw_net).quantize(CENTS)
    net_variance = (net - expected_net).quantize(CENTS)

    has_snapshot = bool(struct.get('earnings_list') or struct.get('deductions_list'))

    # Two severities, deliberately kept apart. `warnings` means "this payslip's
    # numbers don't add up and somebody should look" — it drives the row flag
    # and the page banner, so anything merely informational belongs in `notes`
    # instead. A banner that cries wolf on every legacy row gets ignored, and
    # then the one real discrepancy gets ignored with it.
    # Bonus drift is NOT checked here — it is an employee-month question, not a
    # per-payslip one. See `_flag_bonus_drift`, which runs over the whole batch.
    warnings = []
    if unwithheld > ZERO:
        warnings.append(
            f'Deductions ({deductions:,.2f}) plus advance ({advance:,.2f}) exceed '
            f'gross ({gross:,.2f}). Net was floored at zero, so {unwithheld:,.2f} '
            f'was never actually withheld and is still owed.'
        )
    if net_variance != ZERO:
        warnings.append(
            f'Stored net ({net:,.2f}) differs from gross minus deductions minus advance '
            f'({expected_net:,.2f}) by {net_variance:,.2f}.'
        )

    notes = []
    if not has_snapshot:
        notes.append(
            'This payslip predates component snapshots, so no earnings/deductions '
            'breakdown was stored with it. The totals below are still exact.'
        )

    status = record['status']
    return {
        'id': record['id'],
        'payslip_number': record['payslip_number'] or f"#{record['id']}",
        'status': status,
        'status_label': dict(Payslip.STATUS_CHOICES).get(status, str(status).title()),
        'is_paid': status in PAID_STATUSES,
        'paid_date': record['paid_date'],
        'generated_on': record['generated_on'],
        'effective_date': effective,

        'employee_pk': record['employee_id'],
        'employee_name': record['employee__full_name'] or 'Unknown employee',
        'employee_code': record['employee__employee_code'] or record['employee__employee_id'] or '',
        'initials': _initials(record['employee__full_name']),
        'department': record['employee__department__name'] or '—',
        'designation': record['employee__designation__name'] or '—',
        'branch': record['employee__branch__name'] or '—',

        'run_id': record['payroll_run_id'],
        'run_title': record['payroll_run__title'] or 'Payroll run',
        'run_status': dict(PayrollRun.STATUS_CHOICES).get(
            record['payroll_run__status'], str(record['payroll_run__status'] or '').title()),
        'period_start': period_start,
        'period_label': _period_label(period_start, period_end),
        'period_key': f'{period_start.year:04d}-{period_start.month:02d}' if period_start else '',

        'basic': basic,
        'absent_deduction': absent,
        'bonus': bonus,
        'bonus_live': bonus_live,
        'gross': gross,
        'deductions': deductions,
        'advance': advance,
        'net': net,
        'net_variance': net_variance,
        'unwithheld': unwithheld,

        'earnings_list': _component_list(struct, 'earnings_list'),
        'deductions_list': _component_list(struct, 'deductions_list'),
        'advance_breakdown': (
            struct.get('advance_breakdown')
            if isinstance(struct.get('advance_breakdown'), dict) else {}
        ),

        'warnings': warnings,
        'notes': notes,
        'has_warning': bool(warnings),
    }


def _summarise(rows):
    """Fold the row list into the KPI totals. Single pass, no second query."""
    totals = {
        'payslips': len(rows), 'employees': 0,
        'basic': ZERO, 'bonus': ZERO, 'gross': ZERO, 'deductions': ZERO,
        'advance': ZERO, 'absent': ZERO, 'net': ZERO,
        'net_paid': ZERO, 'net_unpaid': ZERO,
        'paid_count': 0, 'unpaid_count': 0, 'flagged': 0,
        'unwithheld': ZERO, 'variance': ZERO,
    }
    seen = set()
    for row in rows:
        seen.add(row['employee_pk'])
        totals['basic'] += row['basic']
        totals['bonus'] += row['bonus']
        totals['gross'] += row['gross']
        totals['deductions'] += row['deductions']
        totals['advance'] += row['advance']
        totals['absent'] += row['absent_deduction']
        totals['net'] += row['net']
        if row['is_paid']:
            totals['net_paid'] += row['net']
            totals['paid_count'] += 1
        else:
            totals['net_unpaid'] += row['net']
            totals['unpaid_count'] += 1
        totals['unwithheld'] += row['unwithheld']
        totals['variance'] += row['net_variance']
        if row['has_warning']:
            totals['flagged'] += 1
    totals['employees'] = len(seen)
    totals['total_withheld'] = totals['deductions'] + totals['advance']
    # The identity the KPI strip is read against:
    #     net = net_base + unwithheld + variance
    # `net_base` is the straight subtraction a reader will do in their head;
    # the other two terms are why it does not land on `net` by itself.
    totals['net_base'] = (
        totals['gross'] - totals['deductions'] - totals['advance']
    ).quantize(CENTS)
    totals['reconciles'] = (
        totals['net_base'] + totals['unwithheld'] + totals['variance'] == totals['net']
    )
    return totals


def _unpaid_employees(filters, exclude_ids):
    """Active employees matching the employee-level filters but with no payslip."""
    qs = Employee.objects.filter(employee_status='active')
    if exclude_ids:
        qs = qs.exclude(id__in=exclude_ids)
    if filters['department']:
        qs = qs.filter(department_id=filters['department'])
    if filters['branch']:
        qs = qs.filter(branch_id=filters['branch'])
    if filters['search']:
        term = filters['search']
        qs = qs.filter(
            Q(full_name__icontains=term)
            | Q(employee_id__icontains=term)
            | Q(employee_code__icontains=term)
        )
    return list(qs.values(
        'id', 'full_name', 'employee_id', 'employee_code',
        'department__name', 'designation__name', 'branch__name',
    ))


def _group_by_employee(rows, padding, advance_info, salary_info):
    """Aggregate rows per employee, padded with employees who were not paid.

    ``padding`` is the (possibly empty) list of zero-payslip employees to append;
    the caller decides whether padding makes sense for the active filters.
    """
    grouped = {}
    for row in rows:
        key = row['employee_pk']
        bucket = grouped.get(key)
        if bucket is None:
            bucket = grouped[key] = {
                'employee_pk': key,
                'employee_name': row['employee_name'],
                'employee_code': row['employee_code'],
                'initials': row['initials'],
                'department': row['department'],
                'designation': row['designation'],
                'branch': row['branch'],
                'payslips': 0,
                'basic': ZERO, 'bonus': ZERO, 'gross': ZERO, 'deductions': ZERO,
                'advance': ZERO, 'absent_deduction': ZERO, 'net': ZERO,
                'net_paid': ZERO, 'net_unpaid': ZERO,
                'last_period': '', 'last_date': None, 'has_warning': False,
            }
        bucket['payslips'] += 1
        for field in ('basic', 'bonus', 'gross', 'deductions', 'advance',
                      'absent_deduction', 'net'):
            bucket[field] += row[field]
        if row['is_paid']:
            bucket['net_paid'] += row['net']
        else:
            bucket['net_unpaid'] += row['net']
        bucket['has_warning'] = bucket['has_warning'] or row['has_warning']

        marker = row['paid_date'] or row['effective_date']
        if marker and (bucket['last_date'] is None or marker > bucket['last_date']):
            bucket['last_date'] = marker
            bucket['last_period'] = row['period_label']

    for emp in padding:
        grouped[emp['id']] = {
            'employee_pk': emp['id'],
            'employee_name': emp['full_name'] or 'Unknown employee',
            'employee_code': emp['employee_code'] or emp['employee_id'] or '',
            'initials': _initials(emp['full_name']),
            'department': emp['department__name'] or '—',
            'designation': emp['designation__name'] or '—',
            'branch': emp['branch__name'] or '—',
            'payslips': 0,
            'basic': ZERO, 'bonus': ZERO, 'gross': ZERO, 'deductions': ZERO,
            'advance': ZERO, 'absent_deduction': ZERO, 'net': ZERO,
            'net_paid': ZERO, 'net_unpaid': ZERO,
            'last_period': '', 'last_date': None, 'has_warning': False,
        }

    for key, bucket in grouped.items():
        adv = advance_info.get(key) or {}
        bucket['advance_outstanding'] = adv.get('outstanding', ZERO)
        bucket['advance_taken'] = adv.get('taken_in_period', ZERO)
        bucket['current_salary'] = (salary_info.get(key) or {}).get('basic', ZERO)

    return sorted(grouped.values(), key=lambda b: (-b['net'], b['employee_name'].lower()))


def _chart_data(rows, employee_rows):
    """Chart series, derived from the very same rows the table renders."""
    by_period = {}
    by_department = {}
    for row in rows:
        key = row['period_key'] or 'zzzz-unknown'
        bucket = by_period.get(key)
        if bucket is None:
            bucket = by_period[key] = {
                # The label is derived from the bucket key, not from the first
                # row that lands here: a month can hold several runs (weekly,
                # bi-weekly, a re-run), and borrowing one of their labels would
                # title the whole month's bar "01 Aug – 15 Aug".
                'label': _month_label(key),
                'gross': ZERO, 'net': ZERO, 'bonus': ZERO,
            }
        bucket['gross'] += row['gross']
        bucket['net'] += row['net']
        bucket['bonus'] += row['bonus']
        by_department[row['department']] = by_department.get(row['department'], ZERO) + row['net']

    # Sorted by the 'YYYY-MM' dict key, so months run chronologically and the
    # 'zzzz-unknown' bucket (a payslip with no resolvable period) sorts last.
    periods = [bucket for _key, bucket in sorted(by_period.items())][-12:]
    departments = sorted(by_department.items(), key=lambda kv: -kv[1])[:8]
    top_earners = [b for b in employee_rows if b['net'] > ZERO][:8]

    return {
        'period_labels': [b['label'] for b in periods],
        'period_gross': [float(b['gross']) for b in periods],
        'period_net': [float(b['net']) for b in periods],
        'period_bonus': [float(b['bonus']) for b in periods],
        'department_labels': [name for name, _ in departments],
        'department_net': [float(value) for _, value in departments],
        'employee_labels': [b['employee_name'] for b in top_earners],
        'employee_net': [float(b['net']) for b in top_earners],
    }


def build_dataset(filters):
    """The single source of truth for this report.

    Returns ``{rows, employee_rows, totals, charts, include_unpaid}``. Rows are
    materialised in Python (rather than left as a lazy queryset) precisely so
    the totals, the paginated table, the grouped view, the charts and the CSV
    can never be computed from different snapshots of the database.
    """
    rows = _rows_for(_payslip_queryset(filters))
    employee_ids = {row['employee_pk'] for row in rows}

    # "Employees in run #12 with no payslip in run #12" is a contradiction, and
    # the same goes for a payslip-status filter — pad only when neither is set.
    include_unpaid = not filters['run'] and not filters['status']
    padding = _unpaid_employees(filters, exclude_ids=employee_ids) if include_unpaid else []

    context_ids = set(employee_ids) | {e['id'] for e in padding}
    advance_info = _advance_map(context_ids, filters['date_from'], filters['date_to'])
    salary_info = _current_salary_map(context_ids)

    employee_rows = _group_by_employee(rows, padding, advance_info, salary_info)

    return {
        'rows': rows,
        'employee_rows': employee_rows,
        'totals': _summarise(rows),
        'charts': _chart_data(rows, employee_rows),
        'include_unpaid': include_unpaid,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CSV export
# ──────────────────────────────────────────────────────────────────────────────

def _csv_response(filename, header, records):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    # BOM so Excel opens UTF-8 (Nepali names, the – glyph) without mangling it.
    response.write(chr(0xFEFF))
    writer = csv.writer(response)
    writer.writerow(header)
    for record in records:
        writer.writerow(record)
    return response


def _export_payslips(dataset, listing, stamp):
    header = [
        'Payslip No', 'Employee', 'Employee Code', 'Department', 'Designation',
        'Branch', 'Payroll Run', 'Pay Period', 'Pay Date', 'Basic Salary',
        'Absent Deduction', 'Bonus', 'Gross Salary', 'Deductions',
        'Advance Deduction', 'Net Salary', 'Payment Status', 'Paid On', 'Notes',
    ]
    rows = []
    for row in listing:
        rows.append([
            row['payslip_number'], row['employee_name'], row['employee_code'],
            row['department'], row['designation'], row['branch'],
            row['run_title'], row['period_label'], _fmt_date(row['effective_date']),
            _fmt_money(row['basic']), _fmt_money(row['absent_deduction']),
            _fmt_money(row['bonus']), _fmt_money(row['gross']),
            _fmt_money(row['deductions']), _fmt_money(row['advance']),
            _fmt_money(row['net']), row['status_label'], _fmt_date(row['paid_date']),
            ' '.join(row['warnings'] + row['notes']),
        ])
    totals = dataset['totals']
    rows.append([])
    rows.append([
        'TOTAL', f"{totals['employees']} employee(s)", '', '', '', '', '', '', '',
        _fmt_money(totals['basic']), _fmt_money(totals['absent']),
        _fmt_money(totals['bonus']), _fmt_money(totals['gross']),
        _fmt_money(totals['deductions']), _fmt_money(totals['advance']),
        _fmt_money(totals['net']), f"{totals['paid_count']} paid", '', '',
    ])
    return _csv_response(f'salary-report-payslips-{stamp}.csv', header, rows)


def _export_employees(dataset, listing, stamp):
    header = [
        'Employee', 'Employee Code', 'Department', 'Designation', 'Branch',
        'Current Monthly Salary', 'Payslips', 'Basic Total', 'Bonus',
        'Gross Salary', 'Deductions', 'Advance Recovered', 'Net Salary',
        'Net Paid', 'Net Pending', 'Advance Taken (period)',
        'Advance Outstanding', 'Last Pay Period',
    ]
    rows = []
    for row in listing:
        rows.append([
            row['employee_name'], row['employee_code'], row['department'],
            row['designation'], row['branch'], _fmt_money(row['current_salary']),
            row['payslips'], _fmt_money(row['basic']), _fmt_money(row['bonus']),
            _fmt_money(row['gross']), _fmt_money(row['deductions']),
            _fmt_money(row['advance']), _fmt_money(row['net']),
            _fmt_money(row['net_paid']), _fmt_money(row['net_unpaid']),
            _fmt_money(row['advance_taken']), _fmt_money(row['advance_outstanding']),
            row['last_period'] or '—',
        ])
    totals = dataset['totals']
    rows.append([])
    rows.append([
        'TOTAL', f'{len(listing)} employee(s)', '', '', '', '',
        totals['payslips'], _fmt_money(totals['basic']), _fmt_money(totals['bonus']),
        _fmt_money(totals['gross']), _fmt_money(totals['deductions']),
        _fmt_money(totals['advance']), _fmt_money(totals['net']),
        _fmt_money(totals['net_paid']), _fmt_money(totals['net_unpaid']), '', '', '',
    ])
    return _csv_response(f'salary-report-employees-{stamp}.csv', header, rows)


# ──────────────────────────────────────────────────────────────────────────────
# Views
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def salary_report(request):
    """The Salary Report page (and its CSV export, via ``?export=csv``)."""
    filters = parse_filters(request)
    dataset = build_dataset(filters)

    # Sort before exporting as well as before paginating, so a CSV comes out in
    # the order the user arranged on screen.
    listing = sort_listing(
        dataset['employee_rows'] if filters['view'] == 'employees' else dataset['rows'],
        filters['view'], filters['sort'], filters['dir'],
    )

    if (request.GET.get('export') or '').strip().lower() == 'csv':
        stamp = datetime.now().strftime('%Y%m%d-%H%M')
        if filters['view'] == 'employees':
            return _export_employees(dataset, listing, stamp)
        return _export_payslips(dataset, listing, stamp)

    paginator = Paginator(listing, filters['per_page'])
    page_obj = paginator.get_page(request.GET.get('page', 1))

    departments = _ensure_selected(
        list(Department.objects.filter(status='active').order_by('name')),
        filters['department'], Department,
    )
    branches = _ensure_selected(
        list(Branch.objects.filter(status='active').order_by('name')),
        filters['branch'], Branch,
    )
    runs = _ensure_selected(
        list(PayrollRun.objects.order_by('-pay_date', '-created_at')
             .only('id', 'title', 'pay_date', 'status')[:RUN_CHOICE_LIMIT]),
        filters['run'], PayrollRun,
    )

    context = {
        'page_title': 'Salary Report',
        'filters': filters,
        'totals': dataset['totals'],
        'page_obj': page_obj,
        'rows': page_obj.object_list,
        'result_count': len(listing),
        'include_unpaid': dataset['include_unpaid'],
        'charts': dataset['charts'],
        'departments': departments,
        'branches': branches,
        'runs': runs,
        'status_choices': Payslip.STATUS_CHOICES,
        'per_page_choices': PER_PAGE_CHOICES,
        'sort_links': sort_links(filters),
        'sort_options': [
            (key, label) for key, (label, _fn, _dir) in sort_table(filters['view']).items()
        ],
        'filter_qs': filter_querystring(filters),
        # Without `view`, so the view switch can append its own without
        # emitting the parameter twice.
        'filter_qs_noview': filter_querystring(filters, view='', sort='', dir=''),
        'export_qs': filter_querystring(filters, export='csv'),
        'chips': active_filter_chips(filters, departments, branches, runs),
        'today': date.today(),
    }
    return render(request, 'hrm/salary_report.html', context)


def _payslip_detail_payload(payslip_id):
    """Row-drawer payload for one payslip. Read-only; no sync, no healing."""
    # Addressed by primary key, so no user filters apply — but it still goes
    # through `_payslip_queryset`, which is what excludes trashed payslips and
    # supplies the `effective_date` annotation `_build_row` depends on.
    rows = _rows_for(_payslip_queryset(NO_FILTERS).filter(pk=payslip_id))
    if not rows:
        return None
    row = rows[0]

    slip = Payslip.objects.filter(pk=payslip_id).select_related(
        'employee', 'finalized_by').first()

    # Bonus rows behind this period's bonus figure.
    bonuses = []
    if row['period_start']:
        for bonus in Bonus.objects.filter(
            employee_id=row['employee_pk'],
            year=row['period_start'].year,
            month=row['period_start'].month,
            status__in=COUNTED_BONUS_STATUSES,
        ).order_by('-amount'):
            bonuses.append({
                'label': bonus.get_bonus_type_display(),
                'amount': _fmt_money(bonus.amount),
                'status': bonus.get_status_display(),
                'remarks': bonus.remarks or '',
            })

    # Which advances this payslip recovered against, and by how much.
    advances = []
    breakdown = row['advance_breakdown']
    if breakdown:
        adv_ids = [pk for pk in (_int_or_none(k) for k in breakdown) if pk]
        by_id = {a.pk: a for a in AdvancePayment.objects.filter(pk__in=adv_ids)}
        for raw_key, raw_amount in breakdown.items():
            adv = by_id.get(_int_or_none(raw_key))
            advances.append({
                'label': adv.advance_number if adv else f'Advance #{raw_key}',
                'recovered': _fmt_money(raw_amount),
                'total': _fmt_money(adv.amount) if adv else '—',
                'outstanding': _fmt_money(max(adv.amount - adv.amount_repaid, ZERO)) if adv else '—',
                'status': adv.get_status_display() if adv else 'Removed',
                'reason': (adv.reason or '')[:160] if adv else '',
            })

    adjustments = [
        {
            'type': adj.get_adjustment_type_display(),
            'category': adj.get_category_display(),
            'description': adj.description,
            'amount': _fmt_money(adj.amount),
            'reason': adj.reason or '',
        }
        for adj in PayslipAdjustment.objects.filter(payslip_id=payslip_id)
    ]

    audit = [
        {
            'action': log.action,
            'detail': log.detail or '',
            'by': getattr(log.performed_by, 'username', '') or 'System',
            'at': log.performed_at.strftime('%d %b %Y, %I:%M %p') if log.performed_at else '',
        }
        for log in PayslipAuditLog.objects.filter(
            payslip_id=payslip_id).select_related('performed_by')[:12]
    ]

    return {
        'kind': 'payslip',
        'title': row['employee_name'],
        'subtitle': f"{row['payslip_number']} · {row['period_label']}",
        'initials': row['initials'],
        'status': row['status_label'],
        'status_key': row['status'],
        # Identity + named amounts for the full-page payslip view, which needs
        # to address individual figures (and link to the employee / run) rather
        # than just render the flat `figures` list the drawer walks.
        'payslip_id': row['id'],
        'payslip_number': row['payslip_number'],
        'period_label': row['period_label'],
        'employee_pk': row['employee_pk'],
        'employee_code': row['employee_code'],
        'run_id': row['run_id'],
        'run_title': row['run_title'],
        'amounts': {
            'basic': _fmt_money(row['basic']),
            'absent': _fmt_money(row['absent_deduction']),
            'bonus': _fmt_money(row['bonus']),
            'gross': _fmt_money(row['gross']),
            'deductions': _fmt_money(row['deductions']),
            'advance': _fmt_money(row['advance']),
            'net': _fmt_money(row['net']),
            'unwithheld': _fmt_money(row['unwithheld']),
        },
        'meta': [
            {'label': 'Employee code', 'value': row['employee_code'] or '—'},
            {'label': 'Department', 'value': row['department']},
            {'label': 'Designation', 'value': row['designation']},
            {'label': 'Branch', 'value': row['branch']},
            {'label': 'Payroll run', 'value': row['run_title']},
            {'label': 'Run status', 'value': row['run_status'] or '—'},
            {'label': 'Pay period', 'value': row['period_label']},
            {'label': 'Pay date', 'value': _fmt_date(row['effective_date']) or '—'},
            {'label': 'Paid on', 'value': _fmt_date(row['paid_date']) or 'Not paid yet'},
            {'label': 'Finalized', 'value': 'Yes' if (slip and slip.is_finalized) else 'No'},
        ],
        'figures': [
            {'label': 'Basic salary', 'value': _fmt_money(row['basic']), 'tone': 'neutral'},
            {'label': 'Absent deduction', 'value': _fmt_money(row['absent_deduction']), 'tone': 'warn'},
            {'label': 'Bonus', 'value': _fmt_money(row['bonus']), 'tone': 'good'},
            {'label': 'Gross salary', 'value': _fmt_money(row['gross']), 'tone': 'good'},
            {'label': 'Deductions', 'value': _fmt_money(row['deductions']), 'tone': 'bad'},
            {'label': 'Advance recovered', 'value': _fmt_money(row['advance']), 'tone': 'bad'},
            {'label': 'Net salary', 'value': _fmt_money(row['net']), 'tone': 'net'},
        ],
        'earnings': [
            {'name': item['name'], 'amount': _fmt_money(item['amount'])}
            for item in row['earnings_list']
        ],
        'deductions': [
            {'name': item['name'], 'amount': _fmt_money(item['amount'])}
            for item in row['deductions_list']
        ],
        'bonuses': bonuses,
        'advances': advances,
        'adjustments': adjustments,
        'audit': audit,
        'warnings': row['warnings'],
        'notes': row['notes'],
        'links': [
            {'label': 'Open payslip',
             'href': reverse('hrm:payslip_detail', args=[row['id']]),
             'icon': 'fa-file-invoice-dollar'},
            {'label': 'Download PDF',
             'href': reverse('hrm:payslip_download', args=[row['id']]),
             'icon': 'fa-download'},
        ],
    }


def _employee_detail_payload(employee_pk, filters):
    """Row-drawer payload for one employee across the filtered window."""
    employee = Employee.objects.select_related(
        'department', 'designation', 'branch').filter(pk=employee_pk).first()
    if employee is None:
        return None

    # Exactly the page's filters — search included — scoped to this employee in
    # SQL rather than by filtering a whole-company dataset in Python. Keeping
    # `search` is what makes the drawer's totals identical to the summary row
    # the user clicked; dropping it would quietly widen the set and show a
    # different net salary than the row it opened from.
    dataset_rows = _rows_for(
        _payslip_queryset(filters).filter(employee_id=employee_pk)
    )
    totals = _summarise(dataset_rows)

    advance_info = _advance_map([employee_pk], filters['date_from'], filters['date_to'])
    salary_info = _current_salary_map([employee_pk])
    outstanding = (advance_info.get(employee_pk) or {}).get('outstanding', ZERO)
    taken = (advance_info.get(employee_pk) or {}).get('taken_in_period', ZERO)
    current_salary = (salary_info.get(employee_pk) or {}).get('basic', ZERO)

    advances = [
        {
            'label': adv.advance_number,
            'recovered': _fmt_money(adv.amount_repaid),
            'total': _fmt_money(adv.amount),
            'outstanding': _fmt_money(max(adv.amount - adv.amount_repaid, ZERO)),
            'status': adv.get_status_display(),
            'reason': (adv.reason or '')[:160],
        }
        for adv in AdvancePayment.objects.filter(
            employee_id=employee_pk).order_by('-created_at')[:10]
    ]

    bonus_qs = Bonus.objects.filter(
        employee_id=employee_pk, status__in=COUNTED_BONUS_STATUSES)
    if filters['date_from']:
        bonus_qs = bonus_qs.filter(
            Q(year__gt=filters['date_from'].year)
            | Q(year=filters['date_from'].year, month__gte=filters['date_from'].month)
        )
    if filters['date_to']:
        bonus_qs = bonus_qs.filter(
            Q(year__lt=filters['date_to'].year)
            | Q(year=filters['date_to'].year, month__lte=filters['date_to'].month)
        )
    bonuses = [
        {
            'label': f'{b.get_bonus_type_display()} · {calendar.month_abbr[b.month]} {b.year}'
                     if 1 <= b.month <= 12 else b.get_bonus_type_display(),
            'amount': _fmt_money(b.amount),
            'status': b.get_status_display(),
            'remarks': b.remarks or '',
        }
        for b in bonus_qs.order_by('-year', '-month')[:12]
    ]

    history = [
        {
            'name': f"{row['payslip_number']} · {row['period_label']}",
            'amount': _fmt_money(row['net']),
            'status': row['status_label'],
            'href': reverse('hrm:payslip_detail', args=[row['id']]),
        }
        for row in dataset_rows[:24]
    ]

    return {
        'kind': 'employee',
        'title': employee.full_name,
        'subtitle': (
            f"{employee.employee_code or employee.employee_id} · "
            f"{employee.designation.name if employee.designation else 'No designation'}"
        ),
        'initials': _initials(employee.full_name),
        'status': employee.get_employee_status_display(),
        'status_key': 'paid' if totals['net_paid'] > ZERO else 'draft',
        'meta': [
            {'label': 'Employee code', 'value': employee.employee_code or employee.employee_id or '—'},
            {'label': 'Department', 'value': employee.department.name if employee.department else '—'},
            {'label': 'Branch', 'value': employee.branch.name if employee.branch else '—'},
            {'label': 'Employment type', 'value': employee.get_employment_type_display()},
            {'label': 'Joined', 'value': _fmt_date(employee.date_of_joining) or '—'},
            {'label': 'Current monthly salary', 'value': _fmt_money(current_salary)},
            {'label': 'Payslips in range', 'value': str(totals['payslips'])},
            {'label': 'Bank account', 'value': employee.account_number or '—'},
        ],
        'figures': [
            {'label': 'Basic total', 'value': _fmt_money(totals['basic']), 'tone': 'neutral'},
            {'label': 'Bonus', 'value': _fmt_money(totals['bonus']), 'tone': 'good'},
            {'label': 'Gross salary', 'value': _fmt_money(totals['gross']), 'tone': 'good'},
            {'label': 'Deductions', 'value': _fmt_money(totals['deductions']), 'tone': 'bad'},
            {'label': 'Advance recovered', 'value': _fmt_money(totals['advance']), 'tone': 'bad'},
            {'label': 'Advance outstanding', 'value': _fmt_money(outstanding), 'tone': 'warn'},
            {'label': 'Advance taken', 'value': _fmt_money(taken), 'tone': 'warn'},
            {'label': 'Net salary', 'value': _fmt_money(totals['net']), 'tone': 'net'},
        ],
        'earnings': history,
        'deductions': [],
        'bonuses': bonuses,
        'advances': advances,
        'adjustments': [],
        'audit': [],
        'warnings': (
            ['Some payslips in this range are flagged — open them to review.']
            if totals['flagged'] else []
        ),
        'notes': [],
        'links': [
            {'label': 'Employee profile',
             'href': reverse('hrm:employee_detail', args=[employee_pk]),
             'icon': 'fa-user'},
            {'label': 'Advance payments',
             'href': reverse('hrm:advance_payment_list'),
             'icon': 'fa-hand-holding-dollar'},
        ],
    }


@login_required
def salary_report_detail(request):
    """JSON backing the row-detail drawer. ``?type=payslip|employee&id=<pk>``."""
    kind = (request.GET.get('type') or 'payslip').strip().lower()
    target = _int_or_none(request.GET.get('id'))

    if target is None:
        return JsonResponse({'success': False, 'error': 'A valid record id is required.'}, status=400)
    if kind not in ('payslip', 'employee'):
        return JsonResponse({'success': False, 'error': 'Unknown record type.'}, status=400)

    try:
        if kind == 'payslip':
            payload = _payslip_detail_payload(target)
        else:
            payload = _employee_detail_payload(target, parse_filters(request))
    except Exception:
        # A drawer that 500s leaves a spinner on screen forever. Log the real
        # cause and hand the UI something it can show.
        logger.exception('salary_report_detail failed for %s #%s', kind, target)
        return JsonResponse(
            {'success': False, 'error': 'Could not load this record. Please try again.'},
            status=500,
        )

    if payload is None:
        return JsonResponse({'success': False, 'error': 'Record not found.'}, status=404)

    return JsonResponse({'success': True, 'data': payload})
