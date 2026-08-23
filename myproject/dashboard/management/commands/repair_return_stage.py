"""
Re-decide the return stage ('return_processing' vs 'return') of orders that
NCM's own sync already classified, by asking NCM again.

Background: the vendor_return flag was read as "this return is finished", but
NCM sets it the moment an order is marked RTV and keeps reporting it on every
hop of the journey back. So any order that entered the RTV pipeline was written
straight to 'return' - and because 'return' is in bulk sync's terminal status
set, nothing ever synced it again. Those orders are stuck at a stage they had
not reached, and the fixed resolution alone cannot rescue them: they are exactly
the orders the background sync skips.

This command re-fetches each one's current NCM status and re-applies the
corrected resolution, in both directions:

  * still travelling back  -> 'return_processing'
  * confirmed back with us -> stays 'return'

Orders whose status staff set by hand are left alone, as are cancelled orders.

    python manage.py repair_return_stage --dry-run
    python manage.py repair_return_stage --limit 200
"""
import time

from django.core.management.base import BaseCommand
from django.db.models import Q

from dashboard.models import Order, OrderActivityLog
from dashboard.timezone_utils import parse_ncm_datetime
from services.ncm_service import NCMService
from services.status_override import manual_override_holds

#: The stages this command is allowed to move an order between. 'returned' is
#: excluded on purpose: staff scanned that parcel in physically, which outranks
#: anything NCM reports.
REPAIRABLE_STATUSES = ('return', 'return_processing')


class Command(BaseCommand):
    help = "Re-derive return_processing/return from NCM for orders in the return pipeline"

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100,
                            help='Maximum orders to process (default 100)')
        parser.add_argument('--sleep', type=float, default=1.0,
                            help='Seconds between NCM requests (default 1.0)')
        parser.add_argument('--order-id', type=int, default=None,
                            help='Repair a single local Order id')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing anything')

    def handle(self, *args, **options):
        limit = options['limit']
        sleep = options['sleep']
        dry_run = options['dry_run']

        qs = Order.objects.filter(
            is_deleted=False, ncm_order_id__isnull=False,
        ).filter(
            Q(status__in=REPAIRABLE_STATUSES) | Q(order_status__in=REPAIRABLE_STATUSES)
        ).order_by('-updated_at')

        if options['order_id']:
            qs = qs.filter(id=options['order_id'])

        total = qs.count()
        self.stdout.write(f"Return-pipeline orders to check: {total} (processing up to {limit})")

        checked = corrected = held = failed = 0

        for order in qs[:limit]:
            checked += 1
            svc = NCMService(api_config_id=order.api_config_id or None)

            try:
                result = svc.get_order_status(order.ncm_order_id)
            except Exception as exc:
                failed += 1
                self.stderr.write(f"  {order.order_number}: NCM error - {exc}")
                continue
            finally:
                if sleep:
                    time.sleep(sleep)

            data = result.get('data') if result.get('success') else None
            entry = None
            if data:
                candidate = data[0] if isinstance(data, list) else data
                if isinstance(candidate, dict):
                    entry = candidate

            if entry is None:
                failed += 1
                self.stderr.write(
                    f"  {order.order_number}: no status from NCM ({result.get('error', 'empty response')})"
                )
                continue

            raw_status = entry.get('status') or entry.get('Status') or ''
            event_at = parse_ncm_datetime(entry.get('added_time'))
            system_status, payment_status = svc.resolve_delivered_status(entry)

            if system_status not in REPAIRABLE_STATUSES:
                # NCM has moved this order out of the return pipeline entirely
                # (e.g. the RTV was removed). Deciding that is the ordinary
                # sync's job, not this one's.
                self.stdout.write(
                    f"  {order.order_number}: NCM now says '{raw_status}' -> "
                    f"{system_status}; left for the normal sync"
                )
                continue

            if order.status == system_status and order.order_status == system_status:
                continue

            if manual_override_holds(order, raw_status, event_at):
                held += 1
                self.stdout.write(f"  {order.order_number}: status set by hand, kept")
                continue

            old_status = order.status
            if dry_run:
                corrected += 1
                self.stdout.write(
                    f"  [dry-run] {order.order_number}: {old_status} -> {system_status} "
                    f"(NCM: {raw_status})"
                )
                continue

            update_fields = NCMService.sync_order_status_fields(
                order, system_status, payment_status, allow_return_reopen=True
            )
            if not update_fields:
                continue
            order.ncm_status = raw_status
            update_fields.extend(['ncm_status', 'updated_at'])
            order.save(update_fields=list(dict.fromkeys(update_fields)))

            OrderActivityLog.objects.create(
                order=order,
                action_type='status_changed',
                field_name='status',
                old_value=old_status,
                new_value=system_status,
                event_at=event_at,
                description=(f'Return stage repaired from NCM: {old_status} -> {system_status} '
                             f'(NCM: {raw_status})'),
            )
            corrected += 1
            self.stdout.write(f"  {order.order_number}: {old_status} -> {system_status} (NCM: {raw_status})")

        self.stdout.write(self.style.SUCCESS(
            f"Checked {checked} | corrected {corrected} | kept (manual) {held} | failed {failed}"
            + (" [dry run - nothing written]" if dry_run else "")
        ))
