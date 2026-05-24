"""
Celery tasks for async bill OCR processing.

When Celery is not installed / not running, the views fall back to
synchronous processing so the system never hard-fails.
"""
import logging

from django.utils import timezone

logger = logging.getLogger('bill_rewards')

# Try to import Celery – if not installed, define plain functions
try:
    from celery import shared_task
    CELERY_AVAILABLE = True
except ImportError:
    CELERY_AVAILABLE = False
    # Create a no-op decorator so the functions still work synchronously
    def shared_task(func=None, **kwargs):
        if func:
            func.delay = func            # .delay() just calls it inline
            func.apply_async = lambda *a, **kw: func(*a)
            return func
        def wrapper(fn):
            fn.delay = fn
            fn.apply_async = lambda *a, **kw: fn(*a)
            return fn
        return wrapper


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def process_bill_ocr(self, bill_upload_id):
    """
    Main async task: runs OCR, stores results, matches products,
    detects duplicates, and flags low-confidence bills for review.
    """
    from .models import BillUpload, ExtractedBill, ExtractedLineItem
    from .ocr_service import get_extractor
    from .matching_service import match_product, predict_category

    try:
        upload = BillUpload.objects.get(pk=bill_upload_id)
    except BillUpload.DoesNotExist:
        logger.error(f'BillUpload {bill_upload_id} not found')
        return

    # Mark as processing
    upload.status = 'processing'
    upload.processing_started_at = timezone.now()
    if CELERY_AVAILABLE and self and hasattr(self, 'request'):
        upload.celery_task_id = self.request.id or ''
    upload.save(update_fields=['status', 'processing_started_at', 'celery_task_id'])

    try:
        # 1. Run OCR extraction
        extractor = get_extractor()
        upload.ocr_provider = extractor.provider_name
        data = extractor.extract(upload.file)

        # 2. Store raw results
        upload.raw_ocr_json = data.get('raw_json', {})
        upload.raw_ocr_text = data.get('raw_text', '')
        upload.ocr_confidence = data.get('overall_confidence', 0)

        # 3. Duplicate detection via invoice_number + shop_name
        invoice_number = data.get('invoice_number', '')
        shop_name = data.get('shop_name', '')
        if invoice_number:
            dup = BillUpload.objects.filter(
                extracted__invoice_number=invoice_number,
                extracted__shop_name__iexact=shop_name,
            ).exclude(pk=upload.pk).first()
            if dup:
                upload.is_duplicate = True
                upload.duplicate_of = dup

        # Also check by file hash
        if upload.file_hash:
            hash_dup = BillUpload.objects.filter(
                file_hash=upload.file_hash
            ).exclude(pk=upload.pk).first()
            if hash_dup:
                upload.is_duplicate = True
                upload.duplicate_of = hash_dup

        # 4. Create ExtractedBill
        extracted, _ = ExtractedBill.objects.update_or_create(
            upload=upload,
            defaults={
                'customer_name': data.get('customer_name', ''),
                'shop_name': data.get('shop_name', ''),
                'invoice_number': invoice_number,
                'bill_date': data.get('bill_date'),
                'subtotal': data.get('subtotal'),
                'tax_amount': data.get('tax_amount'),
                'total_amount': data.get('total_amount'),
                'confidence_customer_name': data.get('confidence_customer_name'),
                'confidence_shop_name': data.get('confidence_shop_name'),
                'confidence_invoice_number': data.get('confidence_invoice_number'),
                'confidence_bill_date': data.get('confidence_bill_date'),
                'confidence_subtotal': data.get('confidence_subtotal'),
                'confidence_tax': data.get('confidence_tax'),
                'confidence_total': data.get('confidence_total'),
            },
        )

        # 5. Create line items with product matching
        ExtractedLineItem.objects.filter(bill=extracted).delete()
        for idx, li in enumerate(data.get('line_items', [])):
            product, confidence, method = match_product(li.get('description', ''))
            cat = predict_category(li.get('description', '')) if not product else None

            ExtractedLineItem.objects.create(
                bill=extracted,
                raw_description=li.get('description', '')[:500],
                quantity=li.get('quantity'),
                unit_price=li.get('unit_price'),
                line_amount=li.get('line_amount'),
                matched_product=product,
                match_confidence=confidence,
                match_method=method,
                predicted_category=cat,
                order_index=idx,
            )

        # 6. Determine final status
        LOW_CONFIDENCE_THRESHOLD = getattr(
            __import__('django.conf', fromlist=['settings']).settings,
            'BILL_OCR_REVIEW_THRESHOLD', 70
        )

        if upload.is_duplicate:
            upload.status = 'review'
        elif upload.ocr_confidence and upload.ocr_confidence < LOW_CONFIDENCE_THRESHOLD:
            upload.status = 'review'
        else:
            upload.status = 'completed'

        upload.processing_completed_at = timezone.now()
        upload.save()

        logger.info(f'Bill {upload.pk} processed: status={upload.status}, '
                     f'confidence={upload.ocr_confidence}, provider={upload.ocr_provider}')

    except Exception as exc:
        upload.status = 'failed'
        upload.error_message = str(exc)[:1000]
        upload.processing_completed_at = timezone.now()
        upload.save()
        logger.exception(f'Bill OCR failed for {upload.pk}')
        if CELERY_AVAILABLE and self and hasattr(self, 'retry'):
            raise self.retry(exc=exc)


@shared_task
def update_product_trends():
    """
    Periodic task to aggregate product trend data from bills.
    Should be run weekly/daily via Celery Beat or cron.
    """
    from datetime import timedelta
    from django.db.models import Sum, Count
    from .models import ExtractedLineItem, BillProductTrend

    today = timezone.now().date()
    # Process last 7 days
    week_start = today - timedelta(days=today.weekday())

    items = ExtractedLineItem.objects.filter(
        matched_product__isnull=False,
        bill__upload__status__in=['completed', 'approved'],
        bill__bill_date__gte=week_start,
        bill__bill_date__lte=today,
    ).values('matched_product').annotate(
        total_qty=Sum('quantity'),
        total_amt=Sum('line_amount'),
        count=Count('id'),
    )

    for item in items:
        BillProductTrend.objects.update_or_create(
            product_id=item['matched_product'],
            period_date=week_start,
            period_type='week',
            defaults={
                'total_quantity': item['total_qty'] or 0,
                'total_amount': item['total_amt'] or 0,
                'bill_count': item['count'],
            },
        )

    logger.info(f'Updated product trends for week starting {week_start}')
