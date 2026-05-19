import os, sys
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()
from dashboard.models import Order
st = set(Order.objects.values_list('status', flat=True).distinct())
ost = set(Order.objects.values_list('order_status', flat=True).distinct())
print("status:", st)
print("order_status:", ost)
