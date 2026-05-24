import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

from bill_rewards.models import BillUpload
from bill_rewards.ocr_service import get_extractor

# Get the latest uploaded bill
latest = BillUpload.objects.order_by('-created_at').first()
if latest:
    # the extractor expects a file, but we can simulate it by giving the raw_text
    # but the extractor makes an API call.
    # Instead, we just use the new regexes on raw_ocr_text.
    from bill_rewards.ocr_service import _extract_last_number, _parse_date, _parse_decimal
    import re
    full_text = latest.raw_ocr_text
    
    shop_name = ''
    for line in full_text.split('\n'):
        line = line.strip()
        if not line: continue
        only_skip_words = re.sub(r'(?i)\b(e|print|close|x|invoice|receipt)\b|\s+', '', line)
        if not only_skip_words:
            continue
        clean_line = re.sub(r'(?i)\b(e|print|close|x|invoice|receipt)\b', '', line).strip()
        if clean_line:
            shop_name = clean_line
            break
            
    invoice_number = ''
    inv_match = re.search(r'(?:INVOICE|INV|RECEIPT)[ \t]*(?:#|NO\.?)[ \t]*([A-Z0-9\-]+)', full_text, re.IGNORECASE)
    if not inv_match:
        inv_match = re.search(r'^[^\w]*(?:INVOICE|INV)[ \t]+([A-Z0-9\-]+)', full_text, re.IGNORECASE | re.MULTILINE)
    if inv_match:
        invoice_number = inv_match.group(1)
        if invoice_number.upper() in ['OICE', 'ICE', 'CE']:
            invoice_number = ''
            
    bill_date = None
    date_match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,}\s+\d{4})', full_text)
    if date_match:
        bill_date = _parse_date(date_match.group(1))
        
    subtotal = _extract_last_number(r'^[^\w]*(?:SUBTOTAL|SUB\s*TOTAL)', full_text)
    tax_amount = _extract_last_number(r'^[^\w]*(?:TAX|VAT)', full_text)
    total_amount = _extract_last_number(r'^[^\w]*(?:GRAND TOTAL|TOTAL AMOUNT|NET TOTAL|TOTAL\b)', full_text)

    print("----- PARSED DATA -----")
    print(f"Shop: {shop_name}")
    print(f"Invoice: {invoice_number}")
    print(f"Date: {bill_date}")
    print(f"Subtotal: {subtotal}")
    print(f"Tax: {tax_amount}")
    print(f"Total: {total_amount}")

