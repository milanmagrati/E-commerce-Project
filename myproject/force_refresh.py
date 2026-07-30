"""
SUPERSEDED — use `python manage.py repair_rtv_marked_at --order-id <id>`.

This script nulls rtv_marked_at without resetting rtv_marked_at_source, which
leaves the row claiming a provenance for a date it no longer has. It also
hardcodes a Linux path that doesn't exist on every dev machine.
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
sys.path.insert(0, '/home/milan-magrati/Desktop/EcommerceAdmin/myproject')
django.setup()

from dashboard.models import RTVOrder

# Just clear the rtv_marked_at and comment for this specific order
# so the next sync picks it up as a priority
RTVOrder.objects.filter(order_id=20600676).update(rtv_marked_at=None, comment='')

print("Cleared 20600676, it will be fetched in the next UI click.")
