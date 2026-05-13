import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
sys.path.insert(0, '/home/milan-magrati/Desktop/EcommerceAdmin/myproject')
django.setup()

import requests
from dashboard.models import LogisticsAPIConfig

ORDER_ID = 20269594
cfg = LogisticsAPIConfig.objects.filter(logistics_provider='ncm', is_active=True).order_by('-id').first()
base = cfg.get_primary_base_url()
base_v2 = cfg.get_base_url_v2() or base
headers = {'Authorization': f'Token {cfg.api_key}', 'Content-Type': 'application/json'}

urls = [
    f"{base_v2}/order/comment",
    f"{base}/order/comment"
]

for url in urls:
    resp = requests.get(url, headers=headers, params={'id': ORDER_ID}, timeout=5)
    print(f"GET {url}?id={ORDER_ID} => {resp.status_code}")
    if resp.status_code == 200:
        print("Response text:", resp.text[:200])
    else:
        print("Response text:", resp.text)
