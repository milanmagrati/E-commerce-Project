"""
Views for the Bill OCR & Rewards system.
All views are login-required and admin/administrator-restricted.
"""
import json
import logging
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Avg, Q, F
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET

from .models import (
    BillUpload, ExtractedBill, ExtractedLineItem,
    ProductAlias, RewardConfig, RewardTransaction,
    CustomerRewardBalance, BillProductTrend,
)
from .tasks import process_bill_ocr
from .rewards_service import calculate_points, credit_points
from .matching_service import match_product, create_alias_from_match

logger = logging.getLogger('bill_rewards')


# ─────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────

@login_required
def bill_rewards_dashboard(request):
    """Main dashboard for Bill OCR & Rewards."""
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)

    total_bills = BillUpload.objects.count()
    pending_review = BillUpload.objects.filter(status='review').count()
    processed_today = BillUpload.objects.filter(
        created_at__date=now.date(), status__in=['completed', 'approved']
    ).count()
    failed_bills = BillUpload.objects.filter(status='failed').count()

    # Reward stats
    total_points_earned = RewardTransaction.objects.filter(
        transaction_type='earned'
    ).aggregate(total=Sum('points'))['total'] or 0
    total_points_redeemed = RewardTransaction.objects.filter(
        transaction_type='redeemed'
    ).aggregate(total=Sum('points'))['total'] or 0

    # Recent bills
    recent_bills = BillUpload.objects.select_related(
        'uploaded_by', 'customer'
    ).order_by('-created_at')[:10]

    # Trending products (last 30 days)
    trending = BillProductTrend.objects.filter(
        period_date__gte=thirty_days_ago
    ).values('product__name').annotate(
        total_qty=Sum('total_quantity'),
        total_amt=Sum('total_amount'),
        bills=Sum('bill_count'),
    ).order_by('-total_qty')[:10]

    # Bills per day chart data (last 30 days)
    from django.db.models.functions import TruncDate
    bills_per_day = BillUpload.objects.filter(
        created_at__gte=thirty_days_ago
    ).annotate(day=TruncDate('created_at')).values('day').annotate(
        count=Count('id')
    ).order_by('day')

    chart_labels = [item['day'].strftime('%b %d') for item in bills_per_day]
    chart_data = [item['count'] for item in bills_per_day]

    # Reward config
    reward_config = RewardConfig.get_config()

    context = {
        'total_bills': total_bills,
        'pending_review': pending_review,
        'processed_today': processed_today,
        'failed_bills': failed_bills,
        'total_points_earned': total_points_earned,
        'total_points_redeemed': abs(total_points_redeemed),
        'recent_bills': recent_bills,
        'trending_products': trending,
        'chart_labels': json.dumps(chart_labels),
        'chart_data': json.dumps(chart_data),
        'reward_config': reward_config,
    }
    return render(request, 'bill_rewards/dashboard.html', context)


# ─────────────────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────────────────

@login_required
def bill_upload(request):
    """Upload bill page and POST handler."""
    if request.method == 'POST':
        files = request.FILES.getlist('bill_files')
        customer_id = request.POST.get('customer_id')
        customer = None

        if customer_id:
            from dashboard.models import Customer
            try:
                customer = Customer.objects.get(pk=customer_id)
            except Customer.DoesNotExist:
                pass

        uploads = []
        for f in files:
            upload = BillUpload.objects.create(
                file=f,
                file_name=f.name,
                uploaded_by=request.user,
                customer=customer,
            )
            uploads.append(upload)
            # Trigger async processing
            try:
                process_bill_ocr.delay(str(upload.pk))
            except Exception:
                # Fallback to sync if Celery not available
                process_bill_ocr(str(upload.pk))

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': f'{len(uploads)} bill(s) uploaded and queued for processing.',
                'upload_ids': [str(u.pk) for u in uploads],
            })

        return redirect('bill_rewards:bill_list')

    # GET – render upload form
    from dashboard.models import Customer
    customers = Customer.objects.filter(is_active=True).order_by('name')[:100]
    return render(request, 'bill_rewards/upload.html', {'customers': customers})


