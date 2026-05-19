import os, sys
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()
from django.test import Client
from django.contrib.auth import get_user_model

User = get_user_model()
c = Client()
# Get the first active superuser or staff
user = User.objects.filter(is_staff=True).first()
c.force_login(user)

response = c.get('/api/order-overview-data/?days=1')
print("Status:", response.status_code)
print("Content:", response.content.decode('utf-8'))
