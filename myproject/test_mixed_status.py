import os, sys
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()
from dashboard.models import Order
from django.db.models import Q

# Orders that have 'delivered' in one field but 'returned' or 'return' in the other
weird_orders = Order.objects.filter(
    (Q(status__iexact='delivered') & Q(order_status__in=['returned', 'return'])) |
    (Q(order_status__iexact='delivered') & Q(status__in=['returned', 'return']))
)
print("Orders with mixed delivered/returned status:", weird_orders.count())
if weird_orders.count() > 0:
    for o in weird_orders[:5]:
        print(f"ID: {o.id}, status: {o.status}, order_status: {o.order_status}")
