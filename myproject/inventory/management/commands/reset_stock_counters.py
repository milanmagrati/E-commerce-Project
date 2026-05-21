"""
Management command to reset stale reserved_qty and backordered_qty counters
on all products. Run this after deploying the backorder fixes to clean up
any counter drift from previously missing release logic.

Usage:
    python manage.py reset_stock_counters
    python manage.py reset_stock_counters --dry-run
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Reset stale reserved_qty and backordered_qty on products by '
        'recalculating from actual store OrderItem records.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be fixed without making changes.',
        )

    def handle(self, *args, **options):
        from django.db.models import Sum, Q
        from dashboard.models import Product
        from store.models import OrderItem

        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be made\n'))

            # Preview mode: show what would change without modifying
            products = Product.objects.filter(
                Q(reserved_qty__gt=0) | Q(backordered_qty__gt=0)
            )
            total = products.count()
            self.stdout.write(f'Found {total} product(s) with non-zero counters\n')

            fix_count = 0
            for product in products:
                active_items = OrderItem.objects.filter(
                    product=product,
                    order__status__in=['pending', 'confirmed']
                )
                actual_reserved = active_items.aggregate(
                    total=Sum('reserved_qty')
                )['total'] or 0
                actual_backordered = active_items.aggregate(
                    total=Sum('backordered_qty')
                )['total'] or 0

                if (product.reserved_qty != actual_reserved or
                        product.backordered_qty != actual_backordered):
                    self.stdout.write(
                        f'  {product.name} (ID={product.pk}):\n'
                        f'    reserved_qty:    {product.reserved_qty} -> {actual_reserved}\n'
                        f'    backordered_qty: {product.backordered_qty} -> {actual_backordered}\n'
                    )
                    fix_count += 1

            self.stdout.write(
                self.style.WARNING(f'\nWould fix {fix_count} of {total} product(s)')
            )
        else:
            # Delegate to the service function for actual reset
            from inventory.services import reset_stale_counters
            fixed = reset_stale_counters()
            self.stdout.write(
                self.style.SUCCESS(f'✅ Fixed {fixed} product(s) with stale counters')
            )
