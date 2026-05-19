import sys, os
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()

from dashboard.models import Order
from django.utils import timezone
from datetime import datetime, timedelta, time

days = 30
start_date = (timezone.now() - timedelta(days=days - 1)).date()
end_date = timezone.now().date()

start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
end_dt = timezone.make_aware(datetime.combine(end_date, time.max))

orders = Order.objects.filter(created_at__range=(start_dt, end_dt)).values('created_at')
counts_map = {}
for order in orders:
    # Convert to local timezone
    local_dt = timezone.localtime(order['created_at'])
    order_date = local_dt.date()
    counts_map[order_date] = counts_map.get(order_date, 0) + 1

print(counts_map)
