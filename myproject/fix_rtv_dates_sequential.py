#!/usr/bin/env python3
"""
Sequential RTV date fix — 1 request/second, proper 429 backoff.
Retries ALL orders still missing rtv_marked_at after the parallel batch.
"""
import os
import sys
import time
import django

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from dashboard.models import RTVOrder, LogisticsAPIConfig
from services.ncm_service import NCMService
from django.utils.dateparse import parse_datetime

# Optional ID range args: min_id max_id (e.g. python fix.py 14500000 18000000)
min_id = sys.argv[1] if len(sys.argv) > 1 else None
max_id = sys.argv[2] if len(sys.argv) > 2 else None

# Get the active NCM config
cfg = LogisticsAPIConfig.objects.filter(logistics_provider='ncm', is_active=True).first()
if not cfg:
    print("No active NCM config found!")
    sys.exit(1)

ncm = NCMService(api_config_id=cfg.id)

# All orders still missing rtv_marked_at in the given ID range
qs = RTVOrder.objects.filter(rtv_marked_at__isnull=True)
if min_id:
    qs = qs.filter(order_id__gte=min_id)
if max_id:
    qs = qs.filter(order_id__lt=max_id)
null_orders = list(qs.values_list('order_id', flat=True).order_by('-id'))  # newest first

total = len(null_orders)
range_label = f"[{min_id or '*'} - {max_id or '*'}]"
print(f"Orders with NULL rtv_marked_at {range_label}: {total}")
if total == 0:
    print("All done — nothing to fix!")
    sys.exit(0)

updated = 0
no_comment = 0
errors = 0
consecutive_429 = 0

for i, oid in enumerate(null_orders):
    try:
        result = ncm.get_order_comments(oid)
    except Exception as e:
        err_str = str(e)
        if '429' in err_str:
            consecutive_429 += 1
            wait = min(30 * consecutive_429, 120)  # 30s, 60s, 90s, max 120s
            print(f"  [429] order {oid} — backing off {wait}s (streak: {consecutive_429})")
            time.sleep(wait)
            # retry once after backoff
            try:
                result = ncm.get_order_comments(oid)
            except Exception:
                errors += 1
                time.sleep(2.0)
                continue
        else:
            errors += 1
            time.sleep(2.0)
            continue

    # Reset consecutive 429 counter on success
    if isinstance(result, dict) and result.get('success'):
        consecutive_429 = 0

    if not (isinstance(result, dict) and result.get('success') and result.get('data')):
        # Check if 429 came back as error dict
        err = result.get('error', '') if isinstance(result, dict) else ''
        if '429' in str(err):
            consecutive_429 += 1
            wait = min(30 * consecutive_429, 120)
            print(f"  [429-dict] order {oid} — backing off {wait}s")
            time.sleep(wait)
            try:
                result = ncm.get_order_comments(oid)
            except Exception:
                errors += 1
                time.sleep(2.0)
                continue
            if not (isinstance(result, dict) and result.get('success') and result.get('data')):
                no_comment += 1
                time.sleep(1.0)
                continue
        else:
            no_comment += 1
            time.sleep(1.0)
            continue

    consecutive_429 = 0
    comments = result['data']
    rtv_comment = ''
    rtv_marked_at = None

    for c in comments:
        text = c.get('comment', '')
        if text.startswith('RTV marked'):
            rtv_comment = text.replace('RTV marked - ', '').strip()
            at = c.get('added_time', '')
            if at:
                rtv_marked_at = parse_datetime(at)
            break

    if not rtv_comment:
        for c in comments:
            if c.get('added_by', '') == 'NCM Staff':
                rtv_comment = c.get('comment', '')
                if not rtv_marked_at:
                    at = c.get('added_time', '')
                    if at:
                        rtv_marked_at = parse_datetime(at)
                break

    upd = {}
    if rtv_comment:
        upd['comment'] = rtv_comment
    if rtv_marked_at:
        upd['rtv_marked_at'] = rtv_marked_at

    if upd:
        RTVOrder.objects.filter(order_id=oid).update(**upd)
        updated += 1

    # Print progress every 100
    if (i + 1) % 100 == 0:
        pct = (i + 1) / total * 100
        remaining = total - i - 1
        eta_sec = remaining * 1.1
        eta_min = eta_sec / 60
        print(f"  {i+1}/{total} ({pct:.0f}%) | {updated} updated | {no_comment} empty | {errors} errors | ~{eta_min:.0f}m left")

    time.sleep(1.0)  # strict 1 req/sec

print(f"\n=== Done ===")
print(f"Total processed: {total}")
print(f"Updated (got date/comment): {updated}")
print(f"No comment found: {no_comment}")
print(f"Errors: {errors}")

# Final count
remaining_null = RTVOrder.objects.filter(rtv_marked_at__isnull=True).count()
print(f"Still NULL rtv_marked_at: {remaining_null}")
