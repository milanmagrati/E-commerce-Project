"""Re-derive existing attendance rows from their stored clock times.

Working hours, overtime, the late / early-departure flags and the
Present / Half Day / Absent label are all computed once, when the row is
saved, and then stored — so they go stale when a shift's break duration,
working hours or start/end times change, or when a Half Day / Absent
threshold moves on the Attendance Policies page. Those edits now re-derive
the affected rows automatically, but rows saved before that fix (in
particular hand-fixed ones, which the biometric auto-sync skips on purpose)
still carry the old arithmetic. Run this once after deploying to bring the
whole table in line — clock times are never modified:

    python manage.py recompute_attendance_status --dry-run
    python manage.py recompute_attendance_status
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from hrm.models import AttendanceRecord
from hrm.views import _refresh_attendance_records


class Command(BaseCommand):
    help = 'Re-derive attendance hours, overtime, flags and status from the stored clock times.'

    def add_arguments(self, parser):
        parser.add_argument('--employee', type=int, help='Limit to one Employee id.')
        parser.add_argument('--date-from', help='Only records on or after this date (YYYY-MM-DD).')
        parser.add_argument('--date-to', help='Only records on or before this date (YYYY-MM-DD).')
        parser.add_argument('--include-deleted', action='store_true',
                            help='Also recompute rows sitting in the Attendance Adjustments trash.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing anything.')

    def _parse_date(self, value, flag):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            raise CommandError(f'{flag} must be a YYYY-MM-DD date, got "{value}".')

    def handle(self, *args, **options):
        qs = AttendanceRecord.objects.all()
        if not options['include_deleted']:
            qs = qs.filter(is_deleted=False)
        if options['employee']:
            qs = qs.filter(employee_id=options['employee'])
        if options['date_from']:
            qs = qs.filter(date__gte=self._parse_date(options['date_from'], '--date-from'))
        if options['date_to']:
            qs = qs.filter(date__lte=self._parse_date(options['date_to'], '--date-to'))

        total = qs.count()
        self.stdout.write(f'Checking {total} attendance record(s)...')

        if options['dry_run']:
            changes = _refresh_attendance_records(qs, dry_run=True)
            for rec, diff in changes:
                fields = ', '.join(
                    f'{name} {old} -> {new}' for name, (old, new) in sorted(diff.items())
                )
                self.stdout.write(f'  {rec.date} {rec.employee.full_name}: {fields}')
            self.stdout.write(self.style.WARNING(
                f'Dry run: {len(changes)} record(s) would be updated.'
            ))
            return

        updated = _refresh_attendance_records(qs)
        self.stdout.write(self.style.SUCCESS(f'Updated {updated} record(s).'))