# ─────────────────────────────────────────────────────────
# List / Detail
# ─────────────────────────────────────────────────────────

@login_required
def bill_list(request):
    """List all bill uploads with filtering."""
    status_filter = request.GET.get('status', '')
    search = request.GET.get('search', '')

    bills = BillUpload.objects.select_related('uploaded_by', 'customer').all()

    if status_filter:
        bills = bills.filter(status=status_filter)
    if search:
        bills = bills.filter(
            Q(file_name__icontains=search) |
            Q(customer__name__icontains=search) |
            Q(extracted__invoice_number__icontains=search) |
            Q(extracted__shop_name__icontains=search)
        ).distinct()

    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(bills, 20)
    page = request.GET.get('page', 1)
    bills_page = paginator.get_page(page)

    status_counts = {
        'all': BillUpload.objects.count(),
        'pending': BillUpload.objects.filter(status='pending').count(),
        'processing': BillUpload.objects.filter(status='processing').count(),
        'completed': BillUpload.objects.filter(status='completed').count(),
        'review': BillUpload.objects.filter(status='review').count(),
        'approved': BillUpload.objects.filter(status='approved').count(),
        'rejected': BillUpload.objects.filter(status='rejected').count(),
        'failed': BillUpload.objects.filter(status='failed').count(),
    }

    context = {
        'bills': bills_page,
        'status_filter': status_filter,
        'search': search,
        'status_counts': status_counts,
    }
    return render(request, 'bill_rewards/bill_list.html', context)


@login_required
def bill_detail(request, bill_id):
    """Detailed view of a single bill with extracted data."""
    upload = get_object_or_404(BillUpload, pk=bill_id)
    extracted = getattr(upload, 'extracted', None)
    line_items = extracted.line_items.select_related(
        'matched_product', 'predicted_category'
    ).all() if extracted else []

    # Calculate potential reward points
    total = extracted.total_amount if extracted else None
    potential_points = calculate_points(total) if total else 0

    context = {
        'upload': upload,
        'extracted': extracted,
        'line_items': line_items,
        'potential_points': potential_points,
    }
    return render(request, 'bill_rewards/bill_detail.html', context)


# ─────────────────────────────────────────────────────────
# Admin Review Actions
# ─────────────────────────────────────────────────────────

@login_required
@require_POST
def bill_approve(request, bill_id):
    """Approve a bill and optionally credit reward points."""
    upload = get_object_or_404(BillUpload, pk=bill_id)
    extracted = getattr(upload, 'extracted', None)

    upload.status = 'approved'
    upload.reviewed_by = request.user
    upload.reviewed_at = timezone.now()
    upload.review_notes = request.POST.get('review_notes', '')
    upload.save()

    # Credit reward points if customer is linked
    if upload.customer and extracted and extracted.total_amount:
        points = calculate_points(extracted.total_amount)
        if points > 0:
            credit_points(upload.customer, upload, points, user=request.user)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'status': 'approved'})
    return redirect('bill_rewards:bill_detail', bill_id=bill_id)


@login_required
@require_POST
def bill_reject(request, bill_id):
    """Reject a bill."""
    upload = get_object_or_404(BillUpload, pk=bill_id)
    upload.status = 'rejected'
    upload.reviewed_by = request.user
    upload.reviewed_at = timezone.now()
    upload.review_notes = request.POST.get('review_notes', '')
    upload.save()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'status': 'rejected'})
    return redirect('bill_rewards:bill_detail', bill_id=bill_id)


@login_required
@require_POST
def bill_reprocess(request, bill_id):
    """Re-run OCR on a bill."""
    upload = get_object_or_404(BillUpload, pk=bill_id)
    upload.status = 'pending'
    upload.error_message = ''
    upload.save()

    try:
        process_bill_ocr.delay(str(upload.pk))
    except Exception:
        process_bill_ocr(str(upload.pk))

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'message': 'Reprocessing started'})
    return redirect('bill_rewards:bill_detail', bill_id=bill_id)


