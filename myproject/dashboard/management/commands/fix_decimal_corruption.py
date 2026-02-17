"""
Management command to fix corrupted decimal fields in Order and OrderItem models.
This command will scan the database for invalid decimal values and fix them.
Uses raw SQL with direct string formatting to avoid parameter binding issues.
"""

from django.core.management.base import BaseCommand
from django.db import connection
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fix corrupted decimal fields in Order and OrderItem models using raw SQL'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be fixed without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        
        self.stdout.write(self.style.SUCCESS('Starting decimal corruption fix...'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN: No changes will be made'))
        
        # Fix OrderItem items first
        self.fix_order_items(dry_run)
        
        # Fix Orders
        self.fix_orders(dry_run)
        
        self.stdout.write(self.style.SUCCESS('Decimal corruption fix completed!'))

    def fix_order_items(self, dry_run):
        """Fix OrderItem decimal fields"""
        self.stdout.write('\nFixing OrderItem decimal fields...')
        
        from django.db import connection
        
        cursor = connection.cursor()
        cursor.execute("SELECT id, price, total FROM dashboard_orderitem")
        items = cursor.fetchall()
        cursor.close()
        
        fixed_count = 0
        cursor = connection.cursor()
        for item_id, price, total in items:
            new_price = self._safe_decimal(price)
            new_total = self._safe_decimal(total)
            
            if new_price != price or new_total != total:
                if not dry_run:
                    sql = "UPDATE dashboard_orderitem SET price = {}, total = {} WHERE id = {}".format(
                        new_price, new_total, item_id
                    )
                    cursor.execute(sql)
                fixed_count += 1
                self.stdout.write('  OrderItem {}: Fixed price={}->{}, total={}->{}'.format(
                    item_id, price, new_price, total, new_total
                ))
        
        if not dry_run:
            connection.commit()
        cursor.close()
        
        self.stdout.write('Fixed {} OrderItems'.format(fixed_count))

    def fix_orders(self, dry_run):
        """Fix Order decimal fields"""
        self.stdout.write('\nFixing Order decimal fields...')
        
        from django.db import connection
        
        decimal_columns = [
            'discount_amount',
            'shipping_charge', 
            'delivery_charge',
            'expense_amount',
            'tax_percent',
            'total_amount',
            'partial_amount_paid',
            'remaining_amount',
            'cod_collected',
            'package_weight',
        ]
        
        cursor = connection.cursor()
        cols_str = ", ".join(decimal_columns)
        cursor.execute("SELECT id, " + cols_str + " FROM dashboard_order")
        orders = cursor.fetchall()
        cursor.close()
        
        fixed_count = 0
        cursor = connection.cursor()
        for row in orders:
            order_id = row[0]
            fixed_values = {}
            changed = False
            
            for idx, col in enumerate(decimal_columns):
                value = row[idx + 1]
                new_value = self._safe_decimal(value)
                if new_value != value:
                    fixed_values[col] = new_value
                    changed = True
            
            if changed:
                if not dry_run:
                    set_parts = []
                    for col, val in fixed_values.items():
                        set_parts.append("{} = {}".format(col, val))
                    set_clause = ", ".join(set_parts)
                    
                    sql = "UPDATE dashboard_order SET {} WHERE id = {}".format(
                        set_clause, order_id
                    )
                    cursor.execute(sql)
                fixed_count += 1
                self.stdout.write('  Order {}: Fixed decimal fields'.format(order_id))
        
        if not dry_run:
            connection.commit()
        cursor.close()
        
        self.stdout.write('Fixed {} Orders'.format(fixed_count))

    def _safe_decimal(self, value):
        """Safely convert a value to a valid decimal string for SQL"""
        if value is None or value == '':
            return '0.00'
        
        try:
            float_val = float(str(value))
            
            # Check for NaN, Inf, etc.
            if not isinstance(float_val, float) or float_val != float_val or float_val == float('inf') or float_val == float('-inf'):
                return '0.00'
            
            # Clamp to max_digits=10, decimal_places=2 constraint
            # Max value is 99999999.99 (8 integer digits + 2 decimal places)
            MAX_VALUE = 99999999.99
            MIN_VALUE = -99999999.99
            
            if float_val > MAX_VALUE:
                float_val = MAX_VALUE
            elif float_val < MIN_VALUE:
                float_val = MIN_VALUE
            
            # Round to 2 decimal places
            return "{:.2f}".format(float_val)
        except (ValueError, TypeError, OverflowError):
            return '0.00'

