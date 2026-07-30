"""
Verify dashboard.timezone_utils NCM timestamp parsing/formatting.

Pure functions only — no DB reads or writes. Run:
    python test_ncm_datetime_parsing.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone as dt_timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

import django
django.setup()

from django.utils import timezone

from dashboard.timezone_utils import (
    NEPALI_TIMEZONE,
    convert_to_nepali,
    format_ncm_datetime,
    format_nepali_datetime,
    format_nepali_datetime_or_none,
    parse_ncm_datetime,
)

failures = []


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ''))
        failures.append(label)


def eq(label, actual, expected):
    check(label, actual == expected, f"got {actual!r}, expected {expected!r}")


# ---------------------------------------------------------------------------
print("\n1. Offset-bearing ISO (the real NCM format)")
# Sampled verbatim from logs/ncm_integration.log
dt_offset = parse_ncm_datetime('2026-02-20T11:19:53.209447+05:45')
check("parses", dt_offset is not None)
check("aware", dt_offset is not None and timezone.is_aware(dt_offset))
eq("offset is +05:45", dt_offset.utcoffset(), timedelta(hours=5, minutes=45))
eq("formats to Nepal wall clock", format_nepali_datetime(dt_offset), 'Feb 20, 2026 11:19 AM')

# ---------------------------------------------------------------------------
print("\n2. 'Z' (UTC) form stays UTC — must NOT be re-localized to Nepal")
# 05:34:53Z is the same instant as 11:19:53+05:45
dt_utc = parse_ncm_datetime('2026-02-20T05:34:53Z')
check("parses", dt_utc is not None)
eq("offset is zero", dt_utc.utcoffset(), timedelta(0))
eq("same wall clock as case 1", format_nepali_datetime(dt_utc), 'Feb 20, 2026 11:19 AM')
eq(
    "same instant as case 1 (to the second)",
    dt_utc.replace(microsecond=0),
    dt_offset.astimezone(dt_timezone.utc).replace(microsecond=0),
)

# ---------------------------------------------------------------------------
print("\n3. Naive string == Nepal wall clock (not UTC)")
dt_naive = parse_ncm_datetime('2026-07-20 13:52:00')
check("parses", dt_naive is not None)
eq("offset is +05:45", dt_naive.utcoffset(), timedelta(hours=5, minutes=45))
eq("formats unchanged", format_nepali_datetime(dt_naive), 'Jul 20, 2026 01:52 PM')
# The old bare-parse_datetime + convert_to_nepali path produced 07:37 PM.
check(
    "not shifted by +5:45 like the old path",
    format_nepali_datetime(dt_naive) != 'Jul 20, 2026 07:37 PM',
)

# ---------------------------------------------------------------------------
print("\n4. Date-only string -> Nepal midnight")
eq("date-only", format_nepali_datetime(parse_ncm_datetime('2026-07-20')), 'Jul 20, 2026 12:00 AM')

# ---------------------------------------------------------------------------
print("\n5. Junk input returns None without raising")
for junk in (None, '', '   ', 'not a date', 'RTV marked', 0, 1, [], {}, object()):
    try:
        result = parse_ncm_datetime(junk)
        check(f"{junk!r} -> None", result is None, f"got {result!r}")
    except Exception as exc:  # noqa: BLE001 - the point is that nothing escapes
        check(f"{junk!r} -> None", False, f"raised {type(exc).__name__}: {exc}")

# ---------------------------------------------------------------------------
print("\n6. Sanity guard on absurd values")
check("year 1970 rejected", parse_ncm_datetime('1970-01-01T00:00:00+05:45') is None)
far_future = (timezone.now() + timedelta(days=400)).isoformat()
check("now + 400 days rejected", parse_ncm_datetime(far_future) is None)
near_future = (timezone.now() + timedelta(hours=1)).isoformat()
check("now + 1 hour accepted (clock skew)", parse_ncm_datetime(near_future) is not None)

# ---------------------------------------------------------------------------
print("\n7. Idempotence — reparsing our own output is a no-op")
for sample in (
    '2026-02-20T11:19:53.209447+05:45',
    '2026-02-20T05:34:53Z',
    '2026-07-20 13:52:00',
    '2026-07-20',
):
    once = parse_ncm_datetime(sample)
    twice = parse_ncm_datetime(once)
    eq(f"reparse {sample!r}", twice, once)

# ---------------------------------------------------------------------------
print("\n8. Formatter contracts")
eq("format_nepali_datetime(None)", format_nepali_datetime(None), '—')
check("format_nepali_datetime_or_none(None)", format_nepali_datetime_or_none(None) is None)
eq(
    "format_nepali_datetime_or_none(dt)",
    format_nepali_datetime_or_none(dt_offset),
    'Feb 20, 2026 11:19 AM',
)
eq(
    "format_ncm_datetime(raw string)",
    format_ncm_datetime('2026-02-20T11:19:53.209447+05:45'),
    'Feb 20, 2026 11:19 AM',
)
eq("format_ncm_datetime(junk)", format_ncm_datetime('nonsense'), '—')

# ---------------------------------------------------------------------------
print("\n9. convert_to_nepali behaviour unchanged (naive == UTC)")
# Guards against anyone "fixing" convert_to_nepali later, which would shift
# every datetime already stored in the DB.
eq(
    "naive datetime still read as UTC",
    format_nepali_datetime(datetime(2026, 7, 20, 8, 7, 0)),
    'Jul 20, 2026 01:52 PM',
)
aware_utc = timezone.now().astimezone(dt_timezone.utc)
eq(
    "aware value round-trips",
    convert_to_nepali(aware_utc).astimezone(dt_timezone.utc),
    aware_utc,
)
eq("NEPALI_TIMEZONE unchanged", str(NEPALI_TIMEZONE), 'Asia/Kathmandu')

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("All NCM datetime parsing checks passed.")
