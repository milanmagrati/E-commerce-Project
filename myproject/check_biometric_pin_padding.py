"""
Diagnostic for the "missing clock-in/clock-out" attendance bug.

Checks whether raw BiometricAttendance punches for the same employee/day are
being split across differently-padded PIN strings (e.g. '5' vs '05'), which
makes _sync_biometric_to_attendance() see two single-punch groups instead of
one two-punch group — reporting a day as missing a clock-in or clock-out even
though both punches exist on the device.

Read-only: makes no changes. Run on the production DB to confirm/quantify the
issue before and after deploying the _normalize_pin fix in hrm/views.py.

Usage: python check_biometric_pin_padding.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from collections import defaultdict
import pytz
from hrm.models import BiometricAttendance, Employee

NPT = pytz.timezone('Asia/Kathmandu')


def normalize_pin(raw):
    s = str(raw).strip()
    return s.lstrip('0') or '0'


def main():
    raw_pins = set(BiometricAttendance.objects.values_list('pin', flat=True).distinct())
    by_normalized = defaultdict(set)
    for p in raw_pins:
        by_normalized[normalize_pin(p)].add(p)

    colliding = {norm: variants for norm, variants in by_normalized.items() if len(variants) > 1}

    print(f"Distinct raw pin strings in BiometricAttendance: {len(raw_pins)}")
    print(f"Normalized pins with more than one raw variant: {len(colliding)}")
    for norm, variants in sorted(colliding.items()):
        emp = next((e for e in Employee.objects.all() if e.employee_code and normalize_pin(e.employee_code) == norm), None)
        who = emp.full_name if emp else '(no matching employee)'
        print(f"  pin '{norm}' ({who}): raw variants = {sorted(variants)}")

    if not colliding:
        print("No padding collisions found — raw pin strings are already consistent.")
        return

    # For each colliding normalized pin, find the (day) groups that are
    # currently split across >1 raw-pin variant — i.e. days that would merge
    # from two single-punch groups into one two-punch group after the fix,
    # which is exactly what turns a wrongly-flagged "incomplete" day complete.
    print("\nDays where punches are currently split across pin variants (would merge after the fix):")
    punches = BiometricAttendance.objects.filter(pin__in=set().union(*colliding.values())).values('pin', 'timestamp')
    detail = defaultdict(lambda: defaultdict(list))  # (norm, day) -> {raw_pin: [timestamps]}
    for row in punches:
        norm = normalize_pin(row['pin'])
        day = row['timestamp'].astimezone(NPT).date()
        detail[(norm, day)][row['pin']].append(row['timestamp'])

    affected_days = 0
    for (norm, day), by_pin in sorted(detail.items(), key=lambda kv: kv[0][1]):
        if len(by_pin) > 1:
            affected_days += 1
            total_punches = sum(len(v) for v in by_pin.values())
            print(f"  {day} pin '{norm}': {dict((k, len(v)) for k, v in by_pin.items())} -> would become 1 group of {total_punches} punches")

    print(f"\nTotal affected employee-days: {affected_days}")


if __name__ == '__main__':
    main()
