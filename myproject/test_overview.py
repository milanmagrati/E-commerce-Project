from django.utils import timezone
from datetime import timedelta
import sys
sys.path.append('.')
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from dashboard.models import Order
from django.db.models import Count
from django.db.models.functions import TruncHour
from datetime import datetime, time

start_date = timezone.now().date()
end_date = timezone.now().date()
start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
end_dt = timezone.make_aware(datetime.combine(end_date, time.max))

counts_qs = (
    Order.objects
    .filter(created_at__range=(start_dt, end_dt))
    .annotate(order_hour=TruncHour('created_at'))
    .values('order_hour')
    .annotate(count=Count('id'))
    .order_by('order_hour')
)
for entry in counts_qs:
    print(entry)
print("Finished!")
