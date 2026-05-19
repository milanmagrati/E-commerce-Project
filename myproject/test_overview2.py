import sys, os
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()

from dashboard.models import Order
from django.utils import timezone
from datetime import datetime, timedelta, time
from django.db.models.functions import TruncDate, TruncHour
from django.db.models import Count

days = 30
start_date = (timezone.now() - timedelta(days=days - 1)).date()
end_date = timezone.now().date()

start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
end_dt = timezone.make_aware(datetime.combine(end_date, time.max))

print("start_dt:", start_dt)
print("end_dt:", end_dt)

counts_qs = (
    Order.objects
    .filter(created_at__range=(start_dt, end_dt))
    .annotate(order_date=TruncDate('created_at'))
    .values('order_date')
    .annotate(count=Count('id'))
    .order_by('order_date')
)
print(list(counts_qs))
