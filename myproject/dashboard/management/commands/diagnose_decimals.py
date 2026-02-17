"""
Management command to diagnose invalid decimal values in Order model.
Identifies all Order records with decimal.InvalidOperation errors.
"""

from django.core.management.base import BaseCommand
from django.db import connection
from dashboard.models import Order
from decimal import Decimal, InvalidOperation
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Diagnostic: Find all Order records with invalid decimal values'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix-invalid',
            action='store_true',
            dest='fix_invalid',
            help='Automatically fix invalid decimal values (set to 0)',
        )
        parser.add_argument(
            '--report',
            action='store_true',
            dest='report',
            help='Generate detailed report of invalid values',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🔍 Starting decimal validation scan...'))
        
        # List of decimal fields to check
        decimal_fields = [
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
        
        bad_orders = {}  # {order_id: {field: value, ...}}
        
        # Get all orders
        orders = Order.objects.all()
        total_orders = orders.count()
        
        self.stdout.write(f'📊 Checking {total_orders} Order records...')
        
        for idx, order in enumerate(orders, 1):
            if idx % 100 == 0:
                self.stdout.write(f'  Progress: {idx}/{total_orders}')
            
            bad_fields = {}
            
            for field_name in decimal_fields:
                try:
                    value = getattr(order, field_name, None)
                    
                    # Try to access as Decimal (will raise InvalidOperation if corrupt)
                    if value is not None:
                        # Force conversion to trigger any InvalidOperation
                        _ = Decimal(str(value))
                
                except (InvalidOperation, ValueError, TypeError) as e:
                    bad_fields[field_name] = {
                        'value': getattr(order, field_name, None),
                        'error': str(e)
                    }
            
            if bad_fields:
                bad_orders[order.id] = {
                    'order_number': order.order_number,
                    'bad_fields': bad_fields
                }
        
        # Report results
        if bad_orders:
            self.stdout.write(
                self.style.WARNING(f'⚠️  Found {len(bad_orders)} Order(s) with invalid decimals:')
            )
            
            for order_id, info in sorted(bad_orders.items()):
                self.stdout.write(f"\n  Order ID: {order_id} ({info['order_number']})")
                for field, details in info['bad_fields'].items():
                    self.stdout.write(
                        f"    ❌ {field}: {details['value']} → {details['error']}"
                    )
            
            # Save report to file if requested
            if options['report']:
                import json
                from datetime import datetime
                report_file = f'/tmp/decimal_diagnosis_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
                with open(report_file, 'w') as f:
                    json.dump(bad_orders, f, indent=2, default=str)
                self.stdout.write(
                    self.style.SUCCESS(f'📝 Detailed report saved to: {report_file}')
                )
            
            # Fix if requested
            if options['fix_invalid']:
                self.stdout.write(self.style.SUCCESS('\n🔧 Fixing invalid values...'))
                fixed_count = 0
                
                for order_id, info in bad_orders.items():
                    try:
                        order = Order.objects.get(id=order_id)
                        
                        for field in info['bad_fields'].keys():
                            setattr(order, field, Decimal('0'))
                        
                        order.save()
                        fixed_count += 1
                        self.stdout.write(f"  ✅ Fixed Order {order_id}")
                    
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f"  ❌ Failed to fix Order {order_id}: {e}")
                        )
                
                self.stdout.write(
                    self.style.SUCCESS(f'\n✅ Fixed {fixed_count} order(s)')
                )
        else:
            self.stdout.write(
                self.style.SUCCESS('✅ No invalid decimal values found! All orders are healthy.')
            )
        
        self.stdout.write(
            self.style.SUCCESS('\nℹ️  Usage for future checks:')
        )
        self.stdout.write('  python manage.py diagnose_decimals --report    # Generate detailed report')
        self.stdout.write('  python manage.py diagnose_decimals --fix-invalid  # Auto-fix all bad values')
