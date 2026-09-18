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

from django.core.management.base import BaseCommand

from sentinel import services

CHUNK = services.PURGE_CHUNK


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
        # The window definitions live in services so this command and the
        # "Run retention sweep now" button on the settings page cannot drift
        # into deleting different things.
        querysets, window = services.retention_querysets(days=options['days'])
        counts = {name: queryset.count() for name, queryset in querysets.items()}

        self.stdout.write(self.style.MIGRATE_HEADING('Sentinel Vault — retention sweep'))
        self.stdout.write(f'  Event retention    : {window["event_days"]} days')
        self.stdout.write(f'  Page-view retention: {window["view_days"]} days')
        for label, value in counts.items():
            self.stdout.write(f'  {label:<18}: {value:,} row(s) expiring')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run — nothing deleted.'))
            return

        if options['archive'] and counts['events']:
            written = self._archive(querysets['events'], options['archive'])
            self.stdout.write(self.style.SUCCESS(
                f'  Archived {written:,} event(s) to {options["archive"]}'))

        removed = services.run_retention_sweep(days=options['days'])

        self.stdout.write(self.style.SUCCESS(
            f'Removed {removed["events"] + removed["page_views"]:,} event(s), '
            f'{removed["sessions"]:,} session(s), {removed["alerts"]:,} alert(s).'))

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
