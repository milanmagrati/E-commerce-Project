import os
import django
import json
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model

User = get_user_model()
user = User.objects.first()

client = Client()
client.force_login(user)

response = client.post(
    '/api/orders/follow-ups/add/',
    data=json.dumps({
        'name': 'test',
        'phone': '1234567890',
        'lead_source': '',
        'product_ids': [],
        'followup_1': '',
        'followup_2': '',
        'status': '',
        'remarks': ''
    }),
    content_type='application/json'
)

print("Status:", response.status_code)
print("Content:", response.content.decode('utf-8')[:1000])
