#!/usr/bin/env python3
"""
Backfill for approved AttendanceRegularization requests created before the
approval endpoint (hrm/views.py: attendance_regularization_update_status)
was fixed to actually write the requested clock_in/out onto the linked
AttendanceRecord. Previously approving a request only flipped the request's
own status, leaving the attendance record (and therefore the Attendance
Report) showing the original, still-incomplete punch.

Re-applies clock_in/out from every 'approved' regularization onto its
attendance_record using the same metric logic as the live views, so past
approvals catch up to the new behavior. Safe to re-run.
"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from hrm.models import AttendanceRegularization, AttendanceRecord
from hrm.views import _compute_attendance_metrics
from django.db import transaction

regs = AttendanceRegularization.objects.filter(status='approved').select_related(
    'employee__shift', 'employee__attendance_policy', 'attendance_record'
)

fixed = 0
skipped_no_change = 0
created_record = 0

for reg in regs:
    record = reg.attendance_record
    with transaction.atomic():
        if record is None:
            record, was_created = AttendanceRecord.objects.select_related(
                'employee__shift', 'employee__attendance_policy'
            ).get_or_create(employee=reg.employee, date=reg.date)
            if was_created:
                created_record += 1
            reg.attendance_record = record
            reg.save(update_fields=['attendance_record'])

        new_clock_in = reg.clock_in or record.clock_in
        new_clock_out = reg.clock_out or record.clock_out

        if new_clock_in == record.clock_in and new_clock_out == record.clock_out:
            skipped_no_change += 1
            continue

        clock_in_str = new_clock_in.strftime('%H:%M') if new_clock_in else None
        clock_out_str = new_clock_out.strftime('%H:%M') if new_clock_out else None

        status = record.status
        if status == 'incomplete' and clock_in_str and clock_out_str:
            status = 'present'

        shift = record.shift or record.employee.shift
        policy = record.employee.effective_attendance_policy
        metrics = _compute_attendance_metrics(clock_in_str, clock_out_str, shift, policy, status)

        record.clock_in = new_clock_in
        record.clock_out = new_clock_out
        record.status = metrics['status']
        record.working_hours = metrics['working_hours']
        record.overtime_hours = metrics['overtime_hours']
        record.is_early_departure = metrics['is_early']
        record.is_late_arrival = metrics['is_late']
        record.save()

        fixed += 1
        print(f"Fixed: {reg.employee.full_name} {reg.date} -> in={record.clock_in} out={record.clock_out} status={record.status}")

print(f"\nTotal approved regularizations: {regs.count()}")
print(f"Records updated: {fixed}")
print(f"Already in sync (skipped): {skipped_no_change}")
print(f"Attendance records created: {created_record}")
