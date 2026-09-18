"""
Bulk-sync NCM status for all active NCM orders.

This command is now optional. The sync normally drives itself from web
requests (see ncm/scheduler.py), because this project's shared hosting has no
Celery worker and, in practice, no crontab either - which is exactly why NCM
statuses used to sit stale for days.

It remains useful for two things:

  * Running a sync by hand:      python manage.py sync_all_ncm_orders --force
  * Belt-and-braces scheduling, if a crontab IS available. Because the due
    check lives in the database rather than in the crontab, you can safely
    schedule this every minute and let Settings -> API Sync Settings decide the
    real cadence:

        * * * * * cd /path/to/myproject && /path/to/python manage.py sync_all_ncm_orders >> logs/ncm_bulk_sync_cron.log 2>&1

    Without --force it exits immediately unless the configured interval has
    elapsed, and it can never collide with a sync started by a web request:
    both take the same database lock.

Which order statuses are eligible for sync is configurable via
Settings -> API Sync Settings (APISettings.bulk_sync_included_statuses).
"""

import logging

from django.core.management.base import BaseCommand

from ncm.scheduler import maybe_run_bulk_sync

logger = logging.getLogger('ncm')


class Command(BaseCommand):
    help = 'Sync NCM status for all active NCM orders (optional; the app also self-schedules).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Sync now even if the configured interval has not elapsed. '
                 'Still refuses to run alongside a sync that is already in progress.',
        )

    def handle(self, *args, **options):
        force = options.get('force', False)

        # run_in_thread=False: a management command should finish when the work
        # does, and report what happened. The web path threads instead.
        started, summary = maybe_run_bulk_sync(force=force, run_in_thread=False)

        if not started:
            message = (
                'Skipped: a sync is already running, or the configured interval '
                '(Settings -> API Sync Settings) has not elapsed. Use --force to override.'
            )
            self.stdout.write(self.style.WARNING(message))
            return

        summary = summary or {}
        errors = summary.get('errors') or []
        message = (
            f"NCM bulk sync complete: {summary.get('updated_count', 0)}/"
            f"{summary.get('total_orders', 0)} orders updated. "
            f"Errors: {summary.get('error_count', len(errors))}"
        )
        if summary.get('deadline_reached'):
            message += ' (stopped at the time budget; remaining orders go in the next run)'

        self.stdout.write(self.style.SUCCESS(message))
        logger.info(message)

        for err in errors:
            self.stderr.write(self.style.ERROR(err))
