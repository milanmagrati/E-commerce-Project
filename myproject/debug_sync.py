import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
sys.path.insert(0, '/home/milan-magrati/Desktop/EcommerceAdmin/myproject')
django.setup()

from dashboard.views import ncm_rtvs_sync
from django.test import RequestFactory
from django.contrib.auth import get_user_model

User = get_user_model()
user = User.objects.filter(is_superuser=True).first()

factory = RequestFactory()
request = factory.get('/api/ncm-rtv/sync/?mode=comments')
request.user = user

response = ncm_rtvs_sync(request)
print("Response content:", response.content)

# Now let's check the database state for 20600676
from dashboard.models import RTVOrder
try:
    rtv = RTVOrder.objects.get(order_id=20600676)
    print("\nState in DB for 20600676:")
    print("vendor_return:", rtv.vendor_return)
    print("rtv_marked_at:", rtv.rtv_marked_at)
    print("comment:", rtv.comment)
except RTVOrder.DoesNotExist:
    print("RTVOrder 20600676 does not exist in DB!")
