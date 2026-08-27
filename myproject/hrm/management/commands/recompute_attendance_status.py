"""Re-label existing attendance rows against the current Attendance Policy /
Shift thresholds.

The Attendance Report reads AttendanceRecord.status, which is written once
when the row is saved. Changing a Half Day / Absent threshold on the
Attendance Policies page now re-labels affected rows automatically, but rows
saved before that fix (in particular hand-fixed ones, which the biometric
auto-sync skips on purpose) still carry their old label. Run this once after
deploying to bring the whole table in line:

    python manage.py recompute_attendance_status --dry-run
    python manage.py recompute_attendance_status
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from hrm.models import AttendanceRecord
from hrm.views import _refresh_attendance_statuses


class Command(BaseCommand):
    help = 'Recompute Present / Half Day / Absent / Late labels from stored worked hours.'

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
            changes = _refresh_attendance_statuses(qs, dry_run=True)
            for rec, old_status, new_status in changes:
                self.stdout.write(
                    f'  {rec.date} {rec.employee.full_name} '
                    f'{rec.working_hours}h: {old_status} -> {new_status}'
                )
            self.stdout.write(self.style.WARNING(
                f'Dry run: {len(changes)} record(s) would be relabelled.'
            ))
            return

        updated = _refresh_attendance_statuses(qs)
        self.stdout.write(self.style.SUCCESS(f'Relabelled {updated} record(s).'))
