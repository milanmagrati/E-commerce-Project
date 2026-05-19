import os, sys
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()
from dashboard.models import Order
from decimal import Decimal
from django.db.models import Count, Sum, Value, DecimalField
from django.db.models.functions import Coalesce

orders_qs = Order.objects.all()[:10]

print("Status Breakdown test:")
status_breakdown = {
    'delivered': 0, 'pending': 0, 'returns': 0, 'other': 0
}
for field in ('status', 'order_status'):
    for status_item in orders_qs.values(field).annotate(count=Count('id')):
        status = (status_item[field] or 'unknown').lower()
        count = status_item['count']
        if status in ['delivered', 'completed']: status_breakdown['delivered'] += count
        elif status in ['returned', 'return']: status_breakdown['returns'] += count
        elif status in ['pending', 'processing']: status_breakdown['pending'] += count
        else: status_breakdown['other'] += count
print("Original breakdown:", status_breakdown)

print("Testing staff order summary coalesce:")
try:
    summary = list(
        orders_qs.values('created_by__id')
        .annotate(
            order_count=Count('id'),
            total_rev=Coalesce(Sum('total_amount'), Decimal('0')),
        )
    )
    print("Coalesce worked:", summary)
except Exception as e:
    print("Coalesce error:", e)