# ─────────────────────────────────────────────────────────
# Line Item Product Matching
# ─────────────────────────────────────────────────────────

@login_required
@require_POST
def line_item_set_product(request, item_id):
    """Manually match a line item to a product."""
    from dashboard.models import Product

    item = get_object_or_404(ExtractedLineItem, pk=item_id)
    product_id = request.POST.get('product_id')
    create_alias = request.POST.get('create_alias', '') == '1'

    try:
        product = Product.objects.get(pk=product_id)
    except Product.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Product not found'}, status=404)

    item.matched_product = product
    item.match_confidence = 100
    item.match_method = 'manual'
    item.save()

    # Optionally create alias for future auto-matching
    if create_alias and item.raw_description:
        create_alias_from_match(item.raw_description, product, user=request.user)

    return JsonResponse({
        'success': True,
        'product_name': product.name,
        'match_method': 'manual',
    })


# ─────────────────────────────────────────────────────────
# Product Aliases Management
# ─────────────────────────────────────────────────────────

@login_required
def alias_list(request):
    """List and manage product aliases."""
    aliases = ProductAlias.objects.select_related('product', 'created_by').all()
    search = request.GET.get('search', '')
    if search:
        aliases = aliases.filter(
            Q(alias_name__icontains=search) | Q(product__name__icontains=search)
        )

    from django.core.paginator import Paginator
    paginator = Paginator(aliases, 25)
    page = request.GET.get('page', 1)

    context = {
        'aliases': paginator.get_page(page),
        'search': search,
    }
    return render(request, 'bill_rewards/alias_list.html', context)


@login_required
@require_POST
def alias_delete(request, alias_id):
    """Delete a product alias."""
    alias = get_object_or_404(ProductAlias, pk=alias_id)
    alias.delete()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True})
    return redirect('bill_rewards:alias_list')


# ─────────────────────────────────────────────────────────
# Reward Points
# ─────────────────────────────────────────────────────────

@login_required
def reward_transactions(request):
    """View reward transaction history."""
    transactions = RewardTransaction.objects.select_related(
        'customer', 'bill_upload', 'created_by'
    ).all()

    customer_filter = request.GET.get('customer', '')
    type_filter = request.GET.get('type', '')

    if customer_filter:
        transactions = transactions.filter(customer_id=customer_filter)
    if type_filter:
        transactions = transactions.filter(transaction_type=type_filter)

    from django.core.paginator import Paginator
    paginator = Paginator(transactions, 25)
    page = request.GET.get('page', 1)

    # Top customers by balance
    top_customers = CustomerRewardBalance.objects.select_related(
        'customer'
    ).order_by('-current_balance')[:10]

    context = {
        'transactions': paginator.get_page(page),
        'customer_filter': customer_filter,
        'type_filter': type_filter,
        'top_customers': top_customers,
    }
    return render(request, 'bill_rewards/reward_transactions.html', context)


@login_required
def reward_config_view(request):
    """View/edit reward configuration."""
    config = RewardConfig.get_config()

    if request.method == 'POST':
        config.points_per_currency_unit = Decimal(request.POST.get('points_per_currency_unit', '1'))
        config.min_bill_total_for_reward = Decimal(request.POST.get('min_bill_total', '100'))
        config.max_points_per_bill = int(request.POST.get('max_points_per_bill', '5000'))
        config.points_expiry_days = int(request.POST.get('points_expiry_days', '365'))
        config.is_active = request.POST.get('is_active') == '1'
        config.save()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return redirect('bill_rewards:dashboard')

    return render(request, 'bill_rewards/reward_config.html', {'config': config})


# ─────────────────────────────────────────────────────────
# Trending Products / Analytics
# ─────────────────────────────────────────────────────────

