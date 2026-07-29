"""
Bulk-sync NCM status for all active NCM orders in the background.

Intended to be triggered by a cPanel Cron Job (e.g. every 10 minutes) since
this project's shared hosting doesn't support a persistent Celery
worker/beat process:

    */10 * * * * cd /path/to/myproject && /path/to/python manage.py sync_all_ncm_orders >> logs/ncm_bulk_sync_cron.log 2>&1

Which order statuses are considered "active" (eligible for sync) is
configurable via Settings -> API Sync Settings (APISettings.bulk_sync_included_statuses).
"""

import logging
import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from ncm.bulk_sync import run_bulk_ncm_status_sync

logger = logging.getLogger('ncm')

LOCK_FILE = Path(settings.BASE_DIR) / 'logs' / 'ncm_bulk_sync.lock'
STALE_LOCK_SECONDS = 8 * 60  # just under the recommended 10-minute cron interval


class Command(BaseCommand):
    help = 'Sync NCM status for all active NCM orders (run via cPanel cron; not Celery).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Run even if a lock file is present (use when a previous run is known to be dead).',
        )

    def handle(self, *args, **options):
        force = options.get('force', False)

        if not self._acquire_lock(force=force):
            message = (
                'Previous sync_all_ncm_orders run still in progress '
                f'(lock younger than {STALE_LOCK_SECONDS // 60} min) - skipping. '
                'Use --force to override.'
            )
            self.stdout.write(self.style.WARNING(message))
            logger.warning(message)
            return

        try:
            summary = run_bulk_ncm_status_sync(user=None)
        except Exception as e:
            logger.exception('sync_all_ncm_orders failed')
            self.stderr.write(self.style.ERROR(f'sync_all_ncm_orders failed: {e}'))
            return
        finally:
            self._release_lock()

        message = (
            f"NCM bulk sync complete: {summary['updated_count']}/{summary['total_orders']} "
            f"orders updated. Errors: {len(summary['errors'])}"
        )
        self.stdout.write(self.style.SUCCESS(message))
        logger.info(message)

        for err in summary['errors']:
            self.stderr.write(self.style.ERROR(err))
            logger.error(f'sync_all_ncm_orders: {err}')

    def _acquire_lock(self, force=False):
        """Atomically claim the lock file.

        O_CREAT|O_EXCL makes creation-if-absent a single syscall, so two cron
        runs starting together can't both decide the lock was free (a plain
        exists()-then-write check has a window where both pass). A lock older
        than STALE_LOCK_SECONDS is treated as abandoned - a previous run that
        was killed hard never reached its cleanup - and is taken over.
        """
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if not (force or self._lock_is_stale()):
                return False
            # Reclaim: drop the abandoned lock, then retry the atomic create
            # once. If that create loses to a racing process, back off.
            try:
                LOCK_FILE.unlink()
                fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except (FileExistsError, OSError):
                return False

        try:
            os.write(fd, str(time.time()).encode())
        finally:
            os.close(fd)
        return True

    def _lock_is_stale(self):
        try:
            return (time.time() - LOCK_FILE.stat().st_mtime) >= STALE_LOCK_SECONDS
        except OSError:
            # Lock vanished between the failed create and this check - treat
            # as stale so the retry above can claim it.
            return True

    def _release_lock(self):
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            logger.warning(f'Could not remove lock file {LOCK_FILE}')
