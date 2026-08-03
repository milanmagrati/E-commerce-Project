"""
Polls Meta (Facebook/Instagram) for new conversations and lets the AI auto-reply
engine run without anyone having the CRM inbox open in a browser.

Meta cannot deliver webhooks to 127.0.0.1, so during local development the only
thing that pulls new messages in is the inbox page-load sync and the 15-second
AJAX poll — i.e. the bot is awake only while someone is watching it. This command
does the same sync on a timer so auto-replies happen unattended.

In production, where the webhook is reachable, keep this running anyway as a
safety net: `external_id` dedupe makes the webhook and this poller idempotent.

    python manage.py crm_poll_meta                # loop forever, 20s interval
    python manage.py crm_poll_meta --once         # one pass, for cron/Task Scheduler
    python manage.py crm_poll_meta --interval 60
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from trendycrm.meta_sync import LIVE_INTEGRATION_STATUSES, sync_meta_conversations
from trendycrm.models import CRMIntegration

logger = logging.getLogger(__name__)

POLLABLE_CHANNELS = ['facebook', 'instagram']


class Command(BaseCommand):
    help = "Poll Meta for new conversations so the AI chatbot can auto-reply unattended."

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval', type=int, default=20,
            help='Seconds between polls (default: 20).',
        )
        parser.add_argument(
            '--once', action='store_true',
            help='Run a single pass and exit instead of looping.',
        )

    def handle(self, *args, **options):
        interval = max(5, options['interval'])
        run_once = options['once']

        if run_once:
            self._poll_all()
            return

        self.stdout.write(self.style.SUCCESS(
            f"Polling Meta every {interval}s. Press Ctrl+C to stop."
        ))
        try:
            while True:
                self._poll_all()
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nStopped."))

    def _poll_all(self):
        integrations = CRMIntegration.objects.filter(
            channel_type__in=POLLABLE_CHANNELS,
            status__in=LIVE_INTEGRATION_STATUSES,
        ).exclude(access_token='').exclude(access_token__isnull=True)

        if not integrations:
            self.stdout.write(self.style.WARNING(
                "No connected Facebook/Instagram accounts to poll."
            ))
            return

        stamp = timezone.localtime().strftime('%H:%M:%S')
        ok, failed = 0, 0
        for integration in integrations:
            # One bad token must not stop the other pages from being polled.
            try:
                sync_meta_conversations(integration)
                ok += 1
            except Exception:
                failed += 1
                logger.exception(
                    f"crm_poll_meta: sync failed for {integration.account_name or integration.pk}"
                )
                self.stderr.write(
                    f"  ! {integration.account_name or integration.pk}: sync failed (see logs)"
                )

        line = f"[{stamp}] polled {ok} account(s)"
        if failed:
            self.stdout.write(self.style.WARNING(f"{line}, {failed} failed"))
        else:
            self.stdout.write(line)
