"""
Retention enforcement for Sentinel Vault.

    python manage.py sentinel_prune                # honour the configured retention
    python manage.py sentinel_prune --dry-run      # report only
    python manage.py sentinel_prune --archive out.jsonl.gz
    python manage.py sentinel_prune --days 90

Run it from cron (or Celery beat) — this project has no always-on scheduler, and
an unbounded audit table on a busy shop is the one failure mode that turns a
useful vault into a liability.
"""

import gzip
import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from sentinel.context import suppress_capture
from sentinel.models import AuditEvent, DeviceSession, EventType, SecurityAlert, VaultSettings

CHUNK = 2000


class Command(BaseCommand):
    help = 'Delete (optionally archive) audit data past its retention window.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=None,
                            help='Override the configured retention window.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be removed without deleting.')
        parser.add_argument('--archive', type=str, default=None,
                            help='Write expiring events to this gzipped JSONL file first.')

    def handle(self, *args, **options):
        config = VaultSettings.load()
        event_days = options['days'] or config.retention_days
        view_days = min(options['days'] or config.page_view_retention_days,
                        config.page_view_retention_days)
        dry_run = options['dry_run']
        now = timezone.now()

        event_cutoff = now - timedelta(days=event_days)
        view_cutoff = now - timedelta(days=view_days)

        expiring = AuditEvent.objects.filter(created_at__lt=event_cutoff)
        expiring_views = AuditEvent.objects.filter(
            created_at__lt=view_cutoff, event_type=EventType.VIEW)

        # Closed sessions with no surviving events are dead weight.
        session_cutoff = now - timedelta(days=event_days)
        expiring_sessions = DeviceSession.objects.filter(
            is_active=False, started_at__lt=session_cutoff)
        expiring_alerts = SecurityAlert.objects.filter(
            status__in=[SecurityAlert.Status.RESOLVED, SecurityAlert.Status.DISMISSED],
            created_at__lt=event_cutoff)

        counts = {
            'events': expiring.count(),
            'page_views': expiring_views.count(),
            'sessions': expiring_sessions.count(),
            'alerts': expiring_alerts.count(),
        }

        self.stdout.write(self.style.MIGRATE_HEADING('Sentinel Vault — retention sweep'))
        self.stdout.write(f'  Event retention   : {event_days} days (before {event_cutoff:%Y-%m-%d})')
        self.stdout.write(f'  Page-view retention: {view_days} days (before {view_cutoff:%Y-%m-%d})')
        for label, value in counts.items():
            self.stdout.write(f'  {label:<18}: {value:,} row(s) expiring')

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — nothing deleted.'))
            return

        if options['archive'] and counts['events']:
            written = self._archive(expiring, options['archive'])
            self.stdout.write(self.style.SUCCESS(
                f'  Archived {written:,} event(s) to {options["archive"]}'))

        with suppress_capture():
            deleted_views = self._chunked_delete(expiring_views)
            deleted_events = self._chunked_delete(
                AuditEvent.objects.filter(created_at__lt=event_cutoff))
            deleted_alerts = self._chunked_delete(expiring_alerts)
            deleted_sessions = self._chunked_delete(expiring_sessions)

        self.stdout.write(self.style.SUCCESS(
            f'Removed {deleted_events + deleted_views:,} event(s), '
            f'{deleted_sessions:,} session(s), {deleted_alerts:,} alert(s).'))

    def _chunked_delete(self, queryset):
        """Delete in batches so a large sweep doesn't hold one enormous MySQL transaction."""
        total = 0
        while True:
            ids = list(queryset.values_list('pk', flat=True)[:CHUNK])
            if not ids:
                return total
            deleted, _ = queryset.model.objects.filter(pk__in=ids).delete()
            total += len(ids)
            if deleted == 0:
                return total

    def _archive(self, queryset, path):
        opener = gzip.open if path.endswith('.gz') else open
        written = 0
        with opener(path, 'wt', encoding='utf-8') as handle:
            for event in queryset.iterator(chunk_size=CHUNK):
                handle.write(json.dumps({
                    'id': event.id,
                    'created_at': event.created_at.isoformat(),
                    'actor': event.actor_username,
                    'role': event.actor_role,
                    'event_type': event.event_type,
                    'severity': event.severity,
                    'risk_score': event.risk_score,
                    'risk_flags': event.risk_flags,
                    'module': event.module,
                    'action': event.action,
                    'object_type': event.object_type,
                    'object_id': event.object_id,
                    'object_label': event.object_label,
                    'changes': event.changes,
                    'ip_address': event.ip_address,
                    'browser': event.browser,
                    'os': event.operating_system,
                    'path': event.path,
                    'method': event.method,
                    'status_code': event.status_code,
                    'context': event.context,
                }, default=str) + '\n')
                written += 1
        return written
