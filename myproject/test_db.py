import sys, os
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()
from dashboard.models import Order
print(Order.objects.count())
print(Order.objects.earliest('created_at').created_at)
print(Order.objects.latest('created_at').created_at)
