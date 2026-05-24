import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

from bill_rewards.models import BillUpload

# Get the latest uploaded bill
latest_upload = BillUpload.objects.order_by('-created_at').first()
if latest_upload:
    print("----- RAW OCR TEXT -----")
    print(latest_upload.raw_ocr_text)
    print("------------------------")
else:
    print("No uploads found.")