@login_required
def trending_analytics(request):
    """Product trending analytics from bill data."""
    days = int(request.GET.get('days', 30))
    period_start = timezone.now().date() - timedelta(days=days)

    # Products appearing most in bills
    top_products = ExtractedLineItem.objects.filter(
        matched_product__isnull=False,
        bill__upload__status__in=['completed', 'approved'],
        bill__bill_date__gte=period_start,
    ).values(
        'matched_product__id', 'matched_product__name'
    ).annotate(
        total_qty=Sum('quantity'),
        total_amount=Sum('line_amount'),
        bill_count=Count('bill', distinct=True),
        avg_price=Avg('unit_price'),
    ).order_by('-total_qty')[:20]

    # Unmatched items (potential new products)
    unmatched = ExtractedLineItem.objects.filter(
        matched_product__isnull=True,
        bill__upload__status__in=['completed', 'approved'],
        bill__bill_date__gte=period_start,
    ).values('raw_description').annotate(
        count=Count('id'),
        total_amount=Sum('line_amount'),
    ).order_by('-count')[:20]

    # Shop frequency
    top_shops = ExtractedBill.objects.filter(
        upload__status__in=['completed', 'approved'],
        bill_date__gte=period_start,
    ).exclude(shop_name='').values('shop_name').annotate(
        bill_count=Count('id'),
        total_amount=Sum('total_amount'),
    ).order_by('-bill_count')[:15]

    context = {
        'top_products': top_products,
        'unmatched_items': unmatched,
        'top_shops': top_shops,
        'days': days,
    }
    return render(request, 'bill_rewards/trending.html', context)


# ─────────────────────────────────────────────────────────
# API endpoints for AJAX
# ─────────────────────────────────────────────────────────

@login_required
@require_GET
def api_bill_status(request, bill_id):
    """Check processing status of a bill (used for polling)."""
    try:
        upload = BillUpload.objects.get(pk=bill_id)
    except BillUpload.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    return JsonResponse({
        'id': str(upload.pk),
        'status': upload.status,
        'status_display': upload.get_status_display(),
        'confidence': upload.ocr_confidence,
        'is_duplicate': upload.is_duplicate,
        'error_message': upload.error_message,
    })


@login_required
@require_GET
def api_search_products_for_match(request):
    """Search products for manual matching."""
    from dashboard.models import Product
    q = request.GET.get('q', '')
    if len(q) < 2:
        return JsonResponse({'results': []})

    products = Product.objects.filter(
        is_deleted=False, is_active=True
    ).filter(
        Q(name__icontains=q) | Q(barcode__icontains=q)
    )[:15]

    return JsonResponse({
        'results': [
            {'id': p.id, 'name': p.name, 'price': str(p.price), 'barcode': p.barcode or ''}
            for p in products
        ]
    })


@login_required
@require_POST
def api_bill_update_field(request, bill_id):
    """Update a single field on the extracted bill (inline editing)."""
    upload = get_object_or_404(BillUpload, pk=bill_id)
    extracted = getattr(upload, 'extracted', None)
    if not extracted:
        return JsonResponse({'success': False, 'error': 'No extracted data'}, status=400)

    field = request.POST.get('field', '')
    value = request.POST.get('value', '')

    allowed_fields = [
        'customer_name', 'shop_name', 'invoice_number',
        'subtotal', 'tax_amount', 'total_amount',
    ]

    if field == 'bill_date':
        from .ocr_service import _parse_date
        extracted.bill_date = _parse_date(value)
    elif field in allowed_fields:
        if field in ('subtotal', 'tax_amount', 'total_amount'):
            from .ocr_service import _parse_decimal
            setattr(extracted, field, _parse_decimal(value))
        else:
            setattr(extracted, field, value)
    else:
        return JsonResponse({'success': False, 'error': 'Invalid field'}, status=400)

    extracted.save()
    return JsonResponse({'success': True})
