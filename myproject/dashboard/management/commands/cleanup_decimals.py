"""
Management command to clean up invalid decimal values in Order model.
Fixes corrupted DecimalField values and ensures data integrity.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from dashboard.models import Order
from dashboard.decimal_utils import validate_decimal_fields, safe_decimal
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Cleanup: Fix all invalid decimal values in Order model'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            help='Show what would be fixed without making changes',
        )
        parser.add_argument(
            '--order-id',
            type=int,
            dest='order_id',
            help='Fix decimal values for a specific order ID',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        order_filter = options.get('order_id')
        
        self.stdout.write(self.style.SUCCESS('🧹 Starting decimal cleanup...'))
        if dry_run:
            self.stdout.write(self.style.WARNING('(DRY RUN - No changes will be made)'))
        
        # Get orders to process
        if order_filter:
            orders = Order.objects.filter(id=order_filter)
            self.stdout.write(f'🔍 Processing Order {order_filter}...')
        else:
            orders = Order.objects.all()
            self.stdout.write(f'🔍 Processing {orders.count()} orders...')
        
        total_fixed = 0
        orders_with_fixes = 0
        fix_summary = {}
        
        try:
            with transaction.atomic():
                for idx, order in enumerate(orders, 1):
                    if idx % 100 == 0 and not order_filter:
                        self.stdout.write(f'  Progress: {idx}/{orders.count()}')
                    
                    # Validate and fix decimal fields
                    order, fixed_fields = validate_decimal_fields(order)
                    
                    if fixed_fields:
                        orders_with_fixes += 1
                        total_fixed += len(fixed_fields)
                        
                        # Track which fields were most commonly fixed
                        for field in fixed_fields:
                            fix_summary[field] = fix_summary.get(field, 0) + 1
                        
                        # Save if not dry run
                        if not dry_run:
                            order.save()
                            self.stdout.write(
                                f"  ✅ Order {order.id} ({order.order_number}): Fixed {len(fixed_fields)} field(s)"
                            )
                        else:
                            self.stdout.write(
                                f"  [DRY RUN] Order {order.id} ({order.order_number}): Would fix {len(fixed_fields)} field(s)"
                            )
        
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Error during cleanup: {e}')
            )
            return
        
        # Report summary
        self.stdout.write(self.style.SUCCESS('\n📊 Cleanup Summary:'))
        self.stdout.write(f'  Orders processed: {orders.count()}')
        self.stdout.write(f'  Orders with fixes: {orders_with_fixes}')
        self.stdout.write(f'  Total fields fixed: {total_fixed}')
        
        if fix_summary:
            self.stdout.write('\n  Fields fixed (frequency):')
            for field, count in sorted(fix_summary.items(), key=lambda x: -x[1]):
                self.stdout.write(f'    {field}: {count}')
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING('\n⚠️  DRY RUN: No changes were made. Run without --dry-run to apply fixes.')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS('\n✅ Cleanup complete! All invalid decimals have been fixed.')
            )
        
        # Additional recommendations
        self.stdout.write(self.style.SUCCESS('\n💡 Recommendations:'))
        self.stdout.write('  1. Run diagnose_decimals to verify no corrupted values remain')
        self.stdout.write('  2. Test all order views to confirm they work without errors')
        self.stdout.write('  3. Review decimal input handling in forms and APIs')
        self.stdout.write('  4. Use safe_decimal() for all external numeric inputs')
