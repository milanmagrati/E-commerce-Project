"""
OCR Service – pluggable backend for AWS Textract AnalyzeExpense
or Google Document AI.

Falls back to a mock extractor when neither SDK is installed,
so the rest of the app (upload, review, rewards) keeps working
without cloud credentials.
"""
import hashlib
import json
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('bill_rewards')


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _parse_decimal(val):
    """Best-effort parse of a string into Decimal."""
    if val is None:
        return None
    val = str(val).replace(',', '').strip()
    # Remove currency symbols
    val = re.sub(r'[^\d.\-]', '', val)
    try:
        return Decimal(val) if val else None
    except (InvalidOperation, ValueError):
        return None


def _parse_date(val):
    """Best-effort parse of a date string."""
    if not val:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y',
                '%d %b %Y', '%d %B %Y', '%b %d, %Y', '%B %d, %Y'):
        try:
            return datetime.strptime(val.strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _extract_last_number(pattern, text):
    """Finds the line matching the pattern, extracts all numbers, and returns the last one."""
    for line in text.split('\n'):
        if re.search(pattern, line, re.IGNORECASE):
            numbers = re.findall(r'\d+(?:[\.,]\d+)*', line)
            if numbers:
                return _parse_decimal(numbers[-1])
    return None


# ─────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────

class BaseOCRExtractor:
    """Common interface every OCR extractor must implement."""

    provider_name = 'base'

    def extract(self, file_obj):
        """
        Return a dict:
        {
            'raw_json': {},
            'raw_text': '',
            'customer_name': '', 'confidence_customer_name': 0-100,
            'shop_name': '',     'confidence_shop_name': 0-100,
            'invoice_number': '',...
            'bill_date': date|None, ...
            'subtotal': Decimal|None, ...
            'tax_amount': Decimal|None, ...
            'total_amount': Decimal|None, ...
            'overall_confidence': float 0-100,
            'line_items': [
                {'description': '', 'quantity': Decimal, 'unit_price': Decimal,
                 'line_amount': Decimal, 'confidence': float},
                ...
            ]
        }
        """
        raise NotImplementedError


# ─────────────────────────────────────────────────────────
# AWS Textract AnalyzeExpense
# ─────────────────────────────────────────────────────────

class AWSTextractExtractor(BaseOCRExtractor):
    provider_name = 'aws_textract'

    def __init__(self):
        import boto3
        self.client = boto3.client(
            'textract',
            aws_access_key_id=getattr(settings, 'AWS_ACCESS_KEY_ID', ''),
            aws_secret_access_key=getattr(settings, 'AWS_SECRET_ACCESS_KEY', ''),
            region_name=getattr(settings, 'AWS_REGION', 'us-east-1'),
        )

    def extract(self, file_obj):
        file_bytes = file_obj.read()
        file_obj.seek(0)

        response = self.client.analyze_expense(
            Document={'Bytes': file_bytes}
        )

        raw_json = response
        raw_text = ''
        result = {
            'raw_json': _json_safe(raw_json),
            'raw_text': '',
            'line_items': [],
            'overall_confidence': 0,
        }

        for doc in response.get('ExpenseDocuments', []):
            # Summary fields
            for field in doc.get('SummaryFields', []):
                ftype = field.get('Type', {}).get('Text', '')
                fval = field.get('ValueDetection', {}).get('Text', '')
                fconf = field.get('ValueDetection', {}).get('Confidence', 0)
                raw_text += f'{ftype}: {fval}\n'
                self._map_summary_field(result, ftype, fval, fconf)

            # Line item groups
            for group in doc.get('LineItemGroups', []):
                for li in group.get('LineItems', []):
                    item = {'description': '', 'quantity': None,
                            'unit_price': None, 'line_amount': None, 'confidence': 0}
                    confs = []
                    for exp_field in li.get('LineItemExpenseFields', []):
                        ft = exp_field.get('Type', {}).get('Text', '')
                        fv = exp_field.get('ValueDetection', {}).get('Text', '')
                        fc = exp_field.get('ValueDetection', {}).get('Confidence', 0)
                        confs.append(fc)
                        if ft in ('ITEM', 'PRODUCT_CODE', 'DESCRIPTION'):
                            item['description'] = fv
                        elif ft == 'QUANTITY':
                            item['quantity'] = _parse_decimal(fv)
                        elif ft in ('UNIT_PRICE', 'PRICE'):
                            item['unit_price'] = _parse_decimal(fv)
                        elif ft in ('EXPENSE_ROW_AMOUNT', 'AMOUNT'):
                            item['line_amount'] = _parse_decimal(fv)
                    item['confidence'] = sum(confs) / len(confs) if confs else 0
                    result['line_items'].append(item)

        result['raw_text'] = raw_text
        # Compute overall confidence
        all_conf = [v for k, v in result.items()
                    if k.startswith('confidence_') and v is not None]
        result['overall_confidence'] = sum(all_conf) / len(all_conf) if all_conf else 0
        return result

    @staticmethod
    def _map_summary_field(result, ftype, fval, fconf):
        mapping = {
            'NAME': ('customer_name', 'confidence_customer_name'),
            'CUSTOMER_NAME': ('customer_name', 'confidence_customer_name'),
            'RECEIVER_NAME': ('customer_name', 'confidence_customer_name'),
            'VENDOR_NAME': ('shop_name', 'confidence_shop_name'),
            'SUPPLIER_NAME': ('shop_name', 'confidence_shop_name'),
            'INVOICE_RECEIPT_ID': ('invoice_number', 'confidence_invoice_number'),
            'INVOICE_RECEIPT_DATE': ('bill_date_raw', 'confidence_bill_date'),
            'SUBTOTAL': ('subtotal_raw', 'confidence_subtotal'),
            'TAX': ('tax_raw', 'confidence_tax'),
            'TOTAL': ('total_raw', 'confidence_total'),
            'AMOUNT_DUE': ('total_raw', 'confidence_total'),
        }
        key_pair = mapping.get(ftype.upper())
        if key_pair:
            result[key_pair[0]] = fval
            result[key_pair[1]] = fconf

        # Post-process
        if 'bill_date_raw' in result:
            result['bill_date'] = _parse_date(result.pop('bill_date_raw'))
        for raw_key, dec_key in [('subtotal_raw', 'subtotal'),
                                 ('tax_raw', 'tax_amount'),
                                 ('total_raw', 'total_amount')]:
            if raw_key in result:
                result[dec_key] = _parse_decimal(result.pop(raw_key))


# ─────────────────────────────────────────────────────────
# Google Document AI
# ─────────────────────────────────────────────────────────

class GoogleDocAIExtractor(BaseOCRExtractor):
    provider_name = 'google_docai'

    def __init__(self):
        from google.cloud import documentai_v1 as documentai
        self.documentai = documentai
        self.project_id = getattr(settings, 'GOOGLE_DOCAI_PROJECT', '')
        self.location = getattr(settings, 'GOOGLE_DOCAI_LOCATION', 'us')
        self.processor_id = getattr(settings, 'GOOGLE_DOCAI_PROCESSOR_ID', '')

    def extract(self, file_obj):
        file_bytes = file_obj.read()
        file_obj.seek(0)

        client = self.documentai.DocumentProcessorServiceClient()
        name = client.processor_path(self.project_id, self.location, self.processor_id)

        raw_doc = self.documentai.RawDocument(content=file_bytes, mime_type='application/pdf')
        request = self.documentai.ProcessRequest(name=name, raw_document=raw_doc)
        resp = client.process_document(request=request)
        document = resp.document

        result = {
            'raw_json': {'text': document.text[:5000]},
            'raw_text': document.text[:5000],
            'line_items': [],
            'overall_confidence': 0,
        }

        for entity in document.entities:
            etype = entity.type_
            etext = entity.mention_text
            econf = (entity.confidence or 0) * 100

            if etype in ('supplier_name', 'vendor_name'):
                result['shop_name'] = etext
                result['confidence_shop_name'] = econf
            elif etype in ('receiver_name', 'customer_name'):
                result['customer_name'] = etext
                result['confidence_customer_name'] = econf
            elif etype in ('invoice_id', 'invoice_number'):
                result['invoice_number'] = etext
                result['confidence_invoice_number'] = econf
            elif etype in ('invoice_date', 'receipt_date'):
                result['bill_date'] = _parse_date(etext)
                result['confidence_bill_date'] = econf
            elif etype == 'net_amount':
                result['subtotal'] = _parse_decimal(etext)
                result['confidence_subtotal'] = econf
            elif etype in ('total_tax_amount', 'tax_amount'):
                result['tax_amount'] = _parse_decimal(etext)
                result['confidence_tax'] = econf
            elif etype in ('total_amount', 'amount_due'):
                result['total_amount'] = _parse_decimal(etext)
                result['confidence_total'] = econf
            elif etype == 'line_item':
                item = {'description': '', 'quantity': None,
                        'unit_price': None, 'line_amount': None, 'confidence': econf}
                for prop in entity.properties:
                    if prop.type_ in ('line_item/description', 'line_item/product_code'):
                        item['description'] = prop.mention_text
                    elif prop.type_ == 'line_item/quantity':
                        item['quantity'] = _parse_decimal(prop.mention_text)
                    elif prop.type_ in ('line_item/unit_price', 'line_item/price'):
                        item['unit_price'] = _parse_decimal(prop.mention_text)
                    elif prop.type_ == 'line_item/amount':
                        item['line_amount'] = _parse_decimal(prop.mention_text)
                result['line_items'].append(item)

        all_conf = [v for k, v in result.items()
                    if k.startswith('confidence_') and v is not None]
        result['overall_confidence'] = sum(all_conf) / len(all_conf) if all_conf else 0
        return result


# ─────────────────────────────────────────────────────────
# OCR.space
# ─────────────────────────────────────────────────────────

class OCRSpaceExtractor(BaseOCRExtractor):
    provider_name = 'ocr_space'

    def __init__(self):
        self.api_key = getattr(settings, 'OCR_SPACE_API_KEY', '')
        if not self.api_key:
            raise ValueError("OCR_SPACE_API_KEY is not set in settings")

    def extract(self, file_obj):
        import requests
        
        file_bytes = file_obj.read()
        file_obj.seek(0)
        
        url = 'https://api.ocr.space/parse/image'
        
        payload = {
            'apikey': self.api_key,
            'isOverlayRequired': False,
            'language': 'eng',
            'isTable': True,
            'scale': True
        }
        
        import os
        filename = getattr(file_obj, 'name', 'image.jpg')
        filename = os.path.basename(filename)
        if not os.path.splitext(filename)[1]:
            filename += '.jpg'
            
        files = {'file': (filename, file_bytes)}
        
        try:
            response = requests.post(url, data=payload, files=files, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"OCR.space API request failed: {e}")
            raise e
            
        if data.get('IsErroredOnProcessing'):
            err = data.get('ErrorMessage', 'Unknown error')
            if isinstance(err, list):
                err = err[0]
            logger.error(f"OCR.space processing error: {err}")
            raise Exception(f"OCR.space error: {err}")
            
        parsed_results = data.get('ParsedResults', [])
        if not parsed_results:
            full_text = ""
        else:
            full_text = parsed_results[0].get('ParsedText', '')
            
        raw_text_lines = full_text
        
        shop_name = ''
        for line in full_text.split('\n'):
            line = line.strip()
            if not line: continue
            # Skip lines that only contain common UI words like 'print', 'close', 'x'
            only_skip_words = re.sub(r'(?i)\b(e|print|close|x|invoice|receipt)\b|\s+', '', line)
            if not only_skip_words:
                continue
            # Remove those words to get the real shop name
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
            
        return {
            'raw_json': data,
            'raw_text': raw_text_lines,
            'customer_name': '',
            'confidence_customer_name': 0,
            'shop_name': shop_name,
            'confidence_shop_name': 70 if shop_name else 0,
            'invoice_number': invoice_number,
            'confidence_invoice_number': 80 if invoice_number else 0,
            'bill_date': bill_date,
            'confidence_bill_date': 80 if bill_date else 0,
            'subtotal': subtotal,
            'confidence_subtotal': 80 if subtotal else 0,
            'tax_amount': tax_amount,
            'confidence_tax': 80 if tax_amount else 0,
            'total_amount': total_amount,
            'confidence_total': 80 if total_amount else 0,
            'overall_confidence': 75,
            'line_items': [],
        }



# ─────────────────────────────────────────────────────────
# Mock extractor (fallback when no cloud SDK)
# ─────────────────────────────────────────────────────────

class MockExtractor(BaseOCRExtractor):
    """
    Deterministic mock that returns placeholder data so the
    full upload → review → reward workflow can be tested locally.
    """
    provider_name = 'manual'

    def extract(self, file_obj):
        file_bytes = file_obj.read()
        file_obj.seek(0)
        h = hashlib.md5(file_bytes).hexdigest()[:8]
        
        import random
        # Seed random with hash so same image yields same mock data
        random.seed(h)
        
        random_subtotal = Decimal(str(random.randint(100, 5000)) + '.00')
        random_tax = random_subtotal * Decimal('0.13')
        random_total = random_subtotal + random_tax
        
        shop_names = ['SuperMart', 'Trendy Shopping', 'Tech Gadgets', 'Local Grocery', 'MegaStore']
        customer_names = ['Violet Daniel', 'John Doe', 'Jane Smith', 'Alice Johnson']
        
        return {
            'raw_json': {'mock': True, 'hash': h},
            'raw_text': f'[Mock OCR] file hash {h}\nSimulated dynamic extraction.',
            'customer_name': random.choice(customer_names),
            'confidence_customer_name': random.randint(80, 99),
            'shop_name': random.choice(shop_names),
            'confidence_shop_name': random.randint(80, 99),
            'invoice_number': f'INV-{h.upper()}',
            'confidence_invoice_number': random.randint(80, 99),
            'bill_date': timezone.now().date() - __import__('datetime').timedelta(days=random.randint(0, 30)),
            'confidence_bill_date': random.randint(80, 99),
            'subtotal': random_subtotal,
            'confidence_subtotal': random.randint(80, 99),
            'tax_amount': random_tax,
            'confidence_tax': random.randint(80, 99),
            'total_amount': random_total,
            'confidence_total': random.randint(80, 99),
            'overall_confidence': random.randint(80, 99),
            'line_items': [
                {
                    'description': f'Sample Item {h[:4]}',
                    'quantity': Decimal('1'),
                    'unit_price': random_subtotal,
                    'line_amount': random_subtotal,
                    'confidence': random.randint(80, 99)
                }
            ],
        }


# ─────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────

def get_extractor():
    """Return the best available OCR extractor."""
    provider = getattr(settings, 'BILL_OCR_PROVIDER', 'auto')

    if provider == 'aws_textract':
        try:
            return AWSTextractExtractor()
        except Exception as e:
            logger.warning(f'AWS Textract init failed: {e}; falling back to mock')

    elif provider == 'google_docai':
        try:
            return GoogleDocAIExtractor()
        except Exception as e:
            logger.warning(f'Google Document AI init failed: {e}; falling back to mock')

    elif provider == 'ocr_space':
        try:
            return OCRSpaceExtractor()
        except Exception as e:
            logger.warning(f'OCR.space init failed: {e}; falling back to mock')

    elif provider == 'auto':
        # Try AWS if keys exist
        aws_key = getattr(settings, 'AWS_ACCESS_KEY_ID', '')
        if aws_key:
            try:
                return AWSTextractExtractor()
            except Exception:
                pass
        
        # Try Google if project exists
        google_proj = getattr(settings, 'GOOGLE_DOCAI_PROJECT', '')
        if google_proj:
            try:
                return GoogleDocAIExtractor()
            except Exception:
                pass

        # Try OCR.space if key exists
        ocr_space_key = getattr(settings, 'OCR_SPACE_API_KEY', '')
        if ocr_space_key:
            try:
                return OCRSpaceExtractor()
            except Exception:
                pass

    logger.info('Using MockExtractor – configure BILL_OCR_PROVIDER for production')
    return MockExtractor()


def _json_safe(obj):
    """Make boto3 response JSON-serializable."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(i) for i in obj]
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj
