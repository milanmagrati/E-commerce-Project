"""
Re-derive RTVOrder.rtv_marked_at from NCM, for rows whose date is missing or
came from an untrusted source.

Background: the RTV sync used to write NCM's *order creation* date into
rtv_marked_at as a "fallback display date", and every repair query filtered on
rtv_marked_at IS NULL — so those rows were never revisited and the RTV page
showed a date weeks earlier than the actual return. Provenance is now tracked
on the row (rtv_marked_at_source), which is what lets this command find them.

Supersedes the root-level fix_rtv_dates_sequential.py script.
"""
import time

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from dashboard.models import RTVOrder, LogisticsAPIConfig
from dashboard.timezone_utils import format_nepali_datetime
from dashboard.views import rtv_needs_date_verification, sync_rtv_from_ncm_comments
from services.ncm_service import NCMService

# NCMService._make_request now paces and retries around NCM's rate limit;
# this backoff stays as the outer guard for a long unattended run.
BACKOFF_STEP = 30
BACKOFF_MAX = 120


class Command(BaseCommand):
    help = "Re-fetch RTV marked dates from NCM for rows with missing/untrusted dates"

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50,
                            help='Maximum RTVs to process (default 50)')
        parser.add_argument('--sleep', type=float, default=1.0,
                            help='Seconds between NCM requests (default 1.0)')
        parser.add_argument('--order-id', type=int, default=None,
                            help='Repair a single NCM order ID, regardless of its source')
        parser.add_argument('--api-config', type=int, default=None,
                            help='Only RTVs belonging to this LogisticsAPIConfig id')
        parser.add_argument('--sources', type=str, default=None,
                            help="Comma-separated sources to target, e.g. ',order_created'. "
                                 "Default: every untrusted source.")
        parser.add_argument('--min-id', type=int, default=None,
                            help='Only NCM order IDs >= this value')
        parser.add_argument('--max-id', type=int, default=None,
                            help='Only NCM order IDs <= this value')
        parser.add_argument('--recheck-days', type=int, default=None,
                            help='Skip rows checked within the last N days')
        parser.add_argument('--use-status-fallback', action='store_true',
                            help='When no "RTV marked" comment exists, approximate the date '
                                 'from the order status timeline (costs one extra API call)')
        parser.add_argument('--backfill-ncm-created-date', action='store_true',
                            help='Also store NCM order created_date into ncm_created_date')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report the provenance census and the rows that would be '
                                 'processed, without calling NCM or writing anything')

    def handle(self, *args, **options):
        qs = RTVOrder.objects.all()

        if options['api_config']:
            qs = qs.filter(api_config_id=options['api_config'])
        if options['min_id'] is not None:
            qs = qs.filter(order_id__gte=options['min_id'])
        if options['max_id'] is not None:
            qs = qs.filter(order_id__lte=options['max_id'])

        self._print_census(qs)

        if options['order_id']:
            candidates = qs.filter(order_id=options['order_id'])
        elif options['sources'] is not None:
            wanted = [s.strip() for s in options['sources'].split(',')]
            candidates = qs.filter(rtv_marked_at_source__in=wanted).order_by('id')
        else:
            candidates = rtv_needs_date_verification(qs)

        if options['recheck_days'] is not None:
            cutoff = timezone.now() - timezone.timedelta(days=options['recheck_days'])
            # NULL must be included: a never-checked row trivially satisfies
            # "not checked in the last N days", but `__lt` alone drops it
            # (NULL < x is NULL in SQL), which would silently skip the entire
            # unprocessed backlog.
            candidates = candidates.filter(
                Q(rtv_marked_at_checked_at__isnull=True)
                | Q(rtv_marked_at_checked_at__lt=cutoff)
            )

        rows = list(candidates[:options['limit']])
        if not rows:
            self.stdout.write(self.style.SUCCESS('Nothing to repair.'))
            return

        self.stdout.write(f'\nProcessing {len(rows)} RTV(s)...')

        if options['dry_run']:
            for rtv in rows:
                self.stdout.write(
                    f"  would check #{rtv.order_id} "
                    f"(current: {format_nepali_datetime(rtv.rtv_marked_at)} "
                    f"from '{rtv.rtv_marked_at_source or 'unknown'}', "
                    f"last checked {format_nepali_datetime(rtv.rtv_marked_at_checked_at)})"
                )
            self.stdout.write(self.style.WARNING('\nDry run — nothing was fetched or written.'))
            return

        # One service per api_config so each RTV is queried with the token of
        # the portal it actually belongs to.
        services = {}
        default_service = NCMService()

        updated = unresolved = unreachable = 0
        consecutive_429 = 0

        for idx, rtv in enumerate(rows, 1):
            if idx > 1 and options['sleep'] > 0:
                time.sleep(options['sleep'])

            cfg_id = rtv.api_config_id
            if cfg_id and cfg_id not in services:
                services[cfg_id] = NCMService(api_config_id=cfg_id)
            service = services.get(cfg_id, default_service)

            before = rtv.rtv_marked_at
            try:
                result = sync_rtv_from_ncm_comments(
                    service, rtv.order_id,
                    use_status_fallback=options['use_status_fallback'],
                )
                resolved = result.resolved
            except Exception as exc:  # noqa: BLE001 - one bad row must not abort the run
                message = str(exc)
                if '429' in message:
                    consecutive_429 += 1
                    wait = min(BACKOFF_STEP * consecutive_429, BACKOFF_MAX)
                    self.stdout.write(self.style.WARNING(
                        f'  [{idx}/{len(rows)}] #{rtv.order_id}: rate limited, '
                        f'backing off {wait}s (streak {consecutive_429})'
                    ))
                    time.sleep(wait)
                else:
                    self.stdout.write(self.style.ERROR(
                        f'  [{idx}/{len(rows)}] #{rtv.order_id}: {message}'
                    ))
                unreachable += 1
                continue

            if not resolved:
                unreachable += 1
                self.stdout.write(self.style.WARNING(
                    f'  [{idx}/{len(rows)}] #{rtv.order_id}: NCM unreachable, left for next run'
                ))
                continue

            consecutive_429 = 0
            rtv.refresh_from_db()

            if options['backfill_ncm_created_date'] and rtv.ncm_created_date is None:
                self._backfill_created_date(service, rtv)

            if rtv.rtv_marked_at_is_trusted and rtv.rtv_marked_at != before:
                updated += 1
                self.stdout.write(self.style.SUCCESS(
                    f'  [{idx}/{len(rows)}] #{rtv.order_id}: '
                    f'{format_nepali_datetime(before)} -> {format_nepali_datetime(rtv.rtv_marked_at)}'
                ))
            elif rtv.rtv_marked_at_is_trusted:
                updated += 1
                self.stdout.write(
                    f'  [{idx}/{len(rows)}] #{rtv.order_id}: confirmed '
                    f'{format_nepali_datetime(rtv.rtv_marked_at)}'
                )
            else:
                unresolved += 1
                self.stdout.write(
                    f"  [{idx}/{len(rows)}] #{rtv.order_id}: no RTV comment "
                    f"(source now '{rtv.rtv_marked_at_source or 'unknown'}')"
                )

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {updated} trusted, {unresolved} still unresolved, '
            f'{unreachable} unreachable.'
        ))
        self._print_census(RTVOrder.objects.all(), label='Census after run')

    def _backfill_created_date(self, service, rtv):
        """Store NCM's order created_date in its own column (never rtv_marked_at)."""
        from dashboard.timezone_utils import parse_ncm_datetime

        try:
            result = service.get_order_details(rtv.order_id)
        except Exception:  # noqa: BLE001
            return
        if not result.get('success'):
            return
        data = result.get('data') or {}
        if not isinstance(data, dict):
            return
        dt = parse_ncm_datetime(data.get('created_date') or data.get('added_time'))
        if dt:
            RTVOrder.objects.filter(pk=rtv.pk).update(ncm_created_date=dt)

    def _print_census(self, queryset, label='Provenance census'):
        from django.db.models import Count

        self.stdout.write(f'\n{label}:')
        labels = dict(RTVOrder.RTV_MARKED_SOURCES)
        rows = queryset.values('rtv_marked_at_source').annotate(n=Count('id')).order_by('-n')
        for row in rows:
            source = row['rtv_marked_at_source']
            trusted = source in RTVOrder.TRUSTED_SOURCES
            marker = 'trusted  ' if trusted else 'UNTRUSTED'
            self.stdout.write(f'  {marker}  {row["n"]:>6}  {labels.get(source, source)}')
        total = queryset.count()
        pending = rtv_needs_date_verification(queryset).count()
        self.stdout.write(f'  total {total}, needing verification {pending}')
