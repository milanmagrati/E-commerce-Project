import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
sys.path.insert(0, '/home/milan-magrati/Desktop/EcommerceAdmin/myproject')
django.setup()

from services.ncm_service import NCMService
from dashboard.models import RTVOrder, LogisticsAPIConfig
# pyrefly: ignore [missing-import]
from django.utils.dateparse import parse_datetime

ORDER_ID = 20600676

cfg = LogisticsAPIConfig.objects.filter(logistics_provider='ncm', is_active=True).order_by('-id').first()
svc = NCMService(api_config_id=cfg.id)
cresult = svc.get_order_comments(ORDER_ID)

if cresult['success'] and cresult['data']:
    comments = cresult['data']
    rtv_comment = ''
    rtv_marked_at = None
    vendor_return_status = None
    
    print("=== RAW COMMENTS ===")
    for c in comments:
        print(f"[{c.get('added_time')}] {c.get('comment')}")

    for c in comments:
        text = c.get('comment', '')
        if text.startswith('RTV marked'):
            rtv_comment = text.replace('RTV marked - ', '').strip()
            vendor_return_status = True
            at = c.get('added_time', '')
            if at:
                parsed_dt = parse_datetime(at)
                if parsed_dt:
                    rtv_marked_at = parsed_dt
            break
        elif text.startswith('RTV removed'):
            vendor_return_status = False
            break

    print("\n=== PARSED STATE ===")
    print(f"Vendor Return: {vendor_return_status}")
    print(f"RTV Date: {rtv_marked_at}")
    print(f"RTV Comment: {rtv_comment}")
