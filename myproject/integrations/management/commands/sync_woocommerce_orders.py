import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from integrations.services import ingest_polled_order
from services.woocommerce_service import WooCommerceService

logger = logging.getLogger('integrations')


class Command(BaseCommand):
    help = (
        "Pull orders from the WooCommerce REST API - polling fallback until "
        "the push webhook is wired up. Run on a schedule (cron / Task Scheduler), "
        "e.g. every 15 minutes: */15 * * * * python manage.py sync_woocommerce_orders"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--since-hours', type=int, default=24,
            help='Only fetch orders modified in the last N hours (default: 24)',
        )

    def handle(self, *args, **options):
        since = timezone.now() - timezone.timedelta(hours=options['since_hours'])
        service = WooCommerceService()

        synced = 0
        try:
            for raw in service.fetch_all_orders(modified_after=since.isoformat()):
                ingest_polled_order(raw)
                synced += 1
        except Exception:
            logger.exception('WooCommerce order poll failed')
            raise

        self.stdout.write(self.style.SUCCESS(f'Synced {synced} WooCommerce order(s)'))
