import os, sys
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
# pyrefly: ignore [missing-import]
import django
django.setup()
from dashboard.models import Order
all_sources_qs = Order.objects.values_list('order_from', flat=True).distinct()
sources = set()
for s in all_sources_qs:
    sources.add(s if s else 'Direct')
print("All sources:", sources)
