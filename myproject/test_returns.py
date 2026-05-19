import os, sys
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()
from dashboard.models import Order, ReturnRequest

returned_orders_with_delivered_status = ReturnRequest.objects.filter(
    order__status__iexact='delivered'
) | ReturnRequest.objects.filter(
    order__order_status__iexact='delivered'
)
print("Returns on orders still marked delivered:", returned_orders_with_delivered_status.count())
