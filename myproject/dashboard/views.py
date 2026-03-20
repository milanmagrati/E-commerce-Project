from urllib import request
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.views.decorators.http import require_POST, require_http_methods
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from django.contrib import messages
from django.db.models import Sum, Count, Q, F, Prefetch, Min, Max, Avg
from django.db.models.functions import TruncDate
from django.http import JsonResponse, HttpResponse, Http404
from django.core.paginator import Paginator
from datetime import datetime, timedelta
import pytz
import requests
from .models import (Product, Order, OrderItem, Category, Customer,
                     ProductVariation, ProductImage, ProductVariantOption,
                     OrderActivityLog, StockIn, City, StockInItem, Setup,
                     Supplier, Purchase, PurchaseItem, SupplierPayment,
                     BundleComponent, ProductPurchase)
from decimal import Decimal, InvalidOperation
import json
from .forms import ProductForm, ProductVariationForm, ProductVariationFormSet, CustomerForm, OrderForm
from .decimal_utils import safe_decimal, validate_decimal_fields
from django.db import IntegrityError, transaction, connection
from django.utils import timezone
import traceback
import uuid, os
import logging

logger = logging.getLogger(__name__)

from django.core.files.storage import default_storage
from django.core.files.base import File
from django.conf import settings
from django.utils.text import slugify
from .models import ReturnRequest, ReturnItem, ReturnActivityLog, Dispatch, DispatchItem, StaffTarget, OrderFollowUp

# IMPORT DECORATORS
from accounts.decorators import permission_required, admin_only

# GET CUSTOM USER MODEL
User = get_user_model()


def fix_order_decimals(order):
    """Fix any NULL decimal values in order and recalculate totals"""
    if order.discount_amount is None:
        order.discount_amount = Decimal("0")
    if order.shipping_charge is None:
        order.shipping_charge = Decimal("0")
    if order.tax_percent is None:
        order.tax_percent = Decimal("0")
    if order.total_amount is None:
        order.total_amount = Decimal("0")
    
    # ✅ RECALCULATE TOTAL AMOUNT FROM ITEMS
    try:
        subtotal = sum(item.total for item in order.items.all()) or Decimal('0.00')
        after_discount = subtotal - (order.discount_amount or Decimal('0'))
        tax_amount = (after_discount * (order.tax_percent or Decimal('0'))) / 100
        calculated_total = after_discount + tax_amount + (order.shipping_charge or Decimal('0'))
        
        # Update total_amount if it was capped or incorrect
        if order.total_amount != calculated_total:
            order.total_amount = calculated_total
            order.save()  # Save the corrected total
    except Exception as e:
        # If calculation fails, at least set to zero instead of capped value
        import logging
        logging.error(f"Error recalculating order {order.id} totals: {e}")
        if order.total_amount > Decimal('99999999.99'):
            order.total_amount = Decimal('0')
    
    # ENHANCED PARTIAL PAYMENT DECIMAL FIXES
    if order.partial_amount_paid is None:
        order.partial_amount_paid = Decimal("0")
    if order.remaining_amount is None:
        # Auto-calculate remaining if partial payment
        if order.is_partial_payment or order.payment_status == 'partial':
            order.remaining_amount = order.total_amount - order.partial_amount_paid
            if order.remaining_amount < Decimal("0"):
                order.remaining_amount = Decimal("0")
        else:
            order.remaining_amount = Decimal("0")
    
    # ENSURE is_partial_payment IS SYNCED WITH PAYMENT_STATUS
    if order.payment_status == 'partial' and not order.is_partial_payment:
        order.is_partial_payment = True
    elif order.payment_status != 'partial' and order.is_partial_payment:
        order.is_partial_payment = False
    
    return order


def sync_order_status_setup(order):
    """
    Bidirectional sync between string fields and FK Setup records.
    Ensures order.order_status matches order.status_setup,
    order.payment_method matches order.payment_setup, etc.
    """
    from .models import Setup
    
    needs_save = False
    
    # ====== SYNC ORDER STATUS ======
    if order.order_status:
        if order.status_setup:
            # Both exist - ensure they match
            expected = order.status_setup.name.lower().replace(' ', '_')
            if order.order_status != expected:
                order.order_status = expected
                needs_save = True
        else:
            # No FK - create/lookup Setup matching the string
            try:
                setup_name = order.order_status.replace('_', ' ').title()
                setup, _ = Setup.objects.get_or_create(
                    setup_type='status',
                    name=setup_name,
                    defaults={'is_active': True}
                )
                order.status_setup = setup
                needs_save = True
            except Exception:
                pass
    
    # ====== SYNC PAYMENT STATUS ======
    if order.payment_status:
        if order.payment_status_setup:
            # Both exist - ensure they match
            expected = order.payment_status_setup.name.lower().replace(' ', '_')
            if order.payment_status != expected:
                order.payment_status = expected
                needs_save = True
        else:
            # No FK - create/lookup Setup matching the string
            try:
                setup_name = order.payment_status.replace('_', ' ').title()
                setup, _ = Setup.objects.get_or_create(
                    setup_type='payment_status',
                    name=setup_name,
                    defaults={'is_active': True}
                )
                order.payment_status_setup = setup
                needs_save = True
            except Exception:
                pass
    
    # ====== SYNC PAYMENT METHOD ======
    if order.payment_method:
        if order.payment_setup:
            # Both exist - ensure they match
            expected = order.payment_setup.name.lower().replace(' ', '_')
            if order.payment_method != expected:
                order.payment_method = expected
                needs_save = True
        else:
            # No FK - create/lookup Setup matching the string
            try:
                setup_name = order.payment_method.replace('_', ' ').title()
                setup, _ = Setup.objects.get_or_create(
                    setup_type='payment',
                    name=setup_name,
                    defaults={'is_active': True}
                )
                order.payment_setup = setup
                needs_save = True
            except Exception:
                pass
    
    # Save if changes made
    if needs_save:
        try:
            order.save()
        except Exception:
            pass
    
    return order
    
    return order


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            request.session.set_expiry(43200)  # 12-hour session per user
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid username or password')
    
    return render(request, 'login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def dashboard_view(request):
    # System-wide product and order data (show counts to staff like warehouse)
    products = Product.objects.filter(is_deleted=False)
    orders = Order.objects.all()
    
    # Statistics
    total_products = products.count()
    total_orders = orders.count()
    pending_orders = orders.filter(order_status='pending').count()
    processing_orders = orders.filter(order_status='processing').count()
    shipped_orders = orders.filter(order_status='shipped').count()
    delivered_orders = orders.filter(order_status='delivered').count()
    
    total_revenue = orders.filter(payment_status='paid').aggregate(
        total=Sum('total_amount'))['total'] or 0
    
    # Recent orders
    from decimal import Decimal, InvalidOperation
    import logging
    try:
        recent_orders = list(orders.order_by('-created_at')[:5])
        # Defensive: sanitize decimals to avoid InvalidOperation in template
        for order in recent_orders:
            for field in [
                'total_amount', 'discount_amount', 'shipping_charge', 'tax_percent',
                'partial_amount_paid', 'remaining_amount', 'cod_collected', 'delivery_charge', 'expense_amount']:
                val = getattr(order, field, None)
                try:
                    if val is None or val == '' or (isinstance(val, str) and not val.strip()):
                        setattr(order, field, Decimal('0'))
                    else:
                        setattr(order, field, Decimal(str(val)))
                except (InvalidOperation, ValueError, TypeError):
                    setattr(order, field, Decimal('0'))
    except Exception as e:
        logging.error(f"Error fetching recent orders: {e}")
        recent_orders = []
    
    # Low stock products (use custom thresholds if set, fallback to stock <= 10)
    low_stock_products = products.filter(
        low_stock_threshold__gt=0, stock__lte=F('low_stock_threshold'), stock__gt=0
    ).order_by('stock')[:5]
    if not low_stock_products.exists():
        low_stock_products = products.filter(stock__lte=10, stock__gt=0).order_by('stock')[:5]

    low_stock_alert_count = products.filter(
        low_stock_threshold__gt=0, stock__lte=F('low_stock_threshold'), stock__gt=0
    ).count()
    # Include variation alerts in count
    low_stock_alert_count += ProductVariation.objects.filter(
        product__is_deleted=False,
        low_stock_threshold__gt=0, stock__lte=F('low_stock_threshold'), stock__gt=0
    ).count()
    
    # Monthly sales data for chart (last 6 months)
    monthly_sales = []
    for i in range(5, -1, -1):
        date = timezone.now() - timedelta(days=30*i)
        month_name = date.strftime('%b %Y')
        month_start = date.replace(day=1)
        
        if i > 0:
            next_month = (date.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            next_month = timezone.now() + timedelta(days=1)
        
        sales = orders.filter(
            created_at__gte=month_start,
            created_at__lt=next_month,
            payment_status='paid'
        ).aggregate(total=Sum('total_amount'))['total'] or 0
        
        monthly_sales.append({
            'month': month_name,
            'sales': float(sales)
        })
    
    # Order source data by dates (last 7 days - default)
    from django.db.models.functions import TruncDate
    
    # Get all sources first
    all_sources = set()
    source_dates_data = orders.annotate(
        order_date=TruncDate('created_at')
    ).values('order_date', 'order_from').annotate(
        count=Count('id')
    ).order_by('order_date', 'order_from')
    
    for entry in source_dates_data:
        source_name = entry['order_from'] if entry['order_from'] else 'Direct'
        all_sources.add(source_name)
    
    # Generate last 7 days of dates (default view)
    dates_list = []
    for i in range(6, -1, -1):
        date = (timezone.now() - timedelta(days=i)).date()
        dates_list.append(date)
    
    # Build data structure: {date: {source: count}}
    order_sources_by_date = {date: {} for date in dates_list}
    
    for source_name in all_sources:
        source_data = orders.filter(
            order_from=source_name if source_name != 'Direct' else ''
        ).annotate(
            order_date=TruncDate('created_at')
        ).values('order_date').annotate(
            count=Count('id')
        ).order_by('order_date')
        
        for entry in source_data:
            if entry['order_date'] in order_sources_by_date:
                order_sources_by_date[entry['order_date']][source_name] = entry['count']
    
    # Format for JSON: prepare chart data
    order_sources = {
        'dates': [date.strftime('%b %d') for date in dates_list],
        'sources': sorted(list(all_sources)),
        'data': {}
    }
    
    for source in order_sources['sources']:
        counts = []
        for date in dates_list:
            count = order_sources_by_date.get(date, {}).get(source, 0)
            counts.append(count)
        order_sources['data'][source] = counts
    
    context = {
        'total_products': total_products,
        'total_orders': total_orders,
        'pending_orders': pending_orders,
        'processing_orders': processing_orders,
        'shipped_orders': shipped_orders,
        'delivered_orders': delivered_orders,
        'total_revenue': total_revenue,
        'recent_orders': recent_orders,
        'low_stock_products': low_stock_products,
        'low_stock_alert_count': low_stock_alert_count,
        'monthly_sales': json.dumps(monthly_sales),
        'order_sources': json.dumps(order_sources),
        'can_view_total_revenue': request.user.can_view_total_revenue or request.user.role == 'administrator',
    }
    return render(request, 'dashboard.html', context)
@login_required
@permission_required('can_view_products')
def products_view(request):
    # Check for a flag set by product_add to clear any client-side product drafts
    clear_product_draft = request.session.pop('clear_product_draft', False)
    # attach to request for template access
    request.clear_product_draft = clear_product_draft
    """Products list with search, filters, and date range"""
    products = Product.objects.filter(
        is_deleted=False
    ).select_related('category').prefetch_related(
        'variations',
        'variations__attribute_values__attribute_value__attribute'
    ).order_by('-created_at')
    # Search functionality
    search_query = request.GET.get("search", "")
    if search_query:
        products = products.filter(
            Q(name__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(slug__icontains=search_query)
        )
    
    # Category filter
    category_filter = request.GET.get("category", "")
    if category_filter:
        products = products.filter(category__slug=category_filter)
    
    # Status filter
    status_filter = request.GET.get("status", "")
    if status_filter == "active":
        products = products.filter(is_active=True)
    elif status_filter == "inactive":
        products = products.filter(is_active=False)
    
    # Stock filter
    stock_filter = request.GET.get("stock", "")
    if stock_filter == "in_stock":
        products = products.filter(stock__gt=10)
    elif stock_filter == "low_stock":
        products = products.filter(stock__lte=10, stock__gt=0)
    elif stock_filter == "out_of_stock":
        products = products.filter(stock=0)
    
    # Date Range Filter
    date_filter = request.GET.get("date_range", "")
    today = timezone.now().date()
    
    if date_filter == "today":
        products = products.filter(created_at__date=today)
    elif date_filter == "yesterday":
        yesterday = today - timedelta(days=1)
        products = products.filter(created_at__date=yesterday)
    elif date_filter == "last_7_days":
        start_date = today - timedelta(days=7)
        products = products.filter(created_at__date__gte=start_date)
    elif date_filter == "last_30_days":
        start_date = today - timedelta(days=30)
        products = products.filter(created_at__date__gte=start_date)
    elif date_filter == "this_month":
        products = products.filter(
            created_at__year=today.year,
            created_at__month=today.month
        )
    elif date_filter == "last_month":
        first_day_this_month = today.replace(day=1)
        last_month = first_day_this_month - timedelta(days=1)
        products = products.filter(
            created_at__year=last_month.year,
            created_at__month=last_month.month
        )
    elif date_filter == "this_year":
        products = products.filter(created_at__year=today.year)
    elif date_filter == "custom":
        start_date = request.GET.get("start_date")
        end_date = request.GET.get("end_date")
        
        if start_date:
            try:
                start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
                products = products.filter(created_at__date__gte=start_date_obj)
            except ValueError:
                pass
        
        if end_date:
            try:
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
                products = products.filter(created_at__date__lte=end_date_obj)
            except ValueError:
                pass
    
    # Sort filter
    sort_filter = request.GET.get("sort", "")
    if sort_filter == "trending":
        seven_days_ago = timezone.now() - timedelta(days=7)
        products = products.annotate(
            order_count_7d=Count(
                'orderitem__order',
                filter=Q(orderitem__order__created_at__gte=seven_days_ago),
                distinct=True
            ),
            total_sold_7d=Sum(
                'orderitem__quantity',
                filter=Q(orderitem__order__created_at__gte=seven_days_ago)
            )
        ).order_by(F('order_count_7d').desc(nulls_last=True), F('total_sold_7d').desc(nulls_last=True))
    elif sort_filter == "price_low":
        products = products.order_by('price')
    elif sort_filter == "price_high":
        products = products.order_by('-price')
    elif sort_filter == "name_asc":
        products = products.order_by('name')
    elif sort_filter == "name_desc":
        products = products.order_by('-name')
    elif sort_filter == "stock_low":
        products = products.order_by('stock')

    # Calculate statistics (on all user products, not filtered) — bundle-aware
    all_products = Product.objects.filter(is_deleted=False)
    active_count = all_products.filter(is_active=True).count()

    # Non-bundle stats via DB
    nb_products = all_products.exclude(product_type='bundle')
    low_stock_count_nb = nb_products.filter(stock__lte=10, stock__gt=0).count()
    out_of_stock_count_nb = nb_products.filter(stock=0).count()

    # Bundle stats via Python
    b_products = all_products.filter(product_type='bundle').prefetch_related(
        'bundle_components__component_product'
    )
    low_stock_count_b = sum(1 for bp in b_products if 0 < bp.available_stock <= 10)
    out_of_stock_count_b = sum(1 for bp in b_products if bp.available_stock == 0)

    low_stock_count = low_stock_count_nb + low_stock_count_b
    out_of_stock_count = out_of_stock_count_nb + out_of_stock_count_b

    categories = Category.objects.all()
    trash_count = Product.objects.filter(is_deleted=True).count()

    # Pagination
    total_count = products.count()
    paginator = Paginator(products, 25)
    page_number = request.GET.get('page')
    products = paginator.get_page(page_number)

    context = {
        "products": products,
        "categories": categories,
        "search_query": search_query,
        "category_filter": category_filter,
        "status_filter": status_filter,
        "stock_filter": stock_filter,
        "sort_filter": sort_filter,
        "date_filter": date_filter,
        "start_date": request.GET.get("start_date", ""),
        "end_date": request.GET.get("end_date", ""),
        "active_count": active_count,
        "low_stock_count": low_stock_count,
        "out_of_stock_count": out_of_stock_count,
        "total_count": total_count,
        "trash_count": trash_count,
        "clear_product_draft": getattr(request, 'clear_product_draft', False),
    }

    return render(request, "products.html", context)


@login_required
@permission_required('can_delete_products')
def products_bulk_action(request):
    """Handle bulk actions on products"""
    if request.method == 'POST':
        product_ids = request.POST.getlist('product_ids')
        action = request.POST.get('bulk_action')
        
        if not product_ids:
            messages.error(request, 'No products selected!')
            return redirect('products')
        
        try:
            # UPDATED: Remove user filter - show all products
            products = Product.objects.filter(
                id__in=product_ids,
                is_deleted=False
            )
            count = products.count()
            
            if count == 0:
                messages.error(request, 'No valid products found!')
                return redirect('products')
            
            if action == 'delete':
                products.update(is_deleted=True, deleted_at=timezone.now())
                messages.success(request, f'{count} product(s) moved to trash!')
                
            elif action == 'activate':
                products.update(is_active=True)
                messages.success(request, f'{count} product(s) activated!')
                
            elif action == 'deactivate':
                products.update(is_active=False)
                messages.success(request, f'{count} product(s) deactivated!')
                
            elif action == 'increase_price':
                percentage = request.POST.get('percentage')
                if percentage:
                    try:
                        percentage = Decimal(percentage) / 100
                        for product in products:
                            product.price = product.price * (1 + percentage)
                            product.save()
                        messages.success(request, f'Price increased by {float(percentage)*100}% for {count} product(s)!')
                    except (ValueError, TypeError):
                        messages.error(request, 'Invalid percentage value!')
                else:
                    messages.error(request, 'Please provide a percentage!')
                    
            elif action == 'decrease_price':
                percentage = request.POST.get('percentage')
                if percentage:
                    try:
                        percentage = Decimal(percentage) / 100
                        for product in products:
                            new_price = product.price * (1 - percentage)
                            if new_price > 0:
                                product.price = new_price
                                product.save()
                        messages.success(request, f'Price decreased by {float(percentage)*100}% for {count} product(s)!')
                    except (ValueError, TypeError):
                        messages.error(request, 'Invalid percentage value!')
                else:
                    messages.error(request, 'Please provide a percentage!')
                    
            else:
                messages.error(request, 'Invalid action selected!')
                
        except Exception as e:
            messages.error(request, f'Error performing bulk action: {str(e)}')
    
    return redirect('products')


@login_required
def export_products_excel(request):
    """Export selected or all products to Excel with variation details"""
    product_ids = request.POST.getlist('product_ids')

    if product_ids:
        try:
            product_ids = [int(pid) for pid in product_ids]
        except (ValueError, TypeError):
            return HttpResponse("Invalid product IDs", status=400)
        products_qs = Product.objects.filter(id__in=product_ids, is_deleted=False)
    else:
        products_qs = Product.objects.filter(is_deleted=False)

    products_qs = products_qs.select_related('category').prefetch_related(
        'variations'
    ).order_by('-created_at')

    if not products_qs.exists():
        return HttpResponse("No products found", status=404)

    wb = Workbook()
    ws = wb.active
    ws.title = "Products"

    # ── Styles ──
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=10)

    thin_border = Border(
        left=Side(style='thin', color='D1D5DB'),
        right=Side(style='thin', color='D1D5DB'),
        top=Side(style='thin', color='D1D5DB'),
        bottom=Side(style='thin', color='D1D5DB'),
    )
    header_border = Border(
        left=Side(style='thin', color='3B5998'),
        right=Side(style='thin', color='3B5998'),
        top=Side(style='medium', color='3B5998'),
        bottom=Side(style='medium', color='3B5998'),
    )

    center_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left_align = Alignment(horizontal='left', vertical='center', wrap_text=True)
    right_align = Alignment(horizontal='right', vertical='center', wrap_text=True)

    money_font = Font(bold=True, color="059669", size=10)
    cost_font = Font(color="6B7280", size=10)

    product_fill = PatternFill(start_color="EEF2FF", end_color="EEF2FF", fill_type="solid")
    product_name_font = Font(bold=True, size=10, color="1F2937")

    var_fill = PatternFill(start_color="F9FAFB", end_color="F9FAFB", fill_type="solid")
    var_name_font = Font(size=10, color="4B5563")

    # Status/stock conditional fills
    active_fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
    active_font = Font(bold=True, color="065F46", size=9)
    inactive_fill = PatternFill(start_color="F3F4F6", end_color="F3F4F6", fill_type="solid")
    inactive_font = Font(bold=True, color="6B7280", size=9)

    in_stock_font = Font(bold=True, color="059669", size=10)
    low_stock_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
    low_stock_font = Font(bold=True, color="92400E", size=10)
    out_stock_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    out_stock_font = Font(bold=True, color="991B1B", size=10)

    # ── Headers (row 1) ──
    headers = [
        'S.N.', 'Product Name', 'Type', 'Category', 'SKU / Slug',
        'Variation Name', 'Variation SKU',
        'Price', 'Cost Price', 'Stock', 'Stock Status',
        'Status', 'Low Stock Threshold', 'Created Date',
    ]
    header_row = 1
    ws.row_dimensions[header_row].height = 28

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_align
        cell.border = header_border

    # ── Data Rows ──
    row_num = 2
    sn = 1
    total_products = 0
    total_variations = 0
    total_stock = 0

    for product in products_qs:
        total_products += 1

        if product.product_type == 'simple':
            stock_val = product.stock
            total_stock += stock_val
            is_active = product.is_active

            row_data = [
                sn,
                product.name,
                'Simple',
                product.category.name if product.category else 'Uncategorized',
                product.slug,
                '-',
                '-',
                float(product.price),
                float(product.cost_price),
                stock_val,
                product.stock_status.replace('_', ' ').title(),
                'Active' if is_active else 'Inactive',
                product.low_stock_threshold,
                product.created_at.strftime('%Y-%m-%d %I:%M %p'),
            ]

            ws.row_dimensions[row_num].height = 22
            for col_idx, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_num, column=col_idx, value=val)
                cell.border = thin_border

                # Column-specific formatting
                if col_idx == 1:  # S.N.
                    cell.alignment = center_align
                    cell.font = Font(bold=True, color="6B7280", size=10)
                elif col_idx == 2:  # Product Name
                    cell.alignment = left_align
                    cell.font = product_name_font
                elif col_idx == 3:  # Type
                    cell.alignment = center_align
                    cell.font = Font(size=9, color="4338CA", bold=True)
                    cell.fill = PatternFill(start_color="E0E7FF", end_color="E0E7FF", fill_type="solid")
                elif col_idx == 4:  # Category
                    cell.alignment = left_align
                elif col_idx in (5, 7):  # SKU, Var SKU
                    cell.alignment = left_align
                    cell.font = Font(size=9, color="6B7280")
                elif col_idx == 6:  # Variation Name
                    cell.alignment = center_align
                    cell.font = Font(size=9, color="9CA3AF")
                elif col_idx == 8:  # Price
                    cell.alignment = right_align
                    cell.font = money_font
                    cell.number_format = '#,##0.00'
                elif col_idx == 9:  # Cost Price
                    cell.alignment = right_align
                    cell.font = cost_font
                    cell.number_format = '#,##0.00'
                elif col_idx == 10:  # Stock
                    cell.alignment = center_align
                    if stock_val == 0:
                        cell.font = out_stock_font
                        cell.fill = out_stock_fill
                    elif stock_val <= (product.low_stock_threshold or 10):
                        cell.font = low_stock_font
                        cell.fill = low_stock_fill
                    else:
                        cell.font = in_stock_font
                elif col_idx == 11:  # Stock Status
                    cell.alignment = center_align
                    cell.font = Font(size=9)
                elif col_idx == 12:  # Status
                    cell.alignment = center_align
                    if is_active:
                        cell.fill = active_fill
                        cell.font = active_font
                    else:
                        cell.fill = inactive_fill
                        cell.font = inactive_font
                elif col_idx == 13:  # Low Stock Threshold
                    cell.alignment = center_align
                elif col_idx == 14:  # Created Date
                    cell.alignment = center_align
                    cell.font = Font(size=9, color="6B7280")

            row_num += 1
            sn += 1

        else:
            # Variable product
            variations = list(product.variations.all())
            if not variations:
                total_stock += product.stock
                is_active = product.is_active
                stock_val = product.stock
                row_data = [
                    sn,
                    product.name,
                    'Variable',
                    product.category.name if product.category else 'Uncategorized',
                    product.slug,
                    '(no variations)',
                    '-',
                    float(product.price),
                    float(product.cost_price),
                    stock_val,
                    product.stock_status.replace('_', ' ').title(),
                    'Active' if is_active else 'Inactive',
                    product.low_stock_threshold,
                    product.created_at.strftime('%Y-%m-%d %I:%M %p'),
                ]
                ws.row_dimensions[row_num].height = 22
                for col_idx, val in enumerate(row_data, 1):
                    cell = ws.cell(row=row_num, column=col_idx, value=val)
                    cell.border = thin_border
                    cell.fill = product_fill
                    if col_idx == 1:
                        cell.alignment = center_align
                        cell.font = Font(bold=True, color="6B7280", size=10)
                    elif col_idx == 2:
                        cell.alignment = left_align
                        cell.font = product_name_font
                    elif col_idx == 3:
                        cell.alignment = center_align
                        cell.font = Font(size=9, color="92400E", bold=True)
                        cell.fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
                    elif col_idx in (8, 9):
                        cell.alignment = right_align
                        cell.font = money_font if col_idx == 8 else cost_font
                        cell.number_format = '#,##0.00'
                    elif col_idx == 10:
                        cell.alignment = center_align
                        if stock_val == 0:
                            cell.font = out_stock_font
                            cell.fill = out_stock_fill
                        elif stock_val <= (product.low_stock_threshold or 10):
                            cell.font = low_stock_font
                            cell.fill = low_stock_fill
                        else:
                            cell.font = in_stock_font
                    elif col_idx == 12:
                        cell.alignment = center_align
                        if is_active:
                            cell.fill = active_fill
                            cell.font = active_font
                        else:
                            cell.fill = inactive_fill
                            cell.font = inactive_font
                    else:
                        cell.alignment = center_align if col_idx in (6, 11, 13) else left_align
                        cell.font = Font(size=9, color="6B7280") if col_idx in (5, 7, 14) else Font(size=10)
                row_num += 1
                sn += 1
            else:
                for var_idx, var in enumerate(variations):
                    total_variations += 1
                    var_stock = var.stock
                    total_stock += var_stock
                    var_active = var.is_active

                    is_first = var_idx == 0
                    is_var_row = not is_first

                    row_data = [
                        sn if is_first else '',
                        product.name if is_first else '',
                        'Variable' if is_first else '',
                        (product.category.name if product.category else 'Uncategorized') if is_first else '',
                        product.slug if is_first else '',
                        var.variation_name or var.sku,
                        var.sku,
                        float(var.price),
                        float(product.cost_price) if is_first else '',
                        var_stock,
                        'In Stock' if var_stock > 0 else 'Out of Stock',
                        'Active' if var_active else 'Inactive',
                        var.low_stock_threshold,
                        var.created_at.strftime('%Y-%m-%d %I:%M %p'),
                    ]

                    ws.row_dimensions[row_num].height = 22 if is_first else 20
                    for col_idx, val in enumerate(row_data, 1):
                        cell = ws.cell(row=row_num, column=col_idx, value=val)
                        cell.border = thin_border

                        if is_first:
                            # First row of variable product - highlighted
                            if col_idx <= 5:
                                cell.fill = product_fill
                            if col_idx == 1:
                                cell.alignment = center_align
                                cell.font = Font(bold=True, color="6B7280", size=10)
                            elif col_idx == 2:
                                cell.alignment = left_align
                                cell.font = product_name_font
                            elif col_idx == 3:
                                cell.alignment = center_align
                                cell.font = Font(size=9, color="92400E", bold=True)
                                cell.fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
                            elif col_idx == 4:
                                cell.alignment = left_align
                            elif col_idx == 5:
                                cell.alignment = left_align
                                cell.font = Font(size=9, color="6B7280")
                            elif col_idx == 6:
                                cell.alignment = left_align
                                cell.font = Font(size=10, color="4338CA", bold=True)
                            elif col_idx == 7:
                                cell.alignment = left_align
                                cell.font = Font(size=9, color="6B7280")
                            elif col_idx == 8:
                                cell.alignment = right_align
                                cell.font = money_font
                                cell.number_format = '#,##0.00'
                            elif col_idx == 9:
                                cell.alignment = right_align
                                cell.font = cost_font
                                cell.number_format = '#,##0.00'
                            elif col_idx == 10:
                                cell.alignment = center_align
                                if var_stock == 0:
                                    cell.font = out_stock_font
                                    cell.fill = out_stock_fill
                                elif var_stock <= (var.low_stock_threshold or 10):
                                    cell.font = low_stock_font
                                    cell.fill = low_stock_fill
                                else:
                                    cell.font = in_stock_font
                            elif col_idx == 11:
                                cell.alignment = center_align
                                cell.font = Font(size=9)
                            elif col_idx == 12:
                                cell.alignment = center_align
                                if var_active:
                                    cell.fill = active_fill
                                    cell.font = active_font
                                else:
                                    cell.fill = inactive_fill
                                    cell.font = inactive_font
                            elif col_idx == 13:
                                cell.alignment = center_align
                            elif col_idx == 14:
                                cell.alignment = center_align
                                cell.font = Font(size=9, color="6B7280")
                        else:
                            # Subsequent variation rows - subtle styling
                            cell.fill = var_fill
                            if col_idx in (1, 2, 3, 4, 5):
                                cell.alignment = center_align
                                cell.font = Font(size=9, color="D1D5DB")
                            elif col_idx == 6:
                                cell.alignment = left_align
                                cell.font = Font(size=10, color="4338CA")
                            elif col_idx == 7:
                                cell.alignment = left_align
                                cell.font = Font(size=9, color="6B7280")
                            elif col_idx == 8:
                                cell.alignment = right_align
                                cell.font = money_font
                                cell.number_format = '#,##0.00'
                            elif col_idx == 9:
                                cell.alignment = right_align
                                cell.font = cost_font
                            elif col_idx == 10:
                                cell.alignment = center_align
                                if var_stock == 0:
                                    cell.font = out_stock_font
                                    cell.fill = out_stock_fill
                                elif var_stock <= (var.low_stock_threshold or 10):
                                    cell.font = low_stock_font
                                    cell.fill = low_stock_fill
                                else:
                                    cell.font = in_stock_font
                            elif col_idx == 11:
                                cell.alignment = center_align
                                cell.font = Font(size=9)
                            elif col_idx == 12:
                                cell.alignment = center_align
                                if var_active:
                                    cell.fill = active_fill
                                    cell.font = active_font
                                else:
                                    cell.fill = inactive_fill
                                    cell.font = inactive_font
                            elif col_idx == 13:
                                cell.alignment = center_align
                            elif col_idx == 14:
                                cell.alignment = center_align
                                cell.font = Font(size=9, color="6B7280")

                    row_num += 1
                sn += 1

    # ── Column Widths ──
    col_widths = {
        'A': 6, 'B': 32, 'C': 12, 'D': 16, 'E': 20,
        'F': 24, 'G': 18, 'H': 14, 'I': 14,
        'J': 8, 'K': 14, 'L': 10, 'M': 16, 'N': 20,
    }
    for col_letter, width in col_widths.items():
        ws.column_dimensions[col_letter].width = width

    ws.auto_filter.ref = f"A{header_row}:N{header_row}"
    ws.freeze_panes = f'A{header_row + 1}'
    ws.sheet_properties.tabColor = "4472C4"

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    filename = f'products_export_{timezone.now().strftime("%Y%m%d_%H%M")}.xlsx'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


def _save_bundle_components(request, product):
    """Parse bundle component fields from POST and save BundleComponent records."""
    count = int(request.POST.get('bundle_component_count', 0))
    submitted_ids = set()
    for i in range(count):
        comp_product_id = request.POST.get(f'bundle_component_product_{i}')
        comp_qty = request.POST.get(f'bundle_component_qty_{i}')
        comp_id = request.POST.get(f'bundle_component_id_{i}')
        if not comp_product_id or not comp_qty:
            continue
        if comp_id:
            try:
                bc = BundleComponent.objects.get(pk=int(comp_id), bundle_product=product)
                bc.component_product_id = int(comp_product_id)
                bc.quantity_required = int(comp_qty)
                bc.save()
                submitted_ids.add(bc.pk)
            except BundleComponent.DoesNotExist:
                bc = BundleComponent.objects.create(
                    bundle_product=product,
                    component_product_id=int(comp_product_id),
                    quantity_required=int(comp_qty)
                )
                submitted_ids.add(bc.pk)
        else:
            bc = BundleComponent.objects.create(
                bundle_product=product,
                component_product_id=int(comp_product_id),
                quantity_required=int(comp_qty)
            )
            submitted_ids.add(bc.pk)
    product.bundle_components.exclude(pk__in=submitted_ids).delete()


def _get_bundle_context():
    """Return context data for the bundle component section of the product form."""
    simple_products = Product.objects.filter(
        is_deleted=False, is_active=True
    ).exclude(product_type='bundle').order_by('name')
    # Build a JSON map of product_id -> cost_price for JS auto-calculation
    product_cost_map = {
        str(p.id): float(p.average_cost) for p in simple_products
    }
    return {
        'simple_products': simple_products,
        'product_cost_map_json': json.dumps(product_cost_map),
    }


@login_required
@permission_required('can_create_products')

def product_add(request):
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        
        if form.is_valid():
            product = form.save(commit=False)
            product.user = request.user
            product.save()

            # If a temporary uploaded image exists (from a previous failed validation), attach it to the saved product
            try:
                temp_path = request.session.pop('temp_product_image', None)
                if temp_path and default_storage.exists(temp_path):
                    with default_storage.open(temp_path, 'rb') as tf:
                        product.image.save(os.path.basename(temp_path), File(tf), save=True)
                    try:
                        default_storage.delete(temp_path)
                    except Exception:
                        pass
            except Exception:
                pass
            
            # Save variant options if variable product
            if product.product_type == 'variable':
                variant_options = form.cleaned_data.get('variant_options')
                size_options = form.cleaned_data.get('size_options')
                
                if variant_options:
                    ProductVariantOption.objects.create(
                        product=product,
                        option_name='Variant',
                        option_values=variant_options
                    )
                
                if size_options:
                    ProductVariantOption.objects.create(
                        product=product,
                        option_name='Size',
                        option_values=size_options
                    )
                
                # Handle variation formset
                formset = ProductVariationFormSet(request.POST, request.FILES, instance=product)
                if formset.is_valid():
                    formset.save()
                else:
                    # Preserve uploaded image if any by moving to a temp location before deleting the product
                    temp_path = None
                    temp_url = None
                    try:
                        if product.image:
                            # Copy the saved product image to temp_uploads
                            base = os.path.basename(product.image.name)
                            temp_name = f"temp_uploads/{uuid.uuid4().hex}_{base}"
                            with product.image.open('rb') as f:
                                temp_path = default_storage.save(temp_name, f)
                                temp_url = default_storage.url(temp_path)
                                # store in session so it can be reused across requests
                                request.session['temp_product_image'] = temp_path
                    except Exception as e:
                        pass

                    product.delete()
                    messages.error(request, 'Error in product variations. Please check the form.')
                    ctx = {
                        'form': form,
                        'formset': formset,
                        'action': 'Add',
                        'current_step': 2,
                        'user_permissions': {
                            'can_edit_prices': request.user.can_edit_prices,
                            'can_view_cost_price': request.user.can_view_cost_price,
                            'is_administrator': request.user.role == 'administrator',
                        }
                    }
                    if temp_url:
                        ctx['temp_image_url'] = temp_url
                        ctx['temp_image_path'] = temp_path

                    return render(request, 'dashboard/product_form.html', ctx)

            # Handle bundle components
            if product.product_type == 'bundle':
                _save_bundle_components(request, product)

            # Handle gallery images
            gallery_images = request.FILES.getlist('gallery_images')
            for img in gallery_images:
                ProductImage.objects.create(product=product, image=img)
            
            messages.success(request, f'Product "{product.name}" created successfully!')
            # Clean up any temporary uploaded image saved in session
            temp_to_remove = request.session.pop('temp_product_image', None)
            if temp_to_remove and default_storage.exists(temp_to_remove):
                try:
                    default_storage.delete(temp_to_remove)
                except Exception:
                    pass
            # Mark session to clear any product draft stored in localStorage on the products page
            request.session['clear_product_draft'] = True
            return redirect('products')
        else:
            formset = ProductVariationFormSet(request.POST, request.FILES)

            # If user uploaded an image but form validation failed, persist it to temp storage
            try:
                if 'image' in request.FILES:
                    uploaded = request.FILES['image']
                    temp_name = f"temp_uploads/{uuid.uuid4().hex}_{uploaded.name}"
                    temp_path = default_storage.save(temp_name, uploaded)
                    temp_url = default_storage.url(temp_path)
                    request.session['temp_product_image'] = temp_path
                else:
                    temp_path = request.session.get('temp_product_image')
                    temp_url = default_storage.url(temp_path) if temp_path and default_storage.exists(temp_path) else None
            except Exception as e:
                temp_path = None
                temp_url = None

            messages.error(request, 'Please correct the errors below.')
            ctx = {
                'form': form,
                'formset': formset,
                'action': 'Add',
                'current_step': 1,
                'temp_image_url': temp_url,
                'temp_image_path': temp_path,
                'user_permissions': {
                    'can_edit_prices': request.user.can_edit_prices,
                    'can_view_cost_price': request.user.can_view_cost_price,
                    'is_administrator': request.user.role == 'administrator',
                }
            }
            ctx.update(_get_bundle_context())
            return render(request, 'product_form.html', ctx)
    else:
        form = ProductForm()
        formset = ProductVariationFormSet()
    
    # Respect cleared=1 param from Clear & Start Fresh to delete any temp uploaded image
    if 'cleared' in request.GET:
        try:
            tmp_pop = request.session.pop('temp_product_image', None)
            if tmp_pop and default_storage.exists(tmp_pop):
                default_storage.delete(tmp_pop)
        except Exception:
            pass

    # If there's a temp image in session (from previous failed upload), pass it to the template
    temp_path = request.session.get('temp_product_image')
    temp_url = None
    try:
        if temp_path and default_storage.exists(temp_path):
            temp_url = default_storage.url(temp_path)
        else:
            temp_path = None
    except Exception:
        temp_path = None

    ctx = {
        'form': form,
        'formset': formset,
        'action': 'Add',
        'current_step': 1,
        'temp_image_url': temp_url,
        'temp_image_path': temp_path,
        'user_permissions': {
            'can_edit_prices': request.user.can_edit_prices,
            'can_view_cost_price': request.user.can_view_cost_price,
            'is_administrator': request.user.role == 'administrator',
        }
    }
    ctx.update(_get_bundle_context())
    return render(request, 'product_form.html', ctx)
@login_required
@permission_required('can_edit_products')
def product_edit(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_deleted=False)
    
    # Check permission to edit prices
    if not request.user.can_edit_prices and request.user.role != 'administrator':
        messages.error(request, 'You do not have permission to edit product prices.')
        return redirect('product_detail', product_id=product_id)
    
    # Get existing variant options
    variant_option = product.variant_options.filter(option_name='Variant').first()
    size_option = product.variant_options.filter(option_name='Size').first()
    
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product)
        formset = ProductVariationFormSet(request.POST, request.FILES, instance=product)
        
        if form.is_valid():
            product = form.save()

            # If a temporary uploaded image exists (from previous failed validation), attach it to the product
            try:
                temp_path = request.session.pop('temp_product_image', None)
                if temp_path and default_storage.exists(temp_path) and not product.image:
                    with default_storage.open(temp_path, 'rb') as tf:
                        product.image.save(os.path.basename(temp_path), File(tf), save=True)
                    try:
                        default_storage.delete(temp_path)
                    except Exception:
                        pass
            except Exception:
                pass
            
            # Update variant options
            if product.product_type == 'variable':
                variant_options = form.cleaned_data.get('variant_options')
                size_options = form.cleaned_data.get('size_options')
                
                # Update or create variant option
                if variant_options:
                    if variant_option:
                        variant_option.option_values = variant_options
                        variant_option.save()
                    else:
                        ProductVariantOption.objects.create(
                            product=product,
                            option_name='Variant',
                            option_values=variant_options
                        )
                
                # Update or create size option
                if size_options:
                    if size_option:
                        size_option.option_values = size_options
                        size_option.save()
                    else:
                        ProductVariantOption.objects.create(
                            product=product,
                            option_name='Size',
                            option_values=size_options
                        )
                
                # FIXED: Always try to save formset if it's valid
                if formset.is_valid():
                    formset.save()
                    messages.success(request, f'Product "{product.name}" updated successfully!')
                else:
                    # If formset has errors, show them and re-render the form
                    messages.error(request, 'Please correct the variation errors below.')
                    return render(request, 'product_form.html', {
                        'form': form,
                        'formset': formset,
                        'product': product,
                        'action': 'Edit',
                        'current_step': 1,
                    })
            else:
                messages.success(request, f'Product "{product.name}" updated successfully!')

            # Handle bundle components
            if product.product_type == 'bundle':
                _save_bundle_components(request, product)

            # Handle gallery images
            gallery_images = request.FILES.getlist('gallery_images')
            for img in gallery_images:
                ProductImage.objects.create(product=product, image=img)
            
            # Clean up any temporary uploaded image saved in session
            temp_to_remove = request.session.pop('temp_product_image', None)
            if temp_to_remove and default_storage.exists(temp_to_remove):
                try:
                    default_storage.delete(temp_to_remove)
                except Exception:
                    pass
            return redirect('products')
        else:
            messages.error(request, 'Please correct the errors below.')
            # If user uploaded an image but form validation failed, persist it to temp storage
            try:
                if 'image' in request.FILES:
                    uploaded = request.FILES['image']
                    temp_name = f"temp_uploads/{uuid.uuid4().hex}_{uploaded.name}"
                    temp_path = default_storage.save(temp_name, uploaded)
                    request.session['temp_product_image'] = temp_path
            except Exception as e:
                pass
    else:
        # FIXED: Get completely fresh product from database
        # Re-query to avoid any cached instances
        product = Product.objects.get(pk=product_id)
        
        # Get existing variant options
        variant_option = product.variant_options.filter(option_name='Variant').first()
        size_option = product.variant_options.filter(option_name='Size').first()
        
        form = ProductForm(instance=product, initial={
            'variant_options': variant_option.option_values if variant_option else '',
            'size_options': size_option.option_values if size_option else ''
        })
        
        # FIXED: Get completely fresh variations from database
        # Use raw database query to bypass any Django ORM caching
        variations_qs = ProductVariation.objects.filter(product_id=product_id).order_by('created_at')
        
        # Clear any cached relations
        if hasattr(product, '_prefetched_objects_cache'):
            product._prefetched_objects_cache.clear()
        
        # Create formset with fresh queryset
        formset = ProductVariationFormSet(instance=product, queryset=variations_qs)
    
    # If there's a temp image in session (from previous failed upload), pass it to the template
    temp_path = request.session.get('temp_product_image')
    temp_url = None
    try:
        if temp_path and default_storage.exists(temp_path):
            temp_url = default_storage.url(temp_path)
        else:
            temp_path = None
    except Exception:
        temp_path = None

    ctx = {
        'form': form,
        'formset': formset,
        'product': product,
        'action': 'Edit',
        'current_step': 1,
        'temp_image_url': temp_url,
        'temp_image_path': temp_path,
        'user_permissions': {
            'can_edit_prices': request.user.can_edit_prices,
            'can_view_cost_price': request.user.can_view_cost_price,
            'is_administrator': request.user.role == 'administrator',
        },
        'bundle_components': list(product.bundle_components.select_related('component_product').all()) if product.product_type == 'bundle' else [],
    }
    ctx.update(_get_bundle_context())
    return render(request, 'product_form.html', ctx)


@login_required
@permission_required('can_view_products')
def product_detail(request, product_id):
    # FIXED: Use same filter as product_edit for consistency
    product = get_object_or_404(Product, pk=product_id, is_deleted=False)
    # FIXED: Refresh from database to get latest changes
    product.refresh_from_db()

    try:
        effective_cost = product.average_cost if product.average_cost else (product.cost_price or 0)
        profit = (product.price or 0) - effective_cost
    except Exception:
        profit = 0

    context = {
        "product": product,
        "profit": profit,
    }
    
    # Handle variation updates
    if request.method == 'POST':
        if 'delete_variation_id' in request.POST:
            # Delete specific variation
            variation_id = request.POST.get('delete_variation_id')
            variation = get_object_or_404(ProductVariation, id=variation_id, product=product)
            variation.delete()
            messages.success(request, f'Variation "{variation.sku}" deleted successfully!')
            return redirect('product_detail', product_id=product_id)
        
        elif 'update_variations' in request.POST:
            # Update or create variations
            variations_data = {}
            
            # Parse variations from POST data
            for key, value in request.POST.items():
                if key.startswith('variations['):
                    # Extract index and field name
                    parts = key.replace('variations[', '').replace(']', '').split('[')
                    if len(parts) == 2:
                        index, field = parts
                        if index not in variations_data:
                            variations_data[index] = {}
                        variations_data[index][field] = value
            
            # Process each variation
            for index, var_data in variations_data.items():
                variation_id = var_data.get('id')
                sku = var_data.get('sku')
                price = var_data.get('price')
                stock = var_data.get('stock')
                status = var_data.get('status', 'active')
                is_active = f'variations[{index}][is_active]' in request.POST
                
                if variation_id:
                    # Update existing
                    variation = ProductVariation.objects.get(id=variation_id, product=product)
                    variation.sku = sku
                    variation.price = price
                    variation.stock = stock
                    variation.status = status
                    variation.is_active = is_active
                else:
                    # Create new
                    variation = ProductVariation.objects.create(
                        product=product,
                        sku=sku,
                        price=price,
                        stock=stock,
                        status=status,
                        is_active=is_active
                    )
                
                # Handle image upload
                image_key = f'variations[{index}][image]'
                if image_key in request.FILES:
                    variation.image = request.FILES[image_key]
                
                variation.save()
            
            messages.success(request, 'Variations updated successfully!')
            return redirect('product_detail', product_id=product_id)
    
    # Get product data
    product_images = product.images.all()
    order_items = product.orderitem_set.all()[:10]
    
    # FIXED: Explicitly fetch fresh variations from database
    # Clear any cached relations to ensure fresh data
    if hasattr(product, '_prefetched_objects_cache'):
        product._prefetched_objects_cache.clear()
    
    variations = ProductVariation.objects.filter(product=product).order_by('created_at')
    
    # Calculate profit margin
    effective_cost = product.average_cost if product.average_cost else (product.cost_price or 0)
    profit_margin = 0
    if effective_cost and effective_cost > 0 and product.price and product.price > 0:
        profit_margin = ((product.price - effective_cost) / product.price) * 100
    profit = (product.price or 0) - effective_cost
    
    # Get user permissions
    user_permissions = {
        'can_view_cost_price': request.user.can_view_cost_price,
        'can_edit_prices': request.user.can_edit_prices,
        'can_give_discounts': request.user.can_give_discounts,
        'max_discount_percent': float(request.user.max_discount_percent),
        'is_administrator': request.user.role == 'administrator',
    }
    
    # Bundle components for bundle products
    bundle_components = []
    if product.is_bundle:
        bundle_components = product.bundle_components.select_related('component_product').all()

    context = {
        'product': product,
        'variations': variations,  # ADDED: Explicitly pass variations
        'product_images': product_images,
        'recent_items': order_items,
        'profit': profit,
        'profit_margin': profit_margin,
        'user_permissions': user_permissions,  # ADDED: Pass user permissions
        'bundle_components': bundle_components,
    }
    
    return render(request, 'product_detail.html', context)


@login_required
@permission_required('can_view_products')
def products_trash(request):
    """View trashed products"""
    if request.user.is_superuser or request.user.role == 'admin':
        trashed_products = Product.objects.filter(
            is_deleted=True
        ).select_related('category').prefetch_related(
            'bundle_components__component_product'
        ).order_by('-deleted_at')
    else:
        trashed_products = Product.objects.filter(
            user=request.user,
            is_deleted=True
        ).select_related('category').prefetch_related(
            'bundle_components__component_product'
        ).order_by('-deleted_at')

    # Search functionality
    search_query = request.GET.get("search", "")
    if search_query:
        trashed_products = trashed_products.filter(
            Q(name__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(slug__icontains=search_query)
        )
    
    # Category filter
    category_filter = request.GET.get("category", "")
    if category_filter:
        trashed_products = trashed_products.filter(category__slug=category_filter)
    
    categories = Category.objects.all()
    
    context = {
        "trashed_products": trashed_products,
        "categories": categories,
        "search_query": search_query,
        "category_filter": category_filter,
    }
    
    return render(request, "products_trash.html", context)


@login_required
@permission_required('can_delete_products')
def product_move_to_trash(request, product_id):
    """Move product to trash (soft delete)"""
    product = get_object_or_404(Product, id=product_id, is_deleted=False)
    
    if request.method == 'POST':
        product_name = product.name
        product.is_deleted = True
        product.deleted_at = timezone.now()
        product.save()
        
        messages.success(request, f'Product "{product_name}" moved to trash successfully!')
        return redirect('products')
    
    return redirect('product_detail', product_id=product_id)


@login_required
@permission_required('can_delete_products')
def product_restore(request, product_id):
    """Restore product from trash"""
    product = get_object_or_404(Product, id=product_id, user=request.user, is_deleted=True)
    
    if request.method == 'POST':
        product_name = product.name
        product.is_deleted = False
        product.deleted_at = None
        product.save()
        
        messages.success(request, f'Product "{product_name}" restored successfully!')
        return redirect('products_trash')
    
    return redirect('products_trash')


@login_required
@permission_required('can_delete_products')
def product_permanent_delete(request, product_id):
    """Permanently delete product"""
    product = get_object_or_404(Product, id=product_id, user=request.user, is_deleted=True)
    
    if request.method == 'POST':
        product_name = product.name
        product.delete()
        
        messages.success(request, f'Product "{product_name}" permanently deleted!')
        return redirect('products_trash')
    
    return redirect('products_trash')


@login_required
@permission_required('can_delete_products')
def products_trash_bulk_action(request):
    """Handle bulk actions on trashed products"""
    if request.method == "POST":
        product_ids = request.POST.getlist("product_ids")
        action = request.POST.get("bulk_action")
        
        if not product_ids:
            messages.error(request, "No products selected!")
            return redirect('products_trash')
        
        try:
            products = Product.objects.filter(
                id__in=product_ids, 
                user=request.user, 
                is_deleted=True
            )
            count = products.count()
            
            if count == 0:
                messages.error(request, "No valid products found!")
                return redirect('products_trash')
            
            if action == "restore":
                products.update(is_deleted=False, deleted_at=None)
                messages.success(request, f"✅ {count} product(s) restored successfully!")
                
            elif action == "permanent_delete":
                products.delete()
                messages.success(request, f"✅ {count} product(s) permanently deleted!")
                
            else:
                messages.error(request, "Invalid action selected!")
                
        except Exception as e:
            messages.error(request, f"Error performing bulk action: {str(e)}")
            
    return redirect('products_trash')


@login_required
@permission_required('can_delete_products')
def empty_trash(request):
    """Empty all trashed products"""
    if request.method == 'POST':
        trashed_products = Product.objects.filter(user=request.user, is_deleted=True)
        count = trashed_products.count()
        
        if count > 0:
            trashed_products.delete()
            messages.success(request, f'✅ Trash emptied! {count} product(s) permanently deleted.')
        else:
            messages.info(request, 'Trash is already empty.')
        
        return redirect('products_trash')
    
    return redirect('products_trash')


@login_required
@permission_required('can_edit_products')
def delete_product_image(request, image_id):
    """Delete a product gallery image"""
    image = get_object_or_404(ProductImage, id=image_id, product__user=request.user)
    product_id = image.product.id
    product_name = image.product.name
    
    # Delete the image
    image.delete()
    
    messages.success(request, f'Image deleted from "{product_name}" gallery successfully!')
    return redirect('product_detail', product_id=product_id)


@login_required
@permission_required('can_edit_products')
def set_featured_image(request, image_id):
    """Set an image as featured in the gallery"""
    image = get_object_or_404(ProductImage, id=image_id, product__user=request.user)
    
    # Unset all other featured images for this product
    ProductImage.objects.filter(product=image.product).update(is_featured=False)
    
    # Set this image as featured
    image.is_featured = True
    image.save()
    
    messages.success(request, f'Featured image updated for "{image.product.name}"!')
    return redirect('product_detail', product_id=image.product.id)


@login_required
@permission_required('can_edit_products')
def upload_product_images(request, product_id):
    """Upload multiple images to product gallery"""
    product = get_object_or_404(Product, id=product_id, user=request.user)
    
    if request.method == 'POST':
        gallery_images = request.FILES.getlist('images')
        
        if gallery_images:
            # Get current max order
            max_order = ProductImage.objects.filter(product=product).count()
            
            for idx, image in enumerate(gallery_images):
                ProductImage.objects.create(
                    product=product,
                    image=image,
                    order=max_order + idx,
                    alt_text=f"{product.name} - Gallery Image {max_order + idx + 1}"
                )
            
            messages.success(request, f'{len(gallery_images)} image(s) uploaded successfully to "{product.name}" gallery!')
        else:
            messages.warning(request, 'No images were selected.')
        
        return redirect('product_detail', product_id=product.id)
    
    return redirect('product_detail', product_id=product.id)


@login_required
@permission_required('can_edit_products')
def reorder_product_images(request, product_id):
    """Reorder product gallery images via AJAX"""
    if request.method == 'POST':
        product = get_object_or_404(Product, id=product_id, user=request.user)
        
        try:
            order_data = json.loads(request.body)
            
            for item in order_data:
                image_id = item.get('id')
                new_order = item.get('order')
                
                ProductImage.objects.filter(
                    id=image_id, 
                    product=product
                ).update(order=new_order)
            
            return JsonResponse({'success': True, 'message': 'Images reordered successfully!'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
    
    return JsonResponse({'success': False, 'message': 'Invalid request method'})


@login_required
@permission_required('can_view_orders')
def orders_view(request):
    orders = Order.objects.filter(user=request.user)

    # Filter by status
    status_filter = request.GET.get('status', '')
    if status_filter:
        orders = orders.filter(order_status=status_filter)

    # Filter by date_range
    date_range = request.GET.get('date_range', '')
    if date_range == 'last_2_days':
        now = timezone.now()
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_yesterday = start_of_today - timedelta(days=1)
        end_of_today = start_of_today + timedelta(days=1) - timedelta(microseconds=1)
        filtered_orders = orders.filter(created_at__gte=start_of_yesterday, created_at__lte=end_of_today)
        orders = filtered_orders
    # If 'All Time' or empty, do not filter by date

    context = {
        'orders': orders,
        'status_filter': status_filter,
        'date_filter': date_range,
    }

    return render(request, 'orders_list.html', context)




@login_required
def chart_data(request):
    # Get sales data for charts
    today = datetime.now().date()
    
    # Last 7 days sales
    daily_sales = []
    for i in range(7):
        date = today - timedelta(days=i)
        sales = Order.objects.filter(
            user=request.user,
            payment_status='paid',
            created_at__date=date
        ).aggregate(total=Sum('total_amount'))['total'] or 0
        
        daily_sales.insert(0, {
            'date': date.strftime('%d %b'),
            'sales': float(sales)
        })
    
    return JsonResponse({
        'daily_sales': daily_sales
    })


@login_required
def order_sources_data(request):
    """API endpoint to get order sources data with date range filtering"""
    # Check if custom date range is provided
    custom_from = request.GET.get('custom_from')
    custom_to = request.GET.get('custom_to')
    
    if custom_from and custom_to:
        try:
            from datetime import datetime
            start_date = datetime.strptime(custom_from, '%Y-%m-%d').date()
            end_date = datetime.strptime(custom_to, '%Y-%m-%d').date()
            
            # Validate date range
            if end_date < start_date:
                start_date, end_date = end_date, start_date
            
            # Generate dates list based on custom range
            dates_list = []
            current = start_date
            while current <= end_date:
                dates_list.append(current)
                current += timedelta(days=1)
        except (ValueError, TypeError):
            # Default to last 7 days if invalid dates
            dates_list = []
            for i in range(6, -1, -1):
                date = (timezone.now() - timedelta(days=i)).date()
                dates_list.append(date)
    else:
        # Use preset days parameter
        days = request.GET.get('days', 14)
        try:
            days = int(days)
            if days not in [7, 14, 30, 60, 90]:
                days = 14
        except (ValueError, TypeError):
            days = 14
        
        # Generate dates list based on days parameter
        dates_list = []
        for i in range(days - 1, -1, -1):
            date = (timezone.now() - timedelta(days=i)).date()
            dates_list.append(date)
    
    orders = Order.objects.all()
    
    # Get all sources first
    all_sources = set()
    source_dates_data = orders.annotate(
        order_date=TruncDate('created_at')
    ).values('order_date', 'order_from').annotate(
        count=Count('id')
    ).order_by('order_date', 'order_from')
    
    for entry in source_dates_data:
        source_name = entry['order_from'] if entry['order_from'] else 'Direct'
        all_sources.add(source_name)
    
    # Build data structure: {date: {source: count}}
    order_sources_by_date = {date: {} for date in dates_list}
    
    for source_name in all_sources:
        source_data = orders.filter(
            order_from=source_name if source_name != 'Direct' else ''
        ).annotate(
            order_date=TruncDate('created_at')
        ).values('order_date').annotate(
            count=Count('id')
        ).order_by('order_date')
        
        for entry in source_data:
            if entry['order_date'] in order_sources_by_date:
                order_sources_by_date[entry['order_date']][source_name] = entry['count']
    
    # Format for JSON: prepare chart data
    order_sources = {
        'dates': [date.strftime('%b %d') for date in dates_list],
        'sources': sorted(list(all_sources)),
        'data': {}
    }
    
    for source in order_sources['sources']:
        counts = []
        for date in dates_list:
            count = order_sources_by_date.get(date, {}).get(source, 0)
            counts.append(count)
        order_sources['data'][source] = counts
    
    return JsonResponse(order_sources)


@login_required
@permission_required('can_view_customers')
def customers_view(request):
    customers = Customer.objects.all().order_by('-created_at')
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        customers = customers.filter(
            Q(name__icontains=search_query) | 
            Q(email__icontains=search_query) |
            Q(phone__icontains=search_query)
        )
    
    # Add order count and total spent for each customer
    customers_data = []
    for customer in customers:
        orders = Order.objects.filter(customer_email=customer.email)
        total_orders = orders.count()
        total_spent = orders.filter(payment_status='paid').aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0.00')
        
        customers_data.append({
            'customer': customer,
            'total_orders': total_orders,
            'total_spent': total_spent,
            'last_order': orders.first(),
        })
    
    context = {
        'customers_data': customers_data,
        'search_query': search_query,
    }
    # Ensure older view renders the consolidated customers_list template
    context['customer_type'] = ''
    return render(request, 'customers_list.html', context)


@login_required
@permission_required('can_view_customers')
def customer_detail(request, customer_id):
    """View customer details"""
    customer = get_object_or_404(Customer, id=customer_id)
    
    # CORRECT: Query orders by customer email and phone
    customer_orders = Order.objects.filter(
        Q(customer_email=customer.email) | Q(customer_phone=customer.phone)
    ).order_by('-created_at')
    
    # Calculate statistics
    total_orders = customer_orders.count()
    total_spent = customer_orders.filter(payment_status='paid').aggregate(
        total=Sum('total_amount')
    )['total'] or Decimal('0.00')
    
    pending_orders = customer_orders.filter(order_status='pending').count()
    delivered_orders = customer_orders.filter(order_status='delivered').count()
    
    # Get last order date
    last_order = customer_orders.first()
    last_order_date = last_order.created_at if last_order else None
    
    context = {
        'customer': customer,
        'customer_orders': customer_orders,
        'total_orders': total_orders,
        'total_spent': total_spent,
        'pending_orders': pending_orders,
        'delivered_orders': delivered_orders,
        'last_order_date': last_order_date,
    }
    
    return render(request, 'customer_detail.html', context)

@login_required
@permission_required('can_delete_customers')
def customers_bulk_action(request):
    """Handle bulk actions on customers"""
    if request.method == 'POST':
        customer_ids = request.POST.getlist('customer_ids')
        action = request.POST.get('bulk_action')
        
        if not customer_ids:
            messages.error(request, 'No customers selected!')
            return redirect('customers_list')
        
        try:
            customers = Customer.objects.filter(id__in=customer_ids)
            count = customers.count()
            
            if count == 0:
                messages.error(request, 'No valid customers found!')
                return redirect('customers_list')
            
            if action == 'delete':
                customers.delete()
                messages.success(request, f'✅ {count} customer(s) deleted successfully!')
                
            elif action == 'activate':
                customers.update(is_active=True)
                messages.success(request, f'✅ {count} customer(s) activated!')
                
            elif action == 'deactivate':
                customers.update(is_active=False)
                messages.success(request, f'✅ {count} customer(s) deactivated!')
                
            elif action == 'change_type_retail':
                customers.update(customer_type='retail')
                messages.success(request, f'✅ {count} customer(s) changed to Retail!')
                
            elif action == 'change_type_wholesale':
                customers.update(customer_type='wholesale')
                messages.success(request, f'✅ {count} customer(s) changed to Wholesale!')
                
            elif action == 'change_type_vip':
                customers.update(customer_type='vip')
                messages.success(request, f'✅ {count} customer(s) changed to VIP!')
                
            elif action == 'export':
                # Export to Excel
                wb = Workbook()
                ws = wb.active
                ws.title = 'Customers'
                
                # Styling
                from openpyxl.styles import Font, PatternFill, Alignment
                header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
                header_font = Font(bold=True, color='FFFFFF', size=11)
                
                # Headers
                headers = ['Name', 'Email', 'Phone', 'Alternate Phone', 'City', 'Address', 'Type', 'Total Orders', 'Total Spent']
                ws.append(headers)
                
                # Style header row
                for col_num, header in enumerate(headers, 1):
                    cell = ws.cell(row=1, column=col_num)
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                
                # Data rows
                for customer in customers:
                    orders = customer.orders.all()
                    total_spent = orders.filter(payment_status='paid').aggregate(
                        total=Sum('total_amount'))['total'] or Decimal('0.00')
                    
                    ws.append([
                        customer.name,
                        customer.email or '',
                        customer.phone,
                        customer.alternate_phone or '',
                        customer.city,
                        customer.address,
                        customer.customer_type.upper(),
                        orders.count(),
                        float(total_spent)
                    ])
                
                # Auto-adjust column widths
                for column in ws.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(cell.value)
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 50)
                    ws.column_dimensions[column_letter].width = adjusted_width
                
                # Create response
                response = HttpResponse(
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
                response['Content-Disposition'] = f'attachment; filename=customers_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
                wb.save(response)
                return response
                
            else:
                messages.error(request, 'Invalid action selected!')
                
        except Exception as e:
            messages.error(request, f'❌ Error performing bulk action: {str(e)}')
    
    return redirect('customers_list')


@login_required
@admin_only
def category_list(request):
    categories = Category.objects.all().order_by('name')
    
    if request.method == 'POST':
        name = request.POST.get('name')
        slug = request.POST.get('slug')
        
        if name and slug:
            Category.objects.create(name=name, slug=slug)
            messages.success(request, f'Category "{name}" added successfully!')
            return redirect('category_list')
    
    context = {
        'categories': categories,
    }
    return render(request, 'category_list.html', context)


@login_required
def add_category_ajax(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method'}, status=405)
    try:
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        slug = data.get('slug', '').strip()
        if not name or not slug:
            return JsonResponse({'success': False, 'message': 'Name and slug are required'})
        if Category.objects.filter(slug=slug).exists():
            return JsonResponse({'success': False, 'message': 'A category with this slug already exists'})
        category = Category.objects.create(name=name, slug=slug)
        return JsonResponse({'success': True, 'category': {'id': category.id, 'name': category.name}})
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON data'}, status=400)


@login_required
@admin_only
def category_delete(request, category_id):
    category = get_object_or_404(Category, id=category_id)
    
    if request.method == 'POST':
        category_name = category.name
        category.delete()
        messages.success(request, f'Category "{category_name}" deleted successfully!')
        return redirect('category_list')
    
    return redirect('category_list')


@login_required
@admin_only
def category_edit(request, category_id):
    category = get_object_or_404(Category, id=category_id)
    
    if request.method == 'POST':
        name = request.POST.get('name')
        slug = request.POST.get('slug')
        
        if name and slug:
            # Check if slug is already taken by another category
            if Category.objects.filter(slug=slug).exclude(id=category_id).exists():
                messages.error(request, 'A category with this slug already exists!')
                return redirect('category_edit', category_id=category_id)
            
            category.name = name
            category.slug = slug
            category.save()
            messages.success(request, f'Category "{name}" updated successfully!')
            return redirect('category_list')
    
    context = {
        'category': category,
    }
    return render(request, 'category_edit.html', context)


@login_required
@permission_required('can_edit_products')
def product_variations(request, product_id):
    product = get_object_or_404(Product, id=product_id, user=request.user)
    
    if product.product_type != 'variable':
        messages.warning(request, 'This product is not a variable product.')
        return redirect('products')
    
    variations = ProductVariation.objects.filter(product=product)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add_variation':
            sku = request.POST.get('sku', '').strip()
            price = request.POST.get('price') or None
            stock = request.POST.get('stock', 0)
            
            # Validate SKU
            if not sku:
                messages.error(request, 'SKU is required!')
                return redirect('product_variations', product_id=product.id)
            
            # Check if SKU already exists
            if ProductVariation.objects.filter(sku=sku).exists():
                messages.error(request, f'SKU "{sku}" already exists! Please use a unique SKU.')
                return redirect('product_variations', product_id=product.id)
            
            try:
                variation = ProductVariation.objects.create(
                    product=product,
                    sku=sku,
                    price=price,
                    stock=stock
                )
                
                if 'variation_image' in request.FILES:
                    variation.image = request.FILES['variation_image']
                    variation.save()
                
                messages.success(request, f'Variation "{sku}" added successfully!')
                return redirect('product_variations', product_id=product.id)
                
            except IntegrityError:
                messages.error(request, f'SKU "{sku}" already exists! Please use a unique SKU.')
                return redirect('product_variations', product_id=product.id)
            except Exception as e:
                messages.error(request, f'Error creating variation: {str(e)}')
                return redirect('product_variations', product_id=product.id)
    
    context = {
        'product': product,
        'variations': variations,
    }
    return render(request, 'product_variations.html', context) 


@login_required
@permission_required('can_delete_products')
def variation_delete(request, variation_id):
    variation = get_object_or_404(ProductVariation, id=variation_id, product__user=request.user)
    product_id = variation.product.id
    variation_sku = variation.sku
    variation.delete()
    messages.success(request, f'Variation "{variation_sku}" deleted successfully!')
    return redirect('product_variations', product_id=product_id)


# Attributes feature removed: attribute management was removed as requested. Views and templates related to attributes were deleted to simplify product handling.


@login_required
@permission_required('can_view_customers')
def customers_list(request):
    """List all customers with search and filter"""
    customers = Customer.objects.all().order_by('-created_at')
    
    # Search
    search_query = request.GET.get('search', '')
    if search_query:
        customers = customers.filter(
            Q(name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(phone__icontains=search_query)
        )
    
    # Filter by type
    customer_type = request.GET.get('type', '')
    if customer_type:
        customers = customers.filter(customer_type=customer_type)
    
    # Add statistics
    customers_data = []
    for customer in customers:
      orders = customer.orders.all()
      customers_data.append({
            'customer': customer,
            'total_orders': orders.count(),
            'total_spent': orders.filter(payment_status='paid').aggregate(
                total=Sum('total_amount'))['total'] or Decimal('0.00'),
            'last_order': orders.first(),
        })
    
    context = {
        'customers_data': customers_data,
        'search_query': search_query,
        'customer_type': customer_type,
    }
    return render(request, 'customers_list.html', context)


@login_required
@permission_required('can_create_customers')
def customer_add(request):
    """Add new customer"""
    if request.method == 'POST':
        form = CustomerForm(request.POST)
        if form.is_valid():
            customer = form.save(commit=False)
            customer.user = request.user
            customer.save()
            messages.success(request, f'Customer "{customer.name}" added successfully!')
            return redirect('customers_list')
        else:
            # Print form errors for debugging
            messages.error(request, 'Please correct the errors below.')
    else:
        form = CustomerForm()
    
    context = {'form': form, 'action': 'Add'}
    return render(request, 'customer_form.html', context)


@login_required
@permission_required('can_edit_customers')
def customer_edit(request, customer_id):
    """Edit existing customer"""
    customer = get_object_or_404(Customer, id=customer_id)
    
    if request.method == 'POST':
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, f'Customer "{customer.name}" updated successfully!')
            return redirect('customer_detail', customer_id=customer.id)
    else:
        form = CustomerForm(instance=customer)
    
    context = {'form': form, 'customer': customer, 'action': 'Edit'}
    return render(request, 'customer_form.html', context)


@login_required
def customer_detail(request, customer_id):
    """View customer details"""
    customer = get_object_or_404(Customer, id=customer_id)
    orders = customer.orders.all().order_by('-created_at')
    
    # Statistics
    total_orders = orders.count()
    total_spent = orders.filter(payment_status='paid').aggregate(
        total=Sum('total_amount'))['total'] or Decimal('0.00')
    pending_orders = orders.filter(order_status='pending').count()
    
    context = {
        'customer': customer,
        'orders': orders,
        'total_orders': total_orders,
        'total_spent': total_spent,
        'pending_orders': pending_orders,
    }
    return render(request, 'customer_detail.html', context)


@login_required
@permission_required('can_delete_customers')
def customer_delete(request, customer_id):
    """Delete customer"""
    customer = get_object_or_404(Customer, id=customer_id)
    
    if request.method == 'POST':
        customer_name = customer.name
        customer.delete()
        messages.success(request, f'Customer "{customer_name}" deleted successfully!')
        return redirect('customers_list')
    
    return render(request, 'customer_delete.html', {'customer': customer})


# ============ ORDER VIEWS ============

from django.db.models import Prefetch
@login_required
@permission_required('can_view_orders')
def orders_list(request):
    """Display list of orders with filters and statistics - using pure ORM queries"""
    from datetime import timedelta
    from django.utils import timezone
    from django.db.models import Q, Sum
    from decimal import Decimal
    import pytz
    
    # ✅ FIXED: Use pure ORM queries with select_related for performance
    orders = Order.objects.filter(
        is_deleted=False
    ).select_related(
        'customer', 'created_by', 'status_setup', 
        'payment_setup', 'payment_status_setup'
    ).prefetch_related('items').order_by('-created_at')
    
    # GET FILTER PARAMETERS - DEFAULT TO 'last_2_days'
    date_filter = request.GET.get('date_range', 'last_2_days')
    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    payment_filter = request.GET.get('payment', '')
    in_out_filter = request.GET.get('in_out', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    logistics_filter = request.GET.get('logistics_status', '')
    
    # ✅ FIXED: Apply filters using ORM (much more efficient than Python list filtering)
    # Search filter
    if search_query:
        orders = orders.filter(
            Q(order_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(customer_phone__icontains=search_query) |
            Q(customer_email__icontains=search_query)
        )
    
    # Status filter - Now properly handles Setup-based statuses
    if status_filter:
        # Try to find the Setup with matching filter value
        # The filter value comes from setup.name.lower().replace(' ', '_')
        try:
            # First, try to find Setup by matching the filter value format
            status_setup = Setup.objects.filter(
                setup_type='status',
                name__iexact=status_filter.replace('_', ' ')
            ).first()
            
            if status_setup:
                # STRICT FILTER: Primary by status_setup_id, fallback only for null status_setup
                # This prevents "On Hold" showing when "Processing" is selected
                orders = orders.filter(
                    Q(status_setup_id=status_setup.id) |
                    (Q(status_setup_id__isnull=True) & Q(order_status__iexact=status_filter.replace('_', ' ')))
                )
            else:
                # Setup not found, filter by order_status field only
                orders = orders.filter(order_status__iexact=status_filter.replace('_', ' '))
        except Exception:
            # Fallback filtering
            orders = orders.filter(order_status__iexact=status_filter.replace('_', ' '))
    
    # Payment status filter - Now properly handles Setup-based payment statuses
    if payment_filter:
        # Try to find the Setup with matching filter value
        try:
            # First, try to find Setup by matching the filter value format
            payment_setup = Setup.objects.filter(
                setup_type='payment_status',
                name__iexact=payment_filter.replace('_', ' ')
            ).first()
            
            if payment_setup:
                # STRICT FILTER: Primary by payment_status_setup_id, fallback only for null payment_status_setup
                orders = orders.filter(
                    Q(payment_status_setup_id=payment_setup.id) |
                    (Q(payment_status_setup_id__isnull=True) & Q(payment_status__iexact=payment_filter.replace('_', ' ')))
                )
            else:
                # Setup not found, filter by payment_status field only
                orders = orders.filter(payment_status__iexact=payment_filter.replace('_', ' '))
        except Exception:
            # Fallback filtering
            orders = orders.filter(payment_status=payment_filter)
    
    # In/Out Valley filter
    if in_out_filter:
        orders = orders.filter(in_out=in_out_filter)
    
    # Logistics filter
    if logistics_filter == 'sent':
        orders = orders.exclude(ncm_order_id__isnull=True)
    elif logistics_filter == 'not_sent':
        orders = orders.filter(ncm_order_id__isnull=True)
    
    # DATE RANGE FILTER using ORM
    nepali_tz = pytz.timezone('Asia/Kathmandu')
    now_nepal = timezone.now().astimezone(nepali_tz)
    today_nepal = now_nepal.date()
    
    if date_filter == 'last_24_hours':
        last_24_hours = now_nepal - timedelta(hours=24)
        orders = orders.filter(created_at__gte=last_24_hours)
    elif date_filter == 'today':
        orders = orders.filter(created_at__date=today_nepal)
    elif date_filter == 'yesterday':
        yesterday = today_nepal - timedelta(days=1)
        orders = orders.filter(created_at__date=yesterday)
    elif date_filter == 'last_2_days':
        start = today_nepal - timedelta(days=1)
        orders = orders.filter(created_at__date__gte=start)
    elif date_filter == 'last_7_days':
        start = today_nepal - timedelta(days=7)
        orders = orders.filter(created_at__date__gte=start)
    elif date_filter == 'last_30_days':
        start = today_nepal - timedelta(days=30)
        orders = orders.filter(created_at__date__gte=start)
    elif date_filter == 'this_month':
        orders = orders.filter(
            created_at__year=today_nepal.year,
            created_at__month=today_nepal.month
        )
    elif date_filter == 'last_month':
        first_day_this_month = today_nepal.replace(day=1)
        last_day_last_month = first_day_this_month - timedelta(days=1)
        first_day_last_month = last_day_last_month.replace(day=1)
        orders = orders.filter(
            created_at__date__gte=first_day_last_month,
            created_at__date__lte=last_day_last_month
        )
    elif date_filter == 'this_year':
        orders = orders.filter(created_at__year=today_nepal.year)
    elif date_filter == 'custom' and start_date and end_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            orders = orders.filter(created_at__date__gte=start_date_obj, created_at__date__lte=end_date_obj)
        except ValueError:
            pass
    elif date_filter == 'all':
        pass  # No date filter
    
    # ✅ FIXED: Calculate statistics using ORM aggregations (no decimal issues)
    total_orders = orders.count()
    total_revenue = orders.filter(payment_status='paid').aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
    pending_orders = orders.filter(order_status='pending').count()
    confirmed_orders = orders.filter(order_status='confirmed').count()
    dispatched_orders = orders.filter(order_status='dispatched').count()
    
    # Delivered today (using date filter)
    delivered_today = orders.filter(
        order_status='delivered',
        delivered_at__date=today_nepal
    ).count()
    
    # Pagination
    from django.core.paginator import Paginator
    per_page = request.GET.get('per_page', '50')
    if per_page not in ('50', '100', '200'):
        per_page = '50'
    paginator = Paginator(orders, int(per_page))
    page_number = request.GET.get('page')
    orders_page = paginator.get_page(page_number)
    
    # ✅ FETCH DYNAMIC ORDER STATUSES AND PAYMENT STATUSES FROM SETUP MANAGEMENT
    # This ensures filters pull from Setup Management for consistency
    order_setups = Setup.objects.filter(setup_type='status', is_active=True).order_by('name')
    payment_setups = Setup.objects.filter(setup_type='payment_status', is_active=True).order_by('name')
    
    # Convert Setup names to filter values (lowercase with underscores)
    # Format: [(filter_value, display_name), ...]
    # Example: [('pending', 'Pending'), ('confirmed', 'Confirmed')]
    order_status_choices = [
        (setup.name.lower().replace(' ', '_'), setup.name)
        for setup in order_setups
    ]
    payment_status_choices = [
        (setup.name.lower().replace(' ', '_'), setup.name)
        for setup in payment_setups
    ]
    
    # ✅ PREPARE DROPDOWN OPTIONS FOR BULK ACTIONS
    # Format options as (action_value, display_label, icon)
    order_status_bulk_options = [
        (f'status_setup_{setup.id}', f'Mark as {setup.name}', '📋')
        for setup in order_setups
    ]
    payment_status_bulk_options = [
        (f'payment_status_setup_{setup.id}', f'Mark as {setup.name}', '💳')
        for setup in payment_setups
    ]
    
    # ✅ FIX DECIMAL CORRUPTION IN ORDERS BEFORE DISPLAY
    # This ensures amounts are always correct without needing to visit detail page
    order_products = {}
    for order in orders_page.object_list:
        try:
            fix_order_decimals(order)
            # Build product names dictionary for template
            items = order.items.all()
            if items.exists():
                product_names = []
                for item in items:
                    if item.product_name:
                        if item.variation_name:
                            product_names.append(f"{item.product_name} ({item.variation_name})")
                        else:
                            product_names.append(item.product_name)
                order_products[order.id] = ", ".join(product_names)
            else:
                order_products[order.id] = "No products"
        except Exception as e:
            import logging
            logging.error(f"Error fixing decimals for order {order.id}: {e}")
            order_products[order.id] = "No products"
    
    context = {
        'orders': orders_page,
        'order_products': order_products,
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'pending_orders': pending_orders,
        'delivered_today': delivered_today,
        'confirmed_orders': confirmed_orders,
        'dispatched_orders': dispatched_orders,
        'search_query': search_query,
        'status_filter': status_filter,
        'payment_filter': payment_filter,
        'in_out_filter': in_out_filter,
        'date_filter': date_filter,
        'start_date': start_date,
        'end_date': end_date,
        'logistics_filter': logistics_filter,
        'per_page': per_page,
        'order_status_choices': order_status_choices,
        'payment_status_choices': payment_status_choices,
        'order_setups': order_setups,
        'payment_setups': payment_setups,
        'order_status_bulk_options': order_status_bulk_options,
        'payment_status_bulk_options': payment_status_bulk_options,
    }
    
    return render(request, 'orders_list.html', context)
@login_required
@permission_required('can_create_orders')
def order_create(request):
    """Create a new order with city management integration and custom product support"""
    if request.method == "POST":
        try:
            with transaction.atomic():
                customer_name = (request.POST.get("customer_name") or "").strip()
                customer_phone = (request.POST.get("customer_phone") or "").strip()
                customer_email = (request.POST.get("customer_email") or "").strip()
                # UPDATED: Get city from City model
                branch_city_name = (request.POST.get("branch_city") or "").strip()
                shipping_address = (request.POST.get("shipping_address") or "").strip()
                landmark = (request.POST.get("landmark") or "").strip()
                # Get in_out field from form (auto-detected)
                in_out = (request.POST.get("in_out") or "in").strip()

                created_by_id = request.POST.get("created_by")
                order_from = request.POST.get("order_from")
                order_status = request.POST.get("order_status") or "processing"
                payment_method = request.POST.get("payment_method") or ""
                payment_status = request.POST.get("payment_status") or "pending"

                # NEW: Get payment_setup, status_setup, and payment_status_setup from POST
                payment_setup_id = request.POST.get("payment_setup")
                status_setup_id = request.POST.get("status_setup")
                payment_status_setup_id = request.POST.get("payment_status_setup")
                payment_setup = None
                status_setup = None
                payment_status_setup = None

                if payment_setup_id:
                    try:
                        from .models import Setup
                        payment_setup = Setup.objects.get(id=payment_setup_id, setup_type='payment')
                        # Sync payment_method with the setup name
                        payment_method = payment_setup.name.lower().replace(' ', '_')
                    except Setup.DoesNotExist:
                        payment_setup = None

                if status_setup_id:
                    try:
                        from .models import Setup
                        status_setup = Setup.objects.get(id=status_setup_id, setup_type='status')
                        # ✅ SYNC order_status with the setup name (consistent with payment_status sync)
                        order_status = status_setup.name.lower().replace(' ', '_')
                    except Setup.DoesNotExist:
                        status_setup = None

                if payment_status_setup_id:
                    try:
                        from .models import Setup
                        payment_status_setup = Setup.objects.get(id=payment_status_setup_id, setup_type='payment_status')
                        payment_status = payment_status_setup.name.lower().replace(' ', '_')
                    except Setup.DoesNotExist:
                        payment_status_setup = None

                discount_amount = Decimal(request.POST.get("discount") or "0")
                shipping_charge = Decimal(request.POST.get("shipping_charge") or "0")
                tax_percent = Decimal(request.POST.get("tax_percent") or "0")
                total_amount = Decimal(request.POST.get("total_amount") or "0")
                notes = request.POST.get("notes") or ""

                # GET PARTIAL PAYMENT DATA
                is_partial_payment = request.POST.get("is_partial_payment") == "true"
                partial_amount_paid = Decimal(request.POST.get("partial_amount_paid") or "0")
                remaining_amount = Decimal(request.POST.get("remaining_amount") or "0")

                # UPDATED: Added in_out to required fields check
                missing = []
                if not customer_name:
                    missing.append('Customer Name')
                if not customer_phone:
                    missing.append('Phone Number')
                if not branch_city_name:
                    missing.append('Branch/City')
                if not shipping_address:
                    missing.append('Shipping Address')
                if not created_by_id:
                    missing.append('Created By')
                if not in_out:
                    missing.append('IN/OUT')

                if missing:
                    messages.error(request, f"Missing required fields: {', '.join(missing)}")
                    return redirect("order_create")

                created_by = get_object_or_404(User, id=created_by_id)

                # Get or create city from City model
                city, city_created = City.objects.get_or_create(
                    name=branch_city_name,
                    defaults={
                        'valley_status': 'valley' if in_out.lower() == 'in' else 'out_valley',
                        'is_active': True
                    }
                )
                
                # Update city valley status if needed
                if not city_created and in_out.lower() == 'in' and city.valley_status != 'valley':
                    city.valley_status = 'valley'
                    city.save()
                elif not city_created and in_out.lower() == 'out' and city.valley_status != 'out_valley':
                    city.valley_status = 'out_valley'
                    city.save()

                customer, created = Customer.objects.get_or_create(
                    phone=customer_phone,
                    defaults={
                        "name": customer_name,
                        "email": customer_email or None,
                        "city": branch_city_name,
                        "address": shipping_address,
                        "landmark": landmark,
                    },
                )
                customer.name = customer_name
                customer.email = customer_email or None
                customer.city = branch_city_name
                customer.address = shipping_address
                customer.landmark = landmark
                customer.save()

                # ✅ FIXED: Generate unique order number with race condition handling
                from .decimal_utils import safe_decimal
                try:
                    max_attempts = 100
                    order_number = None
                    
                    for attempt in range(max_attempts):
                        # Get the highest order number currently in database
                        last_order = Order.objects.filter(
                            order_number__startswith='T'
                        ).order_by('-order_number').first()
                        
                        if last_order:
                            try:
                                n = int(last_order.order_number[1:])  # Extract number after 'T'
                                order_number = f"T{n+1:03d}"
                            except (ValueError, AttributeError, IndexError):
                                order_number = f"T{Order.objects.filter(order_number__startswith='T').count() + 1:03d}"
                        else:
                            order_number = "T001"
                        
                        # Check if this order number already exists
                        if not Order.objects.filter(order_number=order_number).exists():
                            break
                    
                    if not order_number:
                        order_number = f"T{Order.objects.count() + 1:03d}"
                        
                except Exception as e:
                    logger.error(f"Error generating order number: {str(e)}")
                    order_number = f"T{Order.objects.count() + 1:03d}"

                order_items_json = request.POST.get("order_items") or "[]"
                cart = json.loads(order_items_json)

                if not cart:
                    messages.error(request, "No products in cart.")
                    return redirect("order_create")

                # ✅ FIXED: Use safe_decimal for all external numeric input
                discount_amount_safe = safe_decimal(discount_amount, max_digits=10, decimal_places=2)
                shipping_charge_safe = safe_decimal(shipping_charge, max_digits=10, decimal_places=2)
                tax_percent_safe = safe_decimal(tax_percent, max_digits=5, decimal_places=2)
                total_amount_safe = safe_decimal(total_amount, max_digits=10, decimal_places=2)
                
                # SET PAYMENT STATUS BASED ON PARTIAL PAYMENT
                if is_partial_payment:
                    payment_status = "partial"
                    partial_amount_paid = safe_decimal(partial_amount_paid, max_digits=10, decimal_places=2)
                    remaining_amount = safe_decimal(remaining_amount, max_digits=10, decimal_places=2)

                # UPDATED: Use branch_city from City model, added in_out field, and Setup fields
                # ✅ WITH RETRY LOGIC FOR RACE CONDITIONS
                order = None
                retry_count = 0
                max_retries = 5
                
                while order is None and retry_count < max_retries:
                    try:
                        order = Order.objects.create(
                            order_number=order_number,
                            created_by=created_by,
                            customer=customer,
                            customer_name=customer_name,
                            customer_phone=customer_phone,
                            customer_email=customer_email,
                            branch_city=branch_city_name,
                            in_out=in_out,
                            shipping_address=shipping_address,
                            landmark=landmark,
                            order_from=order_from,
                            order_status=order_status,
                            payment_method=payment_method,
                            payment_status=payment_status,
                            payment_setup=payment_setup,
                            status_setup=status_setup,
                            payment_status_setup=payment_status_setup,
                            discount_amount=discount_amount_safe,
                            shipping_charge=shipping_charge_safe,
                            tax_percent=tax_percent_safe,
                            total_amount=total_amount_safe,
                            notes=notes,
                            # ADD PARTIAL PAYMENT FIELDS
                            is_partial_payment=is_partial_payment,
                            partial_amount_paid=partial_amount_paid if is_partial_payment else None,
                            remaining_amount=remaining_amount if is_partial_payment else None,
                        )
                    except IntegrityError as e:
                        if 'order_number' in str(e):
                            # Order number exists, generate a new one and retry
                            retry_count += 1
                            last_order = Order.objects.filter(
                                order_number__startswith='T'
                            ).order_by('-order_number').first()
                            
                            if last_order:
                                try:
                                    n = int(last_order.order_number[1:])
                                    order_number = f"T{n+1:03d}"
                                except (ValueError, AttributeError, IndexError):
                                    order_number = f"T{Order.objects.filter(order_number__startswith='T').count() + retry_count:03d}"
                            else:
                                order_number = f"T{Order.objects.count() + retry_count:03d}"
                            
                            if retry_count >= max_retries:
                                messages.error(request, "Failed to create order after multiple attempts. Please try again.")
                                return redirect("order_create")
                        else:
                            raise
                
                # ✅ VERIFY ORDER WAS CREATED
                if not order:
                    messages.error(request, "Failed to create order. Please try again.")
                    return redirect("order_create")
                
                # ✅ SYNC ORDER STATUS WITH STATUS SETUP - ENSURES DATA CONSISTENCY
                order = sync_order_status_setup(order)

                # CREATE ORDER ITEMS
                for item in cart:
                    product_id = int(item.get("id"))
                    var_id = item.get("varId")
                    qty = int(item.get("qty") or 1)
                    price = Decimal(str(item.get("price") or "0"))
                    sku = item.get("sku") or ""

                    product = get_object_or_404(Product, id=product_id)

                    variation = None
                    variation_name = None
                    if var_id:
                        variation = get_object_or_404(ProductVariation, id=int(var_id), product=product)
                        sku = variation.sku
                        variation_name = getattr(variation, 'variation_name', None) or variation.sku

                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        product_variation=variation,
                        product_name=product.name,
                        product_sku=sku,
                        variation_name=variation_name,
                        quantity=qty,
                        price=price,
                        total=price * qty,
                    )

                # ADD CITY DETECTION LOG (Order creation is logged via signals.py)
                valley_status = "Valley" if in_out.lower() == 'in' else "Out Valley"
                OrderActivityLog.objects.create(
                    order=order,
                    action_type='city_detected',
                    user=created_by,
                    description=f'City "{branch_city_name}" detected as {valley_status}. IN/OUT set to {in_out.upper()}'
                )

                success_msg = f"Order {order.order_number} created successfully!"
                if is_partial_payment:
                    success_msg += f" | Partial payment: रू {partial_amount_paid} paid"
                
                messages.success(request, success_msg)
                return redirect("orders_list")

        except Exception as e:
            import traceback
            traceback.print_exc()
            messages.error(request, f"Error creating order: {str(e)}")
            return redirect("order_create")

    # GET REQUEST - SHOW FORM
    users = User.objects.filter(is_active=True).order_by("username")
    
    # FIX: Handle decimal conversion errors in recent orders
    try:
        # Convert queryset to list and fix any invalid decimals
        recent_orders_qs = Order.objects.filter(is_deleted=False).order_by("-created_at")[:6]
        recent_orders = []
        for order in recent_orders_qs:
            try:
                order = fix_order_decimals(order)
                recent_orders.append(order)
            except (InvalidOperation, ValueError, TypeError) as e:
                # Log the error but continue with other orders
                logger.error(f"Error fetching recent orders: {e}")
                continue
    except Exception as e:
        # If all else fails, use an empty list
        logger.error(f"Error fetching recent orders: {e}")
        recent_orders = []
    
    # GET CITIES FROM DATABASE
    cities = City.objects.filter(is_active=True).order_by('name')
    
    # GET CATEGORIES FOR CUSTOM PRODUCT MODAL
    categories = Category.objects.all().order_by('name')
    
    # NEW: GET PAYMENT AND STATUS SETUPS
    from .models import Setup
    payment_setups = Setup.objects.filter(setup_type='payment', is_active=True).order_by('name')
    status_setups = Setup.objects.filter(setup_type='status', is_active=True).order_by('name')
    payment_status_setups = Setup.objects.filter(setup_type='payment_status', is_active=True).order_by('name')
    order_source_setups = Setup.objects.filter(setup_type='order_source', is_active=True).order_by('name')

    return render(
        request,
        "order_create.html",
        {
            "users": users,
            "recent_orders": recent_orders,
            "cities": cities,
            "categories": categories,
            "payment_setups": payment_setups,
            "status_setups": status_setups,
            "payment_status_setups": payment_status_setups,
            "order_source_setups": order_source_setups,
        },
    )
@login_required
@permission_required('can_view_orders')
def order_detail(request, order_id):
    """View order details with partial payment info and status updates"""
    # CRITICAL: Always fetch fresh data from database - do NOT use cached objects
    # Use select_related to get ForeignKey relationships efficiently
    order = get_object_or_404(
        Order.objects.select_related(
            'status_setup',
            'payment_setup',
            'payment_status_setup',
            'customer',
            'created_by'
        ),
        id=order_id
    )
    order = fix_order_decimals(order)
    
    # ENSURE PARTIAL PAYMENT FIELDS ARE PROPERLY SET
    if order.payment_status == 'partial' and not order.is_partial_payment:
        order.is_partial_payment = True
        if order.partial_amount_paid is None:
            order.partial_amount_paid = Decimal('0.00')
        if order.remaining_amount is None:
            if order.total_amount and order.partial_amount_paid:
                order.remaining_amount = order.total_amount - order.partial_amount_paid
            else:
                order.remaining_amount = order.total_amount
        order.save()
    
    # Handle POST request for status updates
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'update_status':
            try:
                # Store old values for activity log
                old_order_status = order.order_status
                old_payment_status = order.payment_status
                old_payment_method = order.payment_method
                old_tracking = order.tracking_number or ''
                old_logistics = order.logistics or ''
                old_status_setup = order.status_setup
                old_payment_setup = order.payment_setup
                old_payment_status_setup = order.payment_status_setup

                # Get new values from form
                status_setup_id = request.POST.get('status_setup', '').strip()
                payment_setup_id = request.POST.get('payment_setup', '').strip()
                payment_status_setup_id = request.POST.get('payment_status_setup', '').strip()
                new_tracking = request.POST.get('tracking_number', '').strip()
                new_admin_notes = request.POST.get('admin_notes', '').strip()
                new_logistics_input = request.POST.get('logistics', '').strip()

                from .models import Setup
                changes_made = []

                # ====== UPDATE ORDER STATUS ======
                if status_setup_id:
                    try:
                        status_setup = Setup.objects.get(id=status_setup_id, setup_type='status')
                        if order.status_setup != status_setup:
                            order.status_setup = status_setup
                            # Also update the string field
                            order.order_status = status_setup.name.lower().replace(' ', '_')
                            changes_made.append('Order Status')
                    except Setup.DoesNotExist:
                        messages.warning(request, 'Selected status not found.')
                
                # ====== UPDATE PAYMENT STATUS ======
                if payment_status_setup_id:
                    try:
                        payment_status_setup = Setup.objects.get(id=payment_status_setup_id, setup_type='payment_status')
                        if order.payment_status_setup != payment_status_setup:
                            order.payment_status_setup = payment_status_setup
                            # Also update the string field
                            new_payment_status = payment_status_setup.name.lower().replace(' ', '_')
                            
                            # Handle partial payment logic
                            is_new_partial = 'partial' in new_payment_status.lower()
                            was_old_partial = 'partial' in (order.payment_status or '').lower()
                            
                            if not was_old_partial and is_new_partial:
                                order.is_partial_payment = True
                                if order.partial_amount_paid is None:
                                    order.partial_amount_paid = Decimal('0.00')
                                if order.remaining_amount is None:
                                    order.remaining_amount = order.total_amount
                            elif was_old_partial and not is_new_partial:
                                order.is_partial_payment = False
                            
                            order.payment_status = new_payment_status
                            changes_made.append('Payment Status')
                    except Setup.DoesNotExist:
                        messages.warning(request, 'Selected payment status not found.')
                
                # ====== UPDATE PAYMENT METHOD ======
                if payment_setup_id:
                    try:
                        payment_setup = Setup.objects.get(id=payment_setup_id, setup_type='payment')
                        if order.payment_setup != payment_setup:
                            order.payment_setup = payment_setup
                            order.payment_method = payment_setup.name.lower().replace(' ', '_')
                            changes_made.append('Payment Method')
                    except Setup.DoesNotExist:
                        messages.warning(request, 'Selected payment method not found.')
                
                # ====== UPDATE TRACKING NUMBER ======
                if new_tracking != old_tracking:
                    order.tracking_number = new_tracking
                    if new_tracking:
                        changes_made.append('Tracking Number')
                
                # ====== CREATE ADMIN NOTE ======
                if new_admin_notes:
                    from .models import OrderAdminNote
                    OrderAdminNote.objects.create(
                        order=order,
                        content=new_admin_notes,
                        created_by=request.user
                    )
                    changes_made.append('Admin Notes')
                
                # ====== UPDATE LOGISTICS (only if explicitly selected) ======
                if new_logistics_input and new_logistics_input != old_logistics:
                    order.logistics = new_logistics_input
                    changes_made.append('Logistics Provider')
                
                # ====== SET DELIVERED TIMESTAMP ======
                if order.order_status == 'delivered' and old_order_status != 'delivered':
                    order.delivered_at = timezone.now()
                
                # Save the order
                order.save()
                
                # CRITICAL: Ensure FK relationships are synced and setup records exist
                # Re-fetch the order to apply any FK sync changes
                order = Order.objects.select_related(
                    'status_setup',
                    'payment_setup',
                    'payment_status_setup'
                ).get(id=order.id)
                
                # Sync any missing FK relationships
                if not order.status_setup and order.order_status:
                    try:
                        setup_name = order.order_status.replace('_', ' ').title()
                        order.status_setup, _ = Setup.objects.get_or_create(
                            setup_type='status',
                            name=setup_name,
                            defaults={'is_active': True}
                        )
                        order.save(update_fields=['status_setup'])
                    except:
                        pass
                
                if not order.payment_setup and order.payment_method:
                    try:
                        setup_name = order.payment_method.replace('_', ' ').title()
                        order.payment_setup, _ = Setup.objects.get_or_create(
                            setup_type='payment',
                            name=setup_name,
                            defaults={'is_active': True}
                        )
                        order.save(update_fields=['payment_setup'])
                    except:
                        pass
                
                if not order.payment_status_setup and order.payment_status:
                    try:
                        setup_name = order.payment_status.replace('_', ' ').title()
                        order.payment_status_setup, _ = Setup.objects.get_or_create(
                            setup_type='payment_status',
                            name=setup_name,
                            defaults={'is_active': True}
                        )
                        order.save(update_fields=['payment_status_setup'])
                    except:
                        pass
                
                # ====== CREATE ACTIVITY LOGS ======
                if old_order_status != order.order_status:
                    OrderActivityLog.objects.create(
                        order=order,
                        action_type='status_changed',
                        user=request.user,
                        field_name='order_status',
                        old_value=old_order_status,
                        new_value=order.order_status,
                        description=f'Order status changed from "{old_order_status}" to "{order.order_status}"'
                    )
                
                if old_payment_status != order.payment_status:
                    OrderActivityLog.objects.create(
                        order=order,
                        action_type='payment_changed',
                        user=request.user,
                        field_name='payment_status',
                        old_value=old_payment_status,
                        new_value=order.payment_status,
                        description=f'Payment status changed from "{old_payment_status}" to "{order.payment_status}"'
                    )
                
                if old_payment_method != order.payment_method:
                    OrderActivityLog.objects.create(
                        order=order,
                        action_type='payment_changed',
                        user=request.user,
                        field_name='payment_method',
                        old_value=old_payment_method,
                        new_value=order.payment_method,
                        description=f'Payment method changed from "{old_payment_method}" to "{order.payment_method}"'
                    )
                
                if new_logistics_input and old_logistics != new_logistics_input:
                    logistics_display = {
                        'ncm': 'NCM',
                        'sundarijal': 'Sundarijal',
                        'express': 'Express',
                        'local': 'Local Delivery',
                        'other': 'Other',
                        '': 'None'
                    }
                    old_display = logistics_display.get(old_logistics, old_logistics or 'None')
                    new_display = logistics_display.get(new_logistics_input, new_logistics_input or 'None')
                    
                    OrderActivityLog.objects.create(
                        order=order,
                        action_type='updated',
                        user=request.user,
                        field_name='logistics',
                        old_value=old_logistics,
                        new_value=new_logistics_input,
                        description=f'Logistics provider changed from "{old_display}" to "{new_display}"'
                    )
                
                if changes_made:
                    messages.success(request, f"✅ Order updated! Changed: {', '.join(changes_made)}")
                else:
                    messages.info(request, "ℹ️ No changes were made to the order.")
                
                # CRITICAL: Clear QuerySet cache and redirect to fetch fresh data
                # This ensures edit_order will see the latest data from database
                from django.core.cache import cache
                cache.delete(f'order_{order.id}')  # Clear any order cache
                
                return redirect('order_detail', order_id=order.id)
                
            except Exception as e:
                logger.error(f"Error updating order {order_id}: {str(e)}")
                messages.error(request, f"❌ Error updating order: {str(e)}")
                return redirect('order_detail', order_id=order.id)
    
    # GET request - display order details
    # CRITICAL: Always fetch fresh data to ensure sync with order_edit page
    # SYNCHRONIZE ORDER STATUS WITH SETUP USING HELPER FUNCTION
    from .models import Setup
    order = sync_order_status_setup(order)
    # Force sync FK and string after every update
    if order.order_status and (not order.status_setup or order.order_status != order.status_setup.name.lower().replace(' ', '_')):
        try:
            setup_name = order.order_status.replace('_', ' ').title()
            order.status_setup, _ = Setup.objects.get_or_create(
                setup_type='status',
                name=setup_name,
                defaults={'is_active': True}
            )
            order.save(update_fields=['status_setup'])
        except Exception as e:
            logger.warning(f"Could not force sync status_setup for order {order_id}: {str(e)}")

    if order.payment_method and (not order.payment_setup or order.payment_method != order.payment_setup.name.lower().replace(' ', '_')):
        try:
            setup_name = order.payment_method.replace('_', ' ').title()
            order.payment_setup, _ = Setup.objects.get_or_create(
                setup_type='payment',
                name=setup_name,
                defaults={'is_active': True}
            )
            order.save(update_fields=['payment_setup'])
        except Exception as e:
            logger.warning(f"Could not force sync payment_setup for order {order_id}: {str(e)}")
    
    # CRITICAL: Re-fetch from database to get fresh FK relationships after sync
    order = Order.objects.select_related(
        'status_setup', 
        'payment_setup', 
        'payment_status_setup',
        'customer',
        'created_by'
    ).get(id=order_id)
    
    # Now get order items and activity logs from fresh order instance
    order_items = order.items.select_related('product', 'product_variation').all()
    activity_logs = order.activity_logs.select_related('user').order_by('-created_at')[:20]
    
    # Get Setup options for dropdowns
    status_setups = Setup.objects.filter(setup_type='status', is_active=True).order_by('name')
    payment_setups = Setup.objects.filter(setup_type='payment', is_active=True).order_by('name')
    payment_status_setups = Setup.objects.filter(setup_type='payment_status', is_active=True).order_by('name')
    
    # Calculate subtotal
    subtotal = sum(item.total for item in order_items) or Decimal('0.00')
    
    # Calculate amounts
    after_discount = subtotal - (order.discount_amount or Decimal('0'))
    tax_amount = (after_discount * (order.tax_percent or Decimal('0'))) / 100
    
    # ✅ RECALCULATE TOTAL CORRECTLY
    calculated_total = after_discount + tax_amount + (order.shipping_charge or Decimal('0'))
    
    # If stored total is wrong (capped at 99999999.99), use calculated value
    if order.total_amount != calculated_total:
        order.total_amount = calculated_total
        order.save()
    
    # CALCULATE PARTIAL PAYMENT INFO
    is_partial_payment = order.is_partial_payment or order.payment_status == 'partial'
    partial_amount_paid = order.partial_amount_paid or Decimal('0.00')
    
    # Auto-calculate remaining amount if not set
    if is_partial_payment and order.remaining_amount is None:
        remaining_amount = (calculated_total or Decimal('0.00')) - partial_amount_paid
        if remaining_amount < 0:
            remaining_amount = Decimal('0.00')
    else:
        remaining_amount = order.remaining_amount or Decimal('0.00')
    
    context = {
        'order': order,
        'order_items': order_items,
        'activity_logs': activity_logs,
        'subtotal': subtotal,
        'after_discount': after_discount,
        'tax_amount': tax_amount,
        'calculated_total': calculated_total,  # ✅ Use recalculated total
        
        # ENHANCED PARTIAL PAYMENT INFO
        'is_partial_payment': is_partial_payment,
        'partial_amount_paid': partial_amount_paid,
        'remaining_amount': remaining_amount,
        
        # CALCULATE PARTIAL PAYMENT PERCENTAGE FOR PROGRESS BAR
        'partial_payment_percentage': 0,

        # Setup dropdowns
        'status_setups': status_setups,
        'payment_setups': payment_setups,
        'payment_status_setups': payment_status_setups,
    }
    
    # Calculate percentage for progress bar
    if is_partial_payment and calculated_total and calculated_total > 0:
        try:
            percentage = (partial_amount_paid / calculated_total) * 100
            context['partial_payment_percentage'] = min(100, max(0, float(percentage)))
        except:
            context['partial_payment_percentage'] = 0
    
    return render(request, 'order_detail.html', context)

@login_required
@permission_required('can_edit_orders')
def order_edit(request, order_id):
    """Edit an existing order with city management integration and two-way sync"""
    # CRITICAL: Always fetch fresh data from database
    # Use select_related to load all ForeignKey relationships at once
    order = get_object_or_404(
        Order.objects.select_related(
            'status_setup', 
            'payment_setup', 
            'payment_status_setup',
            'customer',
            'created_by'
        ),
        id=order_id
    )
    
    # Synchronize status with setup records before processing
    order = sync_order_status_setup(order)
    
    if request.method == "POST":
        try:
            with transaction.atomic():
                # Store old values for activity log
                old_payment_method = order.payment_method
                old_is_partial = order.is_partial_payment
                old_partial_paid = order.partial_amount_paid or Decimal('0')
                old_remaining = order.remaining_amount or Decimal('0')
                old_city = order.branch_city
                old_in_out = order.in_out
                old_order_status = order.order_status
                old_payment_status = order.payment_status
                
                # Update basic fields
                order.customer_name = request.POST.get("customer_name", "").strip()
                order.customer_phone = request.POST.get("customer_phone", "").strip()
                order.customer_email = request.POST.get("customer_email", "").strip()
                
                # Get city and in_out field
                branch_city_name = request.POST.get("branch_city", "").strip()
                in_out = request.POST.get("in_out", "in").strip()
                
                order.shipping_address = request.POST.get("shipping_address", "").strip()
                order.landmark = request.POST.get("landmark", "").strip()
                
                created_by_id = request.POST.get("created_by")
                order.created_by = get_object_or_404(User, id=created_by_id)
                
                order.order_from = request.POST.get("order_from")

                # CRITICAL: Get payment_setup, status_setup, and payment_status_setup from POST
                from .models import Setup
                payment_setup_id = request.POST.get("payment_setup")
                status_setup_id = request.POST.get("status_setup")
                payment_status_setup_id = request.POST.get("payment_status_setup")

                # Update status_setup and sync order_status
                if status_setup_id:
                    try:
                        status_setup = Setup.objects.get(id=status_setup_id, setup_type='status')
                        order.status_setup = status_setup
                        # Sync order_status with the setup name so the NOT NULL field stays valid
                        order.order_status = status_setup.name.lower().replace(' ', '_')
                    except Setup.DoesNotExist:
                        order.status_setup = None
                else:
                    # Keep existing order_status if no status_setup is selected
                    order.order_status = order.order_status or 'processing'
                    # But ensure FK is synced if string value exists
                    if order.order_status and not order.status_setup:
                        try:
                            setup_name = order.order_status.replace('_', ' ').title()
                            order.status_setup, _ = Setup.objects.get_or_create(
                                setup_type='status',
                                name=setup_name,
                                defaults={'is_active': True}
                            )
                        except:
                            pass

                # Update payment_setup and sync payment_method
                if payment_setup_id:
                    try:
                        payment_setup = Setup.objects.get(id=payment_setup_id, setup_type='payment')
                        order.payment_setup = payment_setup
                        # Sync payment_method with the setup name
                        order.payment_method = payment_setup.name.lower().replace(' ', '_')
                    except Setup.DoesNotExist:
                        order.payment_setup = None
                else:
                    # Keep existing payment_method if no payment_setup is selected
                    if order.payment_method and not order.payment_setup:
                        try:
                            setup_name = order.payment_method.replace('_', ' ').title()
                            order.payment_setup, _ = Setup.objects.get_or_create(
                                setup_type='payment',
                                name=setup_name,
                                defaults={'is_active': True}
                            )
                        except:
                            pass

                # Update payment_status_setup and sync payment_status
                if payment_status_setup_id:
                    try:
                        ps_setup = Setup.objects.get(id=payment_status_setup_id, setup_type='payment_status')
                        order.payment_status_setup = ps_setup
                        order.payment_status = ps_setup.name.lower().replace(' ', '_')
                    except Setup.DoesNotExist:
                        order.payment_status_setup = None
                else:
                    order.payment_status = order.payment_status or 'pending'
                    # But ensure FK is synced if string value exists
                    if order.payment_status and not order.payment_status_setup:
                        try:
                            setup_name = order.payment_status.replace('_', ' ').title()
                            order.payment_status_setup, _ = Setup.objects.get_or_create(
                                setup_type='payment_status',
                                name=setup_name,
                                defaults={'is_active': True}
                            )
                        except:
                            pass
                
                order.discount_amount = Decimal(request.POST.get("discount") or "0")
                order.shipping_charge = Decimal(request.POST.get("shipping_charge") or "0")
                order.tax_percent = Decimal(request.POST.get("tax_percent") or "0")
                order.total_amount = Decimal(request.POST.get("total_amount") or "0")
                order.notes = request.POST.get("notes", "")
                
                # ✅ CREATE ADMIN NOTE
                new_admin_notes = request.POST.get("admin_notes", "").strip()
                if new_admin_notes:
                    from .models import OrderAdminNote
                    OrderAdminNote.objects.create(
                        order=order,
                        content=new_admin_notes,
                        created_by=request.user
                    )

                # UPDATE PARTIAL PAYMENT DATA
                is_partial_payment = request.POST.get("is_partial_payment") == "true"
                partial_amount_paid = Decimal(request.POST.get("partial_amount_paid") or "0")
                remaining_amount = Decimal(request.POST.get("remaining_amount") or "0")
                
                order.is_partial_payment = is_partial_payment
                order.partial_amount_paid = partial_amount_paid if is_partial_payment else None
                order.remaining_amount = remaining_amount if is_partial_payment else None
                
                # UPDATE PAYMENT STATUS BASED ON PARTIAL PAYMENT
                if is_partial_payment:
                    if partial_amount_paid >= order.total_amount:
                        order.payment_status = "paid"
                    elif partial_amount_paid > 0:
                        order.payment_status = "partial"
                    else:
                        order.payment_status = "pending"

                # Get or create city from City model
                if branch_city_name:
                    city, city_created = City.objects.get_or_create(
                        name=branch_city_name,
                        defaults={
                            'valley_status': 'valley' if in_out.lower() == 'in' else 'out_valley',
                            'is_active': True
                        }
                    )
                    
                    # Update city valley status if needed
                    if not city_created and in_out.lower() == 'in' and city.valley_status != 'valley':
                        city.valley_status = 'valley'
                        city.save()
                    elif not city_created and in_out.lower() == 'out' and city.valley_status != 'out_valley':
                        city.valley_status = 'out_valley'
                        city.save()
                    
                    order.branch_city = branch_city_name
                
                order.in_out = in_out

                # Update customer
                if order.customer:
                    order.customer.name = order.customer_name
                    order.customer.email = order.customer_email or None
                    order.customer.phone = order.customer_phone
                    order.customer.city = order.branch_city
                    order.customer.address = order.shipping_address
                    order.customer.landmark = order.landmark
                    order.customer.save()

                # Update order items
                order.items.all().delete()
                
                order_items_json = request.POST.get("order_items") or "[]"
                cart = json.loads(order_items_json)

                if not cart:
                    messages.error(request, "No products in cart.")
                    return redirect("order_edit", order_id=order.id)

                for item in cart:
                    product_id = int(item.get("id"))
                    var_id = item.get("varId")
                    qty = int(item.get("qty") or 1)
                    price = Decimal(str(item.get("price") or "0"))
                    sku = item.get("sku") or ""

                    product = get_object_or_404(Product, id=product_id)
                    variation = None
                    variation_name = None
                    if var_id:
                        variation = get_object_or_404(ProductVariation, id=int(var_id))
                        sku = variation.sku
                        variation_name = getattr(variation, 'variation_name', None) or variation.sku

                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        product_variation=variation,
                        product_name=product.name,
                        product_sku=sku,
                        variation_name=variation_name,
                        quantity=qty,
                        price=price,
                        total=price * qty,
                    )

                order.save()

                # CREATE ACTIVITY LOG FOR CHANGES
                description = f"Order #{order.order_number} was updated"
                changes = []
                
                # Check status change
                if old_order_status != order.order_status:
                    changes.append(f"Status: {old_order_status} → {order.order_status}")
                
                # Check payment method change
                if old_payment_method != order.payment_method:
                    changes.append(f"Payment Method: {old_payment_method} → {order.payment_method}")
                
                # Check payment status change
                if old_payment_status != order.payment_status:
                    changes.append(f"Payment Status: {old_payment_status} → {order.payment_status}")
                
                # Check if partial payment changed
                if is_partial_payment != old_is_partial:
                    if is_partial_payment:
                        changes.append(f"Changed to Partial Payment: रू {partial_amount_paid} paid, रू {remaining_amount} remaining")
                    else:
                        changes.append(f"Changed from Partial Payment to {order.payment_method.upper()}")
                elif is_partial_payment:
                    if partial_amount_paid != old_partial_paid:
                        changes.append(f"Partial Payment Updated: रू {partial_amount_paid} paid, रू {remaining_amount} remaining")
                
                # Check if city changed
                if old_city != branch_city_name:
                    changes.append(f"City: {old_city} → {branch_city_name}")
                
                # Check if IN/OUT changed
                if old_in_out != in_out:
                    changes.append(f"IN/OUT: {old_in_out.upper()} → {in_out.upper()}")
                
                if changes:
                    description += " | " + " | ".join(changes)
                
                OrderActivityLog.objects.create(
                    order=order,
                    action_type='updated',
                    user=request.user,
                    description=description
                )

                # Success message
                success_msg = f"✅ Order {order.order_number} updated successfully!"
                if is_partial_payment:
                    success_msg += f" | Partial payment: रू {partial_amount_paid} paid"
                
                messages.success(request, success_msg)
                
                # CRITICAL: Clear any cache and redirect to order_detail to ensure fresh data
                # This ensures order_detail will fetch the latest data from database
                from django.core.cache import cache
                cache.delete(f'order_{order.id}')
                
                return redirect("order_detail", order_id=order.id)

        except json.JSONDecodeError:
            messages.error(request, "Invalid cart data.")
            return redirect("order_edit", order_id=order.id)
        except Exception as e:
            messages.error(request, f"Error updating order: {str(e)}")
            import traceback
            traceback.print_exc()
            return redirect("order_edit", order_id=order.id)

    # GET request - show form with fresh data
    # CRITICAL: Fetch fresh order data from database
    order = Order.objects.select_related(
        'status_setup', 
        'payment_setup', 
        'payment_status_setup',
        'customer',
        'created_by'
    ).get(id=order_id)
    
    # CRITICAL: Ensure all FK relationships are properly synced and setup records exist
    # This ensures dropdowns show selected values correctly
    from .models import Setup
    
    # Sync status with setup
    order = sync_order_status_setup(order)
    
    # Ensure status_setup FK exists and is synced with order_status
    if not order.status_setup and order.order_status:
        try:
            setup_name = order.order_status.replace('_', ' ').title()
            order.status_setup, _ = Setup.objects.get_or_create(
                setup_type='status',
                name=setup_name,
                defaults={'is_active': True}
            )
            order.save(update_fields=['status_setup'])
        except Exception as e:
            logger.warning(f"Could not create status_setup for order {order_id}: {str(e)}")
    
    # Ensure payment_setup FK exists and is synced with payment_method
    if not order.payment_setup and order.payment_method:
        try:
            setup_name = order.payment_method.replace('_', ' ').title()
            order.payment_setup, _ = Setup.objects.get_or_create(
                setup_type='payment',
                name=setup_name,
                defaults={'is_active': True}
            )
            order.save(update_fields=['payment_setup'])
        except Exception as e:
            logger.warning(f"Could not create payment_setup for order {order_id}: {str(e)}")
    
    # Ensure payment_status_setup FK exists and is synced with payment_status
    if not order.payment_status_setup and order.payment_status:
        try:
            setup_name = order.payment_status.replace('_', ' ').title()
            order.payment_status_setup, _ = Setup.objects.get_or_create(
                setup_type='payment_status',
                name=setup_name,
                defaults={'is_active': True}
            )
            order.save(update_fields=['payment_status_setup'])
        except Exception as e:
            logger.warning(f"Could not create payment_status_setup for order {order_id}: {str(e)}")
    
    # Reload fresh FK relationships after all syncs
    order = Order.objects.select_related(
        'status_setup', 
        'payment_setup', 
        'payment_status_setup',
        'customer',
        'created_by'
    ).get(id=order_id)
    
    order_items = order.items.select_related('product', 'product_variation').all()
    
    users = User.objects.filter(is_active=True).order_by("username")
    statuses = ["processing", "confirmed", "shipped", "delivered", "cancelled"]
    order_sources = ["website", "facebook", "instagram", "phone", "walk-in"]
    payment_methods = ["cod", "esewa", "khalti", "bank", "cash", "partial"]

    # Prepare JSON for initial client-side cart (safe and linter-friendly)
    initial_items_list = []
    for item in order_items:
        if item.product:
            initial_items_list.append({
                'id': item.product.id,
                'varId': item.product_variation.id if item.product_variation else None,
                'name': item.product_name,
                'sku': item.product_sku or '',
                'price': float(item.price),
                'qty': item.quantity,
            })
    initial_items_json = json.dumps(initial_items_list)

    # Get active cities for Branch/City select (from City management)
    cities = City.objects.filter(is_active=True).order_by('name')
    
    # CRITICAL: GET PAYMENT AND STATUS SETUPS FROM DATABASE
    # These must be fresh to ensure synchronization with order_detail
    from .models import Setup
    payment_setups = Setup.objects.filter(setup_type='payment', is_active=True).order_by('name')
    status_setups = Setup.objects.filter(setup_type='status', is_active=True).order_by('name')
    payment_status_setups = Setup.objects.filter(setup_type='payment_status', is_active=True).order_by('name')
    order_source_setups = Setup.objects.filter(setup_type='order_source', is_active=True).order_by('name')

    # ✅ SAFE: Handle decimal InvalidOperation errors by deferring problematic decimal fields
    # Some orders have corrupted decimal values in total_amount and other fields
    decimal_fields_to_defer = [
        'discount_amount', 'shipping_charge', 'delivery_charge', 
        'expense_amount', 'tax_percent', 'total_amount',
        'partial_amount_paid', 'remaining_amount', 'cod_collected', 
        'package_weight'
    ]
    
    try:
        recent_orders = Order.objects.defer(
            *decimal_fields_to_defer
        ).order_by("-created_at")[:6]
        # Ensure the queryset is evaluated
        list(recent_orders)
    except Exception as e:
        logger.error(f"Error fetching recent orders even with defer: {e}")
        recent_orders = []

    context = {
        "order": order,
        "order_items": order_items,
        "initial_items_json": initial_items_json,
        "users": users,
        "statuses": statuses,
        "order_sources": order_sources,
        "payment_methods": payment_methods,
        "recent_orders": recent_orders,
        "cities": cities,
        "payment_setups": payment_setups,
        "status_setups": status_setups,
        "payment_status_setups": payment_status_setups,
        "order_source_setups": order_source_setups,
        "categories": Category.objects.all().order_by('name'),
    }

    return render(request, "order_edit.html", context)

@login_required
@permission_required('can_delete_orders')
def order_delete(request, order_id):
    order = get_object_or_404(Order, id=order_id, created_by=request.user)

    if request.method == "POST":
        order_number = order.order_number

        # REMOVED STOCK RESTORATION - Stock was never reduced during order creation
        # Only orders with status="dispatched" have reduced stock
        # If you want to restore stock for dispatched orders, check status:
        
        if order.order_status == 'dispatched':
            # Restore stock for dispatched orders only
            for item in order.items.all():
                if item.product_variation:
                    item.product_variation.stock += item.quantity
                    if item.product_variation.stock > 0:
                        item.product_variation.status = 'active'
                    item.product_variation.save()
                elif item.product:
                    item.product.stock += item.quantity
                    if item.product.stock > 0:
                        item.product.stock_status = 'in_stock'
                    item.product.save()

        order.delete()
        messages.success(request, f"Order {order_number} deleted successfully!")
        return redirect("orders_list")

    return render(request, "order_delete.html", {"order": order})


@login_required
@permission_required('can_view_orders')
def orders_trash(request):
    """View trashed orders"""
    # Show all trashed orders (removed user filter)
    trashed_orders = Order.objects.filter(
        is_deleted=True
    ).order_by('-deleted_at')
    
    # Search functionality
    search_query = request.GET.get("search", "")
    if search_query:
        trashed_orders = trashed_orders.filter(
            Q(order_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(customer_phone__icontains=search_query)
        )
    
    # Status filter
    status_filter = request.GET.get("status", "")
    if status_filter:
        trashed_orders = trashed_orders.filter(order_status=status_filter)
    
    context = {
        "trashed_orders": trashed_orders,
        "search_query": search_query,
        "status_filter": status_filter,
    }
    
    return render(request, "orders_trash.html", context)


@login_required
@permission_required('can_delete_orders')
def order_move_to_trash(request, order_id):
    """Move order to trash (soft delete)"""
    # Removed user filter
    order = get_object_or_404(Order, id=order_id, is_deleted=False)
    
    if request.method == 'POST':
        order_number = order.order_number
        order.is_deleted = True
        order.deleted_at = timezone.now()
        order.save()
        
        # Log activity
        OrderActivityLog.objects.create(
            order=order,
            user=request.user,
            action_type='deleted',
            description=f'Order moved to trash by {request.user.username}'
        )
        
        messages.success(request, f'Order "{order_number}" moved to trash successfully!')
        return redirect('orders_list')
    
    return redirect('order_detail', order_id=order_id)


@login_required
@permission_required('can_delete_orders')
def order_restore(request, order_id):
    """Restore order from trash"""
    # Removed user filter
    order = get_object_or_404(Order, id=order_id, is_deleted=True)
    
    if request.method == 'POST':
        order_number = order.order_number
        order.is_deleted = False
        order.deleted_at = None
        order.save()
        
        # Log activity
        OrderActivityLog.objects.create(
            order=order,
            user=request.user,
            action_type='restored',
            description=f'Order restored from trash by {request.user.username}'
        )
        
        messages.success(request, f'Order "{order_number}" restored successfully!')
        return redirect('orders_trash')
    
    return redirect('orders_trash')


@login_required
@permission_required('can_delete_orders')
def order_permanent_delete(request, order_id):
    """Permanently delete order"""
    # Removed user filter
    order = get_object_or_404(Order, id=order_id, is_deleted=True)
    
    if request.method == 'POST':
        order_number = order.order_number
        order.delete()
        
        messages.success(request, f'Order "{order_number}" permanently deleted!')
        return redirect('orders_trash')
    
    return redirect('orders_trash')


@login_required
@permission_required('can_delete_orders')
def orders_trash_bulk_action(request):
    """Handle bulk actions on trashed orders"""
    if request.method == "POST":
        order_ids = request.POST.getlist("order_ids")
        action = request.POST.get("bulk_action")
        
        if not order_ids:
            messages.error(request, "No orders selected!")
            return redirect('orders_trash')
        
        try:
            # Removed user filter
            orders = Order.objects.filter(
                id__in=order_ids,
                is_deleted=True
            )
            count = orders.count()
            
            if count == 0:
                messages.error(request, "No valid orders found!")
                return redirect('orders_trash')
            
            if action == "restore":
                orders.update(is_deleted=False, deleted_at=None)
                
                # Log activity for each restored order
                for order in orders:
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user,
                        action_type='restored',
                        description=f'Order restored from trash by {request.user.username}'
                    )
                
                messages.success(request, f"✅ {count} order(s) restored successfully!")
                
            elif action == "permanent_delete":
                orders.delete()
                messages.success(request, f"✅ {count} order(s) permanently deleted!")
                
            else:
                messages.error(request, "Invalid action selected!")
                
        except Exception as e:
            messages.error(request, f"Error performing bulk action: {str(e)}")
            
    return redirect('orders_trash')


@login_required
@permission_required('can_delete_orders')
def empty_orders_trash(request):
    """Empty all trashed orders"""
    if request.method == 'POST':
        # Removed user filter - empty ALL trashed orders
        trashed_orders = Order.objects.filter(is_deleted=True)
        count = trashed_orders.count()
        
        if count > 0:
            trashed_orders.delete()
            messages.success(request, f'✅ Trash emptied! {count} order(s) permanently deleted.')
        else:
            messages.info(request, 'Trash is already empty.')
        
        return redirect('orders_trash')
    
    return redirect('orders_trash')


@login_required
@permission_required('can_view_orders')
def return_orders_list(request):
    """Display list of orders with Return status"""
    from datetime import timedelta
    from django.utils import timezone
    from django.db.models import Q, Sum
    from decimal import Decimal
    import pytz
    
    # Get all orders with "Return" status
    orders = Order.objects.filter(
        is_deleted=False,
        order_status__iexact='return'  # Case-insensitive search for 'Return' status
    ).select_related(
        'customer', 'created_by', 'status_setup', 
        'payment_setup', 'payment_status_setup'
    ).prefetch_related('items').order_by('-created_at')
    
    # GET FILTER PARAMETERS
    search_query = request.GET.get('search', '')
    payment_filter = request.GET.get('payment', '')
    logistics_filter = request.GET.get('logistics_status', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    
    # Apply filters
    if search_query:
        orders = orders.filter(
            Q(order_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(customer_phone__icontains=search_query) |
            Q(customer_email__icontains=search_query)
        )
    
    # Payment status filter
    if payment_filter:
        try:
            payment_setup = Setup.objects.filter(
                setup_type='payment_status',
                name__iexact=payment_filter.replace('_', ' ')
            ).first()
            
            if payment_setup:
                orders = orders.filter(
                    Q(payment_status_setup_id=payment_setup.id) |
                    (Q(payment_status_setup_id__isnull=True) & Q(payment_status__iexact=payment_filter.replace('_', ' ')))
                )
            else:
                orders = orders.filter(payment_status__iexact=payment_filter.replace('_', ' '))
        except Exception:
            orders = orders.filter(payment_status=payment_filter)
    
    # Logistics filter
    if logistics_filter == 'sent':
        orders = orders.exclude(ncm_order_id__isnull=True)
    elif logistics_filter == 'not_sent':
        orders = orders.filter(ncm_order_id__isnull=True)
    
    # Date filter
    if start_date and end_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            orders = orders.filter(created_at__date__gte=start_date_obj, created_at__date__lte=end_date_obj)
        except ValueError:
            pass
    
    # Calculate statistics
    total_return_orders = orders.count()
    total_return_amount = orders.filter(payment_status='paid').aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
    pending_returns = orders.filter(payment_status='pending').count()
    
    # Calculate average return value
    if total_return_orders > 0:
        average_return_value = total_return_amount / Decimal(total_return_orders)
    else:
        average_return_value = Decimal('0')
    
    # Pagination
    per_page = request.GET.get('per_page', '50')
    if per_page not in ('50', '100', '200'):
        per_page = '50'
    paginator = Paginator(orders, int(per_page))
    page_number = request.GET.get('page')
    orders_page = paginator.get_page(page_number)
    
    # Fix decimal corruption in orders
    for order in orders_page.object_list:
        try:
            fix_order_decimals(order)
        except Exception:
            pass
    
    # Get payment statuses for filters
    payment_setups = Setup.objects.filter(setup_type='payment_status', is_active=True).order_by('name')
    payment_status_choices = [
        (setup.name.lower().replace(' ', '_'), setup.name)
        for setup in payment_setups
    ]
    
    context = {
        'orders': orders_page,
        'total_return_orders': total_return_orders,
        'total_return_amount': total_return_amount,
        'pending_returns': pending_returns,
        'average_return_value': average_return_value,
        'search_query': search_query,
        'payment_filter': payment_filter,
        'logistics_filter': logistics_filter,
        'start_date': start_date,
        'end_date': end_date,
        'per_page': per_page,
        'payment_status_choices': payment_status_choices,
        'page_obj': orders_page,
    }
    
    return render(request, 'return_orders.html', context)


@login_required
@permission_required('can_view_on_hold_orders')
def on_hold_orders_list(request):
    """Display list of orders with On Hold status"""
    from django.db.models import Q, Sum
    from decimal import Decimal

    # Primary query: match both FK and string field for coverage
    on_hold_setup = Setup.objects.filter(
        setup_type='status',
        name__iexact='on hold'
    ).first()

    if on_hold_setup:
        orders = Order.objects.filter(
            is_deleted=False
        ).filter(
            Q(status_setup_id=on_hold_setup.id) |
            (Q(status_setup_id__isnull=True) & Q(order_status__iexact='on hold'))
        )
    else:
        orders = Order.objects.filter(
            is_deleted=False,
            order_status__iexact='on hold'
        )

    orders = orders.select_related(
        'customer', 'created_by', 'status_setup',
        'payment_setup', 'payment_status_setup', 'followup_assigned_to'
    ).prefetch_related('items', 'followups', 'followups__user').order_by('-created_at')

    # Filters
    search_query = request.GET.get('search', '')
    payment_filter = request.GET.get('payment', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')

    if search_query:
        orders = orders.filter(
            Q(order_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(customer_phone__icontains=search_query) |
            Q(customer_email__icontains=search_query)
        )

    if payment_filter:
        try:
            payment_setup = Setup.objects.filter(
                setup_type='payment_status',
                name__iexact=payment_filter.replace('_', ' ')
            ).first()
            if payment_setup:
                orders = orders.filter(
                    Q(payment_status_setup_id=payment_setup.id) |
                    (Q(payment_status_setup_id__isnull=True) & Q(payment_status__iexact=payment_filter.replace('_', ' ')))
                )
            else:
                orders = orders.filter(payment_status__iexact=payment_filter.replace('_', ' '))
        except Exception:
            orders = orders.filter(payment_status=payment_filter)

    if start_date and end_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            orders = orders.filter(created_at__date__gte=start_date_obj, created_at__date__lte=end_date_obj)
        except ValueError:
            pass

    # Statistics
    total_on_hold_orders = orders.count()
    total_on_hold_amount = orders.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
    pending_payment_count = orders.filter(payment_status__iexact='pending').count()
    average_on_hold_value = (total_on_hold_amount / Decimal(total_on_hold_orders)) if total_on_hold_orders > 0 else Decimal('0')

    # Pagination
    per_page = request.GET.get('per_page', '50')
    if per_page not in ('50', '100', '200'):
        per_page = '50'
    paginator = Paginator(orders, int(per_page))
    page_number = request.GET.get('page')
    orders_page = paginator.get_page(page_number)

    nepal_tz = pytz.timezone('Asia/Kathmandu')
    followup_meta = {}
    for order in orders_page.object_list:
        try:
            fix_order_decimals(order)
        except Exception:
            pass
        # Build follow-up metadata for template
        latest = order.followups.first()  # already ordered by -created_at
        followup_meta[order.id] = {
            'has_followups': order.followups.exists(),
            'count': order.followups.count(),
            'last_user': (latest.user.get_full_name() or latest.user.username) if latest and latest.user else None,
            'last_date': latest.created_at.astimezone(nepal_tz).strftime('%b %d, %Y %I:%M %p') if latest else None,
            'last_type': latest.get_followup_type_display() if latest else None,
            'last_comment': latest.comment if latest else None,
        }

    # Dynamic bulk action options
    order_setups = Setup.objects.filter(setup_type='status', is_active=True).order_by('name')
    payment_setups = Setup.objects.filter(setup_type='payment_status', is_active=True).order_by('name')

    order_status_bulk_options = [
        (f'status_setup_{setup.id}', f'Mark as {setup.name}', '📋')
        for setup in order_setups
    ]
    payment_status_bulk_options = [
        (f'payment_status_setup_{setup.id}', f'Mark as {setup.name}', '💳')
        for setup in payment_setups
    ]
    payment_status_choices = [
        (setup.name.lower().replace(' ', '_'), setup.name)
        for setup in payment_setups
    ]

    # Staff members for follow-up assignment
    User = get_user_model()
    staff_members = User.objects.filter(
        is_active=True, is_deleted=False
    ).order_by('first_name', 'last_name')

    context = {
        'orders': orders_page,
        'total_on_hold_orders': total_on_hold_orders,
        'total_on_hold_amount': total_on_hold_amount,
        'pending_payment_count': pending_payment_count,
        'average_on_hold_value': average_on_hold_value,
        'search_query': search_query,
        'payment_filter': payment_filter,
        'start_date': start_date,
        'end_date': end_date,
        'per_page': per_page,
        'payment_status_choices': payment_status_choices,
        'order_status_bulk_options': order_status_bulk_options,
        'payment_status_bulk_options': payment_status_bulk_options,
        'page_obj': orders_page,
        'followup_meta': followup_meta,
        'staff_members': staff_members,
    }

    return render(request, 'on_hold_orders.html', context)


@login_required
@permission_required('can_view_on_hold_orders')
@require_POST
def add_order_followup(request, order_id):
    """AJAX endpoint to add a follow-up comment to an order"""
    order = get_object_or_404(Order, id=order_id, is_deleted=False)

    comment = request.POST.get('comment', '').strip()
    followup_type = request.POST.get('followup_type', 'custom_note')

    if not comment:
        return JsonResponse({'success': False, 'error': 'Comment cannot be empty.'}, status=400)

    valid_types = dict(OrderFollowUp.FOLLOWUP_TYPE_CHOICES)
    if followup_type not in valid_types:
        followup_type = 'custom_note'

    followup = OrderFollowUp.objects.create(
        order=order,
        user=request.user,
        followup_type=followup_type,
        comment=comment
    )

    nepal_tz = pytz.timezone('Asia/Kathmandu')
    created_local = followup.created_at.astimezone(nepal_tz)

    return JsonResponse({
        'success': True,
        'followup': {
            'id': followup.id,
            'user': request.user.get_full_name() or request.user.username,
            'followup_type': followup.get_followup_type_display(),
            'followup_type_key': followup.followup_type,
            'comment': followup.comment,
            'created_at': created_local.strftime('%b %d, %Y %I:%M %p'),
        }
    })


@login_required
@permission_required('can_view_on_hold_orders')
def get_order_followups(request, order_id):
    """AJAX endpoint to get all follow-ups for an order"""
    order = get_object_or_404(Order, id=order_id, is_deleted=False)
    followups = order.followups.select_related('user').all()

    nepal_tz = pytz.timezone('Asia/Kathmandu')
    followup_list = []
    for f in followups:
        created_local = f.created_at.astimezone(nepal_tz)
        followup_list.append({
            'id': f.id,
            'user': f.user.get_full_name() or f.user.username if f.user else 'Unknown',
            'followup_type': f.get_followup_type_display(),
            'followup_type_key': f.followup_type,
            'comment': f.comment,
            'created_at': created_local.strftime('%b %d, %Y %I:%M %p'),
        })

    return JsonResponse({
        'success': True,
        'followups': followup_list,
        'count': len(followup_list)
    })


@login_required
@permission_required('can_view_on_hold_orders')
@require_POST
def update_order_next_followup(request, order_id):
    """AJAX endpoint to set/update next follow-up date, type, assigned staff, and done status"""
    from datetime import datetime as dt

    order = get_object_or_404(Order, id=order_id, is_deleted=False)
    User = get_user_model()

    action = request.POST.get('action', 'update')
    update_fields = []

    if action == 'mark_done':
        order.followup_done = True
        update_fields.append('followup_done')
        order.save(update_fields=update_fields)

        return JsonResponse({
            'success': True,
            'followup_done': True,
            'message': 'Follow-up marked as done. Set a new date for the next cycle.',
        })

    # Handle date
    date_str = request.POST.get('next_followup_date', '').strip()
    if date_str:
        try:
            parsed_date = dt.strptime(date_str, '%Y-%m-%d').date()
            order.next_followup_date = parsed_date
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid date format.'}, status=400)
    else:
        order.next_followup_date = None
    update_fields.append('next_followup_date')

    # Handle type
    followup_type = request.POST.get('followup_type', '').strip()
    valid_types = dict(Order.FOLLOWUP_TYPE_CHOICES)
    if followup_type in valid_types:
        order.followup_type = followup_type
    else:
        order.followup_type = None
    update_fields.append('followup_type')

    # Handle assigned staff
    assigned_to_id = request.POST.get('followup_assigned_to', '').strip()
    if assigned_to_id:
        try:
            staff = User.objects.get(id=int(assigned_to_id), is_active=True)
            order.followup_assigned_to = staff
        except (User.DoesNotExist, ValueError):
            order.followup_assigned_to = None
    else:
        order.followup_assigned_to = None
    update_fields.append('followup_assigned_to')

    # When setting a new date, reset done status
    if order.next_followup_date:
        order.followup_done = False
        update_fields.append('followup_done')

    order.save(update_fields=update_fields)

    # Build response
    from datetime import date as date_cls
    urgency = 'not-set'
    if order.next_followup_date:
        delta = (order.next_followup_date - date_cls.today()).days
        if delta < 0:
            urgency = 'overdue'
        elif delta == 0:
            urgency = 'today'
        elif delta == 1:
            urgency = 'tomorrow'
        else:
            urgency = 'upcoming'

    return JsonResponse({
        'success': True,
        'next_followup_date': order.next_followup_date.strftime('%Y-%m-%d') if order.next_followup_date else None,
        'display_date': order.next_followup_date.strftime('%b %d, %Y') if order.next_followup_date else '',
        'followup_type': order.followup_type or '',
        'followup_type_display': dict(Order.FOLLOWUP_TYPE_CHOICES).get(order.followup_type, ''),
        'followup_assigned_to_id': order.followup_assigned_to_id or '',
        'followup_assigned_to_name': (
            order.followup_assigned_to.get_full_name() or order.followup_assigned_to.username
        ) if order.followup_assigned_to else '',
        'followup_done': order.followup_done,
        'urgency': urgency,
    })


@login_required
@permission_required('can_view_orders')
def order_invoice(request, order_id):
    order = get_object_or_404(Order, id=order_id, created_by=request.user)
    order_items = order.items.all()
    return render(request, "order_invoice.html", {
        "order": order,
        "order_items": order_items,
        "user": request.user,
    })


# API Endpoints for AJAX
@login_required
def api_get_customer(request, customer_id):
    """Get customer details via AJAX"""
    customer = get_object_or_404(Customer, id=customer_id)
    return JsonResponse({
        'name': customer.name,
        'email': customer.email,
        'phone': customer.phone,
        'address': customer.address,
        'city': customer.city,
        'state': customer.state,
        'postal_code': customer.postal_code or '',
    })


# SINGLE PRODUCT API (used by order edit/create modals)
@login_required
@require_http_methods(["GET"])
def api_get_product(request, product_id):
    """Return product details (non-variation) for POS modals"""
    try:
        user = request.user
        # Grant access if superuser/administrator, or if user has can_view_products
        # or can_create_orders permission (assigned from user create/edit page)
        has_access = (
            user.is_superuser
            or getattr(user, 'role', None) == 'administrator'
            or getattr(user, 'can_view_products', False)
            or getattr(user, 'can_create_orders', False)
        )
        if not has_access:
            return JsonResponse({'success': False, 'message': 'You do not have permission to view products.'}, status=403)

        product = get_object_or_404(Product, id=product_id, is_deleted=False)

        data = {
            'id': product.id,
            'name': product.name,
            'sku': product.sku if getattr(product, 'sku', None) else getattr(product, 'slug', ''),
            'price': str(product.price) if getattr(product, 'price', None) is not None else '0',
            'is_custom': bool(getattr(product, 'is_custom_product', False)),
            'category': {
                'id': product.category.id,
                'name': product.category.name
            } if getattr(product, 'category', None) else None,
            'description': product.description or '',
            'image': product.image.url if getattr(product, 'image', None) else None,
            'product_type': getattr(product, 'product_type', 'simple'),
            'stock': getattr(product, 'stock_quantity', getattr(product, 'stock', 0)),
        }

        return JsonResponse({'success': True, 'product': data})

    except Http404:
        return JsonResponse({'success': False, 'message': 'Product not found'}, status=404)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
    

@login_required
def api_search_orders(request):
    """API endpoint to search orders for the POS Search Orders modal"""
    from django.db.models import Q
    from decimal import Decimal
    
    query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    payment_filter = request.GET.get('payment', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 30))
    
    orders = Order.objects.filter(is_deleted=False).select_related(
        'customer', 'created_by', 'status_setup', 'payment_setup', 'payment_status_setup'
    ).prefetch_related('items').order_by('-created_at')
    
    if query:
        orders = orders.filter(
            Q(order_number__icontains=query) |
            Q(customer_name__icontains=query) |
            Q(customer_phone__icontains=query) |
            Q(customer_email__icontains=query) |
            Q(shipping_address__icontains=query) |
            Q(branch_city__icontains=query) |
            Q(notes__icontains=query) |
            Q(tracking_number__icontains=query) |
            Q(barcode__icontains=query)
        )
    
    if status_filter:
        orders = orders.filter(
            Q(order_status__iexact=status_filter) |
            Q(status_setup__name__iexact=status_filter.replace('_', ' '))
        )
    
    if payment_filter:
        orders = orders.filter(
            Q(payment_status__iexact=payment_filter) |
            Q(payment_status_setup__name__iexact=payment_filter.replace('_', ' '))
        )
    
    if date_from:
        try:
            from datetime import datetime
            dt_from = datetime.strptime(date_from, '%Y-%m-%d')
            orders = orders.filter(created_at__date__gte=dt_from.date())
        except ValueError:
            pass
    
    if date_to:
        try:
            from datetime import datetime
            dt_to = datetime.strptime(date_to, '%Y-%m-%d')
            orders = orders.filter(created_at__date__lte=dt_to.date())
        except ValueError:
            pass
    
    total_count = orders.count()
    start = (page - 1) * per_page
    end = start + per_page
    paginated_orders = orders[start:end]
    
    results = []
    for order in paginated_orders:
        items_list = []
        for item in order.items.all():
            items_list.append({
                'product_name': item.product_name or '',
                'variation_name': item.variation_name or '',
                'quantity': item.quantity,
                'price': str(item.price),
                'total': str(item.total),
            })
        
        status_display = order.order_status or 'N/A'
        if order.status_setup:
            status_display = order.status_setup.name
        
        payment_status_display = order.payment_status or 'N/A'
        if order.payment_status_setup:
            payment_status_display = order.payment_status_setup.name
        
        payment_method_display = order.payment_method or 'N/A'
        if order.payment_setup:
            payment_method_display = order.payment_setup.name
        
        created_by_name = ''
        if order.created_by:
            created_by_name = order.created_by.get_full_name() or order.created_by.username
        
        results.append({
            'id': order.id,
            'order_number': order.order_number,
            'customer_name': order.customer_name or '',
            'customer_phone': order.customer_phone or '',
            'customer_email': order.customer_email or '',
            'shipping_address': order.shipping_address or '',
            'branch_city': order.branch_city or '',
            'in_out': order.in_out or '',
            'status': status_display,
            'payment_status': payment_status_display,
            'payment_method': payment_method_display,
            'total_amount': str(order.total_amount or 0),
            'discount_amount': str(order.discount_amount or 0),
            'shipping_charge': str(order.shipping_charge or 0),
            'tracking_number': order.tracking_number or '',
            'notes': order.notes or '',
            'created_by': created_by_name,
            'created_at': order.created_at.strftime('%b %d, %Y %I:%M %p') if order.created_at else '',
            'items': items_list,
            'items_count': len(items_list),
            'items_summary': ', '.join([f"{i['product_name']} x{i['quantity']}" for i in items_list[:3]]),
        })
    
    return JsonResponse({
        'success': True,
        'orders': results,
        'total': total_count,
        'page': page,
        'per_page': per_page,
        'has_more': end < total_count,
    })


@login_required
def api_search_products(request):
    """
    Returns products for POS modal grid.
    Supports ?q= search. If q is empty, returns first 40 products.
    """
    try:
        q = (request.GET.get("q") or "").strip()
        user = request.user

        # Check permission using actual model fields set from user create/edit page.
        # Administrator/superuser always have access.
        # Other users need can_view_products OR can_create_orders to use the POS product picker.
        has_access = (
            user.is_superuser
            or getattr(user, 'role', None) == 'administrator'
            or getattr(user, 'can_view_products', False)
            or getattr(user, 'can_create_orders', False)
        )
        if not has_access:
            return JsonResponse({'success': False, 'error': 'You do not have permission to view products.', 'products': []}, status=403)

        qs = Product.objects.filter(is_active=True, is_deleted=False).prefetch_related(
            'bundle_components__component_product'
        ).order_by("name")

        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(barcode__icontains=q) | Q(slug__icontains=q))

        qs = qs[:40]

        data = []
        for p in qs:
            # For bundle products use available_stock (min of components), else own stock
            try:
                stock = p.available_stock if p.product_type == 'bundle' else (int(p.stock) if p.stock else 0)
            except (AttributeError, ValueError, TypeError):
                stock = 0

            # Get SKU - use barcode first, then slug as fallback
            try:
                sku = p.barcode if p.barcode else p.slug
            except (AttributeError, TypeError):
                sku = p.slug

            data.append({
                "id": p.id,
                "name": p.name,
                "price": str(p.price) if p.price else "0",
                "stock": stock,
                "product_type": p.product_type,
                "image": p.image.url if p.image else None,
                "sku": sku,
            })

        return JsonResponse({
            "success": True,
            "products": data,
            "count": len(data)
        })
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Error in api_search_products: {str(e)}")
        
        return JsonResponse({
            "success": False,
            "error": str(e),
            "products": []
        }, status=500)


# varialble product variations API
@login_required
@require_http_methods(["GET"])
def api_get_product_variations(request, product_id):
    """
    Returns variations for a variable product.
    JSON format matches your JS usage: data.variations[]
    """
    try:
        user = request.user
        # Check permission using actual model fields set from user create/edit page
        has_access = (
            user.is_superuser
            or getattr(user, 'role', None) == 'administrator'
            or getattr(user, 'can_view_products', False)
            or getattr(user, 'can_create_orders', False)
        )
        if not has_access:
            return JsonResponse({'success': False, 'message': 'You do not have permission to view products.', 'variations': []}, status=403)

        product = get_object_or_404(Product, id=product_id, is_deleted=False)

        # Check if variable product
        if product.product_type != "variable":
            return JsonResponse({
                "success": False,
                "message": "This product is not a variable product",
                "variations": []
            })

        # GET ALL ACTIVE VARIATIONS (ignore status field, only check is_active and stock)
        variations = (
            product.variations
            .filter(is_active=True)  # Only check is_active, ignore status
            .order_by("-stock", "sku")  # Show in-stock items first
        )

        if not variations.exists():
            return JsonResponse({
                "success": True,
                "variations": [],
                "total": 0,
                "message": "No active variations found for this product"
            })

        # Build response
        out = []
        for v in variations:
            # ONLY SHOW VARIATIONS WITH STOCK > 0
            if v.stock <= 0:
                continue
            
            # Get variation display name (prefer explicit variation_name, then name, then SKU)
            if hasattr(v, 'variation_name') and v.variation_name:
                variation_display = v.variation_name
            elif hasattr(v, 'name') and v.name:
                variation_display = v.name
            else:
                variation_display = v.sku
            
            out.append({
                "id": v.id,
                "sku": v.sku,
                "variation_name": variation_display,  # Added variation_name
                "price": str(v.price),
                "stock": v.stock,
                "is_active": v.is_active,
                "image": v.image.url if v.image else None,
                "category": {
                    "id": product.category.id,
                    "name": product.category.name
                } if getattr(product, 'category', None) else None,
            })

        return JsonResponse({
            "success": True,
            "variations": out,
            "total": len(out)
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        
        return JsonResponse({
            "success": False,
            "error": str(e),
            "variations": []
        }, status=500)


@login_required
@require_http_methods(["GET"])
def api_bestselling_products(request):
    """
    Returns best-selling products based on order items.
    Parameters:
    - limit: Number of products to return (default: 8)
    - days: Days back to consider for best-sellers (default: 30)
    """
    try:
        limit = int(request.GET.get("limit", 8))
        days = int(request.GET.get("days", 30))

        # Calculate date range
        from django.utils import timezone
        from datetime import timedelta

        days_ago = timezone.now() - timedelta(days=days)

        # Get best-selling products based on order items
        bestselling_products = (
            Product.objects
            .filter(is_active=True, is_deleted=False, orderitem__order__created_at__gte=days_ago)
            .annotate(total_sold=Count('orderitem'))
            .order_by('-total_sold')[:limit]
        )

        bestselling_list = list(bestselling_products)
        bestselling_ids = [p.id for p in bestselling_list]

        # Ensure bundle products are included
        bundle_products = list(
            Product.objects
            .filter(is_active=True, is_deleted=False, product_type='bundle')
            .exclude(id__in=bestselling_ids)
            .annotate(total_sold=Count(
                'orderitem',
                filter=Q(orderitem__order__created_at__gte=days_ago)
            ))
            .order_by('-total_sold')
        )
        for bp in bundle_products:
            if len(bestselling_list) < limit:
                bestselling_list.append(bp)
            else:
                # Replace the lowest-ranked non-bundle product
                for i in range(len(bestselling_list) - 1, -1, -1):
                    if bestselling_list[i].product_type != 'bundle':
                        bestselling_list[i] = bp
                        break

        # If not enough results, add popular products by stock/price
        if len(bestselling_list) < limit:
            remaining_count = limit - len(bestselling_list)
            existing_ids = [p.id for p in bestselling_list]

            additional_products = (
                Product.objects
                .filter(is_active=True, is_deleted=False)
                .exclude(id__in=existing_ids)
                .order_by('-stock')[:remaining_count]
            )

            bestselling_list = bestselling_list + list(additional_products)

        data = []
        for p in bestselling_list:
            # Get stock - use available_stock for bundle products
            try:
                if p.product_type == 'bundle':
                    stock = p.available_stock
                else:
                    stock = int(p.stock) if p.stock else 0
            except (AttributeError, ValueError, TypeError):
                stock = 0
            
            # Get SKU
            try:
                sku = p.sku if hasattr(p, 'sku') and p.sku else p.slug
            except AttributeError:
                sku = p.slug
            
            # Get total sold count
            try:
                total_sold = p.total_sold if hasattr(p, 'total_sold') else 0
            except:
                total_sold = 0

            data.append({
                "id": p.id,
                "name": p.name,
                "price": str(p.price) if p.price else "0",
                "stock": stock,
                "product_type": p.product_type,
                "image": p.image.url if p.image else None,
                "sku": sku,
                "total_sold": total_sold,
                "category": p.category.name if p.category else "Uncategorized"
            })
        
        return JsonResponse({
            "success": True,
            "products": data,
            "count": len(data)
        })
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        
        return JsonResponse({
            "success": False,
            "error": str(e),
            "products": []
        }, status=500)


@login_required
@permission_required('can_export_data')
@require_http_methods(["POST"])
def export_selected_orders_excel(request):
    """Export selected orders to Excel"""
    try:
        from django.db import connection
        import sys
        
        # Get selected order IDs from POST
        order_ids = request.POST.getlist('order_ids')
        
        
        if not order_ids:
            return HttpResponse("No orders selected", status=400)
        
        # Convert to integers
        try:
            order_ids = [int(id) for id in order_ids]
        except (ValueError, TypeError):
            return HttpResponse("Invalid order IDs", status=400)
        
        # Get orders - use same filter as orders_list view (all orders, not just user's)
        orders = Order.objects.filter(id__in=order_ids, is_deleted=False).order_by('-created_at')
        
        
        if not orders.exists():
            return HttpResponse("No orders found", status=404)
        
        # Create workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Selected Orders"
        
        # Define styles
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=10)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        center_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        
        # ============= ROW 1: ALL HEADERS =============
        headers = [
            'Order ID', 'Order Number', 'Order Date', 'Order Status', 'Payment Status', 
            'Payment Method', 'Customer Name', 'Phone Number', 'Email Address', 
            'Shipping Address', 'Branch/City', 'Landmark', 'IN/OUT',
            'Products (with Qty)', 'SKU', 'Quantities', 'Unit Price(s)', 'Total Price(s)',
            'Grand Total'
        ]
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_alignment
            cell.border = border
        
        # ✅ FIXED: Get order items using ORM instead of raw SQL
        from django.db.models import Prefetch
        order_items_qs = OrderItem.objects.filter(
            order_id__in=order_ids
        ).select_related('order').order_by('order_id', 'id')
        
        # Group items by order_id
        items_by_order = {}
        for item in order_items_qs:
            order_id = item.order_id
            if order_id not in items_by_order:
                items_by_order[order_id] = []
            items_by_order[order_id].append({
                'product_sku': item.product_sku,
                'product_name': item.product_name,
                'quantity': item.quantity,
                'price': item.price,
            })
        
        # ============= WRITE ALL ROWS - ONE ROW PER ORDER =============
        current_row = 2
        for order in orders:
            order_items = items_by_order.get(order.id, [])
            
            # Combine all product info into single fields
            if order_items:
                product_names = []
                product_skus = []
                quantities = []
                prices = []
                total_prices = []
                
                for item in order_items:
                    product_sku = item['product_sku'] or "N/A"
                    product_name = item['product_name'] or "N/A"
                    quantity = item['quantity'] or 0
                    price = float(item['price']) if item['price'] else 0.00
                    total_price = quantity * price
                    
                    product_names.append(f"{product_name} (Qty: {quantity})")
                    product_skus.append(product_sku)
                    quantities.append(str(quantity))
                    prices.append(f"रू {price:.2f}")
                    total_prices.append(f"रू {total_price:.2f}")
                
                # Combine with semicolon separator
                combined_products = "; ".join(product_names)
                combined_skus = "; ".join(product_skus)
                combined_quantities = ", ".join(quantities)
                combined_prices = ", ".join(prices)
                combined_total_prices = ", ".join(total_prices)
            else:
                combined_products = "N/A"
                combined_skus = "N/A"
                combined_quantities = ""
                combined_prices = ""
                combined_total_prices = ""
            
            # Single row per order with all products combined
            row_data = [
                order.id, order.order_number, order.created_at.strftime("%Y-%m-%d %H:%M"),
                order.order_status.capitalize(), order.payment_status.upper(),
                order.payment_method.upper(), order.customer_name or "N/A",
                order.customer_phone or "N/A", order.customer_email or "N/A",
                order.shipping_address or "N/A", order.branch_city or "N/A",
                order.landmark or "N/A", order.in_out.upper() if order.in_out else "IN",
                combined_products, combined_skus, combined_quantities, 
                combined_prices, combined_total_prices,
                float(order.total_amount)
            ]
            
            for col_num, value in enumerate(row_data, 1):
                cell = ws.cell(row=current_row, column=col_num)
                cell.value = value
                cell.border = border
                cell.alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)
                if col_num in [19, 20]:  # Price and Total columns
                    cell.alignment = center_alignment
                    cell.number_format = '"रू "#,##0.00'
            
            current_row += 1
        
        # Adjust column widths
        column_widths = [10, 15, 18, 12, 12, 12, 18, 15, 15, 20, 12, 15, 8, 30, 12, 12, 15, 15, 12]
        for col_num, width in enumerate(column_widths, 1):
            ws.column_dimensions[chr(64 + col_num)].width = width
        
        # Create response
        filename = f"Selected_Orders_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename={filename}'
        
        wb.save(response)
        return response
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return HttpResponse(f"Error exporting orders: {str(e)}", status=500)

@login_required
@permission_required('can_export_data')
@require_http_methods(["GET"])
def export_order_details(request, order_id):
    """Export specific order with item details to Excel - ALL IN ONE ROW"""
    try:
        order = get_object_or_404(Order, id=order_id, created_by=request.user)
        
        wb = Workbook()
        ws = wb.active
        ws.title = f"Order {order.order_number}"
        
        # Define styles
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=10)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        center_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        
        # ============= ROW 1: ALL HEADERS IN ONE ROW =============
        headers = [
            'Order ID', 'Order Number', 'Order Date', 'Order Status', 'Payment Status', 
            'Payment Method', 'Customer Name', 'Phone Number', 'Email Address', 
            'Shipping Address', 'Branch/City', 'Landmark', 'IN/OUT',  # UPDATED HEADERS
            'Product #', 'SKU', 'Product Name', 'Quantity', 'Unit Price', 'Total Price',
            'Grand Total'
        ]
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_alignment
            cell.border = border
        
        # ✅ FIXED: Get items using ORM instead of raw SQL
        items = order.items.all()
        
        # ============= ROWS 2+: ONE ROW PER ITEM (WITH ALL DATA) =============
        current_row = 2
        for idx, item in enumerate(items, 1):
            product_sku = item.product_sku or "N/A"
            product_name = item.product_name or "N/A"
            quantity = item.quantity or 0
            price = float(item.price) if item.price else 0.00
            total_price = quantity * price
            
            # All data in one row - UPDATED FIELDS
            row_data = [
                order.id,                                          # Order ID
                order.order_number,                               # Order Number
                order.created_at.strftime("%Y-%m-%d %H:%M"),     # Order Date
                order.order_status.capitalize(),                  # Order Status
                order.payment_status.upper(),                     # Payment Status
                order.payment_method.upper(),                     # Payment Method
                order.customer_name or "N/A",                     # Customer Name
                order.customer_phone or "N/A",                    # Phone Number
                order.customer_email or "N/A",                    # Email Address
                order.shipping_address or "N/A",                  # Shipping Address
                order.branch_city or "N/A",                       # UPDATED: Branch/City
                order.landmark or "N/A",                          # Landmark
                order.in_out.upper() if order.in_out else "IN",  # ADDED: IN/OUT
                idx,                                              # Product #
                product_sku,                                      # SKU
                product_name,                                     # Product Name
                quantity,                                         # Quantity
                price,                                            # Unit Price
                total_price,                                      # Total Price
                float(order.total_amount)                         # Grand Total
            ]
            
            for col_num, value in enumerate(row_data, 1):
                cell = ws.cell(row=current_row, column=col_num)
                cell.value = value
                cell.border = border
                cell.alignment = center_alignment
                
                # Format price columns - ADJUSTED COLUMN NUMBERS
                if col_num in [18, 19, 20]:  # Unit Price, Total Price, Grand Total (adjusted for new columns)
                    cell.number_format = '"रू "#,##0.00'
            
            current_row += 1
        
        # Adjust column widths - ADDED ONE MORE COLUMN FOR IN/OUT
        column_widths = [10, 15, 18, 12, 12, 12, 18, 15, 15, 20, 12, 15, 8, 8, 10, 20, 10, 12, 12, 12]
        for col_num, width in enumerate(column_widths, 1):
            ws.column_dimensions[chr(64 + col_num)].width = width
        
        # Create response
        filename = f"Order_{order.order_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return HttpResponse(f"Error exporting order: {str(e)}", status=500)

@login_required
@require_http_methods(["POST"])
def import_orders_excel(request):
    """
    Import orders from an Excel file.
    
    Required columns (red columns): Customer Name, Phone Number, Products (with Qty),
    Total Price(s), Branch/City, Shipping Address.
    
    Optional columns: Quantities, Order From, Staff Name / Created By.
    Ignored columns: Order Number, Order Date (order number is always auto-generated).
    """
    from openpyxl import load_workbook
    from accounts.models import CustomUser
    import re

    excel_file = request.FILES.get('excel_file')
    if not excel_file:
        messages.error(request, "No file selected. Please choose an Excel file.")
        return redirect('orders_list')

    # Validate file extension
    if not excel_file.name.endswith(('.xlsx', '.xls')):
        messages.error(request, "Invalid file format. Please upload an Excel file (.xlsx or .xls).")
        return redirect('orders_list')

    # Validate file size (max 10MB)
    if excel_file.size > 10 * 1024 * 1024:
        messages.error(request, "File too large. Maximum file size is 10MB.")
        return redirect('orders_list')

    try:
        wb = load_workbook(excel_file, read_only=True, data_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(min_row=1, values_only=True))
        if len(rows) < 2:
            messages.error(request, "The Excel file is empty or has no data rows (only header found).")
            return redirect('orders_list')

        # Parse header row - normalize to lowercase stripped, replace brackets
        raw_headers = rows[0]
        headers = []
        for h in raw_headers:
            if h:
                # Normalize: lowercase, strip, replace square brackets with parentheses
                normalized = str(h).strip().lower().replace('[', '(').replace(']', ')')
                headers.append(normalized)
            else:
                headers.append('')

        # Map expected columns to their indices
        # Keys use normalized format (parentheses, lowercase)
        COLUMN_MAP = {
            'customer name': None,
            'phone number': None,
            'products': None,          # matches "products (with qty)", "products [with qty]", "products"
            'quantities': None,
            'total price': None,       # matches "total price(s)", "total price"
            'branch/city': None,
            'shipping': None,          # matches "shipping", "shipping address"
            'order from': None,
            'staff name': None,
            'created by': None,
            'sku': None,
            'unit price': None,        # matches "unit price(s)", "unit price"
            'grand total': None,
            'email': None,             # matches "email", "email address"
            'landmark': None,
            'in/out': None,
            'order status': None,
            'payment status': None,
            'payment method': None,
        }

        for idx, header in enumerate(headers):
            if not header:
                continue
            for key in COLUMN_MAP:
                if COLUMN_MAP[key] is not None:
                    continue  # Already mapped
                if key in header or header in key:
                    COLUMN_MAP[key] = idx
                    break

        # Check required columns exist
        required_cols = ['customer name', 'phone number', 'branch/city', 'shipping']
        missing_cols = [col for col in required_cols if COLUMN_MAP.get(col) is None]

        # Need at least one price column: 'total price' or 'grand total'
        has_price_col = COLUMN_MAP.get('total price') is not None or COLUMN_MAP.get('grand total') is not None
        if not has_price_col:
            missing_cols.append('total price(s)')

        if missing_cols:
            messages.error(
                request,
                f"Missing required columns in Excel: {', '.join(c.title() for c in missing_cols)}. "
                f"Required columns: Customer Name, Phone Number, Products (with Qty), "
                f"Total Price(s), Branch/City, Shipping."
            )
            return redirect('orders_list')

        def get_cell(row, col_name):
            """Get cell value by column name, returns None if column not mapped"""
            idx = COLUMN_MAP.get(col_name)
            if idx is not None and idx < len(row):
                val = row[idx]
                if val is not None:
                    return str(val).strip()
            return None

        def parse_price(price_str):
            """Extract numeric value from price string like 'रू 1,234.56' or '1300'"""
            if not price_str:
                return Decimal('0')
            cleaned = re.sub(r'[^\d.]', '', str(price_str).replace(',', ''))
            try:
                return Decimal(cleaned) if cleaned else Decimal('0')
            except (InvalidOperation, ValueError):
                return Decimal('0')

        def parse_quantity_from_product(product_text):
            """
            Parse quantity from product text.
            Supports formats:
            - "Product Name (Qty: 2)" -> qty=2, name="Product Name"
            - "2pcs product name" -> qty=2, name="product name"
            - "3 pcs product name" -> qty=3, name="product name"
            - "product name" -> qty=1, name="product name"
            """
            if not product_text:
                return '', 1

            # Try "(Qty: N)" format first
            qty_match = re.search(r'\(Qty:\s*(\d+)\)', product_text)
            if qty_match:
                qty = int(qty_match.group(1))
                name = re.sub(r'\s*\(Qty:\s*\d+\)', '', product_text).strip()
                return name, qty

            # Try "Npcs" or "N pcs" prefix format
            pcs_match = re.match(r'^(\d+)\s*pcs?\s+(.+)$', product_text.strip(), re.IGNORECASE)
            if pcs_match:
                qty = int(pcs_match.group(1))
                name = pcs_match.group(2).strip()
                return name, qty

            return product_text.strip(), 1

        def find_staff_user(staff_name):
            """Look up a CustomUser by username, first_name, or last_name (case-insensitive)"""
            if not staff_name:
                return None
            name = staff_name.strip()
            if not name:
                return None
            # Try exact username match
            user = CustomUser.objects.filter(username__iexact=name, is_deleted=False, is_active=True).first()
            if user:
                return user
            # Try first_name match
            user = CustomUser.objects.filter(first_name__iexact=name, is_deleted=False, is_active=True).first()
            if user:
                return user
            # Try last_name match
            user = CustomUser.objects.filter(last_name__iexact=name, is_deleted=False, is_active=True).first()
            if user:
                return user
            return None

        # === Load default Setup records (same as order create page uses) ===
        from .models import Setup
        default_status_setup = Setup.objects.filter(setup_type='status', is_active=True, is_default=True).first()
        default_payment_status_setup = Setup.objects.filter(setup_type='payment_status', is_active=True, is_default=True).first()
        default_payment_setup = Setup.objects.filter(setup_type='payment', is_active=True, is_default=True).first()

        # Derive string defaults from Setup defaults (fallback to hardcoded if no default set)
        default_order_status = default_status_setup.name.lower().replace(' ', '_') if default_status_setup else 'processing'
        default_payment_status = default_payment_status_setup.name.lower().replace(' ', '_') if default_payment_status_setup else 'pending'
        default_payment_method = default_payment_setup.name.lower().replace(' ', '_') if default_payment_setup else ''

        success_count = 0
        error_rows = []
        skipped_duplicates = []

        data_rows = rows[1:]

        for row_num, row in enumerate(data_rows, start=2):
            # Skip completely empty rows
            if not row or all(cell is None or str(cell).strip() == '' for cell in row):
                continue

            try:
                # === Core required fields ===
                customer_name = get_cell(row, 'customer name') or ''
                customer_phone = get_cell(row, 'phone number') or ''
                shipping_address = get_cell(row, 'shipping') or ''
                branch_city = get_cell(row, 'branch/city') or ''

                # === Optional fields ===
                customer_email = get_cell(row, 'email') or ''
                landmark = get_cell(row, 'landmark') or ''
                in_out_raw = get_cell(row, 'in/out') or 'in'
                in_out = 'in' if in_out_raw.lower().strip() in ('in', 'valley', 'in valley') else 'out'

                # === Status fields: use Setup defaults if not provided in Excel ===
                order_status_raw = get_cell(row, 'order status') or ''
                payment_status_raw = get_cell(row, 'payment status') or ''
                payment_method_raw = get_cell(row, 'payment method') or ''

                # === Price: prefer 'grand total', fallback to 'total price(s)' ===
                grand_total_raw = get_cell(row, 'grand total')
                if not grand_total_raw or parse_price(grand_total_raw) <= 0:
                    grand_total_raw = get_cell(row, 'total price')
                grand_total = parse_price(grand_total_raw)

                # === Optional: Order From ===
                order_from_raw = get_cell(row, 'order from') or ''
                order_from_value = order_from_raw.strip().lower() if order_from_raw.strip() else ''

                # === Optional: Staff Name / Created By ===
                staff_name_raw = get_cell(row, 'staff name') or get_cell(row, 'created by') or ''
                staff_user = find_staff_user(staff_name_raw)
                if not staff_user and staff_name_raw.strip():
                    # Auto-create staff user if name provided in Excel but doesn't exist
                    staff_username = staff_name_raw.strip().lower().replace(' ', '_')
                    # Check if username already exists (may have been created earlier in this import)
                    staff_user = CustomUser.objects.filter(username=staff_username).first()
                    if not staff_user:
                        import uuid
                        staff_user = CustomUser.objects.create(
                            username=staff_username,
                            first_name=staff_name_raw.strip().title(),
                            email=f"{staff_username}_{uuid.uuid4().hex[:8]}@staff.local",
                            role='sales',
                            is_active=True,
                        )
                        staff_user.set_unusable_password()
                        staff_user.save()
                created_by_user = staff_user if staff_user else request.user

                # Validate required fields
                row_errors = []
                if not customer_name:
                    row_errors.append('Customer Name is empty')
                if not customer_phone:
                    row_errors.append('Phone Number is empty')
                if not shipping_address:
                    row_errors.append('Shipping is empty')
                if not branch_city:
                    row_errors.append('Branch/City is empty')
                if grand_total <= 0:
                    row_errors.append('Total Price must be greater than 0')

                if row_errors:
                    error_rows.append(f"Row {row_num}: {'; '.join(row_errors)}")
                    continue

                # Parse product items from combined fields
                products_raw = get_cell(row, 'products') or ''
                skus_raw = get_cell(row, 'sku') or ''
                quantities_raw = get_cell(row, 'quantities') or ''
                unit_prices_raw = get_cell(row, 'unit price') or ''

                # Parse semicolon/comma separated values
                product_entries = [p.strip() for p in products_raw.split(';') if p.strip()] if products_raw else []
                sku_entries = [s.strip() for s in skus_raw.split(';') if s.strip()] if skus_raw else []
                qty_entries = [q.strip() for q in quantities_raw.split(',') if q.strip()] if quantities_raw else []
                price_entries = [p.strip() for p in unit_prices_raw.split(',') if p.strip()] if unit_prices_raw else []

                with transaction.atomic():
                    # Resolve status/payment values: use Excel value if provided, otherwise Setup defaults
                    if order_status_raw.strip():
                        order_status = order_status_raw.lower().replace(' ', '_')
                        # Look up matching Setup record
                        row_status_setup = Setup.objects.filter(
                            setup_type='status', is_active=True,
                            name__iexact=order_status_raw.strip().replace('_', ' ')
                        ).first()
                    else:
                        order_status = default_order_status
                        row_status_setup = default_status_setup

                    if payment_status_raw.strip():
                        payment_status = payment_status_raw.lower().replace(' ', '_')
                        row_payment_status_setup = Setup.objects.filter(
                            setup_type='payment_status', is_active=True,
                            name__iexact=payment_status_raw.strip().replace('_', ' ')
                        ).first()
                    else:
                        payment_status = default_payment_status
                        row_payment_status_setup = default_payment_status_setup

                    if payment_method_raw.strip():
                        payment_method = payment_method_raw.lower().replace(' ', '_')
                        row_payment_setup = Setup.objects.filter(
                            setup_type='payment', is_active=True,
                            name__iexact=payment_method_raw.strip().replace('_', ' ')
                        ).first()
                    else:
                        payment_method = default_payment_method
                        row_payment_setup = default_payment_setup

                    # Get or create customer
                    customer, _ = Customer.objects.get_or_create(
                        phone=customer_phone,
                        defaults={
                            'name': customer_name,
                            'email': customer_email or None,
                            'city': branch_city,
                            'address': shipping_address,
                            'landmark': landmark,
                        }
                    )

                    # Get or create city
                    City.objects.get_or_create(
                        name=branch_city,
                        defaults={
                            'valley_status': 'valley' if in_out == 'in' else 'out_valley',
                            'is_active': True
                        }
                    )

                    # Generate unique order number (T001, T002, ...)
                    new_order_number = None
                    for attempt in range(100):
                        last_order = Order.objects.filter(
                            order_number__regex=r'^T\d+$'
                        ).extra(
                            select={'num': "CAST(SUBSTR(order_number, 2) AS INTEGER)"}
                        ).order_by('-num').first()

                        if last_order:
                            try:
                                n = int(last_order.order_number[1:])
                                new_order_number = f"T{n + 1:03d}"
                            except (ValueError, AttributeError, IndexError):
                                count = Order.objects.filter(order_number__regex=r'^T\d+$').count()
                                new_order_number = f"T{count + 1:03d}"
                        else:
                            new_order_number = "T001"

                        if not Order.objects.filter(order_number=new_order_number).exists():
                            break

                    # Create the order
                    order = Order.objects.create(
                        order_number=new_order_number,
                        created_by=created_by_user,
                        customer=customer,
                        customer_name=customer_name,
                        customer_phone=customer_phone,
                        customer_email=customer_email,
                        branch_city=branch_city,
                        in_out=in_out,
                        shipping_address=shipping_address,
                        landmark=landmark,
                        order_from=order_from_value,
                        order_status=order_status,
                        payment_status=payment_status,
                        payment_method=payment_method,
                        status_setup=row_status_setup,
                        payment_status_setup=row_payment_status_setup,
                        payment_setup=row_payment_setup,
                        total_amount=safe_decimal(grand_total, max_digits=18, decimal_places=2),
                        discount_amount=Decimal('0'),
                        shipping_charge=Decimal('0'),
                        tax_percent=Decimal('0'),
                    )

                    # Sync status with Setup model (creates any missing Setup records)
                    order = sync_order_status_setup(order)

                    # Create order items
                    if product_entries:
                        num_items = len(product_entries)
                        for i in range(num_items):
                            product_text = product_entries[i] if i < len(product_entries) else ''

                            # Parse product name and quantity from text
                            product_name, parsed_qty = parse_quantity_from_product(product_text)

                            # If qty was parsed from product name, use it; otherwise use quantities column
                            if parsed_qty > 1:
                                item_qty = parsed_qty
                            else:
                                try:
                                    item_qty = int(qty_entries[i]) if i < len(qty_entries) else 1
                                except (ValueError, IndexError):
                                    item_qty = 1
                                # Final fallback: ensure at least 1
                                if item_qty < 1:
                                    item_qty = 1

                            item_sku = sku_entries[i] if i < len(sku_entries) else ''

                            # Calculate item price
                            if i < len(price_entries):
                                item_price = parse_price(price_entries[i])
                                item_total = item_price * item_qty
                            elif num_items == 1:
                                # Single product: total price = grand total
                                item_total = grand_total
                                item_price = grand_total / item_qty if item_qty > 0 else grand_total
                            else:
                                # Multiple products without individual prices: distribute evenly
                                item_total = grand_total / num_items
                                item_price = item_total / item_qty if item_qty > 0 else item_total

                            # Try to find product by SKU
                            product_obj = None
                            variation_obj = None
                            variation_name = None

                            if item_sku and item_sku != 'N/A':
                                variation_obj = ProductVariation.objects.filter(sku=item_sku).first()
                                if variation_obj:
                                    product_obj = variation_obj.product
                                    variation_name = getattr(variation_obj, 'variation_name', None) or variation_obj.sku
                                else:
                                    product_obj = Product.objects.filter(barcode=item_sku).first()

                            if not product_obj and product_name:
                                product_obj = Product.objects.filter(name__iexact=product_name).first()

                            OrderItem.objects.create(
                                order=order,
                                product=product_obj,
                                product_variation=variation_obj,
                                product_name=product_name or 'Imported Product',
                                product_sku=item_sku if item_sku != 'N/A' else '',
                                variation_name=variation_name,
                                quantity=item_qty,
                                price=safe_decimal(item_price, max_digits=18, decimal_places=2),
                                total=safe_decimal(item_total, max_digits=18, decimal_places=2),
                            )
                    else:
                        # No product info - create a placeholder item
                        OrderItem.objects.create(
                            order=order,
                            product=None,
                            product_name='Imported Item',
                            product_sku='',
                            quantity=1,
                            price=safe_decimal(grand_total, max_digits=18, decimal_places=2),
                            total=safe_decimal(grand_total, max_digits=18, decimal_places=2),
                        )

                    # Log activity
                    OrderActivityLog.objects.create(
                        order=order,
                        action_type='created',
                        user=request.user,
                        description=f"Order imported from Excel file: {excel_file.name}"
                    )

                    success_count += 1

            except Exception as e:
                error_rows.append(f"Row {row_num}: {str(e)}")
                continue

        wb.close()

        # Build result message
        if success_count > 0:
            messages.success(request, f"Successfully imported {success_count} order(s) from Excel.")

        if skipped_duplicates:
            messages.warning(
                request,
                f"Skipped {len(skipped_duplicates)} duplicate order(s): {'; '.join(skipped_duplicates[:5])}"
                + (f" and {len(skipped_duplicates) - 5} more..." if len(skipped_duplicates) > 5 else "")
            )

        if error_rows:
            messages.error(
                request,
                f"Failed to import {len(error_rows)} row(s): {'; '.join(error_rows[:5])}"
                + (f" and {len(error_rows) - 5} more..." if len(error_rows) > 5 else "")
            )

        if success_count == 0 and not error_rows and not skipped_duplicates:
            messages.warning(request, "No orders found in the Excel file.")

    except Exception as e:
        logger.error(f"Excel import error: {str(e)}")
        messages.error(request, f"Error reading Excel file: {str(e)}")

    return redirect('orders_list')


# new

@login_required
@require_POST
def product_gallery_upload(request, product_id):
    product = get_object_or_404(Product, pk=product_id, user=request.user)

    files = request.FILES.getlist("images")
    if not files:
        messages.error(request, "No files selected.")
        return redirect("product_detail", product_id=product_id)

    for f in files:
        ProductImage.objects.create(product=product, image=f)

    messages.success(request, f"{len(files)} image(s) uploaded.")
    return redirect("product_detail", product_id=product_id)


@login_required
@require_POST
def delete_product_image(request, image_id):
    img = get_object_or_404(ProductImage, pk=image_id)
    product_id = img.product_id

    if img.product.user != request.user:
        messages.error(request, "Permission denied.")
        return redirect("product_detail", product_id=product_id)

    img.delete()
    messages.success(request, "Image deleted.")
    return redirect("product_detail", product_id=product_id)


@login_required
@require_POST
@permission_required('can_create_products')
def variation_create(request, product_id):
    product = get_object_or_404(Product, pk=product_id, user=request.user)

    variation_name = request.POST.get("variation_name", "").strip()
    sku = request.POST.get("sku", "").strip()
    price = request.POST.get("price", "").strip()
    stock = request.POST.get("stock", "").strip()
    status = request.POST.get("status", "active").strip()

    if not variation_name or not sku or price == "" or stock == "":
        messages.error(request, "Variation name, SKU, price and stock are required.")
        return redirect("product_detail", product_id=product_id)

    try:
        price_val = float(price)
        stock_val = int(stock)
    except ValueError:
        messages.error(request, "Invalid price/stock.")
        return redirect("product_detail", product_id=product_id)

    variation = ProductVariation.objects.create(
        product=product,
        variation_name=variation_name,
        sku=sku,
        price=price_val,
        stock=stock_val,
        status=status,
        image=request.FILES.get("image") if "image" in request.FILES else None,
    )

    messages.success(request, f'Variation "{variation.variation_name}" created.')
    return redirect("product_detail", product_id=product_id)


@login_required
@require_POST
@permission_required('can_edit_products')
def variation_update(request, variation_id):
    variation = get_object_or_404(ProductVariation, pk=variation_id)

    if variation.product.user != request.user:
        messages.error(request, "Permission denied.")
        return redirect("product_detail", product_id=variation.product_id)

    variation_name = request.POST.get("variation_name", "").strip()
    sku = request.POST.get("sku", "").strip()
    price = request.POST.get("price", "").strip()
    stock = request.POST.get("stock", "").strip()
    status = request.POST.get("status", "active").strip()

    if not variation_name or not sku or price == "" or stock == "":
        messages.error(request, "Variation name, SKU, price and stock are required.")
        return redirect("product_detail", product_id=variation.product_id)

    try:
        variation.price = float(price)
        variation.stock = int(stock)
    except ValueError:
        messages.error(request, "Invalid price/stock.")
        return redirect("product_detail", product_id=variation.product_id)

    variation.variation_name = variation_name
    variation.sku = sku
    variation.status = status

    if "image" in request.FILES:
        variation.image = request.FILES["image"]

    variation.save()
    messages.success(request, f'Variation "{variation.sku}" updated.')
    return redirect("product_detail", product_id=variation.product_id)


@login_required
@require_POST
def variation_delete(request, variation_id):
    variation = get_object_or_404(ProductVariation, pk=variation_id)
    product_id = variation.product_id

    if variation.product.user != request.user:
        messages.error(request, "Permission denied.")
        return redirect("product_detail", product_id=product_id)

    sku = variation.sku
    variation.delete()
    messages.success(request, f'Variation "{sku}" deleted.')
    return redirect("product_detail", product_id=product_id)

@login_required
@permission_required('can_delete_orders')
def orders_bulk_action(request):
    """Handle bulk actions on orders including NCM bulk sending"""
    if request.method == 'POST':
        order_ids = request.POST.getlist('order_ids')
        action = request.POST.get('bulk_action')
        redirect_to = request.POST.get('redirect_to', 'orders_list')

        if not order_ids:
            messages.error(request, 'No orders selected!')
            return redirect(redirect_to)
        
        try:
            # Removed user filter - show all orders
            orders = Order.objects.filter(
                id__in=order_ids, 
                is_deleted=False
            )
            count = orders.count()
            
            if count == 0:
                messages.error(request, "No valid orders found!")
                return redirect(redirect_to)
            
            # NEW: HANDLE SEND TO NCM ACTION
            if action == 'send_to_ncm':
                return orders_bulk_ncm_send(request, orders)
            
            elif action == 'delete':
                # SOFT DELETE - Move to trash instead of permanent delete
                # Only restore stock for dispatched orders
                for order in orders:
                    if order.order_status == 'dispatched':
                        for item in order.items.all():
                            if item.product_variation:
                                item.product_variation.stock += item.quantity
                                if item.product_variation.stock > 0:
                                    item.product_variation.status = 'active'
                                item.product_variation.save()
                            elif item.product:
                                item.product.stock += item.quantity
                                if item.product.stock > 0:
                                    item.product.stock_status = 'in_stock'
                                item.product.save()
                    
                    # Log activity
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user,
                        action_type='deleted',
                        description=f'Order moved to trash by {request.user.username}'
                    )
                
                # Soft delete instead of permanent delete
                orders.update(is_deleted=True, deleted_at=timezone.now())
                messages.success(request, f'✅ {count} order(s) moved to trash successfully!')
            
            # ✅ DYNAMIC ORDER STATUS UPDATE
            elif action.startswith('status_setup_'):
                try:
                    setup_id = int(action.split('_')[-1])
                    status_setup = Setup.objects.get(id=setup_id, setup_type='status')
                    
                    # Update orders with the selected status
                    for order in orders:
                        order.status_setup = status_setup
                        order.order_status = status_setup.name
                        order.save()
                        
                        # Log activity
                        OrderActivityLog.objects.create(
                            order=order,
                            user=request.user,
                            action_type='status_changed',
                            description=f'Order status changed to {status_setup.name} by {request.user.username}'
                        )
                    
                    messages.success(request, f'✅ {count} order(s) marked as {status_setup.name}!')
                except (ValueError, Setup.DoesNotExist):
                    messages.error(request, 'Invalid status selected!')
            
            # ✅ DYNAMIC PAYMENT STATUS UPDATE
            elif action.startswith('payment_status_setup_'):
                try:
                    setup_id = int(action.split('_')[-1])
                    payment_setup = Setup.objects.get(id=setup_id, setup_type='payment_status')
                    
                    # Update orders with the selected payment status
                    for order in orders:
                        order.payment_status_setup = payment_setup
                        order.payment_status = payment_setup.name
                        order.save()
                        
                        # Log activity
                        OrderActivityLog.objects.create(
                            order=order,
                            user=request.user,
                            action_type='payment_changed',
                            description=f'Payment status changed to {payment_setup.name} by {request.user.username}'
                        )
                    
                    messages.success(request, f'✅ {count} order(s) marked as {payment_setup.name}!')
                except (ValueError, Setup.DoesNotExist):
                    messages.error(request, 'Invalid payment status selected!')
            
            # ✅ LEGACY: Keep backward compatibility with old action names
            elif action == 'mark_delivered':
                orders.update(order_status='delivered')
                
                # Log activity for each order
                for order in orders:
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user,
                        action_type='status_changed',
                        description=f'Order status changed to Delivered by {request.user.username}'
                    )
                
                messages.success(request, f'✅ {count} order(s) marked as delivered!')
                
            elif action == 'mark_cancelled':
                orders.update(order_status='cancelled')
                
                # Log activity for each order
                for order in orders:
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user,
                        action_type='status_changed',
                        description=f'Order status changed to Cancelled by {request.user.username}'
                    )
                
                messages.success(request, f'✅ {count} order(s) marked as cancelled!')
                
            elif action == 'mark_processing':
                orders.update(order_status='processing')
                
                # Log activity for each order
                for order in orders:
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user,
                        action_type='status_changed',
                        description=f'Order status changed to Processing by {request.user.username}'
                    )
                
                messages.success(request, f'✅ {count} order(s) marked as processing!')
            
            elif action == 'mark_shipped':
                orders.update(order_status='shipped')
                
                # Log activity for each order
                for order in orders:
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user,
                        action_type='status_changed',
                        description=f'Order status changed to Shipped by {request.user.username}'
                    )
                
                messages.success(request, f'✅ {count} order(s) marked as shipped!')
            
            elif action == 'mark_paid':
                orders.update(payment_status='paid')
                
                # Log activity for each order
                for order in orders:
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user,
                        action_type='payment_changed',
                        description=f'Payment status changed to Paid by {request.user.username}'
                    )
                
                messages.success(request, f'✅ {count} order(s) marked as paid!')
            
            elif action == 'mark_pending':
                orders.update(payment_status='pending')
                
                # Log activity for each order
                for order in orders:
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user,
                        action_type='payment_changed',
                        description=f'Payment status changed to Pending by {request.user.username}'
                    )
                
                messages.success(request, f'✅ {count} order(s) marked as pending payment!')
                
            else:
                messages.error(request, 'Invalid action selected!')
                
        except Exception as e:
            messages.error(request, f'Error performing bulk action: {str(e)}')

    return redirect(request.POST.get('redirect_to', 'orders_list') if request.method == 'POST' else 'orders_list')


@login_required
@permission_required('can_delete_orders')
def orders_bulk_ncm_send(request):
    """
    Handle Bulk Sending to NCM Logistics
    """
    from ncm.models import NCMBulkLog, NCMBulkLogOrder, NCMBulkLogDetail

    if request.method != 'POST':
        messages.error(request, '❌ Invalid request method')
        return redirect('orders_list')

    # 1. Get Form Data
    order_ids = request.POST.getlist('order_ids')
    from_branch = request.POST.get('from_branch', 'TINKUNE')
    delivery_type = request.POST.get('delivery_type', 'Door2Door')

    # Handle auto_set_logistics checkbox
    auto_set_logistics = request.POST.get('auto_set_logistics') == 'on'

    # Handle weight (default to 1.0 if invalid)
    try:
        default_weight = float(request.POST.get('default_weight', '1.0'))
    except (ValueError, TypeError):
        default_weight = 1.0

    if not order_ids:
        messages.error(request, '❌ No orders selected for NCM dispatch!')
        return redirect('orders_list')

    # 2. Get Orders
    orders = Order.objects.filter(id__in=order_ids, is_deleted=False)
    count = orders.count()

    if count == 0:
        messages.error(request, '❌ No valid active orders found matching selection.')
        return redirect('orders_list')

    # 3. Auto-set Logistics Field (if checked)
    if auto_set_logistics:
        orders.update(logistics='ncm')

    # Create NCM Bulk Log
    bulk_log = NCMBulkLog.objects.create(
        batch_number=NCMBulkLog.generate_batch_number(),
        total_orders=count,
        status='processing',
        from_branch=from_branch,
        delivery_type=delivery_type,
        created_by=request.user,
    )
    NCMBulkLogDetail.objects.create(
        batch=bulk_log,
        action='batch_started',
        message=f'Bulk send started with {count} order(s) from {from_branch}',
        user=request.user,
    )

    # 4. Processing Variables
    success_count = 0
    skip_count = 0
    error_count = 0
    error_details = []

    # 5. Iterate and Send
    for order in orders:
        result = send_single_order_to_ncm(
            request=request,
            order=order,
            from_branch=from_branch,
            delivery_type=delivery_type,
            default_weight=default_weight
        )

        if result['status'] == 'success':
            success_count += 1
            log_status = 'success'
            log_action = 'order_sent'
        elif result['status'] == 'skipped':
            skip_count += 1
            log_status = 'skipped'
            log_action = 'order_skipped'
        else:
            error_count += 1
            log_status = 'failed'
            log_action = 'order_failed'
            if len(error_details) < 3:
                error_details.append(f"{order.order_number}: {result['message']}")

        # Create log entry for this order
        order.refresh_from_db()
        NCMBulkLogOrder.objects.create(
            batch=bulk_log,
            order=order,
            order_number=order.order_number or '',
            customer_name=order.customer_name or '',
            customer_phone=order.customer_phone or '',
            shipping_address=order.shipping_address or '',
            cod_amount=order.total_amount or 0,
            destination_branch=order.branch_city or '',
            ncm_order_id=order.ncm_order_id,
            status=log_status,
            message=result.get('message', ''),
        )
        NCMBulkLogDetail.objects.create(
            batch=bulk_log,
            action=log_action,
            order_number=order.order_number or '',
            message=result.get('message', ''),
            user=request.user,
        )

    # Update bulk log with final counts and status
    if error_count == count:
        final_status = 'failed'
    elif success_count == count:
        final_status = 'completed'
    elif success_count > 0:
        final_status = 'partial'
    else:
        final_status = 'failed'

    bulk_log.success_count = success_count
    bulk_log.failed_count = error_count
    bulk_log.skipped_count = skip_count
    bulk_log.status = final_status
    bulk_log.completed_at = timezone.now()
    bulk_log.save()

    NCMBulkLogDetail.objects.create(
        batch=bulk_log,
        action='batch_completed',
        message=f'Batch completed: {success_count} success, {error_count} failed, {skip_count} skipped',
        user=request.user,
    )

    # 6. Final Feedback
    if success_count > 0:
        messages.success(request, f'✅ Successfully sent {success_count} order(s) to NCM.')

    if skip_count > 0:
        messages.warning(request, f'⚠️ Skipped {skip_count} order(s) (Already sent or missing info).')

    if error_count > 0:
        messages.error(request, f'❌ Failed to send {error_count} order(s).')
        # Show specific API errors
        for err in error_details:
            messages.error(request, f"Error: {err}")

    return redirect('orders_list')

def send_single_order_to_ncm(request, order, from_branch='TINKUNE', delivery_type='Door2Door', default_weight=1.0):
    """
    Helper function to send a single order to NCM API.
    ✅ FIXED: Improved bulk send support with robust vendor ID handling
    """
    try:
        # Validate order has required fields
        if not order.order_number:
            return {'status': 'error', 'message': f'Order {order.id} has no order_number'}
        
        if not order.customer_name or not order.customer_phone or not order.shipping_address:
            return {'status': 'error', 'message': f'Order {order.order_number} missing required customer info'}
        
        # Check if already has NCM ID
        if order.ncm_order_id:
            return {'status': 'skipped', 'message': 'Already has NCM ID'}

        # 1. Get API Credentials from Settings
        # Ensure NCM_API_BASE_URL does NOT have a trailing slash
        base_url = getattr(settings, 'NCM_API_BASE_URL', '').rstrip('/')
        api_key = getattr(settings, 'NCM_API_KEY', '')

        if not base_url or not api_key:
            return {'status': 'error', 'message': 'NCM configuration missing in settings.py'}

        # Construct Endpoint (use /order/create not /ordercreate)
        api_url = f"{base_url}/order/create"

        # 2. Prepare Data
        # Build package description with variant names
        product_name = "General Item"
        try:
            items = order.items.select_related('product_variation').all()[:3]
            if items:
                parts = []
                for item in items:
                    qty = getattr(item, 'quantity', 1) or 1
                    name = item.product_name or 'Item'
                    var_name = item.variation_name or (item.product_variation.variation_name if item.product_variation else None)
                    if var_name:
                        name = f"{name} ({var_name})"
                    parts.append(f"{qty}x {name}")
                product_name = ', '.join(parts)
                total_items = order.items.count()
                if total_items > 3:
                    product_name += f' and {total_items - 3} more'
        except Exception:
            pass

        # Calculate Weight
        weight = default_weight
        # If your order model has a weight field, use it
        if hasattr(order, 'package_weight') and order.package_weight:
             weight = float(order.package_weight)

        # Clean phone number (remove non-digits)
        phone = ''.join(filter(str.isdigit, str(order.customer_phone or "")))
        
        # FIXED: Generate Vendor Reference ID - Use order ID directly
        # NCM requires vrefid field to be populated with order reference
        vendor_ref_id = str(order.id)  # Start with order ID (guaranteed to exist)
        
        # Try to use order_number if available (better for tracking)
        if order.order_number:
            order_num = str(order.order_number).strip()
            if order_num:
                vendor_ref_id = order_num
        
        # If vendor_id exists from creator, use it as main reference
        try:
            if order.created_by and hasattr(order.created_by, 'vendor_id') and order.created_by.vendor_id:
                vendor_id_str = str(order.created_by.vendor_id).strip()
                if vendor_id_str:
                    vendor_ref_id = vendor_id_str
        except:
            pass
        
        # Ensure vendor_ref_id is always set and valid
        if not vendor_ref_id or vendor_ref_id.strip() == "":
            vendor_ref_id = str(order.id)
        
        vendor_ref_id = vendor_ref_id.strip()
        
        payload = {
            "name": str(order.customer_name or "").strip(),
            "phone": phone,
            "phone2": "", # Optional
            "cod_charge": float(order.total_amount or 0),
            "address": str(order.shipping_address or "").strip(),
            "fbranch": from_branch,
            "branch": str(order.branch_city or "KATHMANDU").upper(),
            "package": str(product_name)[:100], # Limit length
            "vref_id": vendor_ref_id,
            "instruction": str(order.notes or "")[:100],
            "deliverytype": delivery_type,
            "weight": weight
        }

        # 3. Send Request
        headers = {
            'Authorization': f'Token {api_key}',
            'Content-Type': 'application/json'
        }

        response = requests.post(api_url, json=payload, headers=headers, timeout=15)

        # 4. Handle Response
        if response.status_code == 200:
            resp_data = response.json()
            
            # Check NCM specific success message
            if resp_data.get('Message') == 'Order Successfully Created':
                # Save NCM ID to Order
                ncm_id = resp_data.get('orderid')
                if ncm_id:
                    order.ncm_order_id = int(ncm_id)
                order.logistics = 'ncm' # Ensure logistics is set
                order.save()

                # ✅ FETCH DELIVERY CHARGE FROM NCM API
                try:
                    # Import ncm_service here to avoid circular imports
                    from services.ncm_service import NCMService
                    ncm_service = NCMService()
                    details_result = ncm_service.get_order_details(order.ncm_order_id)
                    
                    logger.info(f"NCM API response details: {details_result}")
                    
                    if details_result.get('success'):
                        details_data = details_result.get('data', {})
                        logger.info(f"Extracted data from NCM response: {details_data}")
                        
                        # Extract delivery_charge from NCM response - try multiple field names
                        delivery_charge = (details_data.get('chargeDetail') or 
                                         details_data.get('deliveryCharge') or 
                                         details_data.get('deliverycharge') or 
                                         details_data.get('delivery_charge') or 
                                         details_data.get('chargedetail') or 
                                         details_data.get('shippingCharge') or 
                                         details_data.get('shipping_charge') or 
                                         details_data.get('charge') or 
                                         details_data.get('amount') or
                                         0)
                        
                        logger.info(f"Extracted delivery_charge: {delivery_charge} from data keys: {list(details_data.keys())}")
                        
                        if delivery_charge and float(delivery_charge) > 0:
                            order.delivery_charge = Decimal(str(delivery_charge))
                            order.save(update_fields=['delivery_charge'])
                            logger.info(f"✅ Fetched and saved delivery charge: {delivery_charge} for NCM order {order.ncm_order_id}")
                        else:
                            logger.warning(f"⚠️ No delivery charge found in NCM response for order {order.ncm_order_id}. Response data: {details_data}")
                    else:
                        logger.warning(f"⚠️ Failed to fetch order details from NCM: {details_result.get('error', 'Unknown error')}")
                except Exception as e:
                    logger.error(f"Error fetching delivery charge from NCM: {str(e)}", exc_info=True)

                # Log Activity
                OrderActivityLog.objects.create(
                    order=order,
                    user=request.user,
                    action_type='updated',
                    description=f"Sent to NCM. NCM ID: {order.ncm_order_id}, Vendor Ref: {vendor_ref_id}"
                )
                return {'status': 'success', 'message': 'Sent successfully'}
            else:
                # API returned 200 but with an internal error message
                return {'status': 'error', 'message': str(resp_data)}
        
        elif response.status_code == 404:
            # 404 means the URL is wrong OR the Resource ID is wrong. 
            # Since we are creating, it's likely the URL.
            return {'status': 'error', 'message': f'API Endpoint 404. Checked URL: {api_url}'}
            
        else:
            return {'status': 'error', 'message': f'HTTP Error {response.status_code}: {response.text}'}

    except Exception as e:
        return {'status': 'error', 'message': str(e)}
# ==================== DISPATCH MANAGEMENT VIEWS ====================

@login_required
@permission_required('can_view_dispatch')
def dispatch_management(request):
    """Main dispatch scanning page"""
    if request.method == 'POST':
        order_ids_str = request.POST.get('order_ids', '').strip()
        set_status = request.POST.get('set_status')
        logistics = request.POST.get('logistics')
        
        if not order_ids_str:
            messages.error(request, 'Please scan at least one order ID.')
            return redirect('dispatch_management')
        
        if not set_status or not logistics:
            messages.error(request, 'Please select both status and logistics.')
            return redirect('dispatch_management')
        
        # Parse order IDs (comma-separated or newline-separated)
        order_ids_raw = order_ids_str.replace('\n', ',').replace('\r', '').split(',')
        order_ids = [oid.strip() for oid in order_ids_raw if oid.strip()]
        
        if not order_ids:
            messages.error(request, 'No valid order IDs found.')
            return redirect('dispatch_management')
        
        # Check for duplicates
        if len(order_ids) != len(set(order_ids)):
            messages.error(request, 'Duplicate order IDs detected. Please remove duplicates.')
            return redirect('dispatch_management')
        
        try:
            with transaction.atomic():
                # Generate batch number
                batch_number = f"DISPATCH-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                
                # Create dispatch
                dispatch = Dispatch.objects.create(
                    batch_number=batch_number,
                    logistics=logistics,
                    status=set_status,
                    total_orders=len(order_ids),
                    created_by=request.user
                )
                
                # Create dispatch items and update orders
                updated_count = 0
                not_found = []
                stock_warnings = []
                stock_deductions = []  # detailed per-item deduction records
                
                for order_id in order_ids:
                    # Create dispatch item
                    DispatchItem.objects.create(
                        dispatch=dispatch,
                        scanned_order_id=order_id
                    )
                    
                    # Try to find and update order by order_number or barcode
                    order = Order.objects.filter(
                        Q(order_number=order_id) | Q(barcode=order_id)
                    ).first()
                    
                    if order:
                        # Always link the dispatch item to the order (even if already dispatched)
                        DispatchItem.objects.filter(
                            dispatch=dispatch,
                            scanned_order_id=order_id
                        ).update(order=order)

                        # Check if already dispatched
                        if order.order_status == 'dispatched':
                            messages.warning(request, f'⚠️ Order {order_id} already dispatched')
                            continue
                        
                        # ✅ STOCK DEDUCTION: Reduce stock when status is "dispatched"
                        if set_status == 'dispatched':
                            order_items = order.items.select_related(
                                'product', 'product_variation'
                            ).select_for_update()  # Lock rows to prevent race conditions

                            for item in order_items:
                                product = item.product
                                variation = item.product_variation
                                quantity = item.quantity

                                if variation:
                                    # ── Variation stock ──────────────────────────────
                                    old_stock = variation.stock
                                    oversold = variation.stock < quantity
                                    if not oversold:
                                        variation.stock -= quantity
                                    else:
                                        warn_msg = (
                                            f"⚠️ {variation.sku}: Need {quantity}, "
                                            f"Available {variation.stock} (oversold)"
                                        )
                                        stock_warnings.append(warn_msg)
                                        variation.stock = max(0, variation.stock - quantity)

                                    # Update variation status based on its own threshold
                                    threshold = variation.low_stock_threshold or 0
                                    if variation.stock == 0:
                                        variation.status = 'out_of_stock'
                                    elif threshold > 0 and variation.stock <= threshold:
                                        variation.status = 'inactive'  # low-stock flag for variations
                                    variation.save()

                                    # Record deduction detail
                                    product_name = product.name if product else 'Unknown'
                                    stock_deductions.append({
                                        'order_id': order_id,
                                        'name': product_name,
                                        'sku': variation.sku,
                                        'type': 'variation',
                                        'qty': quantity,
                                        'old_stock': old_stock,
                                        'new_stock': variation.stock,
                                        'oversold': oversold,
                                    })

                                    # ── Recalculate parent product stock_status ───────
                                    if product:
                                        total_var_stock = product.variations.aggregate(
                                            total=Sum('stock')
                                        )['total'] or 0
                                        p_threshold = product.low_stock_threshold or 0
                                        if total_var_stock == 0:
                                            product.stock_status = 'out_of_stock'
                                        elif p_threshold > 0 and total_var_stock <= p_threshold:
                                            product.stock_status = 'low_stock'
                                        else:
                                            product.stock_status = 'in_stock'
                                        product.save(update_fields=['stock_status'])

                                else:
                                    if product:
                                        if product.is_bundle:
                                            # ── Bundle product stock ─────────────────────────
                                            components = product.bundle_components.select_related('component_product').all()
                                            for comp in components:
                                                comp_product = comp.component_product
                                                required = comp.quantity_required * quantity
                                                old_stock = comp_product.stock
                                                oversold = comp_product.stock < required
                                                if not oversold:
                                                    comp_product.stock -= required
                                                else:
                                                    warn_msg = (
                                                        f"⚠️ {comp_product.name} (bundle component of {product.name}): "
                                                        f"Need {required}, Available {comp_product.stock} (oversold)"
                                                    )
                                                    stock_warnings.append(warn_msg)
                                                    comp_product.stock = max(0, comp_product.stock - required)

                                                # Update component stock_status
                                                threshold = comp_product.low_stock_threshold or 0
                                                if comp_product.stock == 0:
                                                    comp_product.stock_status = 'out_of_stock'
                                                elif threshold > 0 and comp_product.stock <= threshold:
                                                    comp_product.stock_status = 'low_stock'
                                                elif comp_product.stock <= 10:
                                                    comp_product.stock_status = 'low_stock'
                                                else:
                                                    comp_product.stock_status = 'in_stock'
                                                comp_product.save(update_fields=['stock', 'stock_status'])

                                                # Record deduction detail for each component
                                                stock_deductions.append({
                                                    'order_id': order_id,
                                                    'name': f"{comp_product.name} (component of {product.name})",
                                                    'sku': comp_product.barcode or str(comp_product.id),
                                                    'type': 'bundle_component',
                                                    'qty': required,
                                                    'old_stock': old_stock,
                                                    'new_stock': comp_product.stock,
                                                    'oversold': oversold,
                                                })
                                        else:
                                            # ── Simple product stock ──────────────────────────
                                            old_stock = product.stock
                                            oversold = product.stock < quantity
                                            if not oversold:
                                                product.stock -= quantity
                                            else:
                                                warn_msg = (
                                                    f"⚠️ {product.name}: Need {quantity}, "
                                                    f"Available {product.stock} (oversold)"
                                                )
                                                stock_warnings.append(warn_msg)
                                                product.stock = max(0, product.stock - quantity)

                                            # Update stock_status using configured threshold
                                            threshold = product.low_stock_threshold or 0
                                            if product.stock == 0:
                                                product.stock_status = 'out_of_stock'
                                            elif threshold > 0 and product.stock <= threshold:
                                                product.stock_status = 'low_stock'
                                            elif product.stock <= 10:
                                                product.stock_status = 'low_stock'
                                            else:
                                                product.stock_status = 'in_stock'
                                            product.save(update_fields=['stock', 'stock_status'])

                                            # Record deduction detail
                                            stock_deductions.append({
                                                'order_id': order_id,
                                                'name': product.name,
                                                'sku': product.barcode or str(product.id),
                                                'type': 'simple',
                                                'qty': quantity,
                                                'old_stock': old_stock,
                                                'new_stock': product.stock,
                                                'oversold': oversold,
                                            })
                        
                        # Capture old values BEFORE modification
                        old_order_status = order.order_status

                        # Update order fields - find matching status_setup by name
                        status_setup = Setup.objects.filter(
                            setup_type='status',
                            name__iexact=set_status,
                            is_active=True
                        ).first()
                        # If not found and set_status is 'dispatched', create it
                        if not status_setup and set_status.lower() == 'dispatched':
                            status_setup = Setup.objects.create(
                                setup_type='status',
                                name='Dispatched',
                                is_active=True
                            )
                        if status_setup:
                            order.status_setup = status_setup
                            order.order_status = status_setup.name.lower().replace(' ', '_')
                        else:
                            order.status_setup = None
                            order.order_status = set_status
                        
                        order.logistics = logistics
                        order.dispatch_date = timezone.now()
                        order.save()

                        # Create activity log
                        OrderActivityLog.objects.create(
                            order=order,
                            action_type='status_changed',
                            user=request.user,
                            field_name='order_status',
                            old_value=old_order_status,
                            new_value=set_status,
                            description=f'Order dispatched via batch {batch_number} with {logistics}'
                        )
                        
                        updated_count += 1
                    else:
                        not_found.append(order_id)
                
                # Store detailed stock deduction summary in session for display on detail page
                import json as _json
                request.session['stock_deduction_summary'] = _json.dumps({
                    'deductions': stock_deductions,
                    'warnings': stock_warnings,
                    'not_found': not_found,
                    'updated_count': updated_count,
                    'total_count': len(order_ids),
                    'batch_number': batch_number,
                })

                # Success message
                if updated_count == len(order_ids):
                    messages.success(
                        request,
                        f'✅ Successfully dispatched {updated_count} orders! Batch: {batch_number}'
                    )
                else:
                    messages.warning(
                        request,
                        f'⚠️ Dispatched {updated_count}/{len(order_ids)} orders. '
                        f'{len(not_found)} order(s) not found: {", ".join(not_found)}'
                    )

                return redirect('dispatch_detail', pk=dispatch.pk)
                
        except Exception as e:
            messages.error(request, f'Error creating dispatch: {str(e)}')
            import traceback
            traceback.print_exc()
            return redirect('dispatch_management')
    
    # Get status setups for the dropdown
    status_setups = Setup.objects.filter(setup_type='status', is_active=True).order_by('name')
    
    context = {
        'status_setups': status_setups,
    }
    
    return render(request, 'dispatch_management.html', context)


@login_required
@permission_required('can_view_dispatch')
def dispatch_list(request):
    """List all dispatches (not trashed)"""
    dispatches = Dispatch.objects.filter(is_deleted=False).prefetch_related('items').order_by('-created_at')
    
    # Filters
    logistics_filter = request.GET.get('logistics')
    status_filter = request.GET.get('status')
    search = request.GET.get('search')
    
    if logistics_filter:
        dispatches = dispatches.filter(logistics=logistics_filter)
    
    if status_filter:
        dispatches = dispatches.filter(status=status_filter)
    
    if search:
        dispatches = dispatches.filter(
            Q(batch_number__icontains=search) |
            Q(items__scanned_order_id__icontains=search)
        ).distinct()
    
    context = {
        'dispatches': dispatches,
        'logistics_choices': Dispatch.LOGISTICS_CHOICES,
        'status_choices': Dispatch.STATUS_CHOICES,
        'search': search,
        'logistics_filter': logistics_filter,
        'status_filter': status_filter,
    }
    
    return render(request, 'dispatch_list.html', context)


@login_required
@permission_required('can_view_dispatch')
def dispatch_detail(request, pk):
    """View single dispatch details"""
    import json as _json
    dispatch = get_object_or_404(
        Dispatch.objects.prefetch_related('items__order'),
        pk=pk,
        is_deleted=False
    )

    # Pop one-time stock deduction summary stored by dispatch_management view
    stock_summary_raw = request.session.pop('stock_deduction_summary', None)
    stock_summary = None
    if stock_summary_raw:
        try:
            stock_summary = _json.loads(stock_summary_raw)
        except Exception:
            stock_summary = None

    context = {
        'dispatch': dispatch,
        'stock_summary': stock_summary,
    }

    return render(request, 'dispatch_detail.html', context)


# ==================== DISPATCH TRASH MANAGEMENT ====================

@login_required
@permission_required('can_delete_dispatch')
def dispatch_move_to_trash(request, pk):
    """Move dispatch to trash (soft delete)"""
    dispatch = get_object_or_404(Dispatch, pk=pk, is_deleted=False)
    
    if request.method == 'POST':
        batch_number = dispatch.batch_number
        dispatch.is_deleted = True
        dispatch.deleted_by = request.user
        dispatch.deleted_at = timezone.now()
        dispatch.save()
        
        messages.success(request, f'Dispatch "{batch_number}" moved to trash successfully!')
        return redirect('dispatch_list')
    
    return redirect('dispatch_detail', pk=pk)


@login_required
@permission_required('can_view_dispatch')
def dispatch_trash(request):
    """View trashed dispatches"""
    trashed_dispatches = Dispatch.objects.filter(
        is_deleted=True
    ).select_related('created_by', 'deleted_by').prefetch_related('items').order_by('-deleted_at')
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        trashed_dispatches = trashed_dispatches.filter(
            Q(batch_number__icontains=search_query) |
            Q(items__scanned_order_id__icontains=search_query)
        ).distinct()
    
    # Logistics filter
    logistics_filter = request.GET.get('logistics', '')
    if logistics_filter:
        trashed_dispatches = trashed_dispatches.filter(logistics=logistics_filter)
    
    context = {
        'trashed_dispatches': trashed_dispatches,
        'search_query': search_query,
        'logistics_filter': logistics_filter,
        'logistics_choices': Dispatch.LOGISTICS_CHOICES,
    }
    
    return render(request, 'dispatch_trash.html', context)


@login_required
@permission_required('can_delete_dispatch')
def dispatch_restore(request, pk):
    """Restore dispatch from trash"""
    dispatch = get_object_or_404(Dispatch, pk=pk, is_deleted=True)
    
    if request.method == 'POST':
        batch_number = dispatch.batch_number
        dispatch.is_deleted = False
        dispatch.deleted_by = None
        dispatch.deleted_at = None
        dispatch.save()
        
        messages.success(request, f'Dispatch "{batch_number}" restored successfully!')
        return redirect('dispatch_trash')
    
    return redirect('dispatch_trash')


@login_required
@permission_required('can_delete_dispatch')
def dispatch_permanent_delete(request, pk):
    """Permanently delete dispatch"""
    dispatch = get_object_or_404(Dispatch, pk=pk, is_deleted=True)
    
    if request.method == 'POST':
        batch_number = dispatch.batch_number
        dispatch.delete()
        
        messages.success(request, f'Dispatch "{batch_number}" permanently deleted!')
        return redirect('dispatch_trash')
    
    return redirect('dispatch_trash')


@login_required
@permission_required('can_delete_dispatch')
def dispatch_trash_bulk_action(request):
    """Handle bulk actions on trashed dispatches"""
    if request.method == 'POST':
        dispatch_ids = request.POST.getlist('dispatch_ids')
        action = request.POST.get('bulk_action')
        
        if not dispatch_ids:
            messages.error(request, 'No dispatches selected!')
            return redirect('dispatch_trash')
        
        try:
            dispatches = Dispatch.objects.filter(
                id__in=dispatch_ids,
                is_deleted=True
            )
            count = dispatches.count()
            
            if count == 0:
                messages.error(request, 'No valid dispatches found!')
                return redirect('dispatch_trash')
            
            if action == 'restore':
                dispatches.update(is_deleted=False, deleted_by=None, deleted_at=None)
                messages.success(request, f'✅ {count} dispatch(es) restored successfully!')
                
            elif action == 'permanent_delete':
                dispatches.delete()
                messages.success(request, f'✅ {count} dispatch(es) permanently deleted!')
                
            else:
                messages.error(request, 'Invalid action selected!')
                
        except Exception as e:
            messages.error(request, f'Error performing bulk action: {str(e)}')
    
    return redirect('dispatch_trash')


@login_required
@permission_required('can_delete_dispatch')
def empty_dispatch_trash(request):
    """Empty all trashed dispatches"""
    if request.method == 'POST':
        trashed_dispatches = Dispatch.objects.filter(is_deleted=True)
        count = trashed_dispatches.count()
        
        if count > 0:
            trashed_dispatches.delete()
            messages.success(request, f'✅ Trash emptied! {count} dispatch(es) permanently deleted.')
        else:
            messages.info(request, 'Trash is already empty.')
        
        return redirect('dispatch_trash')
    
    return redirect('dispatch_trash')

@login_required
@permission_required('can_view_dispatch')
def dispatch_list(request):
    """List all dispatches (not trashed)"""
    dispatches = Dispatch.objects.filter(is_deleted=False).prefetch_related('items').order_by('-created_at')
    
    # Filters
    logistics_filter = request.GET.get('logistics')
    status_filter = request.GET.get('status')
    search = request.GET.get('search')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    
    if logistics_filter:
        dispatches = dispatches.filter(logistics=logistics_filter)
    
    if status_filter:
        dispatches = dispatches.filter(status=status_filter)
    
    if search:
        dispatches = dispatches.filter(
            Q(batch_number__icontains=search) |
            Q(items__scanned_order_id__icontains=search)
        ).distinct()
    
    # Date filters
    if date_from:
        dispatches = dispatches.filter(created_at__date__gte=date_from)
    
    if date_to:
        dispatches = dispatches.filter(created_at__date__lte=date_to)
    
    context = {
        'dispatches': dispatches,
        'logistics_choices': Dispatch.LOGISTICS_CHOICES,
        'status_choices': Dispatch.STATUS_CHOICES,
        'search': search,
        'logistics_filter': logistics_filter,
        'status_filter': status_filter,
        'date_from': date_from,
        'date_to': date_to,
    }
    
    return render(request, 'dispatch_list.html', context)


@login_required
@permission_required('can_view_dispatch')
def dispatch_detail(request, pk):
    """View single dispatch details"""
    import json as _json
    dispatch = get_object_or_404(
        Dispatch.objects.prefetch_related('items__order__items__product', 'items__order__items__product_variation'),
        pk=pk
    )

    # ── One-time flash: detailed before/after stock summary from session ──────
    stock_summary_raw = request.session.pop('stock_deduction_summary', None)
    stock_summary = None
    if stock_summary_raw:
        try:
            stock_summary = _json.loads(stock_summary_raw)
        except Exception:
            stock_summary = None

    # ── Persistent: build items table from linked orders (always available) ───
    dispatch_items_detail = []
    for di in dispatch.items.all():
        if di.order:
            for oi in di.order.items.all():
                product = oi.product
                variation = oi.product_variation
                dispatch_items_detail.append({
                    'order_id': di.scanned_order_id,
                    'customer': di.order.customer_name,
                    'product_name': oi.product_name or (product.name if product else 'Unknown'),
                    'sku': oi.product_sku or (variation.sku if variation else (product.barcode if product else '—')),
                    'type': 'variation' if variation else 'simple',
                    'qty': oi.quantity,
                    'current_stock': (variation.stock if variation else (product.stock if product else '—')),
                    'stock_status': (variation.status if variation else (product.stock_status if product else '—')),
                })

    context = {
        'dispatch': dispatch,
        'stock_summary': stock_summary,
        'dispatch_items_detail': dispatch_items_detail,
    }

    return render(request, 'dispatch_detail.html', context)


@login_required
@permission_required('can_delete_dispatch')
def dispatch_delete(request, pk):
    """Delete a dispatch"""
    dispatch = get_object_or_404(Dispatch, pk=pk)
    
    if request.method == 'POST':
        batch_number = dispatch.batch_number
        dispatch.delete()
        messages.success(request, f'Dispatch {batch_number} has been deleted.')
        return redirect('dispatch_list')
    
    return redirect('dispatch_detail', pk=pk)

@login_required
@permission_required('can_view_dispatch')
def dispatch_bulk_action(request):
    """Handle bulk actions on dispatches"""
    if request.method == 'POST':
        dispatch_ids = request.POST.getlist('dispatch_ids')
        action = request.POST.get('bulk_action')
        
        if not dispatch_ids:
            messages.error(request, 'No dispatches selected!')
            return redirect('dispatch_list')
        
        try:
            dispatches = Dispatch.objects.filter(id__in=dispatch_ids, is_deleted=False)
            count = dispatches.count()
            
            if count == 0:
                messages.error(request, 'No valid dispatches found!')
                return redirect('dispatch_list')
            
            if action == 'move_to_trash':
                dispatches.update(
                    is_deleted=True,
                    deleted_by=request.user,
                    deleted_at=timezone.now()
                )
                messages.success(request, f'✅ {count} dispatch(es) moved to trash!')
                
            elif action == 'change_status':
                new_status = request.POST.get('new_status')
                if not new_status:
                    messages.error(request, 'Please select a new status!')
                    return redirect('dispatch_list')
                
                dispatches.update(status=new_status)
                messages.success(request, f'✅ {count} dispatch(es) status updated to {new_status}!')
                
            elif action == 'export_excel':
                # Export functionality (you can implement this later)
                messages.info(request, 'Export functionality coming soon!')
                
            else:
                messages.error(request, 'Invalid action selected!')
                
        except Exception as e:
            messages.error(request, f'Error performing bulk action: {str(e)}')
    
    return redirect('dispatch_list')



# inventory dashboard
@login_required
@permission_required('can_view_inventory')
def inventory_dashboard(request):
    """Modern Inventory Dashboard with Analytics - COMPLETE VERSION"""
    try:
        from datetime import datetime, timedelta
        from django.db.models import Sum
        
        # Get all products (role-based access is handled by @permission_required)
        products = Product.objects.filter(is_deleted=False)
        
        # Stock Statistics — account for bundle products using available_stock
        total_products = products.count()
        has_custom_thresholds = products.filter(low_stock_threshold__gt=0).exists()

        # For accurate stats, evaluate each product's effective stock
        # (bundle products derive stock from components via available_stock)
        non_bundle_products = products.exclude(product_type='bundle')
        bundle_products = products.filter(product_type='bundle').prefetch_related(
            'bundle_components__component_product'
        )

        # Non-bundle stats via DB queries
        out_of_stock_non_bundle = non_bundle_products.filter(stock=0).count()
        if has_custom_thresholds:
            low_stock_non_bundle = non_bundle_products.filter(
                low_stock_threshold__gt=0, stock__lte=F('low_stock_threshold'), stock__gt=0
            ).count()
        else:
            low_stock_non_bundle = non_bundle_products.filter(stock__lte=10, stock__gt=0).count()

        # Bundle stats via Python (available_stock is a computed property)
        out_of_stock_bundle = 0
        low_stock_bundle = 0
        for bp in bundle_products:
            bstock = bp.available_stock
            if bstock == 0:
                out_of_stock_bundle += 1
            elif has_custom_thresholds and bp.low_stock_threshold > 0 and bstock <= bp.low_stock_threshold:
                low_stock_bundle += 1
            elif not has_custom_thresholds and 0 < bstock <= 10:
                low_stock_bundle += 1

        out_of_stock = out_of_stock_non_bundle + out_of_stock_bundle
        low_stock = low_stock_non_bundle + low_stock_bundle
        in_stock = total_products - low_stock - out_of_stock

        # Product Variations Stock
        variations = ProductVariation.objects.filter(product__is_deleted=False)
        total_variations = variations.count()
        variations_out_of_stock = variations.filter(stock=0).count()

        has_var_thresholds = variations.filter(low_stock_threshold__gt=0).exists()
        if has_var_thresholds:
            variations_low_stock = variations.filter(
                low_stock_threshold__gt=0, stock__lte=F('low_stock_threshold'), stock__gt=0
            ).count()
        else:
            variations_low_stock = variations.filter(stock__lte=10, stock__gt=0).count()

        variations_in_stock = total_variations - variations_low_stock - variations_out_of_stock

        # Calculate Total Stock Value — use available_stock for bundles
        all_products_list = list(non_bundle_products) + list(bundle_products)
        total_stock_value = sum(
            p.available_stock * p.price for p in all_products_list if p.available_stock > 0
        )

        # Total Stock Units
        total_stock_units = sum(p.available_stock for p in all_products_list)

        # Low Stock Products — combine non-bundle DB query + bundle Python filter
        if has_custom_thresholds:
            low_stock_products_nb = list(non_bundle_products.filter(
                low_stock_threshold__gt=0, stock__lte=F('low_stock_threshold'), stock__gt=0
            ).order_by('stock')[:10])
        else:
            low_stock_products_nb = list(non_bundle_products.filter(
                stock__lte=10, stock__gt=0
            ).order_by('stock')[:10])

        low_stock_products_b = []
        for bp in bundle_products:
            bstock = bp.available_stock
            if has_custom_thresholds and bp.low_stock_threshold > 0 and 0 < bstock <= bp.low_stock_threshold:
                low_stock_products_b.append(bp)
            elif not has_custom_thresholds and 0 < bstock <= 10:
                low_stock_products_b.append(bp)
        low_stock_products_b.sort(key=lambda p: p.available_stock)

        low_stock_products = sorted(
            low_stock_products_nb + low_stock_products_b[:10],
            key=lambda p: p.available_stock
        )[:10]

        # Out of Stock Products — combine non-bundle + bundle
        out_of_stock_products_nb = list(non_bundle_products.filter(stock=0).order_by('name')[:10])
        out_of_stock_products_b = [bp for bp in bundle_products if bp.available_stock == 0]
        out_of_stock_products_b.sort(key=lambda p: p.name)
        out_of_stock_products = sorted(
            out_of_stock_products_nb + out_of_stock_products_b[:10],
            key=lambda p: p.name
        )[:10]

        # Top Products by Stock Value — use available_stock
        products_with_value = [
            {'product': p, 'value': p.available_stock * p.price}
            for p in all_products_list if p.available_stock > 0
        ]
        top_products = sorted(products_with_value, key=lambda x: x['value'], reverse=True)[:10]
        
        # Recent Dispatched Orders — query via DispatchItem for accuracy
        # (orders are linked to dispatches whether or not order_status was updated)
        dispatched_order_ids = DispatchItem.objects.filter(
            dispatch__is_deleted=False,
            order__isnull=False
        ).values_list('order_id', flat=True).distinct()

        all_dispatched_orders_qs = Order.objects.filter(
            id__in=dispatched_order_ids
        ).select_related('customer').prefetch_related('items').order_by('-dispatch_date', '-id')

        # Pagination for dispatched orders
        dispatch_page_num = int(request.GET.get('dispatch_page', 1))
        dispatch_paginator = Paginator(all_dispatched_orders_qs, 15)
        dispatched_page_obj = dispatch_paginator.get_page(dispatch_page_num)
        recent_dispatched_orders = dispatched_page_obj
        
        # Category-wise Stock Distribution WITH CHART DATA
        categories = Category.objects.all()
        category_stock = []
        category_labels = []
        category_data = []
        total_category_value = 0
        
        for cat in categories:
            cat_products = products.filter(category=cat).prefetch_related(
                'bundle_components__component_product'
            )
            cat_stock = sum(p.available_stock for p in cat_products)
            cat_value = sum(p.available_stock * p.price for p in cat_products if p.available_stock > 0)
            
            if cat_stock > 0:
                category_stock.append({
                    'category': cat.name,
                    'stock': cat_stock,
                    'products': cat_products.count(),
                    'value': cat_value,
                    'percentage': 0  # Will calculate below
                })
                category_labels.append(cat.name)
                category_data.append(cat_stock)
                total_category_value += cat_value
        
        # Calculate percentages for progress bars
        for cat in category_stock:
            if total_category_value > 0:
                cat['percentage'] = (cat['value'] / total_category_value) * 100
            else:
                cat['percentage'] = 0
        
        # ── Stock Movement Data with filter support ──────────────────────
        today = timezone.now().date()

        # Read filter params
        days_param     = request.GET.get('days', '30')
        start_date_param = request.GET.get('start_date', '')
        end_date_param   = request.GET.get('end_date', '')

        if start_date_param and end_date_param:
            try:
                from datetime import datetime as _dt
                movement_start = _dt.strptime(start_date_param, '%Y-%m-%d').date()
                movement_end   = _dt.strptime(end_date_param,   '%Y-%m-%d').date()
                if movement_start > movement_end:
                    movement_start, movement_end = movement_end, movement_start
                selected_days = 'custom'
            except Exception:
                movement_start   = today - timedelta(days=29)
                movement_end     = today
                selected_days    = '30'
                start_date_param = ''
                end_date_param   = ''
        else:
            try:
                days_count = int(days_param)
                if days_count not in (7, 30, 90):
                    days_count = 30
            except Exception:
                days_count = 30
            movement_start   = today - timedelta(days=days_count - 1)
            movement_end     = today
            selected_days    = str(days_count)
            start_date_param = ''
            end_date_param   = ''

        movement_labels = []
        stock_in_data   = []
        stock_out_data  = []

        current_date = movement_start
        while current_date <= movement_end:
            movement_labels.append(current_date.strftime('%b %d'))

            # Stock In for this date
            try:
                stock_ins_day = StockIn.objects.filter(
                    created_at__date=current_date
                ).aggregate(total=Sum('total_quantity'))['total'] or 0
            except Exception:
                stock_ins_day = 0

            # Stock Out (from dispatched orders) for this date
            try:
                orders_day = Order.objects.filter(
                    order_status='dispatched',
                    dispatch_date__date=current_date
                )
                stock_out_day = 0
                for order in orders_day:
                    try:
                        stock_out_day += sum(item.quantity for item in order.items.all())
                    except Exception:
                        pass
            except Exception:
                stock_out_day = 0

            stock_in_data.append(stock_ins_day)
            stock_out_data.append(stock_out_day)
            current_date += timedelta(days=1)
        
        # Stock Turnover Rate
        total_sold_30days = sum(stock_out_data)
        avg_inventory = total_stock_units if total_stock_units > 0 else 1
        stock_turnover_rate = (total_sold_30days / avg_inventory) if avg_inventory > 0 else 0
        
        # Dead Stock (No movement in 90 days)
        ninety_days_ago = timezone.now() - timedelta(days=90)
        try:
            dead_stock_count = products.filter(
                updated_at__lt=ninety_days_ago,
                stock__gt=0
            ).count()
        except:
            dead_stock_count = 0
        
        # SAFE QUERY - Get Recent Stock In Transactions
        recent_stock_ins = []
        try:
            stock_ins_qs = StockIn.objects.all(
            ).only('id', 'reference_number', 'stock_in_type', 'supplier_name', 'created_at', 'total_quantity').order_by('-created_at')[:10]
            
            for stock_in in stock_ins_qs:
                try:
                    # Safely get total_cost
                    try:
                        total_cost = float(stock_in.total_cost) if stock_in.total_cost else 0.0
                    except:
                        total_cost = 0.0
                    
                    # Create safe dict
                    recent_stock_ins.append({
                        'id': stock_in.id,
                        'reference_number': stock_in.reference_number,
                        'stock_in_type': stock_in.stock_in_type,
                        'get_stock_in_type_display': stock_in.get_stock_in_type_display(),
                        'supplier_name': stock_in.supplier_name,
                        'total_quantity': stock_in.total_quantity,
                        'total_cost': total_cost,
                        'created_at': stock_in.created_at,
                        'items_count': stock_in.items.count(),
                    })
                except Exception as e:
                    continue
                    
        except Exception as e:
            recent_stock_ins = []
        
        # Damaged Inventory from Returns
        try:
            damaged_inventory = ReturnItem.objects.filter(
                return_request__is_deleted=False,
                damaged_qty__gt=0
            ).values(
                'product__id',
                'product__name',
                'product__slug',
                'product_sku',
            ).annotate(
                total_damaged=Sum('damaged_qty'),
                total_value=Sum(F('damaged_qty') * F('price')),
            ).order_by('-total_damaged')

            total_damaged_items = sum(item['total_damaged'] for item in damaged_inventory)
            total_damaged_value = sum(float(item['total_value'] or 0) for item in damaged_inventory)

            # Recent damaged return items with details
            damaged_items_detail = ReturnItem.objects.filter(
                return_request__is_deleted=False,
                damaged_qty__gt=0
            ).select_related(
                'return_request', 'product', 'product_variation'
            ).order_by('-created_at')[:20]
        except Exception as e:
            damaged_inventory = []
            total_damaged_items = 0
            total_damaged_value = 0
            damaged_items_detail = []

        # Returns Overview for Inventory Dashboard
        try:
            from collections import defaultdict as _defaultdict

            all_returns_qs = ReturnRequest.objects.filter(
                is_deleted=False,
            )

            # Return stats
            return_stats = {
                'total': all_returns_qs.count(),
                'pending': all_returns_qs.filter(return_status='pending').count(),
                'approved': all_returns_qs.filter(return_status='approved').count(),
                'received': all_returns_qs.filter(return_status='received').count(),
                'inspecting': all_returns_qs.filter(return_status='inspecting').count(),
                'refunded': all_returns_qs.filter(return_status='refunded').count(),
                'rejected': all_returns_qs.filter(return_status='rejected').count(),
            }

            # Total refund amount
            return_stats['total_refund'] = all_returns_qs.filter(
                return_status='refunded'
            ).aggregate(total=Sum('refund_amount'))['total'] or 0

            # Recent returns (both batch and individual) - last 20
            recent_returns = all_returns_qs.select_related(
                'order', 'customer'
            ).order_by('-created_at')[:20]

            # Returned products summary - aggregated by product + variation
            returned_products_summary = ReturnItem.objects.filter(
                return_request__is_deleted=False,
            ).values(
                'product__id', 'product__name', 'product_sku',
                'product_variation__id', 'product_variation__variation_name',
                'product_variation__sku',
            ).annotate(
                total_return_qty=Sum('return_quantity'),
                total_good_qty=Sum('good_qty'),
                total_damaged_qty=Sum('damaged_qty'),
                total_refund=Sum('refund_amount'),
                return_count=Count('return_request', distinct=True),
            ).order_by('-total_return_qty')[:15]

            # Recent return activity logs
            recent_return_logs = ReturnActivityLog.objects.filter(
                return_request__is_deleted=False,
            ).select_related(
                'user', 'return_request'
            ).order_by('-created_at')[:30]

            # Batch vs Individual breakdown
            batch_count = all_returns_qs.filter(
                batch_id__isnull=False
            ).exclude(batch_id='').values('batch_id').distinct().count()
            individual_count = all_returns_qs.filter(
                Q(batch_id__isnull=True) | Q(batch_id='')
            ).count()

            # Restocked summary
            restocked_items = ReturnItem.objects.filter(
                return_request__is_deleted=False,
                restocked=True,
            ).aggregate(
                total_restocked=Sum('good_qty'),
            )
            total_restocked_qty = restocked_items['total_restocked'] or 0

        except Exception:
            return_stats = {
                'total': 0, 'pending': 0, 'approved': 0, 'received': 0,
                'inspecting': 0, 'refunded': 0, 'rejected': 0, 'total_refund': 0,
            }
            recent_returns = []
            returned_products_summary = []
            recent_return_logs = []
            batch_count = 0
            individual_count = 0
            total_restocked_qty = 0

        # Stock Count - All products with stock info for the Stock Count section
        stock_count_products = products.select_related('category').prefetch_related(
            'bundle_components__component_product'
        ).order_by('-stock')

        # Stock Status Distribution for Charts
        stock_chart_data = {
            'labels': ['In Stock', 'Low Stock', 'Out of Stock'],
            'data': [in_stock, low_stock, out_of_stock],
            'colors': ['#10b981', '#f59e0b', '#ef4444']
        }
        
        context = {
            # Basic Stats
            'total_products': total_products,
            'in_stock': in_stock,
            'low_stock': low_stock,
            'out_of_stock': out_of_stock,
            
            # Variations Stats
            'total_variations': total_variations,
            'variations_in_stock': variations_in_stock,
            'variations_low_stock': variations_low_stock,
            'variations_out_of_stock': variations_out_of_stock,
            
            # Value Stats
            'total_stock_value': total_stock_value,
            'total_stock_units': total_stock_units,
            'stock_turnover_rate': stock_turnover_rate,
            'dead_stock_count': dead_stock_count,
            
            # Product Lists
            'low_stock_products': low_stock_products,
            'out_of_stock_products': out_of_stock_products,
            'top_products': top_products,
            'recent_dispatched_orders': recent_dispatched_orders,
            'dispatched_page_obj': dispatched_page_obj,
            'dispatched_total_count': dispatched_order_ids.count(),
            
            # Category Data
            'category_stock': category_stock,
            
            # Stock In Transactions
            'recent_stock_ins': recent_stock_ins,

            # Damaged Inventory from Returns
            'damaged_inventory': damaged_inventory,
            'total_damaged_items': total_damaged_items,
            'total_damaged_value': total_damaged_value,
            'damaged_items_detail': damaged_items_detail,

            # Chart Data (JSON encoded for JavaScript)
            'stock_chart_data': json.dumps(stock_chart_data),
            'category_labels': json.dumps(category_labels),
            'category_data': json.dumps(category_data),
            'movement_labels': json.dumps(movement_labels),
            'stock_in_data': json.dumps(stock_in_data),
            'stock_out_data': json.dumps(stock_out_data),

            # Filter context
            'selected_days': selected_days,
            'start_date': start_date_param,
            'end_date': end_date_param,

            # Returns Overview
            'return_stats': return_stats,
            'recent_returns': recent_returns,
            'returned_products_summary': returned_products_summary,
            'recent_return_logs': recent_return_logs,
            'batch_count': batch_count,
            'individual_count': individual_count,
            'total_restocked_qty': total_restocked_qty,

            # Stock Count
            'stock_count_products': stock_count_products,
        }

        return render(request, 'inventory_dashboard.html', context)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        messages.error(request, f'Error loading inventory dashboard: {str(e)}')
        return redirect('dashboard')

# stock in 
@login_required
@permission_required('can_manage_inventory')
def stock_in_create(request):
    """Create new stock in transaction - BULLETPROOF VERSION"""
    
    if request.method == 'POST':
        try:
            # Get form data
            stock_in_type = request.POST.get('stock_in_type', 'purchase')
            supplier_name = request.POST.get('supplier_name', '').strip()
            notes = request.POST.get('notes', '').strip()
            items_json = request.POST.get('items', '[]')
            
            
            # Parse items
            items = json.loads(items_json)
            
            if not items:
                messages.error(request, 'Please add at least one product')
                return redirect('stock_in_create')
            
            # Create StockIn WITHOUT totals first
            stock_in = StockIn.objects.create(
                stock_in_type=stock_in_type,
                supplier_name=supplier_name,
                notes=notes,
                created_by=request.user,
                total_quantity=0,
                total_cost=0
            )
            
            
            total_qty = 0
            total_cost = 0.0
            
            # Process items
            for idx, item in enumerate(items, 1):
                try:
                    product_id = int(item.get('product_id', 0))
                    quantity = int(item.get('quantity', 0))
                    
                    # Parse unit_cost as FLOAT first
                    unit_cost_str = str(item.get('unit_cost', '0')).strip()
                    try:
                        unit_cost_float = float(unit_cost_str)
                    except:
                        unit_cost_float = 0.0
                    
                    if quantity <= 0 or product_id <= 0:
                        continue
                    
                    # Get product
                    product = Product.objects.get(id=product_id, user=request.user)
                    
                    # Get variation if exists
                    variation = None
                    variation_id = item.get('variation_id')
                    if variation_id:
                        variation = ProductVariation.objects.get(id=int(variation_id))
                    
                    # Calculate total as FLOAT
                    item_total = quantity * unit_cost_float
                    
                    # Create item
                    StockInItem.objects.create(
                        stock_in=stock_in,
                        product=product,
                        product_variation=variation,
                        quantity=quantity,
                        unit_cost=unit_cost_float,
                        total_cost=item_total,
                        notes=item.get('notes', '')
                    )
                    
                    # Update stock
                    if variation:
                        variation.stock += quantity
                        if variation.stock > 0:
                            variation.status = 'active'
                        variation.save()
                    else:
                        product.stock += quantity
                        if product.stock > 0:
                            product.stock_status = 'in_stock'
                        product.save()

                    # Create ProductPurchase record to keep average_cost dynamic
                    if unit_cost_float > 0 and not variation:
                        ProductPurchase.objects.create(
                            product=product,
                            cost_price=unit_cost_float,
                            quantity=quantity,
                        )
                        # Only sync cost_price field for variable cost price products
                        if product.cost_price_type == 'variable':
                            product.refresh_from_db()
                            product.cost_price = product.average_cost
                            product.save(update_fields=['cost_price'])

                    total_qty += quantity
                    total_cost += item_total
                    
                    
                except Exception as e:
                    continue
            
            # Update totals
            stock_in.total_quantity = total_qty
            stock_in.total_cost = total_cost
            stock_in.save()
            
            messages.success(
                request,
                f'✅ Stock In {stock_in.reference_number} created! '
                f'{total_qty} items added worth Rs {total_cost:.2f}'
            )
            
            return redirect('inventory_dashboard')
            
        except json.JSONDecodeError:
            messages.error(request, 'Invalid data format')
        except Exception as e:
            import traceback
            traceback.print_exc()
            messages.error(request, f'Error: {str(e)}')
        
        return redirect('stock_in_create')
    
    # GET request
    products = Product.objects.filter(user=request.user, is_active=True).order_by('name')
    return render(request, 'stock_in_create.html', {'products': products})

@login_required
def stock_in_detail(request, stock_in_id):
    """View stock in transaction details - COMPLETE FIXED VERSION"""
    try:
        from django.db import connection
        from django.contrib.auth.models import User
        
        # Get Stock In basic data using RAW SQL
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    id, reference_number, stock_in_type, supplier_name, 
                    notes, total_quantity, created_at, created_by_id
                FROM dashboard_stockin
                WHERE id = %s AND created_by_id = %s
            """, [stock_in_id, request.user.id])
            
            row = cursor.fetchone()
            
            if not row:
                messages.error(request, 'Stock In transaction not found')
                return redirect('inventory_dashboard')
            
            # Create StockIn object from raw data
            class StockInData:
                def __init__(self, data):
                    self.id = data[0]
                    self.reference_number = data[1]
                    self.stock_in_type = data[2]
                    self.supplier_name = data[3]
                    self.notes = data[4]
                    self.total_quantity = data[5]
                    self.created_at = data[6]
                    self.created_by_id = data[7]
                
                def get_stock_in_type_display(self):
                    types = {
                        'purchase': 'Purchase Order',
                        'return': 'Customer Return',
                        'adjustment': 'Stock Adjustment',
                        'transfer': 'Transfer In',
                        'other': 'Other',
                    }
                    return types.get(self.stock_in_type, self.stock_in_type.title())
            
            stock_in = StockInData(row)
            
            # Get created_by user
            try:
                stock_in.created_by = User.objects.get(id=stock_in.created_by_id)
            except User.DoesNotExist:
                stock_in.created_by = type('obj', (object,), {'username': 'Unknown'})()
        
        # Get items using RAW SQL with product info
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    si.id, 
                    si.product_id, 
                    si.product_variation_id, 
                    si.quantity, 
                    si.notes,
                    p.name as product_name, 
                    p.slug as product_slug, 
                    p.image as product_image
                FROM dashboard_stockinitem si
                JOIN dashboard_product p ON si.product_id = p.id
                WHERE si.stock_in_id = %s
                ORDER BY si.id
            """, [stock_in_id])
            
            item_rows = cursor.fetchall()
        
        # Build items data list
        items_data = []
        total_cost_sum = 0.0
        
        for row in item_rows:
            item_id = row[0]
            product_id = row[1]
            variation_id = row[2]
            quantity = row[3]
            notes = row[4]
            product_name = row[5]
            product_slug = row[6]
            product_image = row[7]
            
            # Get SAFE costs using RAW SQL
            with connection.cursor() as cursor2:
                cursor2.execute("""
                    SELECT unit_cost, total_cost
                    FROM dashboard_stockinitem
                    WHERE id = %s
                """, [item_id])
                
                cost_row = cursor2.fetchone()
                
                if cost_row:
                    try:
                        safe_unit_cost = float(cost_row[0]) if cost_row[0] else 0.0
                    except (TypeError, ValueError, Decimal.InvalidOperation):
                        safe_unit_cost = 0.0
                    
                    try:
                        safe_total_cost = float(cost_row[1]) if cost_row[1] else 0.0
                    except (TypeError, ValueError, Decimal.InvalidOperation):
                        safe_total_cost = 0.0
                else:
                    safe_unit_cost = 0.0
                    safe_total_cost = 0.0
            
            # Get variation name if exists
            variation_name = None
            if variation_id:
                try:
                    variation = ProductVariation.objects.get(id=variation_id)
                    
                    # Get variation attributes
                    attribute_links = variation.attribute_values.select_related(
                        'attribute_value__attribute'
                    ).all()
                    
                    if attribute_links.exists():
                        variation_parts = []
                        for link in attribute_links:
                            attr = link.attribute_value.attribute.name
                            val = link.attribute_value.value
                            variation_parts.append(f"{attr}: {val}")
                        variation_name = " | ".join(variation_parts)
                    else:
                        variation_name = variation.sku
                except ProductVariation.DoesNotExist:
                    variation_name = f"Variation #{variation_id}"
                except Exception as e:
                    variation_name = f"Variation #{variation_id}"
            
            # Create clean item object
            class ItemData:
                def __init__(self):
                    self.id = item_id
                    self.quantity = quantity
                    self.notes = notes if notes else ""
                    
                    # Create product sub-object
                    class ProductObj:
                        def __init__(self):
                            self.id = product_id
                            self.name = product_name
                            self.slug = product_slug
                            
                            # Handle image
                            if product_image:
                                class ImageObj:
                                    def __init__(self, url):
                                        self.url = url
                                self.image = ImageObj(product_image)
                            else:
                                self.image = None
                    
                    self.product = ProductObj()
            
            item = ItemData()
            
            # Add to total
            total_cost_sum += safe_total_cost
            
            # Add to items list
            items_data.append({
                'item': item,
                'variation_name': variation_name,
                'safe_unit_cost': safe_unit_cost,
                'safe_total_cost': safe_total_cost,
            })
        
        # Get safe total cost from StockIn table
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT total_cost
                FROM dashboard_stockin
                WHERE id = %s
            """, [stock_in_id])
            
            total_row = cursor.fetchone()
            
            if total_row and total_row[0]:
                try:
                    safe_total_cost = float(total_row[0])
                except (TypeError, ValueError, Decimal.InvalidOperation):
                    safe_total_cost = total_cost_sum
            else:
                safe_total_cost = total_cost_sum
        
        context = {
            'stock_in': stock_in,
            'items_data': items_data,
            'safe_total_cost': safe_total_cost,
        }
        
        return render(request, 'stock_in_detail.html', context)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        messages.error(request, f'Error loading stock in details: {str(e)}')
        return redirect('inventory_dashboard')

@login_required
@require_http_methods(["GET"])
def api_get_product_for_stockin(request, product_id):
    """API to get product details including variations for stock in"""
    product = get_object_or_404(Product, id=product_id, user=request.user)
    
    data = {
        'id': product.id,
        'name': product.name,
        'type': product.product_type,
        'variations': []
    }
    
    if product.product_type == 'variable':
        variations = product.variations.filter(is_active=True).prefetch_related(
            'attribute_values__attribute_value__attribute'
        ).order_by('sku')
        
        for var in variations:
            attrs = []
            for link in var.attribute_values.all():
                av = link.attribute_value
                attrs.append({
                    'name': av.attribute.name,
                    'value': av.value
                })
            
            data['variations'].append({
                'id': var.id,
                'sku': var.sku,
                'stock': var.stock,
                'price': str(var.price),
                'attributes': attrs
            })
    else:
        data['stock'] = product.stock
        data['price'] = str(product.price)
    
    return JsonResponse(data)


# ==================== CITY MANAGEMENT VIEWS ====================

@login_required
def city_management(request):
    """City management page - Add, edit, delete cities with valley status"""
    user = request.user
    is_admin = user.is_superuser or user.role == 'administrator'

    if not (is_admin or user.can_view_cities):
        messages.error(request, '❌ You do not have permission to access City Management.', extra_tags='permission_denied')
        return redirect('dashboard')
    cities = City.objects.all().order_by('name')
    
    if request.method == 'POST':
        # Add new city
        if 'add_city' in request.POST:
            if not (is_admin or user.can_add_cities):
                messages.error(request, '❌ You do not have permission to add cities.')
            else:
                name = request.POST.get('city_name', '').strip().title()
                valley_status = request.POST.get('valley_status', 'valley')

                if name:
                    city, created = City.objects.get_or_create(
                        name=name,
                        defaults={
                            'valley_status': valley_status,
                            'is_active': True
                        }
                    )

                    if created:
                        messages.success(request, f'✅ City "{name}" added successfully!')
                    else:
                        messages.info(request, f'ℹ️ City "{name}" already exists')
                else:
                    messages.error(request, 'Please enter a city name')

        # Bulk actions
        elif 'bulk_action' in request.POST:
            city_ids = request.POST.getlist('city_ids')
            action = request.POST.get('bulk_action')

            if city_ids and action:
                cities_to_update = City.objects.filter(id__in=city_ids)

                if action == 'delete':
                    if not (is_admin or user.can_delete_cities):
                        messages.error(request, '❌ You do not have permission to delete cities.')
                    else:
                        count = cities_to_update.count()
                        cities_to_update.delete()
                        messages.success(request, f'✅ {count} city(s) deleted successfully!')

                elif action in ['valley', 'out_valley']:
                    if not (is_admin or user.can_edit_cities):
                        messages.error(request, '❌ You do not have permission to edit cities.')
                    else:
                        cities_to_update.update(valley_status=action)
                        messages.success(request, f'✅ {cities_to_update.count()} city(s) updated to {action.replace("_", " ").title()}!')

                elif action == 'activate':
                    if not (is_admin or user.can_edit_cities):
                        messages.error(request, '❌ You do not have permission to edit cities.')
                    else:
                        cities_to_update.update(is_active=True)
                        messages.success(request, f'✅ {cities_to_update.count()} city(s) activated!')

                elif action == 'deactivate':
                    if not (is_admin or user.can_edit_cities):
                        messages.error(request, '❌ You do not have permission to edit cities.')
                    else:
                        cities_to_update.update(is_active=False)
                        messages.success(request, f'✅ {cities_to_update.count()} city(s) deactivated!')
    
    # Get statistics
    total_cities = cities.count()
    valley_cities = cities.filter(valley_status='valley').count()
    out_valley_cities = cities.filter(valley_status='out_valley').count()
    
    context = {
        'cities': cities,
        'total_cities': total_cities,
        'valley_cities': valley_cities,
        'out_valley_cities': out_valley_cities,
        'valley_status_choices': City.VALLEY_STATUS_CHOICES,
        'can_add': is_admin or user.can_add_cities,
        'can_edit': is_admin or user.can_edit_cities,
        'can_delete': is_admin or user.can_delete_cities,
    }
    
    return render(request, 'city_management.html', context)

@login_required
def city_edit(request, city_id):
    """Edit a city"""
    user = request.user
    is_admin = user.is_superuser or user.role == 'administrator'

    if not (is_admin or user.can_edit_cities):
        messages.error(request, '❌ You do not have permission to edit cities.', extra_tags='permission_denied')
        return redirect('city_management')

    city = get_object_or_404(City, id=city_id)
    
    if request.method == 'POST':
        name = request.POST.get('city_name', '').strip().title()
        valley_status = request.POST.get('valley_status', 'valley')
        is_active = 'is_active' in request.POST
        
        if name:
            # Check if name already exists (excluding current city)
            if City.objects.filter(name=name).exclude(id=city.id).exists():
                messages.error(request, f'City "{name}" already exists!')
            else:
                city.name = name
                city.valley_status = valley_status
                city.is_active = is_active
                city.save()
                messages.success(request, f'✅ City "{name}" updated successfully!')
                return redirect('city_management')
        else:
            messages.error(request, 'Please enter a city name')
    
    context = {
        'city': city,
        'valley_status_choices': City.VALLEY_STATUS_CHOICES,
    }
    
    return render(request, 'city_edit.html', context)


@login_required
def city_delete(request, city_id):
    """Delete a city"""
    user = request.user
    is_admin = user.is_superuser or user.role == 'administrator'

    if not (is_admin or user.can_delete_cities):
        messages.error(request, '❌ You do not have permission to delete cities.', extra_tags='permission_denied')
        return redirect('city_management')

    city = get_object_or_404(City, id=city_id)
    
    if request.method == 'POST':
        city_name = city.name
        city.delete()
        messages.success(request, f'✅ City "{city_name}" deleted successfully!')
        return redirect('city_management')
    
    return render(request, 'city_delete.html', {'city': city})

@login_required
def city_quick_add(request):
    """Quick add cities via AJAX"""
    user = request.user
    is_admin = user.is_superuser or user.role == 'administrator'

    if not (is_admin or user.can_add_cities):
        return JsonResponse({'success': False, 'message': 'You do not have permission to add cities.'})

    if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        try:
            data = json.loads(request.body)
            city_name = data.get('city_name', '').strip().title()
            valley_status = data.get('valley_status', 'valley')
            
            if not city_name:
                return JsonResponse({'success': False, 'message': 'City name is required'})
            
            city, created = City.objects.get_or_create(
                name=city_name,
                defaults={
                    'valley_status': valley_status,
                    'is_active': True
                }
            )
            
            if created:
                return JsonResponse({
                    'success': True,
                    'message': f'City "{city_name}" added successfully!',
                    'city': {
                        'id': city.id,
                        'name': city.name,
                        'valley_status': city.valley_status,
                        'is_active': city.is_active,
                    }
                })
            else:
                # If city exists but valley status is different, update it
                if city.valley_status != valley_status:
                    city.valley_status = valley_status
                    city.save()
                    return JsonResponse({
                        'success': True,
                        'message': f'City "{city_name}" already exists. Updated valley status.',
                        'city': {
                            'id': city.id,
                            'name': city.name,
                            'valley_status': city.valley_status,
                            'is_active': city.is_active,
                        }
                    })
                return JsonResponse({
                    'success': True,
                    'message': f'City "{city_name}" already exists',
                    'city': {
                        'id': city.id,
                        'name': city.name,
                        'valley_status': city.valley_status,
                        'is_active': city.is_active,
                    }
                })
                
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'message': 'Invalid data format'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
    
    return JsonResponse({'success': False, 'message': 'Invalid request'})

@login_required
def city_bulk_add(request):
    """Bulk add multiple cities at once via AJAX"""
    user = request.user
    is_admin = user.is_superuser or user.role == 'administrator'

    if not (is_admin or user.can_add_cities):
        return JsonResponse({'success': False, 'message': 'You do not have permission to add cities.'})
    if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        try:
            data = json.loads(request.body)
            city_name = data.get('city_name', '').strip().title()
            valley_status = data.get('valley_status', 'valley')
            is_bulk = data.get('is_bulk', False)
            
            if not city_name:
                return JsonResponse({'success': False, 'message': 'City name is required'})
            
            # Check if city already exists
            existing_city = City.objects.filter(name__iexact=city_name).first()
            
            if existing_city:
                # Check if valley status needs update
                if existing_city.valley_status != valley_status:
                    existing_city.valley_status = valley_status
                    existing_city.save()
                    return JsonResponse({
                        'success': True,
                        'message': f'Updated: {city_name}',
                        'status': 'updated',
                        'city_id': existing_city.id
                    })
                else:
                    return JsonResponse({
                        'success': True,
                        'message': f'Skipped: {city_name} (already exists)',
                        'status': 'skipped',
                        'city_id': existing_city.id
                    })
            
            # Create new city
            city = City.objects.create(
                name=city_name,
                valley_status=valley_status,
                is_active=True
            )
            
            if is_bulk:
                return JsonResponse({
                    'success': True,
                    'message': f'Added: {city_name}',
                    'status': 'added',
                    'city_id': city.id
                })
            else:
                return JsonResponse({
                    'success': True,
                    'message': f'City "{city_name}" added successfully as {city.get_valley_status_display()}',
                    'status': 'added',
                    'city_id': city.id
                })
                
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'message': 'Invalid data format'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
    
    return JsonResponse({'success': False, 'message': 'Invalid request'})


@login_required
@require_http_methods(["GET"])
def api_get_cities(request):
    """API to get all cities for dropdown in order create"""
    try:
        cities = City.objects.filter(is_active=True).order_by('name')
        
        city_list = []
        for city in cities:
            city_list.append({
                'id': city.id,
                'name': city.name,
                'valley_status': city.valley_status,
                'display_status': city.get_valley_status_display(),
                'in_out': 'IN' if city.valley_status == 'valley' else 'OUT'
            })
        
        return JsonResponse({
            'success': True, 
            'cities': city_list,
            'count': len(city_list)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error loading cities: {str(e)}',
            'cities': []
        })
        
@login_required
@require_http_methods(["GET"])
def api_get_city_valley_status(request):
    """API to get valley status for a city (for IN/OUT field in order create)"""
    city_name = request.GET.get('city', '').strip().title()
    
    if not city_name:
        return JsonResponse({'success': False, 'message': 'City name required'})
    
    try:
        # Try exact match first, then case-insensitive match
        city = City.objects.filter(
            Q(name=city_name) | Q(name__iexact=city_name),
            is_active=True
        ).first()
        
        if city:
            return JsonResponse({
                'success': True,
                'city': city.name,
                'valley_status': city.valley_status,
                'display_status': city.get_valley_status_display(),
                'in_out': 'IN' if city.valley_status == 'valley' else 'OUT',
                'exists': True,
                'city_id': city.id
            })
        else:
            # Check if it's in default valley cities
            default_valley_cities = [
                'Kathmandu', 'Lalitpur', 'Bhaktapur', 'Kirtipur', 
                'Thimi', 'Tokha', 'Budhanilkantha', 'Gokarneshwor'
            ]
            
            if city_name in default_valley_cities:
                return JsonResponse({
                    'success': True,
                    'city': city_name,
                    'valley_status': 'valley',
                    'display_status': 'Valley (Inside)',
                    'in_out': 'IN',
                    'exists': False,
                    'is_default': True,
                    'message': f'City "{city_name}" is a default Valley city'
                })
            else:
                # Check common Out Valley cities
                common_out_valley_cities = [
                    'Pokhara', 'Biratnagar', 'Birgunj', 'Dharan', 'Hetauda', 
                    'Butwal', 'Nepalgunj', 'Dhankuta', 'Janakpur', 'Dhangadhi'
                ]
                
                if city_name in common_out_valley_cities:
                    return JsonResponse({
                        'success': True,
                        'city': city_name,
                        'valley_status': 'out_valley',
                        'display_status': 'Out Valley (Outside)',
                        'in_out': 'OUT',
                        'exists': False,
                        'is_default': True,
                        'message': f'City "{city_name}" is a common Out Valley city'
                    })
                else:
                    # Default to Out Valley for unknown cities
                    return JsonResponse({
                        'success': True,
                        'city': city_name,
                        'valley_status': 'out_valley',
                        'display_status': 'Out Valley (Outside)',
                        'in_out': 'OUT',
                        'exists': False,
                        'is_default': True,
                        'message': f'City "{city_name}" not found in database. Marked as Out Valley by default.'
                    })
                    
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error checking city: {str(e)}'
        })
        
# ==================== VALLEY STATUS API ENDPOINT ====================

@login_required
@require_http_methods(["GET"])
def get_valley_status(request):
    """API endpoint to detect if a city is in valley or out valley - FIXED VERSION"""
    city_name = request.GET.get('city', '').strip()
    
    if not city_name:
        return JsonResponse({
            'success': False,
            'message': 'City name is required'
        })
    
    try:
        # Try to find the city in database (case-insensitive)
        city = City.objects.filter(
            Q(name__iexact=city_name) | Q(name__iexact=city_name.title())
        ).first()
        
        if city:
            return JsonResponse({
                'success': True,
                'valley_status': city.valley_status,
                'display_status': city.get_valley_status_display(),
                'exists': True,
                'city': city.name,
                'in_out': 'in' if city.valley_status == 'valley' else 'out'
            })
        else:
            # If city not found, check patterns
            city_lower = city_name.lower()
            
            # Valley city patterns
            valley_patterns = [
                'kathmandu', 'lalitpur', 'bhaktapur', 'kirtipur', 'patan',
                'thamel', 'ktm', 'kath', 'mandu', 'valley'
            ]
            
            # Out-valley city patterns
            out_valley_patterns = [
                'birgunj', 'pokhara', 'dharan', 'biratnagar', 'dumla',
                'butwal', 'bhairahawa', 'nepalgunj', 'dhangadhi',
                'hetauda', 'janakpur', 'dhankuta', 'outside', 'out'
            ]
            
            # Check patterns
            is_valley = any(pattern in city_lower for pattern in valley_patterns)
            is_out_valley = any(pattern in city_lower for pattern in out_valley_patterns)
            
            if is_valley and not is_out_valley:
                status = 'valley'
                display = 'Valley (Pattern Match)'
                in_out = 'in'
            elif is_out_valley and not is_valley:
                status = 'out_valley'
                display = 'Out Valley (Pattern Match)'
                in_out = 'out'
            else:
                # Default to OUT for unknown cities (safer assumption)
                status = 'out_valley'
                display = 'Out Valley (Default)'
                in_out = 'out'
            
            return JsonResponse({
                'success': True,
                'valley_status': status,
                'display_status': display,
                'exists': False,
                'city': city_name,
                'in_out': in_out,
                'message': f'City detected via pattern matching as {display}'
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error detecting valley status: {str(e)}'
        })
        
        
# ==================== RETURN MANAGEMENT VIEWS (WITH TRASH) ====================

@login_required
@permission_required('can_view_returns')
def returns_dashboard(request):
    """Return management dashboard with statistics"""
    
    # Get filter parameters
    date_filter = request.GET.get('date_range', 'all')
    status_filter = request.GET.get('status', '')
    
    # Base queryset - exclude deleted
    returns = ReturnRequest.objects.filter(is_deleted=False).select_related('order', 'customer', 'created_by').all()
    
    # Apply date filter
    today = timezone.now().date()
    if date_filter == 'today':
        returns = returns.filter(created_at__date=today)
    elif date_filter == 'yesterday':
        yesterday = today - timedelta(days=1)
        returns = returns.filter(created_at__date=yesterday)
    elif date_filter == 'last_7_days':
        start = today - timedelta(days=7)
        returns = returns.filter(created_at__date__gte=start)
    elif date_filter == 'last_30_days':
        start = today - timedelta(days=30)
        returns = returns.filter(created_at__date__gte=start)
    elif date_filter == 'this_month':
        returns = returns.filter(created_at__year=today.year, created_at__month=today.month)
    
    # Apply status filter
    if status_filter:
        returns = returns.filter(return_status=status_filter)
    
    # Statistics
    total_returns = returns.count()
    pending_returns = returns.filter(return_status='pending').count()
    approved_returns = returns.filter(return_status='approved').count()
    received_returns = returns.filter(return_status='received').count()
    inspecting_returns = returns.filter(return_status='inspecting').count()
    refunded_returns = returns.filter(return_status='refunded').count()
    rejected_returns = returns.filter(return_status='rejected').count()
    
    total_refund_amount = returns.filter(
        return_status='refunded'
    ).aggregate(total=Sum('refund_amount'))['total'] or Decimal('0.00')
    
    # Return reasons breakdown
    reason_stats = returns.values('return_reason').annotate(
        count=Count('id')
    ).order_by('-count')[:5]
    
    # Recent returns
    recent_returns = returns.order_by('-created_at')[:10]
    
    # Trash count
    trash_count = ReturnRequest.objects.filter(is_deleted=True).count()
    
    context = {
        'returns': recent_returns,
        'total_returns': total_returns,
        'pending_returns': pending_returns,
        'approved_returns': approved_returns,
        'received_returns': received_returns,
        'inspecting_returns': inspecting_returns,
        'refunded_returns': refunded_returns,
        'rejected_returns': rejected_returns,
        'total_refund_amount': total_refund_amount,
        'reason_stats': reason_stats,
        'date_filter': date_filter,
        'status_filter': status_filter,
        'trash_count': trash_count,
    }
    
    return render(request, 'returns/dashboard.html', context)


@login_required
@permission_required('can_view_returns')
def returns_list(request):
    """List all return requests with filters (excluding trash)
    Default view: shows batches + individual returns
    Batch view (?batch=X): shows individual returns in that batch
    """
    from django.core.paginator import Paginator

    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    batch_filter = request.GET.get('batch', '')
    trash_count = ReturnRequest.objects.filter(is_deleted=True).count()

    if batch_filter:
        # ===== BATCH DETAIL VIEW =====
        # Show individual returns in the selected batch
        returns = ReturnRequest.objects.filter(
            is_deleted=False, batch_id=batch_filter
        ).select_related(
            'order', 'customer', 'created_by', 'approved_by'
        ).prefetch_related('items').order_by('-created_at')

        if search_query:
            returns = returns.filter(
                Q(rma_number__icontains=search_query) |
                Q(customer_name__icontains=search_query) |
                Q(order__order_number__icontains=search_query)
            )
        if status_filter:
            returns = returns.filter(return_status=status_filter)

        paginator = Paginator(returns, 50)
        returns_page = paginator.get_page(request.GET.get('page'))

        # Get batch-level stats
        batch_stats = ReturnRequest.objects.filter(
            is_deleted=False, batch_id=batch_filter
        ).aggregate(
            total_returns=Count('id'),
            total_refund=Sum('refund_amount'),
            pending=Count('id', filter=Q(return_status='pending')),
            approved=Count('id', filter=Q(return_status='approved')),
            received=Count('id', filter=Q(return_status='received')),
            inspecting=Count('id', filter=Q(return_status='inspecting')),
            refunded=Count('id', filter=Q(return_status='refunded')),
            rejected=Count('id', filter=Q(return_status='rejected')),
        )

        # Get all return items in this batch with product details (for summary)
        batch_return_items = ReturnItem.objects.filter(
            return_request__batch_id=batch_filter,
            return_request__is_deleted=False
        ).select_related(
            'product', 'product_variation', 'return_request', 'restocked_by'
        ).order_by('-return_request__created_at')

        # Aggregate items by product for a compact summary
        from collections import defaultdict
        product_summary = defaultdict(lambda: {
            'product_name': '', 'product_sku': '', 'total_return_qty': 0,
            'total_good_qty': 0, 'total_damaged_qty': 0, 'total_refund': Decimal('0'),
            'restocked_qty': 0, 'orders': set(), 'variation_name': '',
        })
        for item in batch_return_items:
            key = f"{item.product_id}_{item.product_variation_id or 'none'}"
            summary = product_summary[key]
            summary['product_name'] = item.product_name
            summary['product_sku'] = item.product_sku
            if item.product_variation:
                summary['variation_name'] = str(item.product_variation)
            summary['total_return_qty'] += item.return_quantity
            summary['total_good_qty'] += item.good_qty
            summary['total_damaged_qty'] += item.damaged_qty
            summary['total_refund'] += item.refund_amount
            if item.restocked:
                summary['restocked_qty'] += item.good_qty
            summary['orders'].add(item.return_request.order.order_number)

        # Convert to list and make orders a count
        product_summary_list = []
        for key, data in product_summary.items():
            data['order_count'] = len(data['orders'])
            del data['orders']
            product_summary_list.append(data)

        # Get activity logs for all returns in this batch
        batch_activity_logs = ReturnActivityLog.objects.filter(
            return_request__batch_id=batch_filter,
            return_request__is_deleted=False
        ).select_related('user', 'return_request').order_by('-created_at')[:50]

        context = {
            'returns': returns_page,
            'batch_filter': batch_filter,
            'batch_stats': batch_stats,
            'search_query': search_query,
            'status_filter': status_filter,
            'status_choices': ReturnRequest.RETURN_STATUS_CHOICES,
            'condition_choices': ReturnRequest.CONDITION_CHOICES,
            'trash_count': trash_count,
            'view_mode': 'batch_detail',
            'product_summary': product_summary_list,
            'batch_return_items': batch_return_items,
            'batch_activity_logs': batch_activity_logs,
        }
    else:
        # ===== DEFAULT VIEW — BATCH SUMMARY =====
        # Get batch summaries
        batches_qs = ReturnRequest.objects.filter(
            is_deleted=False, batch_id__isnull=False
        ).exclude(batch_id='')

        if search_query:
            batches_qs = batches_qs.filter(
                Q(batch_id__icontains=search_query) |
                Q(rma_number__icontains=search_query) |
                Q(customer_name__icontains=search_query) |
                Q(order__order_number__icontains=search_query)
            )
        if status_filter:
            batches_qs = batches_qs.filter(return_status=status_filter)

        batches = batches_qs.values('batch_id').annotate(
            total_returns=Count('id'),
            total_refund=Sum('refund_amount'),
            created_at=Min('created_at'),
            pending=Count('id', filter=Q(return_status='pending')),
            approved=Count('id', filter=Q(return_status='approved')),
            received=Count('id', filter=Q(return_status='received')),
            inspecting=Count('id', filter=Q(return_status='inspecting')),
            refunded=Count('id', filter=Q(return_status='refunded')),
            rejected=Count('id', filter=Q(return_status='rejected')),
        ).order_by('-created_at')

        paginator = Paginator(batches, 20)
        batches_page = paginator.get_page(request.GET.get('page'))

        # Also get individual (non-batch) returns
        individual_qs = ReturnRequest.objects.filter(
            is_deleted=False
        ).filter(
            Q(batch_id__isnull=True) | Q(batch_id='')
        ).select_related(
            'order', 'customer', 'created_by'
        ).prefetch_related(
            'items', 'activity_logs'
        ).order_by('-created_at')

        if search_query:
            individual_qs = individual_qs.filter(
                Q(rma_number__icontains=search_query) |
                Q(customer_name__icontains=search_query) |
                Q(order__order_number__icontains=search_query)
            )
        if status_filter:
            individual_qs = individual_qs.filter(return_status=status_filter)

        # Overall stats
        all_returns = ReturnRequest.objects.filter(is_deleted=False)
        stats = {
            'total_batches': ReturnRequest.objects.filter(
                is_deleted=False, batch_id__isnull=False
            ).exclude(batch_id='').values('batch_id').distinct().count(),
            'total_returns': all_returns.count(),
            'pending_count': all_returns.filter(return_status='pending').count(),
            'refunded_count': all_returns.filter(return_status='refunded').count(),
        }

        context = {
            'batches': batches_page,
            'individual_returns': individual_qs[:20],
            'search_query': search_query,
            'status_filter': status_filter,
            'status_choices': ReturnRequest.RETURN_STATUS_CHOICES,
            'condition_choices': ReturnRequest.CONDITION_CHOICES,
            'trash_count': trash_count,
            'stats': stats,
            'view_mode': 'batches',
        }

    return render(request, 'returns/list.html', context)


@login_required
@permission_required('can_create_returns')
def return_create(request):
    """Create return request with barcode scanning support"""
    if request.method == 'POST':
        try:
            with transaction.atomic():
                # Get order ID
                order_id = request.POST.get('order_id')
                if not order_id:
                    messages.error(request, 'Order not selected')
                    return redirect('return_create')
                
                order = get_object_or_404(Order, id=order_id, is_deleted=False)
                
                # Get return details
                return_reason = request.POST.get('return_reason', '')
                refund_type = request.POST.get('refund_type', 'full_refund')
                customer_notes = request.POST.get('customer_notes', '')
                
                # Parse return items
                return_items_json = request.POST.get('return_items', '[]')
                return_items_data = json.loads(return_items_json)
                
                if not return_items_data:
                    messages.error(request, 'No items selected for return')
                    return redirect('return_create')

                # Validate returnable quantities (prevent double returns)
                from django.db.models import Sum
                for item_data in return_items_data:
                    order_item = OrderItem.objects.get(id=item_data['order_item_id'])
                    qty = int(item_data['quantity'])

                    # Calculate already returned qty for this order item
                    already_returned = ReturnItem.objects.filter(
                        order_item=order_item,
                        return_request__is_deleted=False
                    ).aggregate(total=Sum('return_quantity'))['total'] or 0

                    returnable_qty = order_item.quantity - already_returned
                    if qty > returnable_qty:
                        messages.error(
                            request,
                            f'Cannot return {qty} of "{order_item.product_name}" — only {returnable_qty} returnable (already returned {already_returned})'
                        )
                        return redirect('return_create')

                # Calculate total refund
                total_refund = Decimal('0.00')
                for item_data in return_items_data:
                    order_item = OrderItem.objects.get(id=item_data['order_item_id'])
                    qty = int(item_data['quantity'])
                    total_refund += order_item.price * qty
                
                # Create return request
                return_request = ReturnRequest.objects.create(
                    order=order,
                    customer=order.customer,
                    customer_name=order.customer_name,
                    customer_phone=order.customer_phone,
                    customer_email=order.customer_email,
                    return_reason=return_reason,
                    refund_type=refund_type,
                    customer_notes=customer_notes,
                    total_amount=order.total_amount,
                    refund_amount=total_refund,
                    created_by=request.user,
                )
                
                # Create return items
                for item_data in return_items_data:
                    order_item = OrderItem.objects.get(id=item_data['order_item_id'])
                    qty = int(item_data['quantity'])
                    good_qty = int(item_data.get('good_qty', 0))
                    damaged_qty = int(item_data.get('damaged_qty', 0))

                    # Validation: neither can be negative
                    good_qty = max(0, good_qty)
                    damaged_qty = max(0, damaged_qty)

                    # Validation: sum should not exceed return quantity
                    if (good_qty + damaged_qty) > qty:
                        good_qty = 0
                        damaged_qty = 0

                    ReturnItem.objects.create(
                        return_request=return_request,
                        order_item=order_item,
                        product=order_item.product,
                        product_variation=order_item.product_variation,
                        product_name=order_item.product_name,
                        product_sku=order_item.product_sku,
                        quantity=order_item.quantity,
                        price=order_item.price,
                        total=order_item.total,
                        return_quantity=qty,
                        good_qty=good_qty,
                        damaged_qty=damaged_qty,
                        refund_amount=order_item.price * qty
                    )
                
                # Log activity
                ReturnActivityLog.objects.create(
                    return_request=return_request,
                    user=request.user,
                    action_type='created',
                    description=f'Return request {return_request.rma_number} created for order {order.order_number}'
                )
                
                messages.success(request, f'Return request {return_request.rma_number} created successfully!')
                return redirect('returns_list')
                
        except Exception as e:
            messages.error(request, f'Error creating return: {str(e)}')
            import traceback
            traceback.print_exc()
            return redirect('return_create')
    
    # GET request
    # Get recent delivered, shipped, or processing orders for scanning
    recent_orders = Order.objects.filter(
        is_deleted=False
    ).select_related('customer', 'created_by').prefetch_related(
        'items__product',
        'items__product_variation'
    ).order_by('-created_at')[:50]
    
    # If order_id is provided in GET, load that order
    order = None
    order_items = []
    if request.GET.get('order_id'):
        try:
            order = Order.objects.select_related('customer').prefetch_related(
                'items__product',
                'items__product_variation'
            ).get(id=request.GET.get('order_id'), is_deleted=False)
            order_items = order.items.all()
        except Order.DoesNotExist:
            messages.error(request, 'Order not found or not in returnable status')
    
    context = {
        'recent_orders': recent_orders,
        'order': order,
        'order_items': order_items,
        'reason_choices': ReturnRequest.RETURN_REASON_CHOICES,
        'refund_type_choices': ReturnRequest.REFUND_TYPE_CHOICES,
    }
    
    return render(request, 'returns/create.html', context)


@login_required
@permission_required('can_create_returns')
def bulk_return_create(request):
    """Create multiple return requests at once from scanned orders"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
        orders_data = data.get('orders', [])
        common_reason = data.get('return_reason', '')
        common_refund_type = data.get('refund_type', 'full_refund')
        common_notes = data.get('customer_notes', '')

        if not orders_data:
            return JsonResponse({'success': False, 'error': 'No orders provided'})

        # Generate batch ID for grouping
        batch_id = f"BATCH-{timezone.now().strftime('%Y%m%d%H%M%S')}-{len(orders_data)}"

        created_returns = []
        errors = []

        with transaction.atomic():
            for order_entry in orders_data:
                try:
                    order_id = order_entry.get('order_id')
                    order = Order.objects.get(id=order_id, is_deleted=False)
                    items_data = order_entry.get('items', [])

                    if not items_data:
                        errors.append(f'No items selected for order {order.order_number}')
                        continue

                    # Validate returnable quantities
                    valid = True
                    for item_data in items_data:
                        order_item = OrderItem.objects.get(id=item_data['order_item_id'])
                        qty = int(item_data['quantity'])
                        already_returned = ReturnItem.objects.filter(
                            order_item=order_item,
                            return_request__is_deleted=False
                        ).aggregate(total=Sum('return_quantity'))['total'] or 0
                        returnable_qty = order_item.quantity - already_returned
                        if qty > returnable_qty:
                            errors.append(
                                f'Cannot return {qty} of "{order_item.product_name}" in {order.order_number} — only {returnable_qty} returnable'
                            )
                            valid = False
                            break

                    if not valid:
                        continue

                    # Calculate total refund
                    total_refund = Decimal('0.00')
                    for item_data in items_data:
                        order_item = OrderItem.objects.get(id=item_data['order_item_id'])
                        qty = int(item_data['quantity'])
                        total_refund += order_item.price * qty

                    # Use per-order reason/refund if set, otherwise common values
                    reason = order_entry.get('return_reason', '') or common_reason
                    refund_type = order_entry.get('refund_type', '') or common_refund_type
                    notes = order_entry.get('customer_notes', '') or common_notes

                    return_request = ReturnRequest.objects.create(
                        order=order,
                        customer=order.customer,
                        customer_name=order.customer_name,
                        customer_phone=order.customer_phone,
                        customer_email=order.customer_email,
                        return_reason=reason,
                        refund_type=refund_type,
                        customer_notes=notes,
                        total_amount=order.total_amount,
                        refund_amount=total_refund,
                        created_by=request.user,
                        batch_id=batch_id,
                    )

                    for item_data in items_data:
                        order_item = OrderItem.objects.get(id=item_data['order_item_id'])
                        qty = int(item_data['quantity'])
                        good_qty = max(0, int(item_data.get('good_qty', 0)))
                        damaged_qty = max(0, int(item_data.get('damaged_qty', 0)))

                        if (good_qty + damaged_qty) > qty:
                            good_qty = 0
                            damaged_qty = 0

                        ReturnItem.objects.create(
                            return_request=return_request,
                            order_item=order_item,
                            product=order_item.product,
                            product_variation=order_item.product_variation,
                            product_name=order_item.product_name,
                            product_sku=order_item.product_sku,
                            quantity=order_item.quantity,
                            price=order_item.price,
                            total=order_item.total,
                            return_quantity=qty,
                            good_qty=good_qty,
                            damaged_qty=damaged_qty,
                            refund_amount=order_item.price * qty
                        )

                    ReturnActivityLog.objects.create(
                        return_request=return_request,
                        user=request.user,
                        action_type='created',
                        description=f'Return request {return_request.rma_number} created via bulk return for order {order.order_number}'
                    )

                    created_returns.append(return_request.rma_number)

                except Order.DoesNotExist:
                    errors.append(f'Order ID {order_entry.get("order_id")} not found')
                except Exception as e:
                    errors.append(f'Error processing order: {str(e)}')

        if created_returns:
            messages.success(
                request,
                f'Successfully created {len(created_returns)} return request(s): {", ".join(created_returns)}'
            )
        if errors:
            for err in errors:
                messages.warning(request, err)

        return JsonResponse({
            'success': len(created_returns) > 0,
            'created': len(created_returns),
            'errors': errors,
            'rma_numbers': created_returns,
            'batch_id': batch_id if created_returns else None
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def api_get_order_by_barcode(request):
    """AJAX endpoint to fetch order data by barcode/order number"""
    import logging
    logger = logging.getLogger(__name__)
    
    barcode = request.GET.get('barcode', '').strip()
    
    if not barcode:
        return JsonResponse({'success': False, 'error': 'No barcode provided'})
    
    try:
        logger.info(f"API request for barcode: {barcode}")
        # Build query - search by order number
        order = Order.objects.filter(
            order_number__iexact=barcode,  # Case-insensitive match
            is_deleted=False
        ).first()
        
        # If not found, return helpful error
        if not order:
            # Check if order exists but is deleted
            deleted_order = Order.objects.filter(order_number__iexact=barcode, is_deleted=True).first()
            if deleted_order:
                return JsonResponse({
                    'success': False,
                    'error': f'Order "{barcode}" is in trash'
                })

            return JsonResponse({
                'success': False,
                'error': f'Order "{barcode}" not found'
            })
        
        # Calculate already-returned quantities per order item
        from django.db.models import Sum
        already_returned = {}
        existing_returns = ReturnItem.objects.filter(
            order_item__order=order,
            return_request__is_deleted=False
        ).values('order_item_id').annotate(
            total_returned=Sum('return_quantity')
        )
        for r in existing_returns:
            already_returned[r['order_item_id']] = r['total_returned']

        # Prepare order items
        items = []
        try:
            for item in order.items.all():
                try:
                    returned_so_far = already_returned.get(item.id, 0)
                    returnable_qty = item.quantity - returned_so_far

                    # Skip fully returned items
                    if returnable_qty <= 0:
                        continue
                    # Get SKU and barcode with safe defaults
                    sku = item.product_sku or ''
                    barcode_val = ''
                    
                    # Try to get barcode from variation first
                    if item.product_variation and item.product_variation.barcode:
                        barcode_val = item.product_variation.barcode
                    elif item.product and item.product.barcode:
                        barcode_val = item.product.barcode
                    
                    # Get SKU from variation if not set on item
                    if not sku and item.product_variation:
                        sku = item.product_variation.sku or ''
                    
                    items.append({
                        'id': item.id,
                        'product_name': item.product_name or 'Unknown',
                        'product_sku': sku,
                        'product_barcode': barcode_val,
                        'price': str(item.price or 0),
                        'quantity': item.quantity or 1,
                        'returnable_qty': returnable_qty,
                        'already_returned': returned_so_far,
                        'product_variation': item.product_variation.sku if item.product_variation else None
                    })
                except Exception as e:
                    logger.warning(f"Error building item {item.id}: {str(e)}")
                    returned_so_far = already_returned.get(item.id, 0)
                    returnable_qty = (item.quantity or 1) - returned_so_far
                    if returnable_qty <= 0:
                        continue
                    items.append({
                        'id': item.id,
                        'product_name': item.product_name or 'Unknown',
                        'product_sku': item.product_sku or '',
                        'product_barcode': '',
                        'price': str(item.price or 0),
                        'quantity': item.quantity or 1,
                        'returnable_qty': returnable_qty,
                        'already_returned': returned_so_far,
                        'product_variation': None
                    })
        except Exception as e:
            logger.error(f"Error processing items for order {order.id}: {str(e)}")
            items = []
        
        # If no returnable items remain, inform the user
        if not items:
            return JsonResponse({
                'success': False,
                'error': f'All items in order "{order.order_number}" have already been returned'
            })

        return JsonResponse({
            'success': True,
            'order': {
                'id': order.id,
                'order_number': order.order_number,
                'customer_name': order.customer_name or 'N/A',
                'customer_phone': order.customer_phone or 'N/A',
                'customer_email': order.customer_email or '',
                'created_at': order.created_at.strftime('%b %d, %Y') if order.created_at else '',
                'total_amount': str(order.total_amount or 0),
                'items': items
            }
        }, status=200)
        
    except Exception as e:
        import traceback
        logger.error(f"API Error: {str(e)}")
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': f'Server error: {str(e)}'
        }, status=500)


@login_required
@permission_required('can_view_returns')
def return_detail(request, return_id):
    """View return request details and update status"""
    
    return_request = get_object_or_404(
        ReturnRequest.objects.select_related(
            'order', 'customer', 'created_by', 'approved_by', 'quality_checked_by'
        ).prefetch_related('items', 'activity_logs'),
        id=return_id,
        is_deleted=False  # Only show non-deleted returns
    )
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        try:
            if action == 'approve':
                return_request.return_status = 'approved'
                return_request.approved_by = request.user
                return_request.approved_at = timezone.now()
                return_request.save()
                
                ReturnActivityLog.objects.create(
                    return_request=return_request,
                    user=request.user,
                    action_type='approved',
                    description=f'Return approved by {request.user.username}'
                )
                
                messages.success(request, '✅ Return request approved!')
                
            elif action == 'reject':
                rejection_reason = request.POST.get('rejection_reason', '')
                return_request.return_status = 'rejected'
                return_request.rejection_reason = rejection_reason
                return_request.approved_by = request.user
                return_request.approved_at = timezone.now()
                return_request.save()
                
                ReturnActivityLog.objects.create(
                    return_request=return_request,
                    user=request.user,
                    action_type='rejected',
                    description=f'Return rejected: {rejection_reason}'
                )
                
                messages.warning(request, '⚠️ Return request rejected!')
                
            elif action == 'mark_received':
                return_request.return_status = 'received'
                return_request.save()
                
                ReturnActivityLog.objects.create(
                    return_request=return_request,
                    user=request.user,
                    action_type='received',
                    description='Returned items received at warehouse'
                )
                
                messages.success(request, '✅ Return marked as received!')
                
            elif action == 'quality_check':
                condition = request.POST.get('condition_received')
                quality_notes = request.POST.get('quality_check_notes', '')

                return_request.return_status = 'inspecting'
                return_request.condition_received = condition
                return_request.quality_check_notes = quality_notes
                return_request.quality_checked_by = request.user
                return_request.quality_checked_at = timezone.now()
                return_request.save()

                # Update per-item good_qty and damaged_qty from quality check
                for item in return_request.items.all():
                    good_key = f'good_qty_{item.id}'
                    damaged_key = f'damaged_qty_{item.id}'
                    if good_key in request.POST or damaged_key in request.POST:
                        new_good = int(request.POST.get(good_key, item.good_qty))
                        new_damaged = int(request.POST.get(damaged_key, item.damaged_qty))

                        # Validation
                        new_good = max(0, new_good)
                        new_damaged = max(0, new_damaged)
                        if (new_good + new_damaged) > item.return_quantity:
                            new_good = item.good_qty
                            new_damaged = item.damaged_qty

                        item.good_qty = new_good
                        item.damaged_qty = new_damaged
                        item.save()

                ReturnActivityLog.objects.create(
                    return_request=return_request,
                    user=request.user,
                    action_type='quality_checked',
                    description=f'Quality check completed. Condition: {condition}. Per-item good/damaged quantities verified.'
                )

                messages.success(request, 'Quality check completed! Good/Damaged quantities updated.')
                
            elif action == 'process_refund':
                refund_amount = Decimal(request.POST.get('refund_amount', '0'))
                restocking_fee = Decimal(request.POST.get('restocking_fee', '0'))
                
                return_request.refund_amount = refund_amount
                return_request.restocking_fee = restocking_fee
                return_request.return_status = 'refunded'
                return_request.refunded_at = timezone.now()
                return_request.save()
                
                # ✅ Update order status to 'returned' with Setup link
                from .models import Setup
                order = return_request.order
                order.status = 'returned'
                order.order_status = 'returned'
                
                # Link to the "Returned" Setup object
                try:
                    returned_setup = Setup.objects.get(setup_type='status', name='Returned')
                    order.status_setup = returned_setup
                except Setup.DoesNotExist:
                    # Fallback: create it if it doesn't exist
                    returned_setup, _ = Setup.objects.get_or_create(
                        setup_type='status',
                        name='Returned',
                        defaults={'is_active': True}
                    )
                    order.status_setup = returned_setup
                
                order.save()
                
                # Restock items - only restock good_qty items
                restocked_count = 0
                damaged_count = 0

                for item in return_request.items.all():
                    restock_qty = item.good_qty  # Only restock good quantity

                    if restock_qty > 0:
                        if item.product_variation:
                            item.product_variation.stock += restock_qty
                            if item.product_variation.stock > 0:
                                item.product_variation.status = 'active'
                            item.product_variation.save()

                        if item.product:
                            if item.product.is_bundle:
                                # ── Bundle product: restock each component ──────
                                components = item.product.bundle_components.select_related('component_product').all()
                                for comp in components:
                                    comp_product = comp.component_product
                                    restore_qty = comp.quantity_required * restock_qty
                                    comp_product.stock += restore_qty
                                    if comp_product.stock > 0:
                                        if comp_product.low_stock_threshold and comp_product.stock <= comp_product.low_stock_threshold:
                                            comp_product.stock_status = 'low_stock'
                                        else:
                                            comp_product.stock_status = 'in_stock'
                                    comp_product.save(update_fields=['stock', 'stock_status'])
                            else:
                                # ── Simple product: restock directly ─────────────
                                item.product.stock += restock_qty
                                if item.product.stock > 0:
                                    if item.product.low_stock_threshold and item.product.stock <= item.product.low_stock_threshold:
                                        item.product.stock_status = 'low_stock'
                                    else:
                                        item.product.stock_status = 'in_stock'
                                item.product.save(update_fields=['stock', 'stock_status'])

                        item.restocked = True
                        item.restocked_at = timezone.now()
                        item.restocked_by = request.user
                        item.save()
                        restocked_count += restock_qty

                    damaged_count += item.damaged_qty

                restock_msg = f' | {restocked_count} good items restocked, {damaged_count} damaged items'
                ReturnActivityLog.objects.create(
                    return_request=return_request,
                    user=request.user,
                    action_type='refunded',
                    description=f'Refund processed: Rs. {refund_amount} (Restocking fee: Rs. {restocking_fee}){restock_msg}'
                )

                if restocked_count > 0:
                    messages.success(request, f'Refund of Rs. {refund_amount} processed. {restocked_count} good items restocked, {damaged_count} damaged items not restocked.')
                else:
                    messages.success(request, f'Refund of Rs. {refund_amount} processed. No good items to restock ({damaged_count} damaged items).')
                
            elif action == 'update_notes':
                admin_notes = request.POST.get('admin_notes', '')
                return_request.admin_notes = admin_notes
                return_request.save()
                
                ReturnActivityLog.objects.create(
                    return_request=return_request,
                    user=request.user,
                    action_type='notes_updated',
                    description='Admin notes updated'
                )
                
                messages.success(request, '✅ Notes updated!')
            
            return redirect('return_detail', return_id=return_request.id)
            
        except Exception as e:
            messages.error(request, f'❌ Error: {str(e)}')
            return redirect('return_detail', return_id=return_request.id)
    
    return_items = return_request.items.all()
    activity_logs = return_request.activity_logs.all()[:20]
    
    context = {
        'return_request': return_request,
        'return_items': return_items,
        'activity_logs': activity_logs,
        'condition_choices': ReturnRequest.CONDITION_CHOICES,
    }
    
    return render(request, 'returns/detail.html', context)


# TRASH MANAGEMENT VIEWS

@login_required
@permission_required('can_delete_returns')
def return_trash(request, return_id):
    """Move return to trash (soft delete)"""
    return_request = get_object_or_404(ReturnRequest, id=return_id, is_deleted=False)
    
    if request.method == 'POST':
        return_request.soft_delete(request.user)
        
        ReturnActivityLog.objects.create(
            return_request=return_request,
            user=request.user,
            action_type='trashed',
            description=f'Return {return_request.rma_number} moved to trash by {request.user.username}'
        )
        
        messages.success(request, f'✅ Return {return_request.rma_number} moved to trash!')
        return redirect('returns_list')
    
    context = {'return_request': return_request}
    return render(request, 'returns/trash_confirm.html', context)


@login_required
@permission_required('can_view_returns')
def returns_trash_list(request):
    """View all trashed returns"""
    
    trashed_returns = ReturnRequest.objects.filter(is_deleted=True).select_related(
        'order', 'customer', 'created_by', 'deleted_by'
    ).order_by('-deleted_at')
    
    # Search
    search_query = request.GET.get('search', '')
    if search_query:
        trashed_returns = trashed_returns.filter(
            Q(rma_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(customer_phone__icontains=search_query)
        )
    
    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(trashed_returns, 25)
    page_number = request.GET.get('page')
    returns_page = paginator.get_page(page_number)
    
    context = {
        'trashed_returns': returns_page,
        'search_query': search_query,
    }
    
    return render(request, 'returns/trash_list.html', context)


@login_required
@permission_required('can_delete_returns')
def return_restore(request, return_id):
    """Restore return from trash"""
    return_request = get_object_or_404(ReturnRequest, id=return_id, is_deleted=True)
    
    if request.method == 'POST':
        return_request.restore()
        
        ReturnActivityLog.objects.create(
            return_request=return_request,
            user=request.user,
            action_type='restored',
            description=f'Return {return_request.rma_number} restored from trash by {request.user.username}'
        )
        
        messages.success(request, f'✅ Return {return_request.rma_number} restored successfully!')
        return redirect('return_detail', return_id=return_request.id)
    
    context = {'return_request': return_request}
    return render(request, 'returns/restore_confirm.html', context)


@login_required
@admin_only
def return_permanent_delete(request, return_id):
    """Permanently delete return (Admin only)"""
    return_request = get_object_or_404(ReturnRequest, id=return_id, is_deleted=True)
    
    if request.method == 'POST':
        rma_number = return_request.rma_number
        return_request.delete()  # Permanent delete
        
        messages.success(request, f'✅ Return {rma_number} permanently deleted!')
        return redirect('returns_trash_list')
    
    context = {'return_request': return_request}
    return render(request, 'returns/permanent_delete_confirm.html', context)


@login_required
@admin_only
def returns_empty_trash(request):
    """Empty trash - permanently delete all trashed returns (Admin only)"""
    
    if request.method == 'POST':
        trashed_count = ReturnRequest.objects.filter(is_deleted=True).count()
        ReturnRequest.objects.filter(is_deleted=True).delete()
        
        messages.success(request, f'✅ {trashed_count} return(s) permanently deleted from trash!')
        return redirect('returns_trash_list')
    
    trashed_count = ReturnRequest.objects.filter(is_deleted=True).count()
    context = {'trashed_count': trashed_count}
    return render(request, 'returns/empty_trash_confirm.html', context)


# BULK ACTIONS

@login_required
def returns_bulk_action(request):
    """Handle bulk actions on returns"""

    if request.method == 'POST':
        return_ids = request.POST.getlist('return_ids')
        action = request.POST.get('bulk_action')
        batch_id = request.POST.get('batch_id', '')

        if not return_ids:
            messages.error(request, 'No returns selected!')
            return redirect('returns_list')

        returns = ReturnRequest.objects.filter(id__in=return_ids, is_deleted=False)
        count = returns.count()

        if action == 'trash':
            for return_request in returns:
                return_request.soft_delete(request.user)
                ReturnActivityLog.objects.create(
                    return_request=return_request,
                    user=request.user,
                    action_type='trashed',
                    description=f'Bulk moved to trash by {request.user.username}'
                )
            messages.success(request, f'{count} return(s) moved to trash!')

        elif action == 'approve':
            approved = returns.filter(return_status='pending')
            approved_count = approved.count()
            for ret in approved:
                ret.return_status = 'approved'
                ret.approved_by = request.user
                ret.approved_at = timezone.now()
                ret.save()
                ReturnActivityLog.objects.create(
                    return_request=ret,
                    user=request.user,
                    action_type='approved',
                    description=f'Bulk approved by {request.user.username}'
                )
            messages.success(request, f'{approved_count} return(s) approved!')

        elif action == 'reject':
            rejected = returns.filter(return_status='pending')
            rejected_count = rejected.count()
            for ret in rejected:
                ret.return_status = 'rejected'
                ret.approved_by = request.user
                ret.approved_at = timezone.now()
                ret.save()
                ReturnActivityLog.objects.create(
                    return_request=ret,
                    user=request.user,
                    action_type='rejected',
                    description=f'Bulk rejected by {request.user.username}'
                )
            messages.success(request, f'{rejected_count} return(s) rejected!')

        elif action == 'mark_received':
            received = returns.filter(return_status='approved')
            received_count = received.count()
            for ret in received:
                ret.return_status = 'received'
                ret.save()
                ReturnActivityLog.objects.create(
                    return_request=ret,
                    user=request.user,
                    action_type='received',
                    description=f'Bulk marked as received by {request.user.username}'
                )
            messages.success(request, f'{received_count} return(s) marked as received!')

        elif action == 'quality_check':
            inspected = returns.filter(return_status='received')
            inspected_count = inspected.count()
            for ret in inspected:
                ret.return_status = 'inspecting'
                ret.condition_received = 'opened'
                ret.quality_checked_by = request.user
                ret.quality_checked_at = timezone.now()
                ret.save()
                ReturnActivityLog.objects.create(
                    return_request=ret,
                    user=request.user,
                    action_type='quality_checked',
                    description=f'Bulk quality check completed by {request.user.username}'
                )
            messages.success(request, f'{inspected_count} return(s) quality checked!')

        elif action == 'process_refund':
            refunded_returns = returns.filter(return_status='inspecting')
            refunded_count = 0
            total_restocked = 0
            total_damaged = 0
            
            # Get the "Returned" Setup object once
            from .models import Setup
            try:
                returned_setup = Setup.objects.get(setup_type='status', name='Returned')
            except Setup.DoesNotExist:
                returned_setup, _ = Setup.objects.get_or_create(
                    setup_type='status',
                    name='Returned',
                    defaults={'is_active': True}
                )

            for ret in refunded_returns:
                ret.return_status = 'refunded'
                ret.refunded_at = timezone.now()
                ret.save()
                
                # ✅ Update order status to 'returned' with Setup link
                order = ret.order
                order.status = 'returned'
                order.order_status = 'returned'
                order.status_setup = returned_setup
                order.save()

                # Restock good items
                for item in ret.items.all():
                    restock_qty = item.good_qty
                    if restock_qty > 0:
                        if item.product_variation:
                            item.product_variation.stock += restock_qty
                            if item.product_variation.stock > 0:
                                item.product_variation.status = 'active'
                            item.product_variation.save()
                        if item.product:
                            item.product.stock += restock_qty
                            if item.product.stock > 0:
                                if item.product.low_stock_threshold and item.product.stock <= item.product.low_stock_threshold:
                                    item.product.stock_status = 'low_stock'
                                else:
                                    item.product.stock_status = 'in_stock'
                            item.product.save()
                        item.restocked = True
                        item.restocked_at = timezone.now()
                        item.restocked_by = request.user
                        item.save()
                        total_restocked += restock_qty
                    total_damaged += item.damaged_qty

                ReturnActivityLog.objects.create(
                    return_request=ret,
                    user=request.user,
                    action_type='refunded',
                    description=f'Bulk refund processed by {request.user.username}. Amount: Rs. {ret.refund_amount}'
                )
                refunded_count += 1

            messages.success(
                request,
                f'{refunded_count} return(s) refunded! {total_restocked} good items restocked, {total_damaged} damaged items.'
            )

        # Redirect back to batch detail if batch_id was provided
        if batch_id:
            from django.urls import reverse
            return redirect(f'{reverse("returns_list")}?batch={batch_id}')
        return redirect('returns_list')

    return redirect('returns_list')

@login_required
@require_POST
def returns_batch_bulk_action(request):
    """Handle bulk actions on entire batches from the main batch list.
    Supports AJAX (returns JSON) and regular form submission (redirects).
    """
    batch_ids = request.POST.getlist('batch_ids')
    action = request.POST.get('bulk_action')
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if not batch_ids:
        if is_ajax:
            return JsonResponse({'success': False, 'error': 'No batches selected!'})
        messages.error(request, 'No batches selected!')
        return redirect('returns_list')

    # Get all returns within the selected batches
    returns_qs = ReturnRequest.objects.filter(batch_id__in=batch_ids, is_deleted=False)
    count = 0
    msg = ''
    total_restocked = 0
    total_damaged = 0

    if action == 'trash':
        for ret in returns_qs:
            ret.soft_delete(request.user)
            ReturnActivityLog.objects.create(
                return_request=ret, user=request.user,
                action_type='trashed',
                description=f'Batch bulk trashed by {request.user.username}'
            )
            count += 1
        msg = f'{count} return(s) from {len(batch_ids)} batch(es) moved to trash!'

    elif action == 'approve':
        for ret in returns_qs.filter(return_status='pending'):
            ret.return_status = 'approved'
            ret.approved_by = request.user
            ret.approved_at = timezone.now()
            ret.save()
            ReturnActivityLog.objects.create(
                return_request=ret, user=request.user,
                action_type='approved',
                description=f'Batch bulk approved by {request.user.username}'
            )
            count += 1
        msg = f'{count} pending return(s) approved from {len(batch_ids)} batch(es)!'

    elif action == 'mark_received':
        for ret in returns_qs.filter(return_status='approved'):
            ret.return_status = 'received'
            ret.save()
            ReturnActivityLog.objects.create(
                return_request=ret, user=request.user,
                action_type='received',
                description=f'Batch bulk marked as received by {request.user.username}'
            )
            count += 1
        msg = f'{count} return(s) marked as received from {len(batch_ids)} batch(es)!'

    elif action == 'quality_check':
        qc_condition = request.POST.get('condition_received', 'opened')
        qc_notes = request.POST.get('quality_check_notes', '')
        for ret in returns_qs.filter(return_status='received'):
            ret.return_status = 'inspecting'
            ret.condition_received = qc_condition
            ret.quality_checked_by = request.user
            ret.quality_checked_at = timezone.now()
            if qc_notes:
                ret.quality_check_notes = qc_notes
            ret.save()
            ReturnActivityLog.objects.create(
                return_request=ret, user=request.user,
                action_type='quality_checked',
                description=f'Batch bulk quality check by {request.user.username} - Condition: {qc_condition}'
            )
            count += 1
        msg = f'{count} return(s) quality checked from {len(batch_ids)} batch(es)!'

    elif action == 'process_refund':
        from .models import Setup
        
        # Get the "Returned" Setup object once
        try:
            returned_setup = Setup.objects.get(setup_type='status', name='Returned')
        except Setup.DoesNotExist:
            returned_setup, _ = Setup.objects.get_or_create(
                setup_type='status',
                name='Returned',
                defaults={'is_active': True}
            )
        
        for ret in returns_qs.filter(return_status='inspecting'):
            ret.return_status = 'refunded'
            ret.refunded_at = timezone.now()
            ret.save()
            
            # ✅ Update order status to 'returned' with Setup link
            order = ret.order
            order.status = 'returned'
            order.order_status = 'returned'
            order.status_setup = returned_setup
            order.save()
            
            for item in ret.items.all():
                restock_qty = item.good_qty
                if restock_qty > 0:
                    if item.product_variation:
                        item.product_variation.stock += restock_qty
                        if item.product_variation.stock > 0:
                            item.product_variation.status = 'active'
                        item.product_variation.save()
                    if item.product:
                        item.product.stock += restock_qty
                        if item.product.stock > 0:
                            if item.product.low_stock_threshold and item.product.stock <= item.product.low_stock_threshold:
                                item.product.stock_status = 'low_stock'
                            else:
                                item.product.stock_status = 'in_stock'
                        item.product.save()
                    item.restocked = True
                    item.restocked_at = timezone.now()
                    item.restocked_by = request.user
                    item.save()
                    total_restocked += restock_qty
                total_damaged += item.damaged_qty
            ReturnActivityLog.objects.create(
                return_request=ret, user=request.user,
                action_type='refunded',
                description=f'Batch bulk refund by {request.user.username}. Amount: Rs. {ret.refund_amount}'
            )
            count += 1
        msg = f'{count} return(s) refunded from {len(batch_ids)} batch(es)! {total_restocked} good items restocked, {total_damaged} damaged.'

    if is_ajax:
        # Return updated batch stats so the UI can refresh badges
        updated_batches = {}
        for bid in batch_ids:
            batch_returns = ReturnRequest.objects.filter(batch_id=bid, is_deleted=False)
            updated_batches[bid] = {
                'total_returns': batch_returns.count(),
                'pending': batch_returns.filter(return_status='pending').count(),
                'approved': batch_returns.filter(return_status='approved').count(),
                'received': batch_returns.filter(return_status='received').count(),
                'inspecting': batch_returns.filter(return_status='inspecting').count(),
                'refunded': batch_returns.filter(return_status='refunded').count(),
                'rejected': batch_returns.filter(return_status='rejected').count(),
            }
        return JsonResponse({
            'success': True,
            'message': msg,
            'count': count,
            'action': action,
            'total_restocked': total_restocked,
            'total_damaged': total_damaged,
            'updated_batches': updated_batches,
        })

    messages.success(request, msg)
    return redirect('returns_list')

@login_required
@require_POST
def returns_trash_bulk_action(request):
    """Handle bulk actions on trashed returns"""
    return_ids = request.POST.getlist('return_ids')
    action = request.POST.get('bulk_action')
    
    if not return_ids:
        messages.error(request, '❌ No returns selected!')
        return redirect('returns_trash_list')
    
    try:
        returns = ReturnRequest.objects.filter(id__in=return_ids, is_deleted=True)
        count = returns.count()
        
        if count == 0:
            messages.error(request, 'No valid returns found!')
            return redirect('returns_trash_list')
        
        if action == 'restore':
            returns.update(is_deleted=False, deleted_at=None, deleted_by=None)
            
            # Log activity for each restored return
            for return_request in returns:
                ReturnActivityLog.objects.create(
                    return_request=return_request,
                    user=request.user,
                    action_type='restored',
                    description=f'Restored from trash by {request.user.username}'
                )
            
            messages.success(request, f'✅ {count} return(s) restored successfully!')
            
        elif action == 'permanent_delete':
            returns.delete()
            messages.success(request, f'✅ {count} return(s) permanently deleted!')
            
        else:
            messages.error(request, 'Invalid action selected!')
            
    except Exception as e:
        messages.error(request, f'Error processing bulk action: {str(e)}')
        import traceback
        traceback.print_exc()
    
    return redirect('returns_trash_list')

# phone search API
@login_required
@require_http_methods(["GET"])
def search_customer_by_phone(request):
    """Search customer by phone number"""
    phone = request.GET.get('phone', '').strip()
    
    if not phone:
        return JsonResponse({'success': False, 'message': 'Phone number required'})
    
    try:
        # Search in Order model for customer with this phone
        from .models import Order
        
        # Get the most recent order with this phone number
        order = Order.objects.filter(customer_phone=phone).order_by('-created_at').first()
        
        if order:
            return JsonResponse({
                'success': True,
                'customer': {
                    'name': order.customer_name,
                    'email': order.customer_email or '',
                    'phone': order.customer_phone,
                    'address': order.shipping_address or '',
                    'landmark': order.landmark or '',
                    'branch_city': order.branch_city or '',
                }
            })
        else:
            return JsonResponse({
                'success': False,
                'message': 'No customer found with this phone number'
            })
    
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error searching customer: {str(e)}'
        })



# Duplicate order check API
@login_required
@require_http_methods(["POST"])
def check_duplicate_order(request):
    """Check if a similar order exists within 24 hours for the same phone number with matching products and quantities"""
    try:
        data = json.loads(request.body)
        phone = (data.get('phone') or '').strip()
        cart_items = data.get('cart', [])

        if not phone or not cart_items:
            return JsonResponse({'is_duplicate': False})

        # Look for orders with the same phone in the last 24 hours
        from .models import Order, OrderItem
        cutoff_time = timezone.now() - timedelta(hours=24)
        recent_orders = Order.objects.filter(
            customer_phone=phone,
            created_at__gte=cutoff_time,
            is_deleted=False
        ).order_by('-created_at')

        # Build a set of (product_id, variation_id, quantity) from the current cart
        new_cart_set = set()
        for item in cart_items:
            product_id = int(item.get('id', 0))
            var_id = int(item['varId']) if item.get('varId') else None
            qty = int(item.get('qty', 1))
            new_cart_set.add((product_id, var_id, qty))

        for order in recent_orders:
            # Build the same set from the existing order's items
            existing_items = order.items.all()
            existing_set = set()
            for oi in existing_items:
                p_id = oi.product_id
                v_id = oi.product_variation_id
                existing_set.add((p_id, v_id, oi.quantity))

            # Check if the cart items match exactly
            if new_cart_set == existing_set:
                # Calculate time ago
                time_diff = timezone.now() - order.created_at
                minutes = int(time_diff.total_seconds() / 60)
                if minutes < 60:
                    time_ago = f"{minutes} minute{'s' if minutes != 1 else ''} ago"
                else:
                    hours = minutes // 60
                    time_ago = f"{hours} hour{'s' if hours != 1 else ''} ago"

                return JsonResponse({
                    'is_duplicate': True,
                    'order_id': order.id,
                    'order_number': order.order_number,
                    'order_status': order.order_status or 'processing',
                    'time_ago': time_ago,
                })

        return JsonResponse({'is_duplicate': False})

    except Exception as e:
        logger.error(f"Error checking duplicate order: {str(e)}")
        return JsonResponse({'is_duplicate': False})


# add custom product

@login_required
@require_http_methods(["POST"])
def create_custom_product(request):
    """
    Create a custom product for quick sales.
    This product is saved to inventory and can be used immediately.
    """
    try:
        # Get form data
        name = request.POST.get('name', '').strip()
        price = request.POST.get('price', '0')
        stock = request.POST.get('stock', '1')
        sku = request.POST.get('sku', f'CUSTOM-{int(timezone.now().timestamp())}')
        category_id = request.POST.get('category', '')
        stock_status = request.POST.get('stock_status', 'in_stock')
        description = request.POST.get('description', '')
        
        # Validate required fields
        if not name:
            return JsonResponse({
                'success': False,
                'message': 'Product name is required'
            })
        
        # Convert and validate price and stock
        try:
            price = Decimal(price)
            stock = int(stock)
            
            if price <= 0:
                return JsonResponse({
                    'success': False,
                    'message': 'Price must be greater than 0'
                })
            
            if stock < 0:
                return JsonResponse({
                    'success': False,
                    'message': 'Stock cannot be negative'
                })
                
        except (ValueError, InvalidOperation):
            return JsonResponse({
                'success': False,
                'message': 'Invalid price or stock value'
            })
        
        # Get or create category
        category = None
        if category_id:
            try:
                category = Category.objects.get(id=category_id)
            except Category.DoesNotExist:
                pass
        
        # If no category provided or not found, get/create "Custom" category
        if not category:
            category, _ = Category.objects.get_or_create(
                name='Custom',
                defaults={
                    'slug': 'custom',
                }
            )
        
        # Generate unique slug
        base_slug = slugify(sku)
        slug = base_slug
        counter = 1
        while Product.objects.filter(slug=slug).exists():
            slug = f'{base_slug}-{counter}'
            counter += 1
        
        # Create product
        product = Product.objects.create(
            name=name,
            slug=slug,
            description=description or f'Custom product - {name}',
            price=price,
            cost_price=Decimal('0'),  # No cost for custom products
            stock=stock,
            category=category,
            stock_status=stock_status,
            product_type='simple',  # Always simple product
            barcode=sku,  # Use SKU as barcode
            is_active=True,
            is_deleted=False,
            user=request.user,
            is_custom_product=True,  # Mark as custom product
        )
        
        # Handle image upload
        if 'image' in request.FILES:
            product.image = request.FILES['image']
            product.save()
        
        # Return success response
        return JsonResponse({
            'success': True,
            'message': f'Custom product "{name}" created successfully',
            'product': {
                'id': product.id,
                'name': product.name,
                'price': str(product.price),
                'stock': product.stock,
                'sku': sku,
                'image': product.image.url if product.image else None
            }
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'message': f'Error creating custom product: {str(e)}'
        }, status=500)
        
    
    
    
    # my name is milan

@login_required
@permission_required('can_view_ncm_orders')
def ncm_order_detail(request, order_id):
    """
    View detailed information about NCM order with activity logs
    """
    import requests
    from django.conf import settings
    
    order = get_object_or_404(Order, id=order_id, is_deleted=False)
    
    ncm_order_found = True
    ncm_validation_error = None
    
    # Check if order has NCM ID and validate it exists in NCM system
    if order.ncm_order_id:
        try:
            base_url = getattr(settings, 'NCM_API_BASE_URL', None)
            api_key = getattr(settings, 'NCM_API_KEY', None)
            
            if base_url and api_key:
                api_url = f"{base_url.rstrip('/')}/order/status"
                response = requests.get(
                    api_url,
                    params={'id': order.ncm_order_id},
                    headers={
                        'Authorization': f'Token {api_key}',
                        'Content-Type': 'application/json'
                    },
                    timeout=5  # Short timeout for validation
                )
                
                if response.status_code == 404:
                    ncm_order_found = False
                    ncm_validation_error = f'Order ID {order.ncm_order_id} not found in NCM system'
                    messages.error(request, f'❌ NCM Order ID {order.ncm_order_id} not found in NCM system')
                    messages.warning(request, f'⚠️ This order may have been deleted from NCM or the ID is invalid. Options: 1) Resend order to NCM, 2) Clear NCM ID and retry, 3) Check order details')
                elif response.status_code != 200:
                    ncm_validation_error = f'Unable to verify order status (HTTP {response.status_code})'
            else:
                ncm_validation_error = 'NCM API credentials not configured'
        except requests.exceptions.Timeout:
            ncm_validation_error = 'NCM validation timeout - server is not responding'
            logger.warning(f"NCM validation timeout for order {order.id}")
        except requests.exceptions.ConnectionError:
            ncm_validation_error = 'Cannot connect to NCM server - check your internet connection'
            logger.warning(f"NCM connection error for order {order.id}")
        except Exception as e:
            ncm_validation_error = f'Validation error: {str(e)[:100]}'
            logger.error(f"NCM validation error for order {order.id}: {str(e)}", exc_info=True)
    else:
        messages.warning(request, f'⚠️ Order {order.order_number} has not been sent to NCM yet.')
    
    # Get order items
    order_items = order.items.all().select_related('product')
    
    # Calculate subtotal
    subtotal = sum(item.total for item in order_items)
    
    # Get activity logs
    activity_logs = []
    try:
        from dashboard.models import OrderActivityLog
        activity_logs = OrderActivityLog.objects.filter(
            order=order
        ).select_related('user').order_by('-created_at')[:50]
    except Exception as e:
        pass

    context = {
        'order': order,
        'order_items': order_items,
        'activity_logs': activity_logs,
        'subtotal': subtotal,
        'ncm_order_found': ncm_order_found,
        'ncm_validation_error': ncm_validation_error,
    }
    
    return render(request, 'ncm_order_detail.html', context)


@login_required
@permission_required('can_view_ncm_orders')
def ncm_track_order(request, order_id):
    """
    Track NCM order status from NCM API and update local database
    """
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        
        # Check if order has NCM ID
        if not order.ncm_order_id:
            messages.error(request, f'❌ Order {order.order_number} has not been sent to NCM yet.')
            return redirect('logistics_orders_list')
        
        # Get NCM API settings
        base_url = getattr(settings, 'NCM_API_BASE_URL', None)
        api_key = getattr(settings, 'NCM_API_KEY', None)
        
        if not base_url or not api_key:
            messages.error(request, '❌ NCM API credentials not configured in settings.')
            return redirect('ncm_order_detail', order_id=order_id)
        
        # Build API URL
        api_url = f"{base_url.rstrip('/')}/order/status"
        
        
        # Call NCM tracking API
        response = requests.get(
            api_url,
            params={'id': order.ncm_order_id},
            headers={
                'Authorization': f'Token {api_key}',
                'Content-Type': 'application/json'
            },
            timeout=15
        )
        
        
        if response.status_code == 200:
            try:
                data = response.json()
                
                # NCM returns array of status history
                if data and isinstance(data, list) and len(data) > 0:
                    latest_status = data[0]
                    new_status = latest_status.get('status', '')
                    
                    
                    if new_status:
                        old_status = order.ncm_status
                        
                        # Update order status if changed
                        if new_status != old_status:
                            order.ncm_status = new_status
                            order.save()
                            
                            # Log activity
                            try:
                                from dashboard.models import OrderActivityLog
                                OrderActivityLog.objects.create(
                                    order=order,
                                    user=request.user,
                                    action_type='status_changed',
                                    description=f'NCM status updated from "{old_status}" to "{new_status}"',
                                    field_name='ncm_status',
                                    old_value=old_status or 'N/A',
                                    new_value=new_status
                                )
                            except Exception as e:
                                pass

                            messages.success(request, f'✅ Status updated to: {new_status}')
                        else:
                            messages.info(request, f'ℹ️ Current status: {new_status} (No change)')
                    else:
                        messages.warning(request, 'ℹ️ No status information in response')
                else:
                    messages.info(request, 'ℹ️ No tracking data available yet from NCM')
                    
            except ValueError as e:
                messages.error(request, f'❌ Invalid JSON response from NCM API')
        
        elif response.status_code == 404:
            # Order not found in NCM - provide recovery options
            messages.error(request, f'❌ NCM Order ID {order.ncm_order_id} not found in NCM system')
            messages.info(request, f'⚠️ This order may have been deleted from NCM or the ID is invalid. Options: 1) Resend order to NCM, 2) Clear NCM ID and retry, 3) Check order details')
        
        elif response.status_code == 401:
            messages.error(request, '❌ Authentication failed. Check NCM API key.')
        
        else:
            messages.error(request, f'❌ Failed to fetch tracking data (HTTP {response.status_code})')
        
    except requests.exceptions.Timeout:
        messages.error(request, '❌ Request timeout. NCM server is not responding.')
    
    except requests.exceptions.ConnectionError:
        messages.error(request, '❌ Cannot connect to NCM server. Check internet connection.')
    
    except Exception as e:
        messages.error(request, f'❌ Error: {str(e)}')
        import traceback
        traceback.print_exc()
    
    # Redirect back to detail page
    return redirect('ncm_order_detail', order_id=order_id)


@login_required
@permission_required('can_sync_ncm_orders')
def ncm_sync_all_statuses(request):
    """
    Sync status and delivery charges for all NCM orders (admin function)
    """
    if not request.user.is_staff:
        messages.error(request, '❌ Admin access required')
        return redirect('logistics_orders_list')
    
    try:
        from services.ncm_service import NCMService
        from decimal import Decimal
        
        ncm_service = NCMService()
        
        # Get all NCM orders
        ncm_orders = Order.objects.filter(
            is_deleted=False,
            logistics='ncm'
        ).exclude(
            Q(ncm_order_id__isnull=True) | Q(ncm_order_id='')
        )
        
        total = ncm_orders.count()
        updated = 0
        charges_updated = 0
        errors = 0
        
        for order in ncm_orders:
            try:
                # Call tracking for each order
                base_url = getattr(settings, 'NCM_API_BASE_URL', None)
                api_key = getattr(settings, 'NCM_API_KEY', None)
                
                if not base_url or not api_key:
                    continue
                
                api_url = f"{base_url.rstrip('/')}/order/status"
                
                response = requests.get(
                    api_url,
                    params={'id': order.ncm_order_id},
                    headers={
                        'Authorization': f'Token {api_key}',
                        'Content-Type': 'application/json'
                    },
                    timeout=10
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        latest_status = data[0]
                        new_status = latest_status.get('status', '')
                        
                        if new_status and new_status != order.ncm_status:
                            order.ncm_status = new_status
                            order.save(update_fields=['ncm_status', 'updated_at'])
                            updated += 1
                
                # ✅ Fetch and update delivery charge from NCM
                if not order.delivery_charge or order.delivery_charge == 0:
                    try:
                        details_result = ncm_service.get_order_details(order.ncm_order_id)
                        if details_result.get('success'):
                            details_data = details_result.get('data', {})
                            # Try multiple possible field names for delivery charge
                            delivery_charge = (details_data.get('chargeDetail') or 
                                             details_data.get('deliveryCharge') or 
                                             details_data.get('deliverycharge') or 
                                             details_data.get('delivery_charge') or 
                                             details_data.get('chargedetail') or 
                                             details_data.get('shippingCharge') or 
                                             details_data.get('shipping_charge') or 
                                             details_data.get('charge') or 
                                             details_data.get('amount') or 
                                             0)
                            
                            if delivery_charge and float(delivery_charge) > 0:
                                order.delivery_charge = Decimal(str(delivery_charge))
                                order.save(update_fields=['delivery_charge', 'updated_at'])
                                charges_updated += 1
                                logger.info(f"✅ Updated delivery charge for {order.order_number}: {delivery_charge}")
                    except Exception as e:
                        logger.warning(f"Could not fetch delivery charge for order {order.ncm_order_id}: {str(e)}")
                        # Don't fail the whole sync, just log and continue
                        pass
                        
            except Exception as e:
                logger.error(f"Error syncing order {order.ncm_order_id}: {str(e)}")
                errors += 1
                continue
        
        messages.success(request, f'✅ Synced {updated} statuses, {charges_updated} delivery charges out of {total} orders. Errors: {errors}')
    
    except Exception as e:
        messages.error(request, f'❌ Sync failed: {str(e)}')
        logger.error(f"Error in ncm_sync_all_statuses: {str(e)}")

    return redirect('logistics_orders_list')


@login_required
@permission_required('can_view_ncm_branches')
def ncm_branches_json(request):
    """
    Display NCM branches fetched from NCM API as HTML page or JSON API.
    Extracts all branch fields (Name, Code, Areas, Municipality, District, etc.)
    """
    import requests
    from django.conf import settings

    branches = []
    error_message = None
    districts = set()

    MUNICIPALITY_MAPPING = {
        'Arughat Tallo Bazar': 'AARUGHAT RURAL MUNICIPALITY',
        'Arughat': 'AARUGHAT RURAL MUNICIPALITY',
        'Amargadhi': 'AMARGADHI MUNICIPALITY',
        'Amargadhi-5': 'AMARGADHI MUNICIPALITY',
        'Dadeldhura Bazar': 'DADELDHURA MUNICIPALITY',
        'Shitaganga Rural Municipality': 'SHITAGANGA RURAL MUNICIPALITY',
        'Shitaganga': 'SHITAGANGA RURAL MUNICIPALITY',
        'Sunwarshi': 'SUNWARSHI MUNICIPALITY',
        'Chowk, Amargadhi': 'AMARGADHI MUNICIPALITY',
    }

    DISTRICT_TO_REGION = {
        'Taplejung': 'Koshi', 'Panchthar': 'Koshi', 'Ilam': 'Koshi', 'Jhapa': 'Koshi',
        'Morang': 'Koshi', 'Sunsari': 'Koshi', 'Dhankuta': 'Koshi', 'Terhathum': 'Koshi',
        'Bhojpur': 'Koshi', 'Sankhuwasabha': 'Koshi',

        'Saptari': 'Madhesh', 'Siraha': 'Madhesh', 'Dhanusa': 'Madhesh', 'Mahottari': 'Madhesh',
        'Rautahat': 'Madhesh', 'Bara': 'Madhesh', 'Parsa': 'Madhesh',

        'Kathmandu': 'Bagmati', 'Lalitpur': 'Bagmati', 'Bhaktapur': 'Bagmati', 'Nuwakot': 'Bagmati',
        'Rasuwa': 'Bagmati', 'Sindhuli': 'Bagmati', 'Kavre': 'Bagmati', 'Makwanpur': 'Bagmati',
        'Dolakha': 'Bagmati', 'Ramechhap': 'Bagmati',

        'Gorkha': 'Gandaki', 'Lamjung': 'Gandaki', 'Tanahu': 'Gandaki', 'Syangja': 'Gandaki',
        'Kaski': 'Gandaki', 'Manang': 'Gandaki', 'Mustang': 'Gandaki',

        'Nawalpur': 'Lumbini', 'Parasi': 'Lumbini', 'Rupandehi': 'Lumbini', 'Kapilvastu': 'Lumbini',
        'Arghakhanchi': 'Lumbini', 'Gulmi': 'Lumbini', 'Palpa': 'Lumbini',

        'Salyan': 'Karnali', 'Pyuthan': 'Karnali', 'Rolpa': 'Karnali', 'Rukum': 'Karnali',
        'Dailekh': 'Karnali', 'Jajarkot': 'Karnali', 'Jumla': 'Karnali', 'Dolpa': 'Karnali',
        'Humla': 'Karnali', 'Achham': 'Karnali',

        'Dadeldhura': 'Sudurpaschim', 'Baitadi': 'Sudurpaschim', 'Bajhang': 'Sudurpaschim',
        'Bajura': 'Sudurpaschim', 'Kailali': 'Sudurpaschim', 'Kanchanpur': 'Sudurpaschim',
        'Doti': 'Sudurpaschim',
    }

    MUNICIPALITY_TO_DISTRICT = {
        'Arughat Tallo Bazar': 'Gorkha', 'Arughat': 'Gorkha',
        'Shitaganga Rural Municipality-04': 'Arghakhanchi', 'Shitaganga': 'Arghakhanchi',
        'Amargadhi': 'Bajhang', 'Amargadhi Municipality': 'Bajhang',
        'Amarai Arghakhanchi': 'Arghakhanchi',
        'Amardaha': 'Morang', 'Subarnapur': 'Morang',
        'Jitpur Simara': 'Bara', 'Jitpur Simara Sub-Metropolitan City': 'Bara',
        'Sudhodhan Rural Municipality': 'Rupandehi',
        'Aanbuk Khaireni': 'Tanahu',
    }
    
    try:
        base_url = getattr(settings, 'NCM_API_BASE_URL_V2', None)
        if not base_url:
            base_url = getattr(settings, 'NCM_API_BASE_URL', None)

        api_key = getattr(settings, 'NCM_API_KEY', None)

        if not base_url or not api_key:
            error_message = 'NCM API not configured in settings'
        else:
            base_url = base_url.rstrip('/')
            api_url = f"{base_url}/branches"

            response = requests.get(
                api_url,
                headers={
                    'Authorization': f'Token {api_key}',
                    'Content-Type': 'application/json'
                },
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()

                if isinstance(data, list):
                    branches = data
                elif isinstance(data, dict):
                    if 'branches' in data:
                        branches = data['branches']
                    elif 'data' in data:
                        branches = data['data']
                    elif 'results' in data:
                        branches = data['results']
                    else:
                        branches = [data] if data else []

                formatted_branches = []
                for branch in branches:
                    if isinstance(branch, dict):
                        try:
                            code_value = str(branch.get('code') or branch.get('Code') or branch.get('id') or branch.get('ID') or '').strip()
                            name_value = str(branch.get('name') or branch.get('Name') or branch.get('branch_name') or branch.get('Branch_Name') or '').strip()

                            address = branch.get('address', '')
                            municipality_value = ''

                            for address_pattern, muni_name in MUNICIPALITY_MAPPING.items():
                                if address_pattern.lower() in address.lower():
                                    municipality_value = muni_name
                                    break

                            if not municipality_value:
                                municipality_value = address.split(',')[0].strip() if address else ''

                            district_value = str(
                                branch.get('district_name') or
                                branch.get('district') or
                                branch.get('District') or ''
                            ).strip()

                            region_value = str(
                                branch.get('province_name') or
                                branch.get('region') or
                                branch.get('Region') or ''
                            ).strip()

                            areas_value = str(branch.get('areas_covered') or branch.get('Areas_Covered') or branch.get('areas') or branch.get('Areas') or '').strip()
                            phone_value = str(branch.get('phone') or branch.get('Phone') or branch.get('PHONE') or branch.get('telephone') or '').strip()
                            coords_value = str(branch.get('coordinates') or branch.get('Coordinates') or branch.get('lat_long') or '').strip()
                            address_value = str(branch.get('address') or branch.get('Address') or branch.get('location') or '').strip()

                            formatted_branch = {
                                'code': code_value or 'N/A',
                                'name': name_value or 'N/A',
                                'areas_covered': areas_value or 'N/A',
                                'municipality': municipality_value or 'N/A',
                                'district': district_value or 'N/A',
                                'region': region_value or 'N/A',
                                'phone': phone_value or 'N/A',
                                'coordinates': coords_value or 'N/A',
                                'address': address_value or 'N/A',
                                'is_active': branch.get('is_active', True) or branch.get('active', True) or branch.get('Active', True) or True,
                            }

                            if formatted_branch['code'] != 'N/A' and formatted_branch['name'] != 'N/A':
                                formatted_branches.append(formatted_branch)
                                if formatted_branch['district'] != 'N/A':
                                    districts.add(formatted_branch['district'])
                        except Exception:
                            continue

                branches = formatted_branches

            elif response.status_code == 401:
                error_message = 'Authentication failed - Check NCM_API_KEY'
            elif response.status_code == 404:
                error_message = 'NCM API endpoint not found - Check NCM_API_BASE_URL'
            else:
                error_message = f'NCM API Error: HTTP {response.status_code}'

    except requests.exceptions.Timeout:
        error_message = 'Request timeout - NCM server not responding'
    except requests.exceptions.ConnectionError:
        error_message = 'Cannot connect to NCM server'
    except Exception as e:
        error_message = f'Error fetching branches: {str(e)}'
    
    # Return JSON if requested via API
    if request.headers.get('Accept') == 'application/json' or request.GET.get('format') == 'json':
        if error_message:
            return JsonResponse({'error': error_message, 'branches': []}, status=400)
        return JsonResponse({'branches': branches})
    
    # Otherwise return HTML page
    # Ensure districts is always a safe iterable for the template
    safe_districts = sorted(list(districts)) if districts else []
    
    context = {
        'branches': branches,
        'error_message': error_message,
        'page_title': 'NCM Branches',
        'districts': safe_districts,
    }
    return render(request, 'ncm_branches.html', context)



@login_required
def orders_bulk_ncm_send(request):
    """
    Bulk send multiple orders to NCM logistics
    """
    from ncm.models import NCMBulkLog, NCMBulkLogOrder, NCMBulkLogDetail

    if request.method != 'POST':
        messages.error(request, '❌ Invalid request method')
        return redirect('orders_list')

    try:
        # Get form data
        order_ids = request.POST.getlist('order_ids')
        from_branch = request.POST.get('from_branch', 'TINKUNE')
        delivery_type = request.POST.get('delivery_type', 'Door2Door')
        default_weight = float(request.POST.get('default_weight', 1.0))
        auto_set_logistics = request.POST.get('auto_set_logistics') == 'on'

        if not order_ids:
            messages.error(request, '❌ No orders selected')
            return redirect('orders_list')

        # Get orders
        orders = Order.objects.filter(id__in=order_ids, is_deleted=False)

        if not orders.exists():
            messages.error(request, '❌ No valid orders found')
            return redirect('orders_list')

        count = orders.count()

        # Create NCM Bulk Log
        bulk_log = NCMBulkLog.objects.create(
            batch_number=NCMBulkLog.generate_batch_number(),
            total_orders=count,
            status='processing',
            from_branch=from_branch,
            delivery_type=delivery_type,
            created_by=request.user,
        )
        NCMBulkLogDetail.objects.create(
            batch=bulk_log,
            action='batch_started',
            message=f'Bulk send started with {count} order(s) from {from_branch}',
            user=request.user,
        )

        # Track results
        success_count = 0
        skip_count = 0
        error_count = 0

        # Process each order
        for order in orders:
            result = send_single_order_to_ncm(
                request,
                order,
                from_branch=from_branch,
                delivery_type=delivery_type,
                default_weight=default_weight
            )

            if result['status'] == 'success':
                success_count += 1
                log_status = 'success'
                log_action = 'order_sent'
                # Auto-set logistics if enabled
                if auto_set_logistics and order.logistics != 'ncm':
                    order.logistics = 'ncm'
                    order.save()
            elif result['status'] == 'skipped':
                skip_count += 1
                log_status = 'skipped'
                log_action = 'order_skipped'
            else:
                error_count += 1
                log_status = 'failed'
                log_action = 'order_failed'

            # Create log entry for this order
            order.refresh_from_db()
            NCMBulkLogOrder.objects.create(
                batch=bulk_log,
                order=order,
                order_number=order.order_number or '',
                customer_name=order.customer_name or '',
                customer_phone=order.customer_phone or '',
                shipping_address=order.shipping_address or '',
                cod_amount=order.total_amount or 0,
                destination_branch=order.branch_city or '',
                ncm_order_id=order.ncm_order_id,
                status=log_status,
                message=result.get('message', ''),
            )
            NCMBulkLogDetail.objects.create(
                batch=bulk_log,
                action=log_action,
                order_number=order.order_number or '',
                message=result.get('message', ''),
                user=request.user,
            )

        # Update bulk log with final counts and status
        if error_count == count:
            final_status = 'failed'
        elif success_count == count:
            final_status = 'completed'
        elif success_count > 0:
            final_status = 'partial'
        else:
            final_status = 'failed'

        bulk_log.success_count = success_count
        bulk_log.failed_count = error_count
        bulk_log.skipped_count = skip_count
        bulk_log.status = final_status
        bulk_log.completed_at = timezone.now()
        bulk_log.save()

        NCMBulkLogDetail.objects.create(
            batch=bulk_log,
            action='batch_completed',
            message=f'Batch completed: {success_count} success, {error_count} failed, {skip_count} skipped',
            user=request.user,
        )

        # Show results
        if success_count > 0:
            messages.success(
                request,
                f"✅ Successfully sent {success_count} order(s) to NCM."
            )

        if skip_count > 0:
            messages.warning(
                request,
                f"⚠️ Skipped {skip_count} order(s) (Already sent or missing info)."
            )

        if error_count > 0:
            messages.error(
                request,
                f"❌ Failed {error_count} order(s)."
            )

    except Exception as e:
        messages.error(request, f'❌ Bulk send error: {str(e)}')
        import traceback
        traceback.print_exc()

    return redirect('orders_list')


@login_required
@permission_required('can_create_ncm_orders')
def ncm_single_order_send(request, order_id):
    """
    Send single order to NCM from order detail page
    """
    if request.method != 'POST':
        messages.error(request, '❌ Invalid request method')
        return redirect('order_detail', order_id=order_id)
    
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        
        result = send_single_order_to_ncm(request, order)
        
        if result['status'] == 'success':
            messages.success(request, f"✅ {result['message']}")
        elif result['status'] == 'skipped':
            messages.warning(request, f"⚠️ {result['message']}")
        else:
            messages.error(request, f"❌ {result['message']}")
    
    except Exception as e:
        messages.error(request, f'❌ Error: {str(e)}')
    
    return redirect('order_detail', order_id=order_id)


def send_single_order_to_ncm(request, order, from_branch='TINKUNE', delivery_type='Door2Door', default_weight=1.0):
    """
    Helper function to send single order to NCM - COMPLETE VERSION
    Returns: dict with 'status' and 'message'
    """
    import requests
    from django.conf import settings
    from django.utils import timezone
    
    try:
        # CHECK 1: Already sent?
        if hasattr(order, 'ncm_order_id') and order.ncm_order_id:
            return {
                'status': 'skipped',
                'message': f'Already sent (NCM ID: {order.ncm_order_id})'
            }
        
        # CHECK 2: Required fields
        missing = []
        if not getattr(order, 'customer_name', None):
            missing.append('customer_name')
        if not getattr(order, 'customer_phone', None):
            missing.append('customer_phone')
        if not getattr(order, 'shipping_address', None):
            missing.append('shipping_address')
        
        if missing:
            return {
                'status': 'skipped',
                'message': f'Missing required fields: {", ".join(missing)}'
            }
        
        # Get product name with variant info
        product_name = 'General Items'
        try:
            if hasattr(order, 'items'):
                items = order.items.select_related('product_variation').all()[:3]
                if items:
                    parts = []
                    for item in items:
                        qty = getattr(item, 'quantity', 1) or 1
                        name = item.product_name or 'Item'
                        var_name = item.variation_name or (item.product_variation.variation_name if item.product_variation else None)
                        if var_name:
                            name = f"{name} ({var_name})"
                        parts.append(f"{qty}x {name}")
                    product_name = ', '.join(parts)
                    total_items = order.items.count()
                    if total_items > 3:
                        product_name += f' and {total_items - 3} more'
        except Exception:
            pass
        
        # Get weight
        weight = default_weight
        if hasattr(order, 'package_weight') and order.package_weight:
            try:
                weight = float(order.package_weight)
            except:
                weight = default_weight
        
        # Get destination branch
        destination_branch = 'KATHMANDU'
        if hasattr(order, 'branch_city') and order.branch_city:
            destination_branch = str(order.branch_city).upper()
        
        # Get API credentials
        base_url = getattr(settings, 'NCM_API_BASE_URL', None)
        api_key = getattr(settings, 'NCM_API_KEY', None)
        
        if not base_url or not api_key:
            return {
                'status': 'error',
                'message': 'NCM API not configured in settings'
            }
        
        # Build API URL
        base_url = base_url.rstrip('/')
        api_url = f"{base_url}/order/create"
        
        # Build payload
        payload = {
            "name": str(order.customer_name)[:50],
            "phone": str(order.customer_phone),
            "phone2": "",
            "cod_charge": float(order.total_amount or 0),
            "address": str(order.shipping_address)[:200],
            "fbranch": from_branch,
            "branch": destination_branch,
            "package": str(product_name)[:50],
            "vref_id": str(order.order_number),
            "instruction": "",
            "delivery_type": delivery_type,
            "weight": weight
        }
        
        
        # Call NCM API
        response = requests.post(
            api_url,
            json=payload,
            headers={
                'Authorization': f'Token {api_key}',
                'Content-Type': 'application/json'
            },
            timeout=30
        )
        
        
        # Handle response
        if response.status_code == 200:
            try:
                data = response.json()
            except:
                return {
                    'status': 'error',
                    'message': 'Invalid JSON response from NCM'
                }
            
            # NCM SUCCESS: {"Message": "Order Successfully Created", "orderid": 747}
            if data.get('Message') == 'Order Successfully Created':
                ncm_id = data.get('orderid')
                
                if not ncm_id:
                    return {
                        'status': 'error',
                        'message': 'No order ID in NCM response'
                    }
                
                # Update order - assign as integer
                order.ncm_order_id = int(ncm_id)
                order.ncm_status = 'Pickup Order Created'
                order.ncm_created_at = timezone.now()
                order.ncm_from_branch = from_branch
                order.ncm_delivery_type = delivery_type
                order.ncm_destination_branch = destination_branch
                order.save()
                
                
                # Log activity
                try:
                    from dashboard.models import OrderActivityLog
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user if request else None,
                        action_type='status_changed',
                        description=f'Sent to NCM Logistics (ID: {ncm_id}, Branch: {from_branch})'
                    )
                except Exception as e:
                    pass

                return {
                    'status': 'success',
                    'message': f'Sent to NCM (ID: {ncm_id})'
                }
            else:
                error_msg = data.get('Error', data.get('message', str(data)))
                return {
                    'status': 'error',
                    'message': f'NCM Error: {error_msg}'
                }
        
        elif response.status_code == 400:
            try:
                data = response.json()
                errors = data.get('Error', {})
                if isinstance(errors, dict):
                    error_list = [f"{k}: {v}" for k, v in errors.items()]
                    error_msg = ", ".join(error_list)
                else:
                    error_msg = str(errors)
            except:
                error_msg = response.text[:200]
            
            return {
                'status': 'error',
                'message': f'Validation error: {error_msg}'
            }
        
        elif response.status_code == 401:
            return {
                'status': 'error',
                'message': 'Authentication failed - Check NCM_API_KEY'
            }
        
        elif response.status_code == 404:
            return {
                'status': 'error',
                'message': 'API endpoint not found - Check NCM_API_BASE_URL'
            }
        
        else:
            return {
                'status': 'error',
                'message': f'HTTP {response.status_code}'
            }
    
    except requests.exceptions.Timeout:
        return {
            'status': 'error',
            'message': 'Request timeout (30s)'
        }
    
    except requests.exceptions.ConnectionError:
        return {
            'status': 'error',
            'message': 'Cannot connect to NCM server'
        }
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            'status': 'error',
            'message': str(e)[:100]
        }





# ============================================================================
# NCM TRASH VIEWS
# ============================================================================

@login_required
@permission_required('can_view_ncm_trash')
def ncm_orders_trash(request):
    """
    NCM Orders Trash - Shows deleted NCM orders
    """
    # Get all deleted NCM orders
    orders = Order.objects.select_related('customer', 'created_by').filter(
        is_deleted=True,
        logistics='ncm',
        ncm_order_id__isnull=False
    ).order_by('-deleted_at')
    
    # Get filter parameters
    search_query = request.GET.get('search', '').strip()
    branch_filter = request.GET.get('branch', '').strip()
    
    # Search filter
    if search_query:
        orders = orders.filter(
            Q(order_number__icontains=search_query) |
            Q(ncm_order_id__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(customer_phone__icontains=search_query)
        )
    
    # Branch filter
    if branch_filter:
        orders = orders.filter(ncm_from_branch=branch_filter)
    
    # Get total count before pagination
    total_orders = orders.count()
    
    # Get unique branches for filter dropdown
    branches = Order.objects.filter(
        is_deleted=True,
        logistics='ncm'
    ).exclude(
        ncm_from_branch__isnull=True
    ).exclude(
        ncm_from_branch=''
    ).values_list('ncm_from_branch', flat=True).distinct().order_by('ncm_from_branch')
    
    # Pagination
    paginator = Paginator(orders, 25)
    page_number = request.GET.get('page', 1)
    orders_page = paginator.get_page(page_number)
    
    # Get product names
    order_products = {}
    for order in orders_page:
        try:
            first_item = order.items.first()
            if first_item:
                order_products[order.id] = first_item.product_name
            else:
                order_products[order.id] = "No products"
        except:
            order_products[order.id] = "No products"
    
    context = {
        'orders': orders_page,
        'total_orders': total_orders,
        'search_query': search_query,
        'branch_filter': branch_filter,
        'branches': list(branches),
        'order_products': order_products,
    }
    
    return render(request, 'ncm_orders_trash.html', context)


@login_required
@permission_required('can_delete_ncm_orders')
@require_http_methods(["POST"])
def ncm_order_move_to_trash(request, order_id):
    """
    Move NCM order to trash (soft delete)
    """
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        
        # Soft delete
        order.is_deleted = True
        order.deleted_at = timezone.now()
        order.deleted_by = request.user
        order.save()
        
        # Log activity
        try:
            from dashboard.models import OrderActivityLog
            OrderActivityLog.objects.create(
                order=order,
                user=request.user,
                action_type='status_changed',
                description=f'Order moved to trash by {request.user.username}'
            )
        except:
            pass
        
        messages.success(request, f'✅ Order {order.order_number} moved to trash successfully')
    
    except Exception as e:
        messages.error(request, f'❌ Error: {str(e)}')
    
    # Check referer to redirect appropriately
    referer = request.META.get('HTTP_REFERER', '')
    if 'ncm-orders' in referer or 'logistics/orders' in referer:
        return redirect('logistics_orders_list')
    else:
        return redirect('orders_list')


@login_required
@permission_required('can_view_ncm_trash')
@require_http_methods(["POST"])
def ncm_order_restore(request, order_id):
    """
    Restore NCM order from trash
    """
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=True)
        
        # Restore order
        order.is_deleted = False
        order.deleted_at = None
        order.deleted_by = None
        order.save()
        
        # Log activity
        try:
            from dashboard.models import OrderActivityLog
            OrderActivityLog.objects.create(
                order=order,
                user=request.user,
                action_type='status_changed',
                description=f'Order restored from trash by {request.user.username}'
            )
        except:
            pass
        
        messages.success(request, f'✅ Order {order.order_number} restored successfully')
    
    except Exception as e:
        messages.error(request, f'❌ Error: {str(e)}')
    
    return redirect('ncm_orders_trash')


@login_required
@permission_required('can_view_ncm_trash')
@require_http_methods(["POST"])
def ncm_order_permanent_delete(request, order_id):
    """
    Permanently delete NCM order (cannot be undone)
    """
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=True)
        
        order_number = order.order_number
        
        # Permanently delete
        order.delete()
        
        messages.success(request, f'✅ Order {order_number} permanently deleted')
    
    except Exception as e:
        messages.error(request, f'❌ Error: {str(e)}')
    
    return redirect('ncm_orders_trash')


@login_required
@permission_required('can_delete_ncm_orders')
@require_http_methods(["POST"])
def ncm_orders_bulk_trash_action(request):
    """
    Bulk actions for trash: restore or permanent delete
    """
    try:
        order_ids = request.POST.getlist('order_ids')
        action = request.POST.get('bulk_action')
        
        if not order_ids:
            messages.error(request, '❌ No orders selected')
            return redirect('ncm_orders_trash')
        
        if not action:
            messages.error(request, '❌ No action selected')
            return redirect('ncm_orders_trash')
        
        orders = Order.objects.filter(id__in=order_ids, is_deleted=True)
        count = orders.count()
        
        if action == 'restore':
            # Restore all selected orders
            for order in orders:
                order.is_deleted = False
                order.deleted_at = None
                order.deleted_by = None
                order.save()
                
                # Log activity
                try:
                    from dashboard.models import OrderActivityLog
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user,
                        action_type='status_changed',
                        description=f'Order restored from trash (bulk action)'
                    )
                except:
                    pass
            
            messages.success(request, f'✅ {count} order(s) restored successfully')
        
        elif action == 'permanent_delete':
            # Permanently delete all selected orders
            orders.delete()
            messages.success(request, f'✅ {count} order(s) permanently deleted')
        
        else:
            messages.error(request, f'❌ Invalid action: {action}')
    
    except Exception as e:
        messages.error(request, f'❌ Error: {str(e)}')
    
    return redirect('ncm_orders_trash')


@login_required
@permission_required('can_view_ncm_trash')
@require_http_methods(["POST"])
def ncm_orders_empty_trash(request):
    """
    Empty entire NCM trash (delete all trashed orders permanently)
    """
    if not request.user.is_staff:
        messages.error(request, '❌ Admin access required')
        return redirect('ncm_orders_trash')
    
    try:
        # Get all deleted NCM orders
        orders = Order.objects.filter(
            is_deleted=True,
            logistics='ncm'
        ).exclude(
            Q(ncm_order_id__isnull=True) | Q(ncm_order_id='')
        )
        
        count = orders.count()
        
        if count == 0:
            messages.info(request, 'ℹ️ Trash is already empty')
            return redirect('ncm_orders_trash')
        
        # Permanently delete all
        orders.delete()
        
        messages.success(request, f'✅ Trash emptied! {count} order(s) permanently deleted')
    
    except Exception as e:
        messages.error(request, f'❌ Error: {str(e)}')
    
    return redirect('ncm_orders_trash')


# NEW: SETUP MANAGEMENT VIEWS
@login_required
@permission_required('can_view_orders')
def setup_management(request):
    """Manage Payment, Status, Payment Status, and Order Source setups"""
    from .models import Setup

    # Get all setups grouped by type
    payment_setups = Setup.objects.filter(setup_type='payment').order_by('name')
    status_setups = Setup.objects.filter(setup_type='status').order_by('name')
    payment_status_setups = Setup.objects.filter(setup_type='payment_status').order_by('name')
    order_source_setups = Setup.objects.filter(setup_type='order_source').order_by('name')

    context = {
        'payment_setups': payment_setups,
        'status_setups': status_setups,
        'payment_status_setups': payment_status_setups,
        'order_source_setups': order_source_setups,
        'page_title': 'Setup Management',
    }

    return render(request, 'setup_management.html', context)


@login_required
@permission_required('can_create_orders')
def setup_add(request):
    """Add new setup (Payment or Status)"""
    from .models import Setup
    
    if request.method == 'POST':
        setup_type = request.POST.get('setup_type')
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        
        if not setup_type or not name:
            messages.error(request, '❌ Setup type and name are required!')
            return redirect('setup_management')
        
        # Check if setup already exists
        if Setup.objects.filter(setup_type=setup_type, name=name).exists():
            messages.error(request, f'❌ {name} already exists!')
            return redirect('setup_management')
        
        try:
            setup = Setup.objects.create(
                setup_type=setup_type,
                name=name,
                description=description,
                is_active=is_active
            )
            messages.success(request, f'✅ {name} setup created successfully!')
        except Exception as e:
            messages.error(request, f'❌ Error creating setup: {str(e)}')
        
        return redirect('setup_management')
    
    return redirect('setup_management')


@login_required
@permission_required('can_create_orders')
def setup_edit(request, setup_id):
    """Edit existing setup"""
    from .models import Setup
    
    setup = get_object_or_404(Setup, id=setup_id)
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        
        if not name:
            messages.error(request, '❌ Setup name is required!')
            return redirect('setup_management')
        
        # Check if name already exists (excluding current setup)
        if Setup.objects.filter(setup_type=setup.setup_type, name=name).exclude(id=setup_id).exists():
            messages.error(request, f'❌ {name} already exists!')
            return redirect('setup_management')
        
        try:
            setup.name = name
            setup.description = description
            setup.is_active = is_active
            setup.save()
            messages.success(request, f'✅ {name} setup updated successfully!')
        except Exception as e:
            messages.error(request, f'❌ Error updating setup: {str(e)}')
        
        return redirect('setup_management')
    
    return redirect('setup_management')


@login_required
@permission_required('can_delete_orders')
def setup_delete(request, setup_id):
    """Delete setup"""
    from .models import Setup
    
    setup = get_object_or_404(Setup, id=setup_id)
    setup_name = setup.name
    
    try:
        setup.delete()
        messages.success(request, f'✅ {setup_name} setup deleted successfully!')
    except Exception as e:
        messages.error(request, f'❌ Error deleting setup: {str(e)}')

    return redirect('setup_management')


@login_required
@permission_required('can_create_orders')
def setup_toggle_default(request, setup_id):
    """Toggle default status for a setup item. Only one default per setup_type."""
    from .models import Setup
    
    setup = get_object_or_404(Setup, id=setup_id)
    
    if setup.is_default:
        # Unset default
        setup.is_default = False
        setup.save()
        messages.success(request, f'✅ {setup.name} is no longer the default.')
    else:
        # Clear any existing default for this setup_type, then set this one
        Setup.objects.filter(setup_type=setup.setup_type, is_default=True).update(is_default=False)
        setup.is_default = True
        setup.save()
        messages.success(request, f'⭐ {setup.name} is now the default {setup.get_setup_type_display().lower()}.')
    
    return redirect('setup_management')


# ==================== NCM BULK ORDER LOG VIEWS ====================

@login_required
@permission_required('can_view_ncm_bulk_logs')
def ncm_bulk_log_detail(request, log_id):
    """View details of a single NCM bulk send batch"""
    from ncm.models import NCMBulkLog, NCMBulkLogOrder, NCMBulkLogDetail

    bulk_log = get_object_or_404(NCMBulkLog, id=log_id, is_deleted=False)
    batch_orders = NCMBulkLogOrder.objects.filter(batch=bulk_log)
    log_details = NCMBulkLogDetail.objects.filter(batch=bulk_log).order_by('timestamp')

    context = {
        'bulk_log': bulk_log,
        'batch_orders': batch_orders,
        'log_details': log_details,
    }
    return render(request, 'ncm_bulk_log_detail.html', context)


@login_required
@permission_required('can_manage_ncm_bulk_logs')
def ncm_bulk_log_trash(request, log_id):
    """Move a bulk log to trash (soft delete)"""
    from ncm.models import NCMBulkLog

    if request.method == 'POST':
        bulk_log = get_object_or_404(NCMBulkLog, id=log_id, is_deleted=False)
        bulk_log.is_deleted = True
        bulk_log.deleted_at = timezone.now()
        bulk_log.save()
        messages.success(request, f'Batch "{bulk_log.batch_number}" moved to trash.')

    return redirect('logistics_bulk_logs_list')


@login_required
@permission_required('can_manage_ncm_bulk_logs')
def ncm_bulk_logs_bulk_action(request):
    """Handle bulk actions on NCM bulk logs"""
    from ncm.models import NCMBulkLog

    if request.method == 'POST':
        action = request.POST.get('bulk_action')
        log_ids = request.POST.getlist('log_ids')

        if not log_ids:
            messages.warning(request, 'No batches selected.')
            return redirect('logistics_bulk_logs_list')

        if action == 'move_to_trash':
            count = NCMBulkLog.objects.filter(id__in=log_ids, is_deleted=False).update(
                is_deleted=True,
                deleted_at=timezone.now()
            )
            messages.success(request, f'{count} batch(es) moved to trash.')

    return redirect('logistics_bulk_logs_list')


# ==================== LOW STOCK ALERT SETTINGS ====================
@login_required
def low_stock_settings(request):
    """Display all products (with variations) with editable low stock threshold inputs"""
    products = Product.objects.filter(is_deleted=False).prefetch_related(
        'variations', 'bundle_components__component_product'
    ).order_by('name')

    search = request.GET.get('search', '')
    if search:
        products = products.filter(
            Q(name__icontains=search) | Q(barcode__icontains=search) |
            Q(variations__sku__icontains=search) | Q(variations__variation_name__icontains=search)
        ).distinct()

    category_filter = request.GET.get('category', '')
    if category_filter:
        products = products.filter(category_id=category_filter)

    status_filter = request.GET.get('status', '')
    if status_filter == 'low':
        products = products.filter(
            Q(low_stock_threshold__gt=0, stock__gt=0, stock__lte=F('low_stock_threshold')) |
            Q(variations__low_stock_threshold__gt=0, variations__stock__gt=0, variations__stock__lte=F('variations__low_stock_threshold'))
        ).distinct()
    elif status_filter == 'ok':
        products = products.filter(stock__gt=0).filter(
            Q(stock__gt=F('low_stock_threshold')) | Q(low_stock_threshold=0)
        )
    elif status_filter == 'out':
        products = products.filter(Q(stock=0) | Q(variations__stock=0)).distinct()

    categories = Category.objects.all()

    # Counts for summary (products + variations) — bundle-aware
    all_products = Product.objects.filter(is_deleted=False)
    all_variations = ProductVariation.objects.filter(product__is_deleted=False)
    total = all_products.count()
    total_variations = all_variations.count()

    # Non-bundle counts via DB
    non_bundle_all = all_products.exclude(product_type='bundle')
    low_count_nb = non_bundle_all.filter(
        low_stock_threshold__gt=0, stock__lte=F('low_stock_threshold'), stock__gt=0
    ).count()
    out_count_nb = non_bundle_all.filter(stock=0).count()

    # Bundle counts via Python
    bundle_all = all_products.filter(product_type='bundle').prefetch_related(
        'bundle_components__component_product'
    )
    low_count_b = 0
    out_count_b = 0
    for bp in bundle_all:
        bstock = bp.available_stock
        if bstock == 0:
            out_count_b += 1
        elif bp.low_stock_threshold > 0 and bstock <= bp.low_stock_threshold:
            low_count_b += 1

    low_count = low_count_nb + low_count_b
    low_variation_count = all_variations.filter(
        low_stock_threshold__gt=0, stock__lte=F('low_stock_threshold'), stock__gt=0
    ).count()
    out_count = out_count_nb + out_count_b

    paginator = Paginator(products, 50)
    page = request.GET.get('page', 1)
    products_page = paginator.get_page(page)

    context = {
        'products': products_page,
        'categories': categories,
        'search': search,
        'category_filter': category_filter,
        'status_filter': status_filter,
        'total_products': total,
        'total_variations': total_variations,
        'low_stock_count': low_count + low_variation_count,
        'out_of_stock_count': out_count,
    }
    return render(request, 'low_stock_settings.html', context)


@login_required
@require_POST
def save_low_stock_thresholds(request):
    """Bulk save low stock threshold values for all products and variations"""
    updated = 0
    for key, value in request.POST.items():
        if key.startswith('threshold_'):
            product_id = key.replace('threshold_', '')
            try:
                product = Product.objects.get(id=product_id)
                threshold = int(value) if value else 0
                if threshold < 0:
                    threshold = 0
                if product.low_stock_threshold != threshold:
                    product.low_stock_threshold = threshold
                    product.save(update_fields=['low_stock_threshold'])
                    updated += 1
            except (Product.DoesNotExist, ValueError):
                continue
        elif key.startswith('var_threshold_'):
            variation_id = key.replace('var_threshold_', '')
            try:
                variation = ProductVariation.objects.get(id=variation_id)
                threshold = int(value) if value else 0
                if threshold < 0:
                    threshold = 0
                if variation.low_stock_threshold != threshold:
                    variation.low_stock_threshold = threshold
                    variation.save(update_fields=['low_stock_threshold'])
                    updated += 1
            except (ProductVariation.DoesNotExist, ValueError):
                continue

    messages.success(request, f'Thresholds updated for {updated} item(s).')
    return redirect('low_stock_settings')


@login_required
def low_stock_alerts(request):
    """Display all products and variations that are currently below their low stock threshold"""
    # Non-bundle low stock products via DB
    low_stock_products_nb = list(Product.objects.filter(
        is_deleted=False,
        low_stock_threshold__gt=0, stock__lte=F('low_stock_threshold'), stock__gt=0
    ).exclude(product_type='bundle').annotate(
        deficit=F('low_stock_threshold') - F('stock')
    ).select_related('category').order_by('stock'))

    # Bundle low stock products via Python
    bundle_products_alert = Product.objects.filter(
        is_deleted=False, product_type='bundle', low_stock_threshold__gt=0
    ).prefetch_related('bundle_components__component_product').select_related('category')
    bundle_low = []
    bundle_out = []
    for bp in bundle_products_alert:
        bstock = bp.available_stock
        if bstock == 0:
            bp.deficit = bp.low_stock_threshold
            bundle_out.append(bp)
        elif bstock <= bp.low_stock_threshold:
            bp.deficit = bp.low_stock_threshold - bstock
            bundle_low.append(bp)

    low_stock_products = sorted(
        low_stock_products_nb + bundle_low, key=lambda p: p.available_stock
    )

    out_of_stock_products_nb = list(Product.objects.filter(
        is_deleted=False, stock=0,
        low_stock_threshold__gt=0
    ).exclude(product_type='bundle').select_related('category').order_by('name'))

    out_of_stock_products = sorted(
        out_of_stock_products_nb + bundle_out, key=lambda p: p.name
    )

    # Variation alerts
    low_stock_variations = ProductVariation.objects.filter(
        product__is_deleted=False,
        low_stock_threshold__gt=0, stock__lte=F('low_stock_threshold'), stock__gt=0
    ).annotate(
        deficit=F('low_stock_threshold') - F('stock')
    ).select_related('product', 'product__category').order_by('stock')

    out_of_stock_variations = ProductVariation.objects.filter(
        product__is_deleted=False, stock=0,
        low_stock_threshold__gt=0
    ).select_related('product', 'product__category').order_by('product__name')

    # Group low-stock variations by product id
    low_var_by_product = {}
    for var in low_stock_variations:
        low_var_by_product.setdefault(var.product_id, []).append(var)

    # Group out-of-stock variations by product id
    out_var_by_product = {}
    for var in out_of_stock_variations:
        out_var_by_product.setdefault(var.product_id, []).append(var)

    # Attach variations to low stock products
    for product in low_stock_products:
        product.alert_variations = low_var_by_product.get(product.id, [])

    # Attach variations to out of stock products
    for product in out_of_stock_products:
        product.alert_variations = out_var_by_product.get(product.id, [])

    # Find variable products with alert variations but not already in product lists
    low_product_ids = set(p.id for p in low_stock_products)
    out_product_ids = set(p.id for p in out_of_stock_products)

    # Variable products that have low-stock variations but product itself is not low stock
    extra_low_products = []
    for pid, vars_list in low_var_by_product.items():
        if pid not in low_product_ids:
            product = vars_list[0].product
            product.alert_variations = vars_list
            product.deficit = 0
            extra_low_products.append(product)

    # Variable products that have out-of-stock variations but product itself is not out of stock
    extra_out_products = []
    for pid, vars_list in out_var_by_product.items():
        if pid not in out_product_ids:
            product = vars_list[0].product
            product.alert_variations = vars_list
            extra_out_products.append(product)

    low_count = len(low_stock_products)
    out_count = len(out_of_stock_products)
    low_var_count = low_stock_variations.count()
    out_var_count = out_of_stock_variations.count()

    total_low = low_count + len(extra_low_products)
    total_out = out_count + len(extra_out_products)
    total_alert_count = total_low + total_out + low_var_count + out_var_count

    context = {
        'low_stock_products': list(low_stock_products) + extra_low_products,
        'out_of_stock_products': list(out_of_stock_products) + extra_out_products,
        'low_count': total_low,
        'out_count': total_out,
        'low_var_count': low_var_count,
        'out_var_count': out_var_count,
        'total_alert_count': total_alert_count,
        'play_sound': total_alert_count > 0,
    }

    from django.shortcuts import render
    return render(request, "low_stock_alerts.html", context)


# ==================== SALES REPORT ====================

@login_required
@permission_required('can_view_sales_reports')
def sales_report(request):
    """Comprehensive sales analytics with smart forecasting"""
    from django.db.models.functions import TruncDate, TruncHour, ExtractHour
    from collections import defaultdict
    import math

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ── Date Filtering ──
    period = request.GET.get('period', 'month')
    date_from_str = request.GET.get('date_from', '')
    date_to_str = request.GET.get('date_to', '')
    compare = request.GET.get('compare', '') == 'on'

    if period == 'today':
        date_from = today_start
        date_to = now
    elif period == 'yesterday':
        date_from = today_start - timedelta(days=1)
        date_to = today_start
    elif period == 'week':
        date_from = today_start - timedelta(days=7)
        date_to = now
    elif period == 'custom' and date_from_str and date_to_str:
        try:
            date_from = timezone.make_aware(datetime.strptime(date_from_str, '%Y-%m-%d'))
            date_to = timezone.make_aware(datetime.strptime(date_to_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59))
        except ValueError:
            date_from = today_start - timedelta(days=30)
            date_to = now
    else:  # month
        period = 'month'
        date_from = today_start - timedelta(days=30)
        date_to = now

    period_days = max((date_to - date_from).days, 1)

    # Previous period for comparison
    prev_from = date_from - timedelta(days=period_days)
    prev_to = date_from

    # ── Base Querysets ──
    orders_qs = Order.objects.filter(
        is_deleted=False,
        created_at__gte=date_from,
        created_at__lte=date_to,
    )
    prev_orders_qs = Order.objects.filter(
        is_deleted=False,
        created_at__gte=prev_from,
        created_at__lt=prev_to,
    )

    # ── 1. Stats Cards ──
    total_revenue = orders_qs.aggregate(t=Sum('total_amount'))['t'] or Decimal('0')
    total_orders = orders_qs.count()
    avg_order_value = (total_revenue / total_orders) if total_orders > 0 else Decimal('0')

    items_qs = OrderItem.objects.filter(
        order__is_deleted=False,
        order__created_at__gte=date_from,
        order__created_at__lte=date_to,
    )
    products_sold = items_qs.aggregate(t=Sum('quantity'))['t'] or 0

    # Previous period stats
    prev_revenue = prev_orders_qs.aggregate(t=Sum('total_amount'))['t'] or Decimal('0')
    prev_orders = prev_orders_qs.count()
    prev_items = OrderItem.objects.filter(
        order__is_deleted=False,
        order__created_at__gte=prev_from,
        order__created_at__lt=prev_to,
    ).aggregate(t=Sum('quantity'))['t'] or 0

    def calc_growth(current, previous):
        if previous and previous > 0:
            return round(float((current - previous) / previous * 100), 1)
        return 0

    revenue_growth = calc_growth(total_revenue, prev_revenue)
    orders_growth = calc_growth(total_orders, prev_orders)
    products_growth = calc_growth(products_sold, prev_items)

    # ── 2. Daily Sales Trend ──
    daily_sales_raw = (
        orders_qs
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(revenue=Sum('total_amount'), count=Count('id'))
        .order_by('day')
    )
    daily_labels = []
    daily_revenue_data = []
    daily_orders_data = []
    daily_breakdown = []
    for entry in daily_sales_raw:
        day_str = entry['day'].strftime('%b %d')
        daily_labels.append(day_str)
        daily_revenue_data.append(float(entry['revenue'] or 0))
        daily_orders_data.append(entry['count'])
        # Products sold that day
        day_products = OrderItem.objects.filter(
            order__is_deleted=False,
            order__created_at__date=entry['day'],
        ).aggregate(t=Sum('quantity'))['t'] or 0
        daily_breakdown.append({
            'date': entry['day'].strftime('%b %d, %Y'),
            'orders': entry['count'],
            'revenue': float(entry['revenue'] or 0),
            'products': day_products,
        })

    # Calculate daily growth
    for i, day in enumerate(daily_breakdown):
        if i > 0 and daily_breakdown[i - 1]['revenue'] > 0:
            day['growth'] = round((day['revenue'] - daily_breakdown[i - 1]['revenue']) / daily_breakdown[i - 1]['revenue'] * 100, 1)
        else:
            day['growth'] = 0

    # ── 3. Top 10 Products ──
    top_products_raw = (
        items_qs
        .filter(product__isnull=False)
        .values('product__id', 'product__name', 'product__cost_price', 'product__stock',
                'product__product_type', 'product_variation__id', 'product_variation__variation_name',
                'product_variation__stock')
        .annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total'),
        )
        .order_by('-qty_sold')[:10]
    )
    top_product_labels = []
    top_product_data = []
    top_products_table = []

    # Pre-fetch per-product 30-day daily sales for smart_daily_rate
    # Used by BOTH top products (Section 3) AND stock alerts (Section 6)
    from inventory.forecasting import (
        exponential_smoothing, smart_daily_rate,
        days_of_stock_remaining, restock_urgency,
    )
    _30d_start = today_start - timedelta(days=30)
    _all_daily_raw = (
        OrderItem.objects.filter(
            order__is_deleted=False,
            order__created_at__gte=_30d_start,
            product__isnull=False,
        )
        .annotate(day=TruncDate('order__created_at'))
        .values('product__id', 'product_variation__id', 'day')
        .annotate(day_qty=Sum('quantity'))
        .order_by('product__id', 'product_variation__id', 'day')
    )
    _all_day_map = defaultdict(dict)
    for _r in _all_daily_raw:
        _key = (_r['product__id'], _r['product_variation__id'])
        _all_day_map[_key][_r['day']] = _r['day_qty'] or 0

    def _build_sales_list(prod_id, var_id):
        """Build a 30-element daily sales list (oldest→newest, 0-fill)."""
        dm = _all_day_map.get((prod_id, var_id), {})
        sl = []
        for _off in range(30):
            _d = (_30d_start + timedelta(days=_off)).date()
            sl.append(dm.get(_d, 0))
        return sl

    for p in top_products_raw:
        label = p['product__name'] or ''
        variant = p['product_variation__variation_name'] or ''
        if variant:
            label = f"{label} ({variant})"
        top_product_labels.append((label or 'Unnamed')[:25])
        top_product_data.append(float(p['revenue'] or 0))

        cost = float(p['product__cost_price'] or 0)
        rev = float(p['revenue'] or 0)
        qty = p['qty_sold'] or 0
        profit_pct = round((rev - cost * qty) / rev * 100, 1) if rev > 0 else 0
        stock = p['product_variation__stock'] if p['product_variation__id'] else p['product__stock']
        stock = stock or 0

        # Days until stockout — hybrid EWS + spike detection
        _sales = _build_sales_list(p['product__id'], p['product_variation__id'])
        _rate = smart_daily_rate(_sales)
        days_left = days_of_stock_remaining(stock, _sales)
        _urgency = restock_urgency(days_left, lead_time_days=3)

        top_products_table.append({
            'name': p['product__name'] or 'Unnamed Product',
            'variant': variant,
            'product_type': p['product__product_type'] or 'simple',
            'qty_sold': qty,
            'revenue': rev,
            'profit_pct': profit_pct,
            'stock': stock,
            'avg_daily': round(_rate, 1),
            'days_left': days_left,
            'urgency': _urgency,
        })

    # Sort top products by days_left ascending (most urgent first)
    top_products_table.sort(key=lambda x: x['days_left'])

    # ── 4. Sales by Category ──
    category_sales = (
        items_qs
        .values('product__category__name')
        .annotate(revenue=Sum('total'))
        .order_by('-revenue')
    )
    category_labels = []
    category_data = []
    for c in category_sales:
        cat_name = c['product__category__name'] or 'Uncategorized'
        category_labels.append(cat_name)
        category_data.append(float(c['revenue'] or 0))

    # ── 5. Hourly Sales Pattern ──
    hourly_raw = (
        orders_qs
        .annotate(hour=ExtractHour('created_at'))
        .values('hour')
        .annotate(count=Count('id'), revenue=Sum('total_amount'))
        .order_by('hour')
    )
    hourly_map = {h['hour']: {'count': h['count'], 'revenue': float(h['revenue'] or 0)} for h in hourly_raw}
    hourly_labels = []
    hourly_data = []
    for h in range(24):
        hourly_labels.append(f'{h:02d}:00')
        hourly_data.append(hourly_map.get(h, {}).get('count', 0))

    # Best selling hour
    best_hour = max(range(24), key=lambda h: hourly_map.get(h, {}).get('count', 0)) if hourly_map else 14
    best_hour_end = best_hour + 1 if best_hour < 23 else 23

    # ── 6. AI Predictions & Insights (Hybrid EWS + Spike Detection) ──
    # (forecasting functions already imported in Section 3)

    # --- Revenue forecast using EWS on 30-day daily revenue ---
    last_30_start = _30d_start  # reuse from Section 3
    daily_rev_30 = (
        Order.objects.filter(
            is_deleted=False,
            created_at__gte=last_30_start,
            created_at__lte=now,
        )
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(rev=Sum('total_amount'), cnt=Count('id'))
        .order_by('day')
    )
    # Build a full 30-day list (0 for days with no orders)
    rev_by_day = {r['day']: float(r['rev'] or 0) for r in daily_rev_30}
    cnt_by_day = {r['day']: r['cnt'] for r in daily_rev_30}
    revenue_series = []
    orders_series = []
    for offset in range(30):
        d = (last_30_start + timedelta(days=offset)).date()
        revenue_series.append(rev_by_day.get(d, 0.0))
        orders_series.append(cnt_by_day.get(d, 0))

    avg_daily_revenue = exponential_smoothing(revenue_series, alpha=0.4)
    avg_daily_orders = exponential_smoothing(orders_series, alpha=0.4)

    forecast_7_revenue = round(avg_daily_revenue * 7, 2)
    forecast_30_revenue = round(avg_daily_revenue * 30, 2)

    # --- Stock runout alerts with EWS + spike detection ---
    stock_alerts = []

    # Get per-product name + stock via a lightweight query (FK fields only,
    # no denormalized fields — avoids GROUP BY split when product was renamed)
    product_stock_raw = (
        OrderItem.objects.filter(
            order__is_deleted=False,
            order__created_at__gte=last_30_start,
            product__isnull=False,
        )
        .values(
            'product__id', 'product__name', 'product__stock',
            'product_variation__id', 'product_variation__variation_name',
            'product_variation__stock',
        )
        .annotate(_cnt=Count('id'))  # force distinct grouping
        .order_by('product__id', 'product_variation__id')
    )

    # Build meta lookup (name + stock) keyed by (product_id, variation_id)
    product_meta = {}
    for row in product_stock_raw:
        key = (row['product__id'], row['product_variation__id'])
        if key not in product_meta:
            stock = (row['product_variation__stock']
                     if row['product_variation__id']
                     else row['product__stock']) or 0
            name = row['product__name'] or ''
            variant = row['product_variation__variation_name'] or ''
            display = f"{name} ({variant})" if variant else name
            product_meta[key] = {
                'name': display or 'Unnamed Product',
                'stock': stock,
            }

    # Iterate products that had any sales in the last 30 days
    # (reuse _all_day_map built in Section 3 — no duplicate query)
    for key, day_map in _all_day_map.items():
        meta = product_meta.get(key)
        if not meta:
            continue

        sales_list = _build_sales_list(key[0], key[1])

        rate = smart_daily_rate(sales_list)
        if rate <= 0:
            continue

        dl = days_of_stock_remaining(meta['stock'], sales_list)
        urgency = restock_urgency(dl, lead_time_days=3)

        if urgency != 'ok':
            reorder_qty = math.ceil(rate * 30)  # 30-day supply
            reorder_date = (now + timedelta(days=max(dl - 3, 0))).strftime('%b %d')

            stock_alerts.append({
                'name': meta['name'],
                'stock': meta['stock'],
                'days_left': dl,
                'avg_daily': round(rate, 1),
                'reorder_qty': reorder_qty,
                'reorder_date': reorder_date,
                'severity': 'danger' if urgency == 'critical' else (
                    'warning' if urgency in ('urgent', 'low') else 'warning'),
                'urgency': urgency,
            })
    stock_alerts.sort(key=lambda x: x['days_left'])

    # Trending products — compare last 7 days vs previous 7 days
    trending_products = []
    last_7_start = today_start - timedelta(days=7)
    prev_7_start = today_start - timedelta(days=14)
    last_7_sales = (
        OrderItem.objects.filter(order__is_deleted=False, order__created_at__gte=last_7_start,
                                 product__isnull=False)
        .values('product__id', 'product__name', 'product_variation__variation_name')
        .annotate(qty=Sum('quantity'))
    )
    prev_7_sales = (
        OrderItem.objects.filter(order__is_deleted=False, order__created_at__gte=prev_7_start,
                                 order__created_at__lt=last_7_start, product__isnull=False)
        .values('product__id', 'product__name', 'product_variation__variation_name')
        .annotate(qty=Sum('quantity'))
    )
    prev_7_map = {(p['product__id'], p['product_variation__variation_name']): p['qty'] for p in prev_7_sales}
    for item in last_7_sales:
        key = (item['product__id'], item['product_variation__variation_name'])
        prev_qty = prev_7_map.get(key, 0)
        curr_qty = item['qty'] or 0
        if prev_qty > 0:
            growth = round((curr_qty - prev_qty) / prev_qty * 100, 1)
        elif curr_qty > 0:
            growth = 100.0
        else:
            growth = 0
        if growth > 20:
            name = item['product__name'] or ''
            variant = item['product_variation__variation_name'] or ''
            display = f"{name} ({variant})" if variant else name
            trending_products.append({
                'name': display or 'Unnamed Product',
                'current_qty': curr_qty,
                'prev_qty': prev_qty,
                'growth': growth,
            })
    trending_products.sort(key=lambda x: x['growth'], reverse=True)
    trending_products = trending_products[:10]

    # Slow moving products
    slow_products = []
    all_active_products = Product.objects.filter(is_deleted=False, is_active=True, stock__gt=0)
    for product in all_active_products:
        last_sale = OrderItem.objects.filter(
            product=product, order__is_deleted=False
        ).order_by('-order__created_at').first()
        if last_sale:
            days_idle = (now - last_sale.order.created_at).days
        else:
            days_idle = (now - product.created_at).days
        if days_idle >= 15:
            slow_products.append({
                'name': product.name,
                'product_type': product.product_type,
                'last_sold': last_sale.order.created_at.strftime('%b %d, %Y') if last_sale else 'Never',
                'days_idle': days_idle,
                'stock': product.stock,
            })
    slow_products.sort(key=lambda x: x['days_idle'], reverse=True)
    slow_products = slow_products[:20]

    # Smart insights
    insights = []

    # Weekend vs weekday analysis
    weekend_orders = orders_qs.filter(created_at__week_day__in=[1, 7]).count()
    weekday_orders = orders_qs.exclude(created_at__week_day__in=[1, 7]).count()
    total_weekdays_in_range = max(period_days * 5 / 7, 1)
    total_weekends_in_range = max(period_days * 2 / 7, 1)
    if total_weekends_in_range > 0 and total_weekdays_in_range > 0:
        weekend_avg = weekend_orders / total_weekends_in_range
        weekday_avg = weekday_orders / total_weekdays_in_range
        if weekend_avg > weekday_avg * 1.1:
            pct = round((weekend_avg - weekday_avg) / weekday_avg * 100) if weekday_avg > 0 else 0
            insights.append({
                'icon': 'calendar-week',
                'color': 'info',
                'text': f'Weekend sales are {pct}% higher than weekdays - stock up before Friday',
            })
        elif weekday_avg > weekend_avg * 1.1:
            pct = round((weekday_avg - weekend_avg) / weekend_avg * 100) if weekend_avg > 0 else 0
            insights.append({
                'icon': 'briefcase',
                'color': 'info',
                'text': f'Weekday sales are {pct}% higher than weekends',
            })

    # Best selling time
    if hourly_map:
        insights.append({
            'icon': 'clock',
            'color': 'success',
            'text': f'Best selling time: {best_hour:02d}:00 - {best_hour_end:02d}:00',
        })

    # Revenue growth insight
    if revenue_growth > 0:
        insights.append({
            'icon': 'arrow-up',
            'color': 'success',
            'text': f'Revenue is up {revenue_growth}% compared to previous period',
        })
    elif revenue_growth < 0:
        insights.append({
            'icon': 'arrow-down',
            'color': 'danger',
            'text': f'Revenue is down {abs(revenue_growth)}% compared to previous period',
        })

    # Stock alert insight
    critical_alerts = [a for a in stock_alerts if a['urgency'] == 'critical']
    if critical_alerts:
        insights.append({
            'icon': 'exclamation-triangle',
            'color': 'danger',
            'text': f'{len(critical_alerts)} product(s) will run out within 3 days — restock immediately!',
        })

    # ── 7. Forecast data for chart (EWS-based) ──
    forecast_labels = []
    forecast_data = []
    for i in range(1, 8):
        future_date = now + timedelta(days=i)
        forecast_labels.append(future_date.strftime('%b %d'))
        forecast_data.append(round(avg_daily_revenue, 2))  # EWS-weighted daily avg

    # ── Build Context ──
    context = {
        # Filters
        'period': period,
        'date_from': date_from.strftime('%Y-%m-%d'),
        'date_to': date_to.strftime('%Y-%m-%d'),
        'compare': compare,

        # Stats
        'total_revenue': float(total_revenue),
        'total_orders': total_orders,
        'avg_order_value': round(float(avg_order_value), 2),
        'products_sold': products_sold,
        'revenue_growth': revenue_growth,
        'orders_growth': orders_growth,
        'products_growth': products_growth,

        # Charts (JSON)
        'daily_labels': json.dumps(daily_labels),
        'daily_revenue_data': json.dumps(daily_revenue_data),
        'daily_orders_data': json.dumps(daily_orders_data),
        'top_product_labels': json.dumps(top_product_labels),
        'top_product_data': json.dumps(top_product_data),
        'category_labels': json.dumps(category_labels),
        'category_data': json.dumps(category_data),
        'hourly_labels': json.dumps(hourly_labels),
        'hourly_data': json.dumps(hourly_data),
        'forecast_labels': json.dumps(forecast_labels),
        'forecast_data': json.dumps(forecast_data),

        # Tables
        'top_products_table': top_products_table,
        'slow_products': slow_products,
        'daily_breakdown': daily_breakdown,

        # Predictions
        'avg_daily_revenue': round(avg_daily_revenue, 2),
        'avg_daily_orders': round(avg_daily_orders, 1),
        'forecast_7_revenue': forecast_7_revenue,
        'forecast_30_revenue': forecast_30_revenue,
        'stock_alerts': stock_alerts,
        'trending_products': trending_products,
        'insights': insights,
    }

    return render(request, 'sales_report.html', context)


# ==================== DAILY SALES REPORT ====================

@login_required
@permission_required('can_view_daily_sales_reports')
def daily_sales_report(request):
    """Daily sales report — detailed breakdown for a specific date"""
    from django.db.models.functions import ExtractHour

    now = timezone.now()
    today = now.date()

    # Date selection
    date_str = request.GET.get('date', '')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = today
    else:
        selected_date = today

    day_start = timezone.make_aware(datetime.combine(selected_date, datetime.min.time()))
    day_end = timezone.make_aware(datetime.combine(selected_date, datetime.max.time()))

    # Previous day for comparison
    prev_date = selected_date - timedelta(days=1)
    prev_start = timezone.make_aware(datetime.combine(prev_date, datetime.min.time()))
    prev_end = timezone.make_aware(datetime.combine(prev_date, datetime.max.time()))

    # ── Orders for selected date ──
    orders_qs = Order.objects.filter(
        is_deleted=False,
        created_at__gte=day_start,
        created_at__lte=day_end,
    ).select_related('customer', 'created_by').prefetch_related('items__product', 'items__product_variation', 'activity_logs__user').order_by('-created_at')

    prev_orders_qs = Order.objects.filter(
        is_deleted=False,
        created_at__gte=prev_start,
        created_at__lte=prev_end,
    )

    # ── Summary Stats ──
    total_orders = orders_qs.count()
    total_revenue = orders_qs.aggregate(t=Sum('total_amount'))['t'] or Decimal('0')
    total_discount = orders_qs.aggregate(t=Sum('discount_amount'))['t'] or Decimal('0')
    total_shipping = orders_qs.aggregate(t=Sum('shipping_charge'))['t'] or Decimal('0')

    items_qs = OrderItem.objects.filter(
        order__is_deleted=False,
        order__created_at__gte=day_start,
        order__created_at__lte=day_end,
    )
    total_products_sold = items_qs.aggregate(t=Sum('quantity'))['t'] or 0
    total_unique_customers = orders_qs.values('customer_phone').distinct().count()

    # Previous day stats
    prev_revenue = prev_orders_qs.aggregate(t=Sum('total_amount'))['t'] or Decimal('0')
    prev_order_count = prev_orders_qs.count()

    def calc_growth(current, previous):
        if previous and previous > 0:
            return round(float((current - previous) / previous * 100), 1)
        return 0

    revenue_growth = calc_growth(total_revenue, prev_revenue)
    orders_growth = calc_growth(total_orders, prev_order_count)

    avg_order_value = round(float(total_revenue / total_orders), 2) if total_orders > 0 else 0

    # ── Payment Status Breakdown ──
    payment_breakdown = (
        orders_qs.values('payment_status')
        .annotate(count=Count('id'), amount=Sum('total_amount'))
        .order_by('-count')
    )

    # ── Payment Method Breakdown ──
    method_breakdown = (
        orders_qs.values('payment_method')
        .annotate(count=Count('id'), amount=Sum('total_amount'))
        .order_by('-count')
    )

    # ── Order Status Breakdown ──
    status_breakdown = (
        orders_qs.values('order_status')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    # ── Order Source Breakdown ──
    source_breakdown = (
        orders_qs.values('order_from')
        .annotate(count=Count('id'), amount=Sum('total_amount'))
        .order_by('-count')
    )

    # ── Staff/Created By Breakdown ──
    staff_breakdown = (
        orders_qs.values('created_by__username')
        .annotate(count=Count('id'), amount=Sum('total_amount'))
        .order_by('-count')
    )

    # ── Hourly Breakdown ──
    hourly_data = (
        orders_qs.annotate(hour=ExtractHour('created_at'))
        .values('hour')
        .annotate(count=Count('id'), revenue=Sum('total_amount'))
        .order_by('hour')
    )
    hourly_map = {h['hour']: {'count': h['count'], 'revenue': float(h['revenue'] or 0)} for h in hourly_data}
    hourly_labels = [f'{h:02d}:00' for h in range(24)]
    hourly_counts = [hourly_map.get(h, {}).get('count', 0) for h in range(24)]
    hourly_revenues = [hourly_map.get(h, {}).get('revenue', 0) for h in range(24)]

    peak_hour = max(range(24), key=lambda h: hourly_map.get(h, {}).get('count', 0)) if hourly_map else 0

    # ── Top Products Sold Today ──
    top_products = (
        items_qs
        .values('product__name', 'product_variation__variation_name', 'product__product_type')
        .annotate(qty=Sum('quantity'), revenue=Sum('total'))
        .order_by('-qty')[:15]
    )

    # ── Orders List for Table ──
    orders_list = []
    for order in orders_qs:
        order_items = []
        for item in order.items.all():
            order_items.append({
                'product_name': item.product_name or 'Unknown',
                'product_sku': item.product_sku or '',
                'variation_name': item.variation_name or '',
                'quantity': item.quantity,
                'price': float(item.price),
                'total': float(item.total),
            })
        item_names = ', '.join([
            f"{item.product_name} x{item.quantity}"
            for item in order.items.all()
        ])

        subtotal = sum(i['total'] for i in order_items)
        orders_list.append({
            'id': order.id,
            'order_number': order.order_number,
            'time': order.created_at.strftime('%I:%M %p'),
            'customer_name': order.customer_name,
            'customer_phone': order.customer_phone,
            'customer_email': order.customer_email or '',
            'shipping_address': order.shipping_address or '',
            'items_summary': item_names,
            'items': order_items,
            'items_count': len(order_items),
            'total_qty': sum(i['quantity'] for i in order_items),
            'subtotal': subtotal,
            'total_amount': float(order.total_amount),
            'discount': float(order.discount_amount or 0),
            'shipping': float(order.shipping_charge or 0),
            'tax_percent': float(order.tax_percent or 0),
            'payment_method': order.payment_method,
            'payment_status': order.payment_status,
            'order_status': order.order_status,
            'created_by': order.created_by.username if order.created_by else 'N/A',
            'order_from': order.order_from,
            'notes': order.notes or '',
            'branch_city': order.branch_city or '',
            'landmark': order.landmark or '',
            'logs': [
                {
                    'action': log.get_action_type_display(),
                    'field': log.field_name or '',
                    'old_value': log.old_value or '',
                    'new_value': log.new_value or '',
                    'description': log.description or '',
                    'user': log.user.username if log.user else 'System',
                    'time': log.created_at.strftime('%I:%M %p'),
                }
                for log in order.activity_logs.all()
            ],
        })

    # Top products with order numbers
    top_products_enriched = []
    for tp in top_products:
        prod_name = tp['product__name'] or 'Unknown'
        var_name = tp['product_variation__variation_name'] or ''
        # Find which orders contain this product
        order_nums = list(
            items_qs.filter(product__name=prod_name)
            .filter(
                **({'product_variation__variation_name': var_name} if var_name else {})
            )
            .values_list('order__order_number', flat=True)
            .distinct()
        )
        top_products_enriched.append({
            'product__name': prod_name,
            'product_variation__variation_name': var_name,
            'product__product_type': tp['product__product_type'] or 'simple',
            'qty': tp['qty'],
            'revenue': tp['revenue'],
            'orders': order_nums,
        })

    context = {
        'selected_date': selected_date.strftime('%Y-%m-%d'),
        'selected_date_display': selected_date.strftime('%B %d, %Y'),
        'is_today': selected_date == today,
        'prev_date': prev_date.strftime('%Y-%m-%d'),
        'next_date': (selected_date + timedelta(days=1)).strftime('%Y-%m-%d') if selected_date < today else '',

        # Stats
        'total_orders': total_orders,
        'total_revenue': float(total_revenue),
        'total_discount': float(total_discount),
        'total_shipping': float(total_shipping),
        'total_products_sold': total_products_sold,
        'total_unique_customers': total_unique_customers,
        'avg_order_value': avg_order_value,
        'revenue_growth': revenue_growth,
        'orders_growth': orders_growth,

        # Breakdowns
        'payment_breakdown': list(payment_breakdown),
        'method_breakdown': list(method_breakdown),
        'status_breakdown': list(status_breakdown),
        'source_breakdown': list(source_breakdown),
        'staff_breakdown': list(staff_breakdown),
        'top_products': top_products_enriched,

        # Hourly
        'hourly_labels': json.dumps(hourly_labels),
        'hourly_counts': json.dumps(hourly_counts),
        'hourly_revenues': json.dumps(hourly_revenues),
        'peak_hour': f'{peak_hour:02d}:00',

        # Orders table
        'orders_list': orders_list,
        'orders_list_json': json.dumps(orders_list, default=str),
        'payment_breakdown_json': json.dumps(list(payment_breakdown), default=str),
        'method_breakdown_json': json.dumps(list(method_breakdown), default=str),
        'status_breakdown_json': json.dumps(list(status_breakdown), default=str),
        'source_breakdown_json': json.dumps(list(source_breakdown), default=str),
        'staff_breakdown_json': json.dumps(list(staff_breakdown), default=str),
        'top_products_json': json.dumps(top_products_enriched, default=str),
    }

    return render(request, 'daily_sales_report.html', context)


# Financial Report View
@login_required
@permission_required('can_view_financial_reports')
@login_required
def financial_report(request):
    """
    Render the financial report page for NCM delivered orders with delivery charges and all required stats, charts, and tables.
    Data for charts/tables should be loaded via AJAX endpoints (to be implemented separately).
    """
    return render(request, 'financial_report.html')

@login_required
def financial_report_data(request):
    """
    Returns JSON data for the financial report page, filtered by period or custom date range.
    - Daily Summary: Shows all dispatched orders (from dispatch management) grouped by dispatch date
    - NCM Revenue: Shows dispatched orders with NCM data, delivery charges synced from NCM API
    - Stats/Charts: Based on all dispatched orders in the selected period
    - Filters: today, yesterday, last 7 days, last 30 days, custom, all
    """
    try:
        from decimal import Decimal
        
        # Get filter params
        period = request.GET.get('period', 'all')
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        tz = pytz.timezone('Asia/Kathmandu')
        now = datetime.now(tz)

        # Date range logic - matches frontend labels
        start = None
        end = None
        if period == 'all':
            # No date restriction
            pass
        elif period == 'today':
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == 'yesterday':
            yesterday = now - timedelta(days=1)
            start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
            end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == 'week':
            # Last 7 Days
            start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == 'month':
            # Last 30 Days
            start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == 'custom' and start_date and end_date:
            try:
                start = tz.localize(datetime.strptime(start_date, '%Y-%m-%d'))
                end = tz.localize(datetime.strptime(end_date, '%Y-%m-%d')).replace(hour=23, minute=59, second=59, microsecond=999999)
            except ValueError as e:
                logger.error(f"Date parsing error: {str(e)}")
                start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
                end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            # Fallback to last 30 days
            start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        from .models import Order, DispatchItem
        from django.db.models import Q

        logger.info(f"=== FINANCIAL REPORT: Period={period}, Start={start}, End={end} ===")

        # Get all dispatched orders (orders linked through DispatchItem)
        dispatched_order_ids = DispatchItem.objects.filter(
            order__isnull=False
        ).values_list('order_id', flat=True).distinct()

        orders_base = Order.objects.filter(
            id__in=dispatched_order_ids
        ).exclude(is_deleted=True)

        # Apply date filter using dispatch_date (fallback to created_at)
        if period == 'all':
            orders = orders_base
        elif start and end:
            orders = orders_base.filter(
                Q(dispatch_date__range=(start, end)) |
                Q(dispatch_date__isnull=True, created_at__range=(start, end))
            )
        else:
            orders = orders_base

        logger.info(f"Dispatched orders in period: {orders.count()}")

        # Sync NCM delivery charges from NCM API for orders that need it
        ncm_orders_to_sync = list(
            orders.filter(
                ncm_order_id__isnull=False
            ).filter(
                Q(delivery_charge__isnull=True) | Q(delivery_charge=0)
            ).values_list('id', 'ncm_order_id', named=True)[:50]
        )

        if ncm_orders_to_sync:
            try:
                from services.ncm_service import NCMService
                ncm_service = NCMService()

                for item in ncm_orders_to_sync:
                    try:
                        result = ncm_service.get_order_details(item.ncm_order_id)
                        if result.get('success') and result.get('data'):
                            ncm_data = result['data']
                            update_fields = []

                            # Extract delivery charge from NCM API response
                            charge = None
                            for field_name in ['chargeDetail', 'deliveryCharge', 'delivery_charge', 'charge', 'serviceCharge']:
                                val = ncm_data.get(field_name)
                                if val is not None:
                                    try:
                                        charge = Decimal(str(val))
                                        if charge > 0:
                                            break
                                    except (ValueError, TypeError):
                                        pass

                            order_obj = Order.objects.get(id=item.id)
                            if charge and charge > 0:
                                order_obj.delivery_charge = charge
                                update_fields.append('delivery_charge')

                            # Update NCM status if available
                            ncm_status_val = ncm_data.get('status') or ncm_data.get('Status', '')
                            if ncm_status_val and ncm_status_val != order_obj.ncm_status:
                                order_obj.ncm_status = ncm_status_val
                                update_fields.append('ncm_status')

                            if update_fields:
                                order_obj.save(update_fields=update_fields)
                                logger.info(f"Synced NCM data for order {item.ncm_order_id}: {update_fields}")
                    except Exception as e:
                        logger.warning(f"NCM sync failed for order {item.ncm_order_id}: {e}")
            except ImportError:
                logger.error("NCMService not available for sync")
            except Exception as e:
                logger.error(f"NCM batch sync error: {e}")

            # Re-query to get updated data after sync
            if period == 'all':
                orders = orders_base
            elif start and end:
                orders = orders_base.filter(
                    Q(dispatch_date__range=(start, end)) |
                    Q(dispatch_date__isnull=True, created_at__range=(start, end))
                )
            else:
                orders = orders_base

        # Prefetch related items and products for all orders (optimization)
        orders = orders.prefetch_related('items__product')


        # Helper function to convert Decimal to float
        def to_float(value):
            if isinstance(value, Decimal):
                return float(value)
            return float(value) if value else 0

        # Stats - calculate based on all dispatched orders in range
        total_revenue = orders.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        orders_delivered = orders.filter(ncm_status__icontains='delivered').count()
        orders_in_transit = orders.filter(ncm_status__icontains='transit').count()
        
        cod_collected = orders.filter(payment_method='cod', payment_status='paid').aggregate(total=Sum('cod_collected'))['total'] or Decimal('0')
        pending_payments = orders.filter(payment_status__in=['pending', 'partial']).aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        ncm_delivery_charges = orders.filter(delivery_charge__isnull=False).aggregate(total=Sum('delivery_charge'))['total'] or Decimal('0')
        
        # Calculate total expenses from product cost_price
        expenses = Decimal('0')
        for order in orders:
            for item in order.items.all():
                if item.product and item.product.cost_price:
                    expenses += (item.product.cost_price * item.quantity)
        
        net_profit = total_revenue - ncm_delivery_charges - expenses

        # Key metrics
        avg_order_value = total_revenue / orders.count() if orders.count() else Decimal('0')
        profit_margin = (net_profit / total_revenue * 100) if total_revenue else Decimal('0')
        cod_collection_rate = (cod_collected / total_revenue * 100) if total_revenue else Decimal('0')

        # Payment status breakdown
        paid_amt = orders.filter(payment_status='paid').aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        unpaid_amt = orders.filter(payment_status='pending').aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        partial_amt = orders.filter(payment_status='partial').aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        total_amt = paid_amt + unpaid_amt + partial_amt
        payment_status = [
            {"status": "Paid", "amount": to_float(paid_amt), "percent": to_float((paid_amt/total_amt*100) if total_amt else 0)},
            {"status": "Unpaid", "amount": to_float(unpaid_amt), "percent": to_float((unpaid_amt/total_amt*100) if total_amt else 0)},
            {"status": "Partial", "amount": to_float(partial_amt), "percent": to_float((partial_amt/total_amt*100) if total_amt else 0)},
        ]

        # Branch-wise summary - calculate with proper expense calculation
        branch_data = {}
        
        for order in orders:
            branch = order.ncm_destination_branch or 'Not Assigned'
            
            # Calculate cost for this order
            order_cost = Decimal('0')
            for item in order.items.all():
                if item.product and item.product.cost_price:
                    order_cost += (item.product.cost_price * item.quantity)
            
            if branch not in branch_data:
                branch_data[branch] = {
                    'ncm_destination_branch': branch,
                    'orders': 0,
                    'revenue': Decimal('0'),
                    'ncm_charges': Decimal('0'),
                    'expenses': Decimal('0'),
                }
            
            branch_data[branch]['orders'] += 1
            branch_data[branch]['revenue'] += order.total_amount or Decimal('0')
            branch_data[branch]['ncm_charges'] += order.delivery_charge or Decimal('0')
            branch_data[branch]['expenses'] += order_cost
        
        # Calculate profit for each branch and convert to list
        branch_with_summary = []
        for branch, data in sorted(branch_data.items(), key=lambda x: x[1]['revenue'], reverse=True):
            data['profit'] = data['revenue'] - data['ncm_charges'] - data['expenses']
            branch_with_summary.append(data)
        
        logger.info(f"Branch-wise orders (by ncm_destination_branch): {branch_with_summary}")
        
        branch_summary = branch_with_summary
        logger.info(f"Final branch_summary: {branch_summary}")
        
        # Convert Decimals in branch_summary
        for branch in branch_summary:
            branch['revenue'] = to_float(branch['revenue'])
            branch['ncm_charges'] = to_float(branch['ncm_charges'])
            branch['expenses'] = to_float(branch['expenses'])
            branch['profit'] = to_float(branch['profit'])

        # Daily summary - dispatched orders grouped by dispatch date
        daily_data = {}

        for order in orders:
            # Use dispatch_date if available, fallback to created_at
            order_date = (order.dispatch_date or order.created_at).date()

            order_cost = Decimal('0')
            for item in order.items.all():
                if item.product and item.product.cost_price:
                    order_cost += (item.product.cost_price * item.quantity)

            if order_date not in daily_data:
                daily_data[order_date] = {
                    'date': order_date,
                    'orders': 0,
                    'revenue': Decimal('0'),
                    'ncm_charges': Decimal('0'),
                    'expenses': Decimal('0'),
                }

            daily_data[order_date]['orders'] += 1
            daily_data[order_date]['revenue'] += order.total_amount or Decimal('0')
            daily_data[order_date]['ncm_charges'] += order.delivery_charge or Decimal('0')
            daily_data[order_date]['expenses'] += order_cost

        daily_summary = []
        for date, data in sorted(daily_data.items(), reverse=True):
            data['net_profit'] = data['revenue'] - data['ncm_charges'] - data['expenses']
            daily_summary.append(data)

        for day in daily_summary:
            day['revenue'] = to_float(day['revenue'])
            day['ncm_charges'] = to_float(day['ncm_charges'])
            day['expenses'] = to_float(day['expenses'])
            day['net_profit'] = to_float(day['net_profit'])
            if day['date']:
                day['date'] = str(day['date'])

        # NCM Delivery Revenue Table - only delivered + paid orders
        ncm_orders_in_period = orders.filter(
            ncm_order_id__isnull=False,
            ncm_status__icontains='delivered',
            payment_status='paid'
        )
        logger.info(f"NCM delivered & paid orders in period: {ncm_orders_in_period.count()}")

        ncm_revenue_list = list(
            ncm_orders_in_period.values(
                'delivered_at', 'id', 'order_number', 'customer__name', 'ncm_order_id', 'ncm_status',
                'total_amount', 'shipping_charge', 'delivery_charge', 'payment_status', 'payment_method', 'cod_collected', 'created_at'
            )
        )
        
        # Sort: put delivered orders first (non-null delivered_at), then pending (null delivered_at)
        # Within each group, sort by date descending
        def sort_key(order):
            is_pending = order['delivered_at'] is None
            if is_pending:
                # Pending orders sorted by created_at descending
                return (1, -order['created_at'].timestamp() if order['created_at'] else 0)
            else:
                # Delivered orders sorted by delivered_at descending
                return (0, -order['delivered_at'].timestamp())
        
        ncm_revenue = sorted(ncm_revenue_list, key=sort_key)
        
        # Convert Decimals and format dates in ncm_revenue
        for order in ncm_revenue:
            order['total_amount'] = to_float(order['total_amount'])
            order['shipping_charge'] = to_float(order['shipping_charge'])
            order['delivery_charge'] = to_float(order['delivery_charge'])
            order['cod_collected'] = to_float(order['cod_collected'])
            # Handle delivered_at — fall back to created_at for undelivered orders
            if order['delivered_at'] is not None:
                order['delivered_at'] = order['delivered_at'].isoformat()
            elif order['created_at'] is not None:
                order['delivered_at'] = order['created_at'].isoformat()
            else:
                order['delivered_at'] = None
            # Always convert created_at to string for JSON serialization
            if order['created_at'] is not None:
                order['created_at'] = order['created_at'].isoformat()
            else:
                order['created_at'] = None

        # Payment method pie chart
        payment_methods = list(
            orders.values('payment_method').annotate(amount=Sum('total_amount')).order_by('-amount')
        )
        
        # Convert Decimals in payment_methods
        for method in payment_methods:
            method['amount'] = to_float(method['amount'])
            if not method['payment_method']:
                method['payment_method'] = 'Unknown'

        # Daily revenue line chart - use dispatch_date with fallback to created_at
        from django.db.models.functions import Coalesce
        daily_revenue = list(
            orders.annotate(
                date=TruncDate(Coalesce('dispatch_date', 'created_at'))
            ).values('date').annotate(amount=Sum('total_amount')).order_by('date')
        )
        
        # Convert Decimals in daily_revenue
        for day in daily_revenue:
            day['amount'] = to_float(day['amount'])
            if day['date']:
                day['date'] = str(day['date'])

        # Revenue vs Expenses bar chart
        revenue_vs_expenses = [
            {"label": "Revenue", "amount": to_float(total_revenue)},
            {"label": "NCM Charges", "amount": to_float(ncm_delivery_charges)},
            {"label": "Expenses", "amount": to_float(expenses)},
            {"label": "Net Profit", "amount": to_float(net_profit)},
        ]

        return JsonResponse({
            "success": True,
            "stats": {
                "total_revenue": to_float(total_revenue),
                "orders_delivered": orders_delivered,
                "orders_in_transit": orders_in_transit,
                "cod_collected": to_float(cod_collected),
                "pending_payments": to_float(pending_payments),
                "net_profit": to_float(net_profit),
                "ncm_delivery_charges": to_float(ncm_delivery_charges),
            },
            "key_metrics": {
                "avg_order_value": to_float(avg_order_value),
                "total_ncm_charges": to_float(ncm_delivery_charges),
                "profit_margin": float(profit_margin),
                "cod_collection_rate": float(cod_collection_rate),
            },
            "payment_status": payment_status,
            "branch_summary": branch_summary,
            "daily_summary": daily_summary,
            "ncm_revenue": ncm_revenue,
            "payment_methods": payment_methods,
            "daily_revenue": daily_revenue,
            "revenue_vs_expenses": revenue_vs_expenses,
        })
    
    except Exception as e:
        logger.error(f"Error in financial_report_data: {str(e)}", exc_info=True)
        return JsonResponse({
            "success": False,
            "error": str(e),
            "message": "Failed to load financial report data. Please try again later."
        }, status=500)


# ==================== STAFF PERFORMANCE ANALYTICS ====================

@login_required(login_url='login')
def staff_performance_analytics(request):
    """Staff Performance Analytics Dashboard with Session Persistence"""

    if not (request.user.is_superuser or request.user.role == 'administrator' or request.user.can_view_staff_performance):
        messages.error(request, "You don't have permission to view Staff Performance.", extra_tags='permission_denied')
        return redirect('dashboard')

    # ========== SESSION PERSISTENCE LOGIC ==========
    # Check if user wants to clear filters
    clear_filters = request.GET.get('clear_filters', 'false') == 'true'
    
    if clear_filters:
        # Clear the session filters (including custom date range)
        for key in ('staff_performance_period', 'staff_performance_filter',
                    'staff_performance_custom_start', 'staff_performance_custom_end',
                    'staff_performance_filters_saved_at'):
            if key in request.session:
                del request.session[key]
        request.session.modified = True
        # Redirect without the clear_filters param
        return redirect('staff_performance_analytics')

    # Auto-clear staff performance filters after 1 hour
    import time as _time
    filters_saved_at = request.session.get('staff_performance_filters_saved_at')
    if filters_saved_at and (_time.time() - filters_saved_at) > 3600:
        for key in ('staff_performance_period', 'staff_performance_filter',
                    'staff_performance_custom_start', 'staff_performance_custom_end',
                    'staff_performance_filters_saved_at'):
            if key in request.session:
                del request.session[key]
        request.session.modified = True

    # Get filter params from query string (prioritize GET over session)
    period = request.GET.get('period', None)
    staff_filter = request.GET.get('staff_filter', None)
    custom_start_str = request.GET.get('custom_start', None)
    custom_end_str = request.GET.get('custom_end', None)

    # Fall back to session if not in GET params
    if period is None:
        period = request.session.get('staff_performance_period', 'this_month')
    if staff_filter is None:
        staff_filter = request.session.get('staff_performance_filter', 'all')
    if custom_start_str is None:
        custom_start_str = request.session.get('staff_performance_custom_start', '')
    if custom_end_str is None:
        custom_end_str = request.session.get('staff_performance_custom_end', '')

    # Save current filters to session with timestamp (1-hour expiry)
    request.session['staff_performance_period'] = period
    request.session['staff_performance_filter'] = staff_filter
    if custom_start_str:
        request.session['staff_performance_custom_start'] = custom_start_str
    if custom_end_str:
        request.session['staff_performance_custom_end'] = custom_end_str
    request.session['staff_performance_filters_saved_at'] = _time.time()
    request.session.modified = True  # Ensure session is saved
    
    # Calculate date range based on period
    today = timezone.now().date()

    # Helper to safely parse YYYY-MM-DD date strings
    def _parse_date(date_str):
        try:
            from datetime import date as _date
            return _date.fromisoformat(date_str.strip())
        except Exception:
            return None
    
    if period == 'today':
        start_date = today
        end_date = today
        date_range_text = f"Today ({today.strftime('%d %b %Y')})"
    elif period == 'last_30_days':
        start_date = today - timedelta(days=30)
        end_date = today
        date_range_text = f"{start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')}"
    elif period == 'ytd':
        start_date = today.replace(month=1, day=1)
        end_date = today
        current_year = today.year
        date_range_text = f"YTD {current_year} ({start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')})"
    elif period == 'custom':
        parsed_start = _parse_date(custom_start_str) if custom_start_str else None
        parsed_end = _parse_date(custom_end_str) if custom_end_str else None
        if parsed_start and parsed_end:
            # Ensure correct order and clamp end to today
            start_date = min(parsed_start, parsed_end)
            end_date = min(max(parsed_start, parsed_end), today)
        else:
            # Fallback to current month if custom dates are missing
            start_date = today.replace(day=1)
            end_date = today
        date_range_text = f"{start_date.strftime('%d %b %Y')} \u2013 {end_date.strftime('%d %b %Y')} (Custom)"
    else:  # this_month
        start_date = today.replace(day=1)
        end_date = today
        date_range_text = start_date.strftime('%B %Y')
    
    # Base queryset for orders in date range
    orders_qs = Order.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
        is_deleted=False
    )
    
    # Filter by staff if specified
    if staff_filter != 'all':
        try:
            staff_id = int(staff_filter)
            orders_qs = orders_qs.filter(created_by_id=staff_id)
        except (ValueError, TypeError):
            pass
    
    # ========== KPI CALCULATIONS ==========
    total_orders = orders_qs.count()
    
    # Successful deliveries
    successful_orders = orders_qs.filter(
        Q(status='delivered') | Q(order_status='delivered')
    ).count()
    success_rate = (successful_orders / total_orders * 100) if total_orders > 0 else 0
    
    # Returns
    return_requests = ReturnRequest.objects.filter(
        order__created_at__date__gte=start_date,
        order__created_at__date__lte=end_date,
        is_deleted=False
    )
    if staff_filter != 'all':
        try:
            staff_id = int(staff_filter)
            return_requests = return_requests.filter(order__created_by_id=staff_id)
        except (ValueError, TypeError):
            pass
    
    returns_count = return_requests.count()
    return_rate = (returns_count / total_orders * 100) if total_orders > 0 else 0
    
    # Revenue - use aggregation with larger max_digits for aggregated totals
    revenue_result = orders_qs.aggregate(Sum('total_amount'))
    total_revenue = safe_decimal(
        revenue_result.get('total_amount__sum') or 0,
        max_digits=12,  # Allow aggregated sums to exceed single order limit
        decimal_places=2
    )
    
    # Products sold
    total_products_sold = OrderItem.objects.filter(
        order__in=orders_qs
    ).count()
    
    # Active staff count
    active_staff = User.objects.filter(
        is_active=True,
        is_deleted=False,
        role__in=['sales', 'warehouse']
    ).count()
    
    # ========== STAFF PERFORMANCE DATA ==========
    # Show ALL active users in the dropdown (not just sales/warehouse)
    staff_members = User.objects.filter(
        is_active=True,
        is_deleted=False,
        is_superuser=False,
    ).order_by('first_name', 'last_name')
    
    # ✅ Create a separate list for filtering based on selected staff
    staff_to_show = staff_members
    if staff_filter != 'all':
        try:
            staff_id = int(staff_filter)
            # Only include the selected staff member
            staff_to_show = staff_members.filter(id=staff_id)
        except (ValueError, TypeError):
            pass
    
    staff_performance_data = []
    
    for staff in staff_to_show:
        staff_orders = orders_qs.filter(created_by=staff)
        staff_delivered = staff_orders.filter(
            Q(status='delivered') | Q(order_status='delivered')
        ).count()
        staff_return_qs = return_requests.filter(order__created_by=staff)
        staff_returns = staff_return_qs.count()

        # Use aggregation for revenue instead of loop
        staff_revenue_result = staff_orders.aggregate(Sum('total_amount'))
        staff_revenue = safe_decimal(
            staff_revenue_result.get('total_amount__sum') or 0,
            max_digits=12,  # Allow aggregated sums to exceed single order limit
            decimal_places=2
        )

        staff_success_rate = (staff_delivered / staff_orders.count() * 100) if staff_orders.count() > 0 else 0

        # Return status breakdown per staff
        staff_return_statuses = staff_return_qs.values('return_status').annotate(
            count=Count('id')
        )
        staff_return_status_map = {item['return_status']: item['count'] for item in staff_return_statuses}

        # Return reason breakdown per staff
        staff_return_reasons = staff_return_qs.exclude(return_reason='').values('return_reason').annotate(
            count=Count('id')
        ).order_by('-count')

        # Total refund amount per staff
        staff_refund_result = staff_return_qs.aggregate(
            total_refund=Sum('refund_amount'),
            total_return_amount=Sum('total_amount')
        )
        staff_total_refund = safe_decimal(
            staff_refund_result.get('total_refund') or 0,
            max_digits=12, decimal_places=2
        )
        staff_total_return_amount = safe_decimal(
            staff_refund_result.get('total_return_amount') or 0,
            max_digits=12, decimal_places=2
        )

        staff_performance_data.append({
            'id': staff.id,
            'name': staff.get_full_name() or staff.username,
            'role': staff.get_role_display(),
            'total_orders': staff_orders.count(),
            'successful_orders': staff_delivered,
            'return_count': staff_returns,
            'revenue': staff_revenue,
            'success_rate': round(staff_success_rate, 2),
            'return_pending': staff_return_status_map.get('pending', 0),
            'return_approved': staff_return_status_map.get('approved', 0),
            'return_refunded': staff_return_status_map.get('refunded', 0),
            'return_rejected': staff_return_status_map.get('rejected', 0),
            'return_received': staff_return_status_map.get('received', 0),
            'return_inspecting': staff_return_status_map.get('inspecting', 0),
            'total_refund': staff_total_refund,
            'total_return_amount': staff_total_return_amount,
            'return_reasons': list(staff_return_reasons),
        })
    
    # Sort by success rate descending (only when showing all staff)
    if staff_filter == 'all':
        staff_performance_data.sort(key=lambda x: x['success_rate'], reverse=True)
    
    # ========== TOP PERFORMING PRODUCTS ==========
    # Build product revenue with proportional order totals
    from django.db.models import F, Case, When, Value, DecimalField
    
    product_revenues = {}
    
    # Get all order items and their parent order totals
    order_items = OrderItem.objects.filter(
        order__in=orders_qs,
        product__isnull=False
    ).select_related('product', 'order').values_list(
        'product_id', 'product__name', 'product__product_type', 
        'order_id', 'order__total_amount', 'quantity', 'total'
    )
    
    for product_id, product_name, product_type, order_id, order_total, qty, item_total in order_items:
        if product_id not in product_revenues:
            product_revenues[product_id] = {
                'product__name': product_name,
                'product__product_type': product_type,
                'units_sold': 0,
                'total_revenue': Decimal('0'),
                'order_totals': {}  # Track orders to avoid double-counting
            }
        
        product_revenues[product_id]['units_sold'] += qty
        
        # Calculate this item's proportional share of the order total
        # Use SafeDecimal to handle the calculation
        if order_id not in product_revenues[product_id]['order_totals']:
            product_revenues[product_id]['order_totals'][order_id] = safe_decimal(order_total, max_digits=12, decimal_places=2)
            product_revenues[product_id]['total_revenue'] += safe_decimal(order_total, max_digits=12, decimal_places=2)
    
    # Convert to list format expected by the rest of the code
    top_products_data = []
    for product_id, info in product_revenues.items():
        top_products_data.append({
            'product_id': product_id,
            'product__name': info['product__name'],
            'product__product_type': info['product__product_type'],
            'units_sold': info['units_sold'],
            'total_revenue': info['total_revenue']
        })
    
    # Sort by revenue descending and take top 5
    top_products_data.sort(key=lambda x: x['total_revenue'], reverse=True)
    top_products_data = top_products_data[:5]
    
    top_products = []
    for i, item in enumerate(top_products_data, 1):
        # Use safe_decimal on aggregated value with larger max_digits
        revenue_raw = item['total_revenue'] or Decimal('0')
        revenue = safe_decimal(
            revenue_raw,
            max_digits=12,  # Allow aggregated sums to exceed single order limit
            decimal_places=2
        )
        
        # Determine if product is simple or variable
        product_type = item.get('product__product_type', 'simple')
        product_name = item['product__name'] or 'Unknown Product'
        units_sold = item['units_sold'] or 0
        product_id = item['product_id']
        
        product_data = {
            'rank': i,
            'name': product_name,
            'product_id': product_id,
            'product_type': product_type,
            'units': units_sold,
            'revenue': revenue,
            'variants': []
        }
        
        # ✅ FETCH VARIANT DETAILS FOR VARIABLE PRODUCTS
        if product_type == 'variable':
            variants_data = OrderItem.objects.filter(
                order__in=orders_qs,
                product_id=product_id,
                product_variation__isnull=False
            ).values(
                'product_variation_id',
                'product_variation__variation_name',
                'product_name'
            ).annotate(
                variant_units=Sum('quantity'),
                variant_revenue=Sum('total')
            ).order_by('-variant_revenue')
            
            # ✅ COLLECT VARIANT DATA AND SUM VARIANT UNITS (but NOT revenue)
            total_variant_units = 0
            
            for variant in variants_data:
                variant_revenue = safe_decimal(
                    variant['variant_revenue'] or Decimal('0'),
                    max_digits=12,
                    decimal_places=2
                )
                variant_units = variant['variant_units'] or 0
                
                # Sum units for main product totals
                total_variant_units += variant_units
                
                product_data['variants'].append({
                    'name': variant['product_variation__variation_name'] or variant['product_name'],
                    'units': variant_units,
                    'revenue': variant_revenue,  # Variant row shows OrderItem.total
                })
            
            # ✅ UPDATE MAIN PRODUCT UNITS (from variant sum) BUT KEEP REVENUE FROM Order.total_amount
            product_data['units'] = total_variant_units
            # NOTE: product_data['revenue'] was already set to Order.total_amount sum above - do NOT override it
        
        top_products.append(product_data)
    
    # ========== PERFORMANCE TRENDS OVER TIME ==========
    daily_data = orders_qs.values('created_at__date').annotate(
        daily_orders=Count('id'),
        daily_delivered=Count('id', filter=Q(status='delivered') | Q(order_status='delivered')),
        daily_revenue=Sum('total_amount')
    ).order_by('created_at__date')

    performance_trends = []
    for entry in daily_data:
        date = entry['created_at__date']
        orders = entry['daily_orders'] or 0
        delivered = entry['daily_delivered'] or 0
        # Use data directly from aggregation with larger max_digits for daily totals
        revenue = safe_decimal(
            entry['daily_revenue'] or 0,
            max_digits=12,  # Allow aggregated sums to exceed single order limit
            decimal_places=2
        )
        returns = return_requests.filter(
            order__created_at__date=date
        ).count()
        success_rate_daily = (delivered / orders * 100) if orders > 0 else 0
        
        performance_trends.append({
            'date': date.strftime('%d %b'),
            'orders': orders,
            'delivered': delivered,
            'returns': returns,
            'revenue': float(revenue),
            'success_rate': round(success_rate_daily, 2)
        })
    
    # Convert to JSON for chart
    performance_trends_json = json.dumps(performance_trends)
    
    # ========== ORDER STATUS BREAKDOWN ==========
    order_statuses = orders_qs.values('status').annotate(count=Count('id')).order_by('-count')
    status_breakdown = {
        'delivered': 0,
        'pending': 0,
        'returns': 0,
        'other': 0
    }
    
    for status_item in order_statuses:
        status = status_item['status'] or 'unknown'
        count = status_item['count']
        if status.lower() in ['delivered', 'completed']:
            status_breakdown['delivered'] += count
        elif status.lower() in ['returned', 'return']:
            status_breakdown['returns'] += count
        elif status.lower() in ['pending', 'processing']:
            status_breakdown['pending'] += count
        else:
            status_breakdown['other'] += count
    
    # Ensure status_breakdown has all keys for chart
    status_breakdown_json = json.dumps(status_breakdown)

    # ========== DETAILED RETURNS DATA ==========
    # Recent return requests with items
    recent_returns = return_requests.select_related(
        'order', 'customer', 'created_by'
    ).prefetch_related('items').order_by('-created_at')[:20]

    # Return stats summary
    return_stats = {
        'total': returns_count,
        'pending': return_requests.filter(return_status='pending').count(),
        'approved': return_requests.filter(return_status='approved').count(),
        'received': return_requests.filter(return_status='received').count(),
        'inspecting': return_requests.filter(return_status='inspecting').count(),
        'refunded': return_requests.filter(return_status='refunded').count(),
        'rejected': return_requests.filter(return_status='rejected').count(),
        'total_refund': safe_decimal(
            return_requests.aggregate(Sum('refund_amount')).get('refund_amount__sum') or 0,
            max_digits=12, decimal_places=2
        ),
    }

    # Returned products summary (aggregated across all returns in period)
    returned_products_summary = ReturnItem.objects.filter(
        return_request__in=return_requests
    ).values(
        'product__name', 'product_sku',
        'product_variation__id', 'product_variation__variation_name'
    ).annotate(
        total_return_qty=Sum('return_quantity'),
        total_good_qty=Sum('good_qty'),
        total_damaged_qty=Sum('damaged_qty'),
        total_refund=Sum('refund_amount'),
        return_count=Count('return_request', distinct=True)
    ).order_by('-total_return_qty')

    # Return reason breakdown (overall)
    return_reason_breakdown = return_requests.exclude(
        return_reason=''
    ).values('return_reason').annotate(
        count=Count('id')
    ).order_by('-count')

    # ========== CONTEXT ==========
    context = {
        'total_orders': total_orders,
        'successful_deliveries': successful_orders,
        'success_rate': round(success_rate, 2),
        'returns': returns_count,
        'return_rate': round(return_rate, 2),
        'total_revenue': total_revenue,
        'total_products_sold': total_products_sold,
        'active_staff_count': active_staff,
        'staff_performance_data': staff_performance_data,
        'top_products': top_products,
        'performance_trends': performance_trends_json,
        'staff_members': staff_members,
        'selected_period': period,
        'selected_staff': staff_filter,
        'date_range': date_range_text,
        'custom_start_date': custom_start_str or '',
        'custom_end_date': custom_end_str or '',
        'today_date': today.strftime('%Y-%m-%d'),
        'status_breakdown': status_breakdown_json,
        'recent_returns': recent_returns,
        'return_stats': return_stats,
        'returned_products_summary': returned_products_summary,
        'return_reason_breakdown': return_reason_breakdown,
    }
    
    return render(request, 'staff_performance.html', context)


# ==================== PRODUCT SALES REPORT ====================
@login_required
@permission_required('can_view_product_sales_reports')
def product_sales_report(request):
    """Product-level sales analytics with staff ranking and trend charts"""
    from django.db.models.functions import TruncDate, Coalesce
    from django.db.models import Avg

    products = Product.objects.filter(is_deleted=False, is_active=True).order_by('name')

    selected_product_id = request.GET.get('product_id', '')
    selected_variation_id = request.GET.get('variation_id', '')
    from_date_str = request.GET.get('from_date', '')
    to_date_str = request.GET.get('to_date', '')

    now = timezone.now()

    # Parse date range (default: last 30 days)
    try:
        from_date = timezone.make_aware(datetime.strptime(from_date_str, '%Y-%m-%d')) if from_date_str else now - timedelta(days=30)
    except ValueError:
        from_date = now - timedelta(days=30)

    try:
        to_date = timezone.make_aware(datetime.strptime(to_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)) if to_date_str else now
    except ValueError:
        to_date = now

    selected_product = None
    selected_variation = None
    summary = {}
    staff_ranking = []
    chart_labels = []
    chart_qty_data = []
    chart_revenue_data = []
    staff_chart_labels = []
    staff_chart_data = []
    status_chart_labels = []
    status_chart_data = []
    variations = []
    variant_breakdown = []
    variant_chart_labels = []
    variant_chart_data = []
    is_variable = False
    is_bundle = False
    bundle_components_data = []

    if selected_product_id:
        try:
            selected_product = Product.objects.get(id=selected_product_id, is_deleted=False)
        except Product.DoesNotExist:
            selected_product = None

    if selected_product:
        is_variable = selected_product.product_type == 'variable'
        is_bundle = selected_product.product_type == 'bundle'

        # Load variations for variable products
        if is_variable:
            variations = list(selected_product.variations.all().order_by('variation_name'))

            # Check if a specific variation is selected
            if selected_variation_id:
                try:
                    selected_variation = ProductVariation.objects.get(
                        id=selected_variation_id, product=selected_product
                    )
                except ProductVariation.DoesNotExist:
                    selected_variation = None

        # Base queryset: order items for this product in date range
        items_qs = OrderItem.objects.filter(
            product=selected_product,
            order__is_deleted=False,
            order__created_at__gte=from_date,
            order__created_at__lte=to_date,
        )

        # If a specific variation is selected, filter further
        if selected_variation:
            items_qs = items_qs.filter(product_variation=selected_variation)

        # -- Summary card --
        agg = items_qs.aggregate(
            total_qty=Coalesce(Sum('quantity'), 0),
            total_revenue=Coalesce(Sum('total'), Decimal('0')),
            avg_price=Coalesce(Avg('price'), Decimal('0')),
        )

        # Stock: for variable products sum all variation stocks, for bundles use available_stock, for simple use product stock
        if is_variable:
            if selected_variation:
                current_stock = selected_variation.stock
            else:
                current_stock = sum(v.stock for v in variations)
        elif is_bundle:
            current_stock = selected_product.available_stock
        else:
            current_stock = selected_product.stock

        summary = {
            'total_qty': agg['total_qty'],
            'total_revenue': agg['total_revenue'],
            'avg_price': round(agg['avg_price'], 2),
            'current_stock': current_stock,
        }

        # -- Bundle component breakdown (for bundle products) --
        if is_bundle:
            components = BundleComponent.objects.filter(
                bundle_product=selected_product
            ).select_related('component_product')
            total_bundles_sold = agg['total_qty'] or 0
            for comp in components:
                consumed = total_bundles_sold * comp.quantity_required
                bundle_components_data.append({
                    'name': comp.component_product.name,
                    'qty_required': comp.quantity_required,
                    'consumed': consumed,
                    'stock': comp.component_product.stock,
                    'component_id': comp.component_product.id,
                })

        # -- Variant breakdown (only for variable products, when no specific variant selected) --
        if is_variable and not selected_variation:
            variant_data = (
                items_qs
                .values('product_variation__id', 'product_variation__variation_name')
                .annotate(
                    units_sold=Sum('quantity'),
                    revenue=Sum('total'),
                    avg_price_val=Coalesce(Avg('price'), Decimal('0')),
                )
                .order_by('-units_sold')
            )
            for row in variant_data:
                vname = row['product_variation__variation_name'] or 'No Variant'
                # Find the variation object to get current stock
                v_stock = 0
                for v in variations:
                    if v.id == row['product_variation__id']:
                        v_stock = v.stock
                        break
                variant_breakdown.append({
                    'name': vname,
                    'units_sold': row['units_sold'],
                    'revenue': row['revenue'],
                    'avg_price': round(row['avg_price_val'], 2),
                    'stock': v_stock,
                })
                variant_chart_labels.append(vname)
                variant_chart_data.append(row['units_sold'])

        # -- Staff ranking --
        staff_data = (
            items_qs
            .values('order__created_by__id', 'order__created_by__first_name', 'order__created_by__last_name', 'order__created_by__username')
            .annotate(
                units_sold=Sum('quantity'),
                revenue=Sum('total'),
                last_sale=Max('order__created_at'),
            )
            .order_by('-units_sold')
        )
        for rank, row in enumerate(staff_data, start=1):
            first = row['order__created_by__first_name'] or ''
            last = row['order__created_by__last_name'] or ''
            name = f"{first} {last}".strip() or row['order__created_by__username'] or 'Unknown'
            staff_ranking.append({
                'rank': rank,
                'name': name,
                'units_sold': row['units_sold'],
                'revenue': row['revenue'],
                'last_sale': row['last_sale'],
            })
            staff_chart_labels.append(name)
            staff_chart_data.append(row['units_sold'])

        # -- Chart data (daily) --
        daily = (
            items_qs
            .annotate(day=TruncDate('order__created_at'))
            .values('day')
            .annotate(qty=Sum('quantity'), rev=Sum('total'))
            .order_by('day')
        )
        for entry in daily:
            chart_labels.append(entry['day'].strftime('%b %d'))
            chart_qty_data.append(int(entry['qty'] or 0))
            chart_revenue_data.append(float(entry['rev'] or 0))

        # -- Order status breakdown for this product --
        status_data = (
            items_qs
            .values('order__order_status')
            .annotate(count=Count('order__id', distinct=True))
            .order_by('-count')
        )
        for row in status_data:
            label = (row['order__order_status'] or 'unknown').replace('_', ' ').title()
            status_chart_labels.append(label)
            status_chart_data.append(row['count'])

    # JSON for AJAX
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'summary': summary,
            'staff_ranking': staff_ranking,
            'chart_labels': chart_labels,
            'chart_qty_data': chart_qty_data,
            'chart_revenue_data': chart_revenue_data,
            'staff_chart_labels': staff_chart_labels,
            'staff_chart_data': staff_chart_data,
            'status_chart_labels': status_chart_labels,
            'status_chart_data': status_chart_data,
            'variant_breakdown': variant_breakdown,
            'variant_chart_labels': variant_chart_labels,
            'variant_chart_data': variant_chart_data,
            'is_variable': is_variable,
            'is_bundle': is_bundle,
            'bundle_components_data': bundle_components_data,
        }, safe=False)

    # Determine active quick filter
    active_filter = ''
    if from_date_str and to_date_str:
        today_str = now.strftime('%Y-%m-%d')
        seven_days_ago = (now - timedelta(days=6)).strftime('%Y-%m-%d')
        fifteen_days_ago = (now - timedelta(days=14)).strftime('%Y-%m-%d')
        one_month_ago = (now.replace(day=now.day) - timedelta(days=0))
        try:
            one_month_ago = now.replace(month=now.month - 1)
        except ValueError:
            one_month_ago = now.replace(year=now.year - 1, month=12)
        one_month_ago_str = one_month_ago.strftime('%Y-%m-%d')
        if from_date_str == today_str and to_date_str == today_str:
            active_filter = 'today'
        elif from_date_str == seven_days_ago and to_date_str == today_str:
            active_filter = '7days'
        elif from_date_str == fifteen_days_ago and to_date_str == today_str:
            active_filter = '15days'
        elif from_date_str == one_month_ago_str and to_date_str == today_str:
            active_filter = '1month'

    context = {
        'products': products,
        'selected_product': selected_product,
        'selected_product_id': selected_product_id,
        'selected_variation': selected_variation,
        'selected_variation_id': selected_variation_id,
        'from_date': from_date.strftime('%Y-%m-%d'),
        'to_date': to_date.strftime('%Y-%m-%d'),
        'summary': summary,
        'staff_ranking': staff_ranking,
        'chart_labels': json.dumps(chart_labels),
        'chart_qty_data': json.dumps(chart_qty_data),
        'chart_revenue_data': json.dumps(chart_revenue_data),
        'staff_chart_labels': json.dumps(staff_chart_labels),
        'staff_chart_data': json.dumps(staff_chart_data),
        'status_chart_labels': json.dumps(status_chart_labels),
        'status_chart_data': json.dumps(status_chart_data),
        'is_variable': is_variable,
        'is_bundle': is_bundle,
        'bundle_components_data': bundle_components_data,
        'variations': variations,
        'variant_breakdown': variant_breakdown,
        'variant_chart_labels': json.dumps(variant_chart_labels),
        'variant_chart_data': json.dumps(variant_chart_data),
        'active_filter': active_filter,
    }
    return render(request, 'product_sales_report.html', context)


# ==================== PURCHASE REPORT ====================
@login_required
def purchase_report(request):
    """Purchase-level analytics with supplier ranking, product breakdown and trend charts"""
    from django.db.models.functions import TruncDate, Coalesce
    from django.db.models import Avg

    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    products = Product.objects.filter(is_deleted=False, is_active=True).order_by('name')

    selected_supplier_id = request.GET.get('supplier_id', '')
    selected_product_id = request.GET.get('product_id', '')
    selected_variation_id = request.GET.get('variation_id', '')
    from_date_str = request.GET.get('from_date', '')
    to_date_str = request.GET.get('to_date', '')
    payment_status_filter = request.GET.get('payment_status', '')

    now = timezone.now()

    # Parse date range (default: last 30 days)
    try:
        from_date = timezone.make_aware(datetime.strptime(from_date_str, '%Y-%m-%d')) if from_date_str else now - timedelta(days=30)
    except ValueError:
        from_date = now - timedelta(days=30)

    try:
        to_date = timezone.make_aware(datetime.strptime(to_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)) if to_date_str else now
    except ValueError:
        to_date = now

    # Base querysets
    purchases_qs = Purchase.objects.filter(
        purchase_date__gte=from_date.date(),
        purchase_date__lte=to_date.date(),
    )
    items_qs = PurchaseItem.objects.filter(
        purchase__purchase_date__gte=from_date.date(),
        purchase__purchase_date__lte=to_date.date(),
    )

    # Apply filters
    if selected_supplier_id:
        purchases_qs = purchases_qs.filter(supplier_id=selected_supplier_id)
        items_qs = items_qs.filter(purchase__supplier_id=selected_supplier_id)

    if selected_product_id:
        # For bundle products, also include component products
        try:
            filter_product = Product.objects.get(id=selected_product_id, is_deleted=False)
        except Product.DoesNotExist:
            filter_product = None

        if filter_product and filter_product.product_type == 'bundle':
            component_ids = list(
                BundleComponent.objects.filter(bundle_product=filter_product)
                .values_list('component_product_id', flat=True)
            )
            product_ids = [filter_product.id] + component_ids
            items_qs = items_qs.filter(product_id__in=product_ids)
        else:
            items_qs = items_qs.filter(product_id=selected_product_id)
            # Filter by specific variation if selected
            if selected_variation_id and filter_product and filter_product.product_type == 'variable':
                items_qs = items_qs.filter(product_variation_id=selected_variation_id)
        purchases_qs = purchases_qs.filter(id__in=items_qs.values_list('purchase_id', flat=True))

    if payment_status_filter:
        purchases_qs = purchases_qs.filter(payment_status=payment_status_filter)
        items_qs = items_qs.filter(purchase__payment_status=payment_status_filter)

    # Summary cards
    purchase_agg = purchases_qs.aggregate(
        total_purchases=Count('id'),
        total_amount=Coalesce(Sum('total_amount'), Decimal('0')),
    )
    items_agg = items_qs.aggregate(
        total_qty=Coalesce(Sum('quantity'), 0),
        total_items_value=Coalesce(Sum('total'), Decimal('0')),
    )

    # Scope payments to the filtered purchases
    total_paid = SupplierPayment.objects.filter(
        payment_date__gte=from_date.date(),
        payment_date__lte=to_date.date(),
    )
    if selected_supplier_id:
        total_paid = total_paid.filter(supplier_id=selected_supplier_id)
    # When filtered by product or payment status, restrict to matching purchases only
    filtered_purchase_ids = list(purchases_qs.values_list('id', flat=True))
    if selected_product_id or payment_status_filter:
        total_paid = total_paid.filter(purchase_id__in=filtered_purchase_ids)
    total_paid_amount = total_paid.aggregate(total=Coalesce(Sum('amount'), Decimal('0')))['total']

    summary = {
        'total_purchases': purchase_agg['total_purchases'],
        'total_amount': purchase_agg['total_amount'],
        'total_qty': items_agg['total_qty'],
        'total_paid': total_paid_amount,
        'outstanding': purchase_agg['total_amount'] - total_paid_amount,
    }

    # Supplier ranking
    supplier_data = (
        purchases_qs
        .values('supplier__id', 'supplier__name')
        .annotate(
            purchase_count=Count('id'),
            total_value=Coalesce(Sum('total_amount'), Decimal('0')),
            last_purchase=Max('purchase_date'),
        )
        .order_by('-total_value')
    )
    supplier_ranking = []
    supplier_chart_labels = []
    supplier_chart_data = []
    for rank, row in enumerate(supplier_data, start=1):
        name = row['supplier__name'] or 'Unknown'
        supplier_ranking.append({
            'rank': rank,
            'name': name,
            'purchase_count': row['purchase_count'],
            'total_value': row['total_value'],
            'last_purchase': row['last_purchase'],
        })
        supplier_chart_labels.append(name)
        supplier_chart_data.append(float(row['total_value']))

    # Product breakdown
    product_data = (
        items_qs
        .values('product__id', 'product__name', 'product__product_type')
        .annotate(
            total_qty=Sum('quantity'),
            total_value=Sum('total'),
            avg_rate=Coalesce(Avg('rate'), Decimal('0')),
        )
        .order_by('-total_qty')
    )
    product_breakdown = []
    product_chart_labels = []
    product_chart_data = []
    for row in product_data:
        pname = row['product__name'] or 'Unknown'
        product_breakdown.append({
            'name': pname,
            'product_type': row['product__product_type'] or 'simple',
            'total_qty': row['total_qty'],
            'total_value': row['total_value'],
            'avg_rate': round(row['avg_rate'], 2),
        })
        product_chart_labels.append(pname)
        product_chart_data.append(row['total_qty'])

    # Payment status breakdown
    status_data = (
        purchases_qs
        .values('payment_status')
        .annotate(count=Count('id'), value=Coalesce(Sum('total_amount'), Decimal('0')))
        .order_by('-count')
    )
    payment_status_labels = []
    payment_status_data = []
    for row in status_data:
        label = (row['payment_status'] or 'unknown').replace('_', ' ').title()
        payment_status_labels.append(label)
        payment_status_data.append(row['count'])

    # Daily trend chart
    daily = (
        purchases_qs
        .values('purchase_date')
        .annotate(
            count=Count('id'),
            amount=Coalesce(Sum('total_amount'), Decimal('0')),
        )
        .order_by('purchase_date')
    )
    chart_labels = []
    chart_count_data = []
    chart_amount_data = []
    for entry in daily:
        chart_labels.append(entry['purchase_date'].strftime('%b %d'))
        chart_count_data.append(int(entry['count']))
        chart_amount_data.append(float(entry['amount']))

    # Staff (created_by) ranking for purchases
    staff_data = (
        purchases_qs
        .filter(created_by__isnull=False)
        .values('created_by__id', 'created_by__first_name', 'created_by__last_name', 'created_by__username')
        .annotate(
            purchase_count=Count('id'),
            total_value=Coalesce(Sum('total_amount'), Decimal('0')),
            last_purchase=Max('purchase_date'),
        )
        .order_by('-total_value')
    )
    staff_ranking = []
    staff_chart_labels = []
    staff_chart_data = []
    for rank, row in enumerate(staff_data, start=1):
        first = row['created_by__first_name'] or ''
        last = row['created_by__last_name'] or ''
        name = f"{first} {last}".strip() or row['created_by__username'] or 'Unknown'
        staff_ranking.append({
            'rank': rank,
            'name': name,
            'purchase_count': row['purchase_count'],
            'total_value': row['total_value'],
            'last_purchase': row['last_purchase'],
        })
        staff_chart_labels.append(name)
        staff_chart_data.append(float(row['total_value']))

    # Determine active quick filter
    active_filter = ''
    if from_date_str and to_date_str:
        today_str = now.strftime('%Y-%m-%d')
        seven_days_ago = (now - timedelta(days=6)).strftime('%Y-%m-%d')
        fifteen_days_ago = (now - timedelta(days=14)).strftime('%Y-%m-%d')
        try:
            one_month_ago = now.replace(month=now.month - 1)
        except ValueError:
            one_month_ago = now.replace(year=now.year - 1, month=12)
        one_month_ago_str = one_month_ago.strftime('%Y-%m-%d')
        if from_date_str == today_str and to_date_str == today_str:
            active_filter = 'today'
        elif from_date_str == seven_days_ago and to_date_str == today_str:
            active_filter = '7days'
        elif from_date_str == fifteen_days_ago and to_date_str == today_str:
            active_filter = '15days'
        elif from_date_str == one_month_ago_str and to_date_str == today_str:
            active_filter = '1month'

    # Get selected objects for display
    selected_supplier = None
    selected_product = None
    if selected_supplier_id:
        try:
            selected_supplier = Supplier.objects.get(id=selected_supplier_id)
        except Supplier.DoesNotExist:
            pass
    if selected_product_id:
        try:
            selected_product = Product.objects.get(id=selected_product_id, is_deleted=False)
        except Product.DoesNotExist:
            pass

    # Variation support for variable products
    variations = []
    selected_variation = None
    variant_breakdown = []
    variant_chart_labels = []
    variant_chart_data = []
    is_variable = False

    if selected_product and selected_product.product_type == 'variable':
        is_variable = True
        variations = list(selected_product.variations.filter(is_active=True).order_by('variation_name'))

        if selected_variation_id:
            try:
                selected_variation = ProductVariation.objects.get(
                    id=selected_variation_id, product=selected_product
                )
            except ProductVariation.DoesNotExist:
                selected_variation = None

        # Variant breakdown (when no specific variant selected)
        if not selected_variation:
            variant_data = (
                items_qs
                .filter(product=selected_product)
                .values('product_variation__id', 'product_variation__variation_name')
                .annotate(
                    total_qty=Sum('quantity'),
                    total_value=Sum('total'),
                    avg_rate=Coalesce(Avg('rate'), Decimal('0')),
                )
                .order_by('-total_qty')
            )
            for row in variant_data:
                vname = row['product_variation__variation_name'] or 'No Variant'
                v_stock = 0
                for v in variations:
                    if v.id == row['product_variation__id']:
                        v_stock = v.stock
                        break
                variant_breakdown.append({
                    'name': vname,
                    'total_qty': row['total_qty'],
                    'total_value': row['total_value'],
                    'avg_rate': round(row['avg_rate'], 2),
                    'stock': v_stock,
                })
                variant_chart_labels.append(vname)
                variant_chart_data.append(row['total_qty'])

    # Bundle component info for selected bundle product
    is_bundle = False
    bundle_components_info = []
    if selected_product and selected_product.product_type == 'bundle':
        is_bundle = True
        components = selected_product.bundle_components.select_related('component_product').all()
        for comp in components:
            bundle_components_info.append({
                'name': comp.component_product.name,
                'qty_required': comp.quantity_required,
                'stock': comp.component_product.stock,
            })

    has_filters = bool(selected_supplier_id or selected_product_id or payment_status_filter)

    context = {
        'suppliers': suppliers,
        'products': products,
        'selected_supplier': selected_supplier,
        'selected_supplier_id': selected_supplier_id,
        'selected_product': selected_product,
        'selected_product_id': selected_product_id,
        'selected_variation': selected_variation,
        'selected_variation_id': selected_variation_id,
        'variations': variations,
        'is_variable': is_variable,
        'is_bundle': is_bundle,
        'bundle_components_info': bundle_components_info,
        'variant_breakdown': variant_breakdown,
        'variant_chart_labels': json.dumps(variant_chart_labels),
        'variant_chart_data': json.dumps(variant_chart_data),
        'payment_status_filter': payment_status_filter,
        'from_date': from_date.strftime('%Y-%m-%d'),
        'to_date': to_date.strftime('%Y-%m-%d'),
        'summary': summary,
        'has_filters': has_filters,
        'supplier_ranking': supplier_ranking,
        'product_breakdown': product_breakdown,
        'staff_ranking': staff_ranking,
        'chart_labels': json.dumps(chart_labels),
        'chart_count_data': json.dumps(chart_count_data),
        'chart_amount_data': json.dumps(chart_amount_data),
        'supplier_chart_labels': json.dumps(supplier_chart_labels),
        'supplier_chart_data': json.dumps(supplier_chart_data),
        'product_chart_labels': json.dumps(product_chart_labels),
        'product_chart_data': json.dumps(product_chart_data),
        'payment_status_labels': json.dumps(payment_status_labels),
        'payment_status_data': json.dumps(payment_status_data),
        'staff_chart_labels': json.dumps(staff_chart_labels),
        'staff_chart_data': json.dumps(staff_chart_data),
        'active_filter': active_filter,
    }
    return render(request, 'purchase/purchase_report.html', context)


# ==================== PURCHASE MANAGEMENT VIEWS ====================

@login_required
def purchase_dashboard(request):
    """Purchase Management Dashboard"""
    if not (request.user.is_superuser or request.user.role == 'administrator' or request.user.can_view_purchases):
        messages.error(request, "You don't have permission to access Purchase Management.", extra_tags='permission_denied')
        return redirect('dashboard')

    today = timezone.now().date()
    first_day_of_month = today.replace(day=1)

    # Summary cards
    today_purchases = Purchase.objects.filter(purchase_date=today).aggregate(
        total=Sum('total_amount'))['total'] or Decimal('0')
    month_purchases = Purchase.objects.filter(purchase_date__gte=first_day_of_month).aggregate(
        total=Sum('total_amount'))['total'] or Decimal('0')
    total_paid = SupplierPayment.objects.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_purchase_amount = Purchase.objects.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
    total_opening = Supplier.objects.aggregate(total=Sum('opening_balance'))['total'] or Decimal('0')
    total_remaining = total_opening + total_purchase_amount - total_paid

    # Supplier-wise outstanding
    suppliers = Supplier.objects.filter(is_active=True).annotate(
        total_purchase=Sum('purchases__total_amount'),
        paid=Sum('payments__amount'),
    )
    supplier_outstanding = []
    for s in suppliers:
        tp = (s.total_purchase or Decimal('0'))
        p = (s.paid or Decimal('0'))
        remaining = s.opening_balance + tp - p
        supplier_outstanding.append({
            'id': s.id,
            'name': s.name,
            'total_purchase': tp,
            'paid': p,
            'remaining': remaining,
        })

    # Low stock alerts
    low_stock_products = Product.objects.filter(
        is_deleted=False,
        is_active=True,
        low_stock_threshold__gt=0,
        stock__lte=F('low_stock_threshold'),
    ).order_by('stock')[:10]

    # Recent purchases
    recent_purchases = Purchase.objects.select_related('supplier', 'created_by').order_by('-purchase_date', '-created_at')[:15]

    context = {
        'today_purchases': today_purchases,
        'month_purchases': month_purchases,
        'total_paid': total_paid,
        'total_remaining': total_remaining,
        'supplier_outstanding': supplier_outstanding,
        'low_stock_products': low_stock_products,
        'recent_purchases': recent_purchases,
    }
    return render(request, 'purchase/purchase_dashboard.html', context)


@login_required
def supplier_list(request):
    """List all suppliers"""
    if not (request.user.is_superuser or request.user.role == 'administrator' or request.user.can_view_purchases or request.user.can_manage_suppliers):
        messages.error(request, "Permission denied.", extra_tags='permission_denied')
        return redirect('dashboard')

    suppliers = Supplier.objects.filter(is_active=True).annotate(
        total_purchase=Sum('purchases__total_amount'),
        paid=Sum('payments__amount'),
    )
    return render(request, 'purchase/supplier_list.html', {'suppliers': suppliers})


@login_required
def supplier_add(request):
    """Add a new supplier"""
    if not (request.user.is_superuser or request.user.role == 'administrator' or request.user.can_manage_suppliers):
        messages.error(request, "Permission denied.", extra_tags='permission_denied')
        return redirect('dashboard')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        address = request.POST.get('address', '').strip()
        opening_balance = request.POST.get('opening_balance', '0').strip()

        if not name:
            messages.error(request, "Supplier name is required.")
            form_supplier = type('obj', (object,), {'name': name, 'phone': phone, 'address': address, 'opening_balance': opening_balance})()
            return render(request, 'purchase/supplier_form.html', {'action': 'Add', 'supplier': form_supplier})

        try:
            opening_balance = Decimal(opening_balance) if opening_balance else Decimal('0')
        except (InvalidOperation, ValueError):
            opening_balance = Decimal('0')

        Supplier.objects.create(
            name=name,
            phone=phone,
            address=address,
            opening_balance=opening_balance,
        )
        messages.success(request, f"Supplier '{name}' added successfully.")
        return redirect('supplier_list')

    return render(request, 'purchase/supplier_form.html', {'action': 'Add'})


@login_required
def supplier_edit(request, supplier_id):
    """Edit supplier"""
    if not (request.user.is_superuser or request.user.role == 'administrator' or request.user.can_manage_suppliers):
        messages.error(request, "Permission denied.", extra_tags='permission_denied')
        return redirect('dashboard')

    supplier = get_object_or_404(Supplier, id=supplier_id)

    if request.method == 'POST':
        supplier.name = request.POST.get('name', '').strip()
        supplier.phone = request.POST.get('phone', '').strip()
        supplier.address = request.POST.get('address', '').strip()
        try:
            supplier.opening_balance = Decimal(request.POST.get('opening_balance', '0').strip())
        except (InvalidOperation, ValueError):
            pass
        supplier.save()
        messages.success(request, f"Supplier '{supplier.name}' updated.")
        return redirect('supplier_detail', supplier_id=supplier.id)

    return render(request, 'purchase/supplier_form.html', {'action': 'Edit', 'supplier': supplier})


@login_required
def supplier_detail(request, supplier_id):
    """Supplier detail page with purchases, products, payments, and ledger"""
    if not (request.user.is_superuser or request.user.role == 'administrator' or request.user.can_view_purchases or request.user.can_manage_suppliers):
        messages.error(request, "Permission denied.", extra_tags='permission_denied')
        return redirect('dashboard')

    supplier = get_object_or_404(Supplier, id=supplier_id)
    today = timezone.now().date()

    # Purchase invoices
    purchases = Purchase.objects.filter(supplier=supplier).order_by('-purchase_date')
    purchase_data = []
    for p in purchases:
        paid = p.get_total_paid()
        remaining = p.total_amount - paid
        purchase_data.append({
            'id': p.id,
            'invoice_number': p.invoice_number,
            'purchase_date': p.purchase_date,
            'total_amount': p.total_amount,
            'paid': paid,
            'remaining': remaining,
            'payment_status': p.payment_status,
            'items': p.purchase_items.select_related('product').all(),
        })

    # Products supplied
    products_supplied = PurchaseItem.objects.filter(
        purchase__supplier=supplier
    ).values('product__id', 'product__name').annotate(
        total_qty=Sum('quantity'),
        avg_rate=Avg('rate'),
        last_purchase=Max('purchase__purchase_date'),
    ).order_by('product__name')

    # Payment history
    payments = SupplierPayment.objects.filter(supplier=supplier).select_related('purchase').order_by('-payment_date')

    # Ledger (Tally-style)
    ledger_entries = []
    # Add opening balance
    if supplier.opening_balance > 0:
        ledger_entries.append({
            'date': supplier.created_at.date() if supplier.created_at else today,
            'description': 'Opening Balance',
            'debit': supplier.opening_balance,
            'credit': Decimal('0'),
            'sort_key': (supplier.created_at.date() if supplier.created_at else today, 0),
        })

    for p in purchases:
        ledger_entries.append({
            'date': p.purchase_date,
            'description': f'Purchase - {p.invoice_number}',
            'debit': p.total_amount,
            'credit': Decimal('0'),
            'sort_key': (p.purchase_date, 1),
        })

    for pay in payments:
        desc = f'Payment - {pay.reference_no}' if pay.reference_no else 'Payment'
        if pay.purchase:
            desc += f' (Inv: {pay.purchase.invoice_number})'
        ledger_entries.append({
            'date': pay.payment_date,
            'description': desc,
            'debit': Decimal('0'),
            'credit': pay.amount,
            'sort_key': (pay.payment_date, 2),
        })

    ledger_entries.sort(key=lambda x: x['sort_key'])

    # Calculate running balance
    running_balance = Decimal('0')
    for entry in ledger_entries:
        running_balance += entry['debit'] - entry['credit']
        entry['balance'] = running_balance

    outstanding = supplier.get_outstanding()

    context = {
        'supplier': supplier,
        'purchase_data': purchase_data,
        'products_supplied': products_supplied,
        'payments': payments,
        'ledger_entries': ledger_entries,
        'outstanding': outstanding,
    }
    return render(request, 'purchase/supplier_detail.html', context)


@login_required
def purchase_create(request):
    """Create a new purchase with items"""
    if not (request.user.is_superuser or request.user.role == 'administrator' or request.user.can_create_purchases):
        messages.error(request, "Permission denied.", extra_tags='permission_denied')
        return redirect('dashboard')

    suppliers = Supplier.objects.filter(is_active=True)
    products = Product.objects.filter(is_deleted=False, is_active=True).order_by('name')

    if request.method == 'POST':
        supplier_id = request.POST.get('supplier')
        purchase_date = request.POST.get('purchase_date', '')
        invoice_number = request.POST.get('invoice_number', '').strip()
        payment_method = request.POST.get('payment_method', '').strip()
        payment_amount = request.POST.get('payment_amount', '').strip()
        notes = request.POST.get('notes', '').strip()
        product_ids = request.POST.getlist('product_id[]')
        quantities = request.POST.getlist('quantity[]')
        rates = request.POST.getlist('rate[]')
        variation_ids = request.POST.getlist('variation_id[]')

        # Build context for re-rendering on error
        form_data = {
            'supplier_id': supplier_id,
            'invoice_number': invoice_number,
            'purchase_date': purchase_date,
            'payment_method': payment_method,
            'payment_amount': payment_amount,
            'notes': notes,
            'item_rows': [
                {'product_id': product_ids[i] if i < len(product_ids) else '',
                 'quantity': quantities[i] if i < len(quantities) else '1',
                 'rate': rates[i] if i < len(rates) else '0'}
                for i in range(max(len(product_ids), 1))
            ],
        }
        error_context = {
            'suppliers': suppliers,
            'products': products,
            'today': timezone.now().date().isoformat(),
            'form_data': form_data,
        }

        if not supplier_id or not invoice_number:
            messages.error(request, "Supplier and Invoice Number are required.")
            return render(request, 'purchase/purchase_form.html', error_context)

        # Check duplicate invoice
        if Purchase.objects.filter(invoice_number=invoice_number).exists():
            messages.error(request, f"Invoice number '{invoice_number}' already exists.")
            return render(request, 'purchase/purchase_form.html', error_context)

        supplier = get_object_or_404(Supplier, id=supplier_id)

        try:
            p_date = datetime.strptime(purchase_date, '%Y-%m-%d').date() if purchase_date else timezone.now().date()
        except ValueError:
            p_date = timezone.now().date()

        with transaction.atomic():
            purchase = Purchase.objects.create(
                supplier=supplier,
                purchase_date=p_date,
                invoice_number=invoice_number,
                payment_method=payment_method,
                notes=notes,
                created_by=request.user,
            )

            # Process items
            product_ids = request.POST.getlist('product_id[]')
            quantities = request.POST.getlist('quantity[]')
            rates = request.POST.getlist('rate[]')
            variation_ids = request.POST.getlist('variation_id[]')

            total_amount = Decimal('0')
            for i in range(len(product_ids)):
                if not product_ids[i]:
                    continue
                try:
                    product = Product.objects.get(id=product_ids[i])
                    qty = int(quantities[i]) if i < len(quantities) and quantities[i] else 1
                    rate = Decimal(rates[i]) if i < len(rates) and rates[i] else Decimal('0')

                    # Resolve variation for variable products
                    variation = None
                    variation_id_val = variation_ids[i] if i < len(variation_ids) else ''
                    if variation_id_val and product.product_type == 'variable':
                        try:
                            variation = ProductVariation.objects.get(id=int(variation_id_val), product=product)
                        except (ProductVariation.DoesNotExist, ValueError):
                            variation = None

                    item = PurchaseItem.objects.create(
                        purchase=purchase,
                        product=product,
                        product_variation=variation,
                        quantity=qty,
                        rate=rate,
                    )
                    total_amount += item.total

                    # Update stock based on product type
                    if product.product_type == 'bundle':
                        # Bundle: update each component product's stock
                        components = product.bundle_components.select_related('component_product').all()
                        for comp in components:
                            comp_product = comp.component_product
                            add_qty = comp.quantity_required * qty
                            comp_product.stock += add_qty
                            if comp_product.stock > 0:
                                comp_product.stock_status = 'in_stock'
                            comp_product.save(update_fields=['stock', 'stock_status'])

                            # Create ProductPurchase per component for accurate average cost
                            if rate > 0 and components.count() > 0:
                                # Distribute cost proportionally across components
                                comp_rate = rate / components.count()
                                ProductPurchase.objects.create(
                                    product=comp_product,
                                    cost_price=comp_rate,
                                    quantity=add_qty,
                                )
                                if comp_product.cost_price_type == 'variable':
                                    comp_product.refresh_from_db()
                                    comp_product.cost_price = comp_product.average_cost
                                    comp_product.save(update_fields=['cost_price'])
                    else:
                        # Simple / Variable: update product stock directly
                        if variation:
                            variation.stock += qty
                            if variation.stock > 0:
                                variation.status = 'active'
                            variation.save(update_fields=['stock', 'status'])
                        product.stock += qty
                        if product.stock > 0:
                            product.stock_status = 'in_stock'
                        product.save(update_fields=['stock', 'stock_status'])

                        # Create ProductPurchase record to keep average_cost dynamic
                        if rate > 0:
                            ProductPurchase.objects.create(
                                product=product,
                                cost_price=rate,
                                quantity=qty,
                            )
                            # Only sync cost_price field for variable cost price products
                            if product.cost_price_type == 'variable':
                                product.refresh_from_db()
                                product.cost_price = product.average_cost
                                product.save(update_fields=['cost_price'])
                except (Product.DoesNotExist, ValueError, InvalidOperation):
                    continue

            purchase.total_amount = total_amount
            purchase.save(update_fields=['total_amount'])

            # Handle payment if provided
            payment_amount = request.POST.get('payment_amount', '').strip()
            if payment_amount:
                try:
                    pay_amount = Decimal(payment_amount)
                    if pay_amount > 0:
                        SupplierPayment.objects.create(
                            supplier=supplier,
                            purchase=purchase,
                            amount=pay_amount,
                            payment_date=p_date,
                            payment_method=payment_method,
                            created_by=request.user,
                        )
                except (InvalidOperation, ValueError):
                    pass

        messages.success(request, f"Purchase {invoice_number} created successfully.")
        return redirect('purchase_dashboard')

    # Build product variations map for JS
    variable_products = products.filter(product_type='variable').prefetch_related('variations')
    product_variations_map = {}
    for vp in variable_products:
        product_variations_map[vp.id] = [
            {'id': v.id, 'name': v.variation_name or v.sku, 'sku': v.sku, 'stock': v.stock}
            for v in vp.variations.filter(is_active=True).order_by('variation_name')
        ]

    # Build bundle components map for JS
    bundle_products = products.filter(product_type='bundle').prefetch_related(
        'bundle_components__component_product'
    )
    bundle_components_map = {}
    for bp in bundle_products:
        bundle_components_map[bp.id] = [
            {
                'name': comp.component_product.name,
                'qty': comp.quantity_required,
                'stock': comp.component_product.stock,
            }
            for comp in bp.bundle_components.select_related('component_product').all()
        ]

    context = {
        'suppliers': suppliers,
        'products': products,
        'today': timezone.now().date().isoformat(),
        'product_variations_json': json.dumps(product_variations_map),
        'bundle_components_json': json.dumps(bundle_components_map),
    }
    return render(request, 'purchase/purchase_form.html', context)


@login_required
def purchase_detail(request, purchase_id):
    """View purchase details"""
    if not (request.user.is_superuser or request.user.role == 'administrator' or request.user.can_view_purchases):
        messages.error(request, "Permission denied.", extra_tags='permission_denied')
        return redirect('dashboard')

    purchase = get_object_or_404(Purchase, id=purchase_id)
    items = purchase.purchase_items.select_related('product').all()
    payments = purchase.payments.all()
    paid = purchase.get_total_paid()
    remaining = purchase.total_amount - paid

    context = {
        'purchase': purchase,
        'items': items,
        'payments': payments,
        'paid': paid,
        'remaining': remaining,
    }
    return render(request, 'purchase/purchase_detail.html', context)


@login_required
def supplier_payment_add(request):
    """Record a payment to supplier"""
    if not (request.user.is_superuser or request.user.role == 'administrator' or request.user.can_make_supplier_payments):
        messages.error(request, "Permission denied.", extra_tags='permission_denied')
        return redirect('dashboard')

    if request.method == 'POST':
        supplier_id = request.POST.get('supplier')
        purchase_id = request.POST.get('purchase', '')
        amount = request.POST.get('amount', '').strip()
        payment_date = request.POST.get('payment_date', '')
        payment_method = request.POST.get('payment_method', '').strip()
        reference_no = request.POST.get('reference_no', '').strip()
        notes = request.POST.get('notes', '').strip()

        # Build context for re-rendering on error
        all_suppliers = Supplier.objects.filter(is_active=True)
        error_context = {
            'suppliers': all_suppliers,
            'selected_supplier': supplier_id or '',
            'purchases': Purchase.objects.filter(supplier_id=supplier_id).order_by('-purchase_date') if supplier_id else [],
            'form_data': {
                'amount': amount,
                'payment_date': payment_date,
                'payment_method': payment_method,
                'reference_no': reference_no,
                'notes': notes,
                'purchase_id': purchase_id,
            },
        }

        if not supplier_id or not amount:
            messages.error(request, "Supplier and amount are required.")
            return render(request, 'purchase/payment_form.html', error_context)

        supplier = get_object_or_404(Supplier, id=supplier_id)

        try:
            pay_amount = Decimal(amount)
        except (InvalidOperation, ValueError):
            messages.error(request, "Invalid amount.")
            return render(request, 'purchase/payment_form.html', error_context)

        try:
            p_date = datetime.strptime(payment_date, '%Y-%m-%d').date() if payment_date else timezone.now().date()
        except ValueError:
            p_date = timezone.now().date()

        purchase = None
        if purchase_id:
            try:
                purchase = Purchase.objects.get(id=purchase_id)
            except Purchase.DoesNotExist:
                pass

        SupplierPayment.objects.create(
            supplier=supplier,
            purchase=purchase,
            amount=pay_amount,
            payment_date=p_date,
            payment_method=payment_method,
            reference_no=reference_no,
            notes=notes,
            created_by=request.user,
        )
        messages.success(request, f"Payment of Rs.{pay_amount} recorded for {supplier.name}.")
        return redirect('supplier_detail', supplier_id=supplier.id)

    suppliers = Supplier.objects.filter(is_active=True)
    # Pre-select supplier if provided via query param
    selected_supplier = request.GET.get('supplier', '')
    purchases = []
    if selected_supplier:
        purchases = Purchase.objects.filter(supplier_id=selected_supplier).order_by('-purchase_date')

    context = {
        'suppliers': suppliers,
        'selected_supplier': selected_supplier,
        'purchases': purchases,
    }
    return render(request, 'purchase/payment_form.html', context)


@login_required
def product_purchase_history(request, product_id):
    """Product purchase history page"""
    if not (request.user.is_superuser or request.user.role == 'administrator'):
        messages.error(request, "Permission denied.", extra_tags='permission_denied')
        return redirect('dashboard')

    product = get_object_or_404(Product, id=product_id)

    # Total purchased
    purchase_data = PurchaseItem.objects.filter(product=product).aggregate(
        total_qty=Sum('quantity'),
        avg_rate=Avg('rate'),
    )
    total_purchased = purchase_data['total_qty'] or 0
    avg_purchase_rate = purchase_data['avg_rate'] or Decimal('0')

    # Total sold
    total_sold = OrderItem.objects.filter(
        product=product,
        order__is_deleted=False,
    ).aggregate(total=Sum('quantity'))['total'] or 0

    current_stock = product.stock

    # Purchase history table
    purchase_items = PurchaseItem.objects.filter(product=product).select_related(
        'purchase', 'purchase__supplier'
    ).order_by('-purchase__purchase_date')

    # Supplier comparison
    supplier_comparison = PurchaseItem.objects.filter(product=product).values(
        'purchase__supplier__id', 'purchase__supplier__name'
    ).annotate(
        total_qty=Sum('quantity'),
        avg_rate=Avg('rate'),
    ).order_by('avg_rate')

    context = {
        'product': product,
        'total_purchased': total_purchased,
        'total_sold': total_sold,
        'current_stock': current_stock,
        'avg_purchase_rate': avg_purchase_rate,
        'purchase_items': purchase_items,
        'supplier_comparison': supplier_comparison,
    }
    return render(request, 'purchase/product_purchase_history.html', context)


@login_required
def api_supplier_purchases(request, supplier_id):
    """API to get purchases for a supplier (used in payment form dropdown)"""
    purchases = Purchase.objects.filter(supplier_id=supplier_id).order_by('-purchase_date')
    data = [{'id': p.id, 'invoice_number': p.invoice_number, 'total': str(p.total_amount),
             'remaining': str(p.get_remaining())} for p in purchases]
    return JsonResponse({'purchases': data})


# ==================== STAFF TARGET VIEWS ====================

@login_required(login_url='login')
def manage_targets(request):
    """Admin/Manager view: list all staff targets with filters"""
    user = request.user
    is_admin = user.is_superuser or user.role == 'administrator'
    if not (is_admin or user.can_view_targets):
        if user.can_view_own_targets:
            messages.warning(request, 'You do not have permission to manage targets. Redirected to your targets.')
            return redirect('my_targets')
        messages.error(request, 'You do not have permission to view targets.')
        return redirect('dashboard')

    # Filters
    staff_filter = request.GET.get('staff', '')
    type_filter = request.GET.get('target_type', '')
    period_filter = request.GET.get('period', '')

    targets = StaffTarget.objects.select_related('staff', 'set_by').all()

    if staff_filter:
        targets = targets.filter(staff_id=staff_filter)
    if type_filter:
        targets = targets.filter(target_type=type_filter)
    if period_filter:
        targets = targets.filter(period=period_filter)

    today = timezone.now().date()

    # Calculate achievement for each target
    targets_data = []
    for target in targets:
        achieved = _calculate_achievement(target)
        remaining = max(float(target.target_value) - achieved, 0)
        pct = (achieved / float(target.target_value) * 100) if float(target.target_value) > 0 else 0
        pct = min(pct, 100)

        if target.end_date < today:
            status = 'met' if pct >= 100 else 'not_met'
        else:
            status = 'in_progress'

        targets_data.append({
            'target': target,
            'achieved': round(achieved, 2),
            'remaining': round(remaining, 2),
            'percentage': round(pct, 1),
            'status': status,
        })

    staff_members = User.objects.filter(is_active=True, is_deleted=False, role__in=['sales', 'warehouse']).order_by('first_name')

    # KPI counts
    kpi = {
        'total': len(targets_data),
        'met': sum(1 for t in targets_data if t['status'] == 'met'),
        'in_progress': sum(1 for t in targets_data if t['status'] == 'in_progress'),
        'not_met': sum(1 for t in targets_data if t['status'] == 'not_met'),
    }

    context = {
        'targets_data': targets_data,
        'staff_members': staff_members,
        'staff_filter': staff_filter,
        'type_filter': type_filter,
        'period_filter': period_filter,
        'is_admin_view': True,
        'can_set': is_admin or user.can_set_targets,
        'can_edit': is_admin or user.can_edit_targets,
        'can_delete': is_admin or user.can_delete_targets,
        'kpi': kpi,
    }
    return render(request, 'staff_targets.html', context)


@login_required(login_url='login')
def my_targets(request):
    """Staff view: see only their own targets"""
    user = request.user
    is_admin = user.is_superuser or user.role == 'administrator'
    if not (is_admin or user.can_view_own_targets):
        messages.error(request, 'You do not have permission to view targets.')
        return redirect('dashboard')

    today = timezone.now().date()

    targets = StaffTarget.objects.filter(staff=user).order_by('-start_date')

    targets_data = []
    for target in targets:
        achieved = _calculate_achievement(target)
        remaining = max(float(target.target_value) - achieved, 0)
        pct = (achieved / float(target.target_value) * 100) if float(target.target_value) > 0 else 0
        pct = min(pct, 100)

        if target.end_date < today:
            status = 'met' if pct >= 100 else 'not_met'
        else:
            status = 'in_progress'

        targets_data.append({
            'target': target,
            'achieved': round(achieved, 2),
            'remaining': round(remaining, 2),
            'percentage': round(pct, 1),
            'status': status,
        })

    # KPI counts
    kpi = {
        'total': len(targets_data),
        'met': sum(1 for t in targets_data if t['status'] == 'met'),
        'in_progress': sum(1 for t in targets_data if t['status'] == 'in_progress'),
        'not_met': sum(1 for t in targets_data if t['status'] == 'not_met'),
    }

    context = {
        'targets_data': targets_data,
        'is_admin_view': False,
        'kpi': kpi,
    }
    return render(request, 'staff_targets.html', context)


@login_required(login_url='login')
@require_http_methods(["POST"])
def set_target(request):
    """Admin action: create a new target"""
    user = request.user
    is_admin = user.is_superuser or user.role == 'administrator'
    if not (is_admin or user.can_set_targets):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        staff_id = request.POST.get('staff_id')
        target_type = request.POST.get('target_type')
        target_value = request.POST.get('target_value')
        period = request.POST.get('period')
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        note = request.POST.get('note', '')

        if not all([staff_id, target_type, target_value, period, start_date, end_date]):
            messages.error(request, 'All required fields must be filled.')
            return redirect('manage_targets')

        staff = User.objects.get(id=staff_id, is_active=True, is_deleted=False)
        StaffTarget.objects.create(
            staff=staff,
            target_type=target_type,
            target_value=Decimal(target_value),
            period=period,
            start_date=start_date,
            end_date=end_date,
            set_by=user,
            note=note,
        )
        messages.success(request, f'Target set for {staff.get_full_name() or staff.username}.')
    except User.DoesNotExist:
        messages.error(request, 'Invalid staff member.')
    except Exception as e:
        messages.error(request, f'Error setting target: {e}')

    return redirect('manage_targets')


@login_required(login_url='login')
@require_http_methods(["POST"])
def edit_target(request, target_id):
    """Admin action: edit existing target"""
    user = request.user
    is_admin = user.is_superuser or user.role == 'administrator'
    if not (is_admin or user.can_edit_targets):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    target = get_object_or_404(StaffTarget, id=target_id)

    try:
        target.target_type = request.POST.get('target_type', target.target_type)
        target.target_value = Decimal(request.POST.get('target_value', target.target_value))
        target.period = request.POST.get('period', target.period)
        target.start_date = request.POST.get('start_date', target.start_date)
        target.end_date = request.POST.get('end_date', target.end_date)
        target.note = request.POST.get('note', target.note)
        target.save()
        messages.success(request, 'Target updated successfully.')
    except Exception as e:
        messages.error(request, f'Error updating target: {e}')

    return redirect('manage_targets')


@login_required(login_url='login')
@require_http_methods(["POST"])
def delete_target(request, target_id):
    """Admin action: delete a target"""
    user = request.user
    is_admin = user.is_superuser or user.role == 'administrator'
    if not (is_admin or user.can_delete_targets):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    target = get_object_or_404(StaffTarget, id=target_id)
    staff_name = target.staff.get_full_name() or target.staff.username
    target.delete()
    messages.success(request, f'Target for {staff_name} deleted.')
    return redirect('manage_targets')


@login_required(login_url='login')
def api_target_detail(request, target_id):
    """API to get target details for edit modal"""
    user = request.user
    is_admin = user.is_superuser or user.role == 'administrator'
    if not (is_admin or user.can_edit_targets):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    target = get_object_or_404(StaffTarget, id=target_id)
    return JsonResponse({
        'id': target.id,
        'staff_id': target.staff_id,
        'staff_name': target.staff.get_full_name() or target.staff.username,
        'target_type': target.target_type,
        'target_value': str(target.target_value),
        'period': target.period,
        'start_date': target.start_date.strftime('%Y-%m-%d'),
        'end_date': target.end_date.strftime('%Y-%m-%d'),
        'note': target.note,
    })


def _calculate_achievement(target):
    """Calculate achievement value for a target based on its type and period"""
    start = target.start_date
    end = target.end_date

    if target.target_type == 'sales':
        # Sum of total_amount from delivered/confirmed orders created by this staff in the period
        result = Order.objects.filter(
            created_by=target.staff,
            created_at__date__gte=start,
            created_at__date__lte=end,
            is_deleted=False,
        ).filter(
            Q(order_status='delivered') | Q(status='delivered') |
            Q(order_status='confirmed') | Q(status='confirmed') |
            Q(order_status='shipped') | Q(status='shipped')
        ).aggregate(total=Sum('total_amount'))
        return float(result['total'] or 0)

    elif target.target_type == 'warehouse':
        # Count of dispatched/processed orders in the period
        count = Order.objects.filter(
            created_by=target.staff,
            created_at__date__gte=start,
            created_at__date__lte=end,
            is_deleted=False,
        ).filter(
            Q(order_status='dispatched') | Q(status='dispatched') |
            Q(order_status='shipped') | Q(status='shipped') |
            Q(order_status='delivered') | Q(status='delivered') |
            Q(order_status='packed') | Q(status='packed')
        ).count()
        return float(count)

    return 0


# ===================== PICK AND DROP LOGISTICS =====================

def send_single_order_to_pnd(request, order, default_weight=1.0):
    """
    Helper function to send single order to Pick and Drop
    Returns: dict with 'status' and 'message'
    """
    import requests
    from django.conf import settings
    from django.utils import timezone

    try:
        # CHECK 1: Already sent?
        if hasattr(order, 'pnd_order_id') and order.pnd_order_id:
            return {
                'status': 'skipped',
                'message': f'Already sent (PND ID: {order.pnd_order_id})'
            }

        # CHECK 2: Required fields
        missing = []
        if not getattr(order, 'customer_name', None):
            missing.append('customer_name')
        if not getattr(order, 'customer_phone', None):
            missing.append('customer_phone')
        if not getattr(order, 'shipping_address', None):
            missing.append('shipping_address')

        if missing:
            return {
                'status': 'skipped',
                'message': f'Missing required fields: {", ".join(missing)}'
            }

        # Get product description
        product_name = 'General Items'
        try:
            if hasattr(order, 'items'):
                items = order.items.select_related('product_variation').all()[:3]
                if items:
                    parts = []
                    for item in items:
                        qty = getattr(item, 'quantity', 1) or 1
                        name = item.product_name or 'Item'
                        var_name = item.variation_name or (item.product_variation.variation_name if item.product_variation else None)
                        if var_name:
                            name = f"{name} ({var_name})"
                        parts.append(f"{qty}x {name}")
                    product_name = ', '.join(parts)
                    total_items = order.items.count()
                    if total_items > 3:
                        product_name += f' and {total_items - 3} more'
        except Exception:
            pass

        # Get weight
        weight = default_weight
        if hasattr(order, 'package_weight') and order.package_weight:
            try:
                weight = float(order.package_weight)
            except Exception:
                weight = default_weight

        # Get destination branch
        destination_branch = 'KATHMANDU VALLEY'
        if hasattr(order, 'branch_city') and order.branch_city:
            destination_branch = str(order.branch_city)

        # Get API credentials
        api_key = getattr(settings, 'PND_API_KEY', None)
        api_secret = getattr(settings, 'PND_API_SECRET', None)
        base_url = getattr(settings, 'PND_API_BASE_URL', None)

        if not base_url or not api_key or not api_secret:
            return {
                'status': 'error',
                'message': 'Pick and Drop API not configured in settings'
            }

        # Build API URL
        base_url = base_url.rstrip('/')
        api_url = f"{base_url}/api/method/logi360.api.create_order"

        # Sanitize phone number: PND API requires exactly 10 digits
        import re
        raw_phone = str(order.customer_phone or '')
        digits_only = re.sub(r'\D', '', raw_phone)
        # If number starts with country code 977, strip it
        if digits_only.startswith('977') and len(digits_only) == 13:
            digits_only = digits_only[3:]
        # Take last 10 digits if longer
        if len(digits_only) > 10:
            digits_only = digits_only[-10:]
        if len(digits_only) != 10:
            return {
                'status': 'error',
                'message': f'Phone number must be 10 digits. Got: {raw_phone} ({len(digits_only)} digits)'
            }

        # Build payload
        payload = {
            "customerName": str(order.customer_name)[:50],
            "primaryMobileNo": digits_only,
            "destinationBranch": destination_branch,
            "destinationCityArea": str(order.shipping_address or destination_branch)[:200],
            "codAmount": float(order.total_amount or 0),
            "orderDescription": str(product_name)[:200],
            "vendorTrackingNumber": str(order.order_number),
            "landmark": str(order.landmark or order.shipping_address or 'N/A')[:200],
            "weight": str(weight),
            "orderType": "Regular",
            "instruction": str(order.notes or ''),
        }

        # Call Pick and Drop API
        response = requests.post(
            api_url,
            json=payload,
            headers={
                'Authorization': f'token {api_key}:{api_secret}',
                'Content-Type': 'application/json'
            },
            timeout=30
        )

        # Handle response
        if response.status_code == 200:
            try:
                data = response.json()
            except Exception:
                return {
                    'status': 'error',
                    'message': 'Invalid JSON response from Pick and Drop'
                }

            # Success: {"message": {"status": "success", "data": {...}}}
            msg = data.get('message', {})
            if isinstance(msg, dict) and msg.get('status') == 'success':
                response_data = msg.get('data', {})
                pnd_order_id = response_data.get('orderID', '')
                tracking_url = response_data.get('tracking_url', '')

                # Update order
                order.pnd_order_id = str(pnd_order_id)
                order.pnd_status = response_data.get('status', 'Order Created')
                order.pnd_created_at = timezone.now()
                order.pnd_destination_branch = destination_branch
                order.pnd_tracking_url = tracking_url or ''
                order.save()

                # Log activity
                try:
                    from dashboard.models import OrderActivityLog
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user if request else None,
                        action_type='status_changed',
                        description=f'Sent to Pick and Drop Logistics (ID: {pnd_order_id})'
                    )
                except Exception:
                    pass

                return {
                    'status': 'success',
                    'message': f'Sent to Pick and Drop (ID: {pnd_order_id})'
                }
            else:
                if isinstance(msg, dict):
                    error_msg = msg.get('message', str(msg))
                elif isinstance(msg, str):
                    error_msg = msg
                else:
                    error_msg = str(data)
                return {
                    'status': 'error',
                    'message': f'Pick and Drop error: {error_msg}'
                }
        else:
            return {
                'status': 'error',
                'message': f'Pick and Drop API error (HTTP {response.status_code})'
            }

    except Exception as e:
        return {
            'status': 'error',
            'message': f'Error: {str(e)}'
        }


def orders_bulk_pnd_send(request):
    """
    Bulk send multiple orders to Pick and Drop logistics
    """
    from pick_and_drop.models import PNDBulkLog, PNDBulkLogOrder, PNDBulkLogDetail

    if request.method != 'POST':
        messages.error(request, 'Invalid request method')
        return redirect('orders_list')

    try:
        # Get form data
        order_ids = request.POST.getlist('order_ids')
        default_weight = float(request.POST.get('default_weight', 1.0))
        auto_set_logistics = request.POST.get('auto_set_logistics') == 'on'

        if not order_ids:
            messages.error(request, 'No orders selected')
            return redirect('orders_list')

        # Get orders
        orders = Order.objects.filter(id__in=order_ids, is_deleted=False)

        if not orders.exists():
            messages.error(request, 'No valid orders found')
            return redirect('orders_list')

        count = orders.count()

        # Create PND Bulk Log
        bulk_log = PNDBulkLog.objects.create(
            batch_number=PNDBulkLog.generate_batch_number(),
            total_orders=count,
            status='processing',
            created_by=request.user,
        )
        PNDBulkLogDetail.objects.create(
            batch=bulk_log,
            action='batch_started',
            message=f'Bulk send started with {count} order(s)',
            user=request.user,
        )

        # Track results
        success_count = 0
        skip_count = 0
        error_count = 0
        error_details = []

        # Process each order
        for order in orders:
            result = send_single_order_to_pnd(
                request,
                order,
                default_weight=default_weight
            )

            if result['status'] == 'success':
                success_count += 1
                log_status = 'success'
                log_action = 'order_sent'
                # Auto-set logistics if enabled
                if auto_set_logistics and order.logistics != 'pick_and_drop':
                    order.logistics = 'pick_and_drop'
                    order.save()
            elif result['status'] == 'skipped':
                skip_count += 1
                log_status = 'skipped'
                log_action = 'order_skipped'
            else:
                error_count += 1
                log_status = 'failed'
                log_action = 'order_failed'
                error_details.append(f"#{order.order_number}: {result.get('message', 'Unknown error')}")

            # Create log entry for this order
            order.refresh_from_db()
            PNDBulkLogOrder.objects.create(
                batch=bulk_log,
                order=order,
                order_number=order.order_number or '',
                customer_name=order.customer_name or '',
                customer_phone=order.customer_phone or '',
                shipping_address=order.shipping_address or '',
                cod_amount=order.total_amount or 0,
                destination_branch=order.branch_city or '',
                pnd_order_id=order.pnd_order_id,
                status=log_status,
                message=result.get('message', ''),
            )
            PNDBulkLogDetail.objects.create(
                batch=bulk_log,
                action=log_action,
                order_number=order.order_number or '',
                message=result.get('message', ''),
                user=request.user,
            )

        # Update bulk log with final counts and status
        if error_count == count:
            final_status = 'failed'
        elif success_count == count:
            final_status = 'completed'
        elif success_count > 0:
            final_status = 'partial'
        else:
            final_status = 'failed'

        bulk_log.success_count = success_count
        bulk_log.failed_count = error_count
        bulk_log.skipped_count = skip_count
        bulk_log.status = final_status
        bulk_log.completed_at = timezone.now()
        bulk_log.save()

        PNDBulkLogDetail.objects.create(
            batch=bulk_log,
            action='batch_completed',
            message=f'Batch completed: {success_count} success, {error_count} failed, {skip_count} skipped',
            user=request.user,
        )

        # Show results
        if success_count > 0:
            messages.success(
                request,
                f"Successfully sent {success_count} order(s) to Pick and Drop."
            )

        if skip_count > 0:
            messages.warning(
                request,
                f"Skipped {skip_count} order(s) (Already sent or missing info)."
            )

        if error_count > 0:
            detail_str = '; '.join(error_details[:5])
            extra = f' (+{error_count - 5} more)' if error_count > 5 else ''
            messages.error(
                request,
                f"Failed {error_count} order(s): {detail_str}{extra}"
            )

    except Exception as e:
        messages.error(request, f'Bulk send error: {str(e)}')
        import traceback
        traceback.print_exc()

    return redirect('orders_list')


@login_required
def pnd_bulk_log_detail(request, log_id):
    """View details of a single PND bulk send batch"""
    from pick_and_drop.models import PNDBulkLog, PNDBulkLogOrder, PNDBulkLogDetail

    bulk_log = get_object_or_404(PNDBulkLog, id=log_id, is_deleted=False)
    batch_orders = PNDBulkLogOrder.objects.filter(batch=bulk_log)
    log_details = PNDBulkLogDetail.objects.filter(batch=bulk_log).order_by('timestamp')

    context = {
        'bulk_log': bulk_log,
        'batch_orders': batch_orders,
        'log_details': log_details,
    }
    return render(request, 'pnd_bulk_log_detail.html', context)


@login_required
def pnd_bulk_log_trash(request, log_id):
    """Move a PND bulk log to trash (soft delete)"""
    from pick_and_drop.models import PNDBulkLog

    if request.method == 'POST':
        bulk_log = get_object_or_404(PNDBulkLog, id=log_id, is_deleted=False)
        bulk_log.is_deleted = True
        bulk_log.deleted_at = timezone.now()
        bulk_log.save()
        messages.success(request, f'Batch "{bulk_log.batch_number}" moved to trash.')

    return redirect('logistics_bulk_logs_list')


@login_required
def pnd_bulk_logs_bulk_action(request):
    """Handle bulk actions on PND bulk logs"""
    from pick_and_drop.models import PNDBulkLog

    if request.method == 'POST':
        action = request.POST.get('bulk_action')
        log_ids = request.POST.getlist('log_ids')

        if not log_ids:
            messages.warning(request, 'No batches selected.')
            return redirect('logistics_bulk_logs_list')

        if action == 'move_to_trash':
            count = PNDBulkLog.objects.filter(id__in=log_ids, is_deleted=False).update(
                is_deleted=True,
                deleted_at=timezone.now()
            )
            messages.success(request, f'{count} batch(es) moved to trash.')

    return redirect('logistics_bulk_logs_list')


# ==================== UNIFIED LOGISTICS VIEWS ====================

@login_required
def logistics_orders_list(request):
    """
    Unified Logistics Orders Page - Shows NCM and/or PND orders with a provider toggle filter
    """
    provider = request.GET.get('provider', 'all').strip()

    # Build base queryset based on provider filter
    if provider == 'ncm':
        orders = Order.objects.select_related('customer', 'created_by').filter(
            is_deleted=False,
            logistics='ncm',
            ncm_order_id__isnull=False
        ).order_by('-ncm_created_at')
    elif provider == 'pnd':
        orders = Order.objects.select_related('customer', 'created_by').filter(
            is_deleted=False,
            logistics='pick_and_drop',
            pnd_order_id__isnull=False
        ).order_by('-pnd_created_at')
    else:
        # All logistics orders
        ncm_orders = Order.objects.select_related('customer', 'created_by').filter(
            is_deleted=False,
            logistics='ncm',
            ncm_order_id__isnull=False
        )
        pnd_orders = Order.objects.select_related('customer', 'created_by').filter(
            is_deleted=False,
            logistics='pick_and_drop',
            pnd_order_id__isnull=False
        )
        orders = (ncm_orders | pnd_orders).order_by('-created_at')

    # Get filter parameters
    search_query = request.GET.get('search', '').strip()
    branch_filter = request.GET.get('branch', '').strip()
    status_filter = request.GET.get('status', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    # Search filter
    if search_query:
        if provider == 'ncm':
            orders = orders.filter(
                Q(order_number__icontains=search_query) |
                Q(ncm_order_id__icontains=search_query) |
                Q(customer_name__icontains=search_query) |
                Q(customer_phone__icontains=search_query)
            )
        elif provider == 'pnd':
            orders = orders.filter(
                Q(order_number__icontains=search_query) |
                Q(pnd_order_id__icontains=search_query) |
                Q(customer_name__icontains=search_query) |
                Q(customer_phone__icontains=search_query)
            )
        else:
            orders = orders.filter(
                Q(order_number__icontains=search_query) |
                Q(ncm_order_id__icontains=search_query) |
                Q(pnd_order_id__icontains=search_query) |
                Q(customer_name__icontains=search_query) |
                Q(customer_phone__icontains=search_query)
            )

    # Branch filter
    if branch_filter:
        if provider == 'ncm':
            orders = orders.filter(ncm_from_branch=branch_filter)
        elif provider == 'pnd':
            orders = orders.filter(pnd_destination_branch=branch_filter)
        else:
            orders = orders.filter(
                Q(ncm_from_branch=branch_filter) | Q(pnd_destination_branch=branch_filter)
            )

    # Status filter
    if status_filter:
        if provider == 'ncm':
            orders = orders.filter(ncm_status=status_filter)
        elif provider == 'pnd':
            orders = orders.filter(pnd_status=status_filter)
        else:
            orders = orders.filter(
                Q(ncm_status=status_filter) | Q(pnd_status=status_filter)
            )

    # Date range filter
    if date_from:
        try:
            if provider == 'ncm':
                orders = orders.filter(ncm_created_at__date__gte=date_from)
            elif provider == 'pnd':
                orders = orders.filter(pnd_created_at__date__gte=date_from)
            else:
                orders = orders.filter(created_at__date__gte=date_from)
        except:
            pass

    if date_to:
        try:
            if provider == 'ncm':
                orders = orders.filter(ncm_created_at__date__lte=date_to)
            elif provider == 'pnd':
                orders = orders.filter(pnd_created_at__date__lte=date_to)
            else:
                orders = orders.filter(created_at__date__lte=date_to)
        except:
            pass

    # Get total count before pagination
    total_orders = orders.count()

    # Get unique branches and statuses for filter dropdowns based on provider
    if provider == 'ncm':
        branches = list(Order.objects.filter(
            is_deleted=False, logistics='ncm'
        ).exclude(ncm_from_branch__isnull=True).exclude(ncm_from_branch='').values_list(
            'ncm_from_branch', flat=True
        ).distinct().order_by('ncm_from_branch'))
        statuses = list(Order.objects.filter(
            is_deleted=False, logistics='ncm'
        ).exclude(ncm_status__isnull=True).exclude(ncm_status='').values_list(
            'ncm_status', flat=True
        ).distinct().order_by('ncm_status'))
    elif provider == 'pnd':
        branches = list(Order.objects.filter(
            is_deleted=False, logistics='pick_and_drop'
        ).exclude(pnd_destination_branch__isnull=True).exclude(pnd_destination_branch='').values_list(
            'pnd_destination_branch', flat=True
        ).distinct().order_by('pnd_destination_branch'))
        statuses = list(Order.objects.filter(
            is_deleted=False, logistics='pick_and_drop'
        ).exclude(pnd_status__isnull=True).exclude(pnd_status='').values_list(
            'pnd_status', flat=True
        ).distinct().order_by('pnd_status'))
    else:
        ncm_branches = list(Order.objects.filter(
            is_deleted=False, logistics='ncm'
        ).exclude(ncm_from_branch__isnull=True).exclude(ncm_from_branch='').values_list(
            'ncm_from_branch', flat=True
        ).distinct())
        pnd_branches = list(Order.objects.filter(
            is_deleted=False, logistics='pick_and_drop'
        ).exclude(pnd_destination_branch__isnull=True).exclude(pnd_destination_branch='').values_list(
            'pnd_destination_branch', flat=True
        ).distinct())
        branches = sorted(set(ncm_branches + pnd_branches))

        ncm_statuses = list(Order.objects.filter(
            is_deleted=False, logistics='ncm'
        ).exclude(ncm_status__isnull=True).exclude(ncm_status='').values_list(
            'ncm_status', flat=True
        ).distinct())
        pnd_statuses = list(Order.objects.filter(
            is_deleted=False, logistics='pick_and_drop'
        ).exclude(pnd_status__isnull=True).exclude(pnd_status='').values_list(
            'pnd_status', flat=True
        ).distinct())
        statuses = sorted(set(ncm_statuses + pnd_statuses))

    # Count per provider
    ncm_count = Order.objects.filter(is_deleted=False, logistics='ncm', ncm_order_id__isnull=False).count()
    pnd_count = Order.objects.filter(is_deleted=False, logistics='pick_and_drop', pnd_order_id__isnull=False).count()

    # Pagination
    paginator = Paginator(orders, 25)
    page_number = request.GET.get('page', 1)
    orders_page = paginator.get_page(page_number)

    # Get product names for paginated orders
    order_products = {}
    for order in orders_page:
        try:
            first_item = order.items.first()
            if first_item:
                order_products[order.id] = first_item.product_name
            else:
                order_products[order.id] = "No products"
        except:
            order_products[order.id] = "No products"

    # GET ACTIVITY LOGS FOR PAGINATED ORDERS
    activity_logs = {}
    try:
        from dashboard.models import OrderActivityLog

        order_ids = [order.id for order in orders_page]
        all_logs = OrderActivityLog.objects.filter(
            order_id__in=order_ids
        ).select_related('user', 'order').order_by('-created_at')

        for log in all_logs:
            if log.order_id not in activity_logs:
                activity_logs[log.order_id] = []
            activity_logs[log.order_id].append(log)

        for order_id in activity_logs:
            activity_logs[order_id] = activity_logs[order_id][:10]
    except Exception:
        activity_logs = {}

    # Trash count for NCM
    trash_count = Order.objects.filter(is_deleted=True, logistics='ncm').count()

    context = {
        'orders': orders_page,
        'total_orders': total_orders,
        'search_query': search_query,
        'branch_filter': branch_filter,
        'status_filter': status_filter,
        'date_from': date_from,
        'date_to': date_to,
        'branches': branches,
        'statuses': statuses,
        'order_products': order_products,
        'activity_logs': activity_logs,
        'provider': provider,
        'ncm_count': ncm_count,
        'pnd_count': pnd_count,
        'trash_count': trash_count,
    }

    return render(request, 'logistics_orders_list.html', context)


@login_required
def logistics_bulk_logs_list(request):
    """
    Unified Bulk Logs Page - Shows NCM and/or PND bulk logs with a provider toggle filter.
    Supports provider=all (default), ncm, or pnd.
    """
    from ncm.models import NCMBulkLog, NCMBulkLogOrder
    from pick_and_drop.models import PNDBulkLog, PNDBulkLogOrder
    from itertools import chain

    provider = request.GET.get('provider', 'all').strip()
    if provider not in ('all', 'ncm', 'pnd'):
        provider = 'all'

    # Handle AJAX request for expandable order rows
    ajax_batch_id = request.GET.get('ajax_batch_orders')
    ajax_provider = request.GET.get('ajax_provider', '').strip()
    if ajax_batch_id:
        try:
            # Determine which model to query based on ajax_provider param
            if ajax_provider == 'pnd':
                batch_orders = PNDBulkLogOrder.objects.filter(batch_id=ajax_batch_id)
                orders_data = []
                for o in batch_orders:
                    orders_data.append({
                        'order_number': o.order_number,
                        'customer_name': o.customer_name,
                        'customer_phone': o.customer_phone,
                        'address': o.shipping_address[:80] if o.shipping_address else '',
                        'cod_amount': str(o.cod_amount),
                        'branch': o.destination_branch or '-',
                        'logistics_order_id': o.pnd_order_id or '',
                        'status': o.status,
                        'status_display': o.get_status_display(),
                    })
            else:
                batch_orders = NCMBulkLogOrder.objects.filter(batch_id=ajax_batch_id)
                orders_data = []
                for o in batch_orders:
                    orders_data.append({
                        'order_number': o.order_number,
                        'customer_name': o.customer_name,
                        'customer_phone': o.customer_phone,
                        'address': o.shipping_address[:80] if o.shipping_address else '',
                        'cod_amount': str(o.cod_amount),
                        'branch': o.destination_branch or '-',
                        'logistics_order_id': o.ncm_order_id or '',
                        'status': o.status,
                        'status_display': o.get_status_display(),
                    })
            return JsonResponse({'orders': orders_data})
        except Exception:
            return JsonResponse({'orders': []})

    # Filters
    search_query = request.GET.get('search', '').strip()
    branch_filter = request.GET.get('branch', '').strip()
    status_filter = request.GET.get('status', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    def apply_filters(qs, branch_field):
        """Apply common filters to a queryset."""
        nonlocal search_query, branch_filter, status_filter, date_from, date_to
        if search_query:
            qs = qs.filter(
                Q(batch_number__icontains=search_query) |
                Q(orders__order_number__icontains=search_query) |
                Q(orders__customer_name__icontains=search_query)
            ).distinct()
        if branch_filter:
            qs = qs.filter(**{branch_field: branch_filter})
        if status_filter:
            qs = qs.filter(status=status_filter)
        if date_from:
            try:
                qs = qs.filter(created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
            except ValueError:
                pass
        if date_to:
            try:
                qs = qs.filter(created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
            except ValueError:
                pass
        return qs

    branches = []

    if provider == 'ncm':
        ncm_logs = apply_filters(NCMBulkLog.objects.filter(is_deleted=False), 'from_branch')
        all_logs_ncm = NCMBulkLog.objects.filter(is_deleted=False)
        all_logs_pnd = PNDBulkLog.objects.none()
        branches = list(
            NCMBulkLog.objects.filter(is_deleted=False)
            .values_list('from_branch', flat=True)
            .distinct().order_by('from_branch')
        )
        # Annotate provider for template
        combined_logs = list(ncm_logs.order_by('-created_at'))
        for log in combined_logs:
            log.log_provider = 'ncm'
            log.branch_display = log.from_branch

    elif provider == 'pnd':
        pnd_logs = apply_filters(PNDBulkLog.objects.filter(is_deleted=False), 'destination_branch')
        all_logs_ncm = NCMBulkLog.objects.none()
        all_logs_pnd = PNDBulkLog.objects.filter(is_deleted=False)
        branches = list(
            PNDBulkLog.objects.filter(is_deleted=False)
            .values_list('destination_branch', flat=True)
            .distinct().order_by('destination_branch')
        )
        combined_logs = list(pnd_logs.order_by('-created_at'))
        for log in combined_logs:
            log.log_provider = 'pnd'
            log.branch_display = log.destination_branch

    else:
        # ALL - combine both
        ncm_logs = apply_filters(NCMBulkLog.objects.filter(is_deleted=False), 'from_branch')
        pnd_logs = apply_filters(PNDBulkLog.objects.filter(is_deleted=False), 'destination_branch')
        all_logs_ncm = NCMBulkLog.objects.filter(is_deleted=False)
        all_logs_pnd = PNDBulkLog.objects.filter(is_deleted=False)

        ncm_branches = list(
            NCMBulkLog.objects.filter(is_deleted=False)
            .values_list('from_branch', flat=True)
            .distinct()
        )
        pnd_branches = list(
            PNDBulkLog.objects.filter(is_deleted=False)
            .values_list('destination_branch', flat=True)
            .distinct()
        )
        branches = sorted(set(ncm_branches + pnd_branches))

        ncm_list = list(ncm_logs)
        for log in ncm_list:
            log.log_provider = 'ncm'
            log.branch_display = log.from_branch

        pnd_list = list(pnd_logs)
        for log in pnd_list:
            log.log_provider = 'pnd'
            log.branch_display = log.destination_branch

        combined_logs = sorted(
            chain(ncm_list, pnd_list),
            key=lambda x: x.created_at,
            reverse=True
        )

    # Statistics (across the selected provider scope, unfiltered)
    ncm_stats = all_logs_ncm.aggregate(
        batches=Count('id'),
        orders=Sum('total_orders'),
        success=Sum('success_count'),
        failed=Sum('failed_count'),
    ) if provider in ('ncm', 'all') else {'batches': 0, 'orders': 0, 'success': 0, 'failed': 0}

    pnd_stats = all_logs_pnd.aggregate(
        batches=Count('id'),
        orders=Sum('total_orders'),
        success=Sum('success_count'),
        failed=Sum('failed_count'),
    ) if provider in ('pnd', 'all') else {'batches': 0, 'orders': 0, 'success': 0, 'failed': 0}

    total_batches = (ncm_stats.get('batches') or 0) + (pnd_stats.get('batches') or 0)
    total_orders_sent = (ncm_stats.get('orders') or 0) + (pnd_stats.get('orders') or 0)
    total_success = (ncm_stats.get('success') or 0) + (pnd_stats.get('success') or 0)
    total_failed = (ncm_stats.get('failed') or 0) + (pnd_stats.get('failed') or 0)

    # Manual pagination for combined list
    page_number = request.GET.get('page', 1)
    paginator = Paginator(combined_logs, 20)
    logs = paginator.get_page(page_number)

    context = {
        'logs': logs,
        'total_batches': total_batches,
        'total_orders_sent': total_orders_sent,
        'total_success': total_success,
        'total_failed': total_failed,
        'branches': branches,
        'search_query': search_query,
        'branch_filter': branch_filter,
        'status_filter': status_filter,
        'date_from': date_from,
        'date_to': date_to,
        'provider': provider,
    }
    return render(request, 'logistics_bulk_logs.html', context)


@login_required
def logistics_branches(request):
    """
    Unified Logistics Branches page - currently shows NCM branches.
    Delegates to ncm_branches_json which renders ncm_branches.html for browser requests.
    """
    return ncm_branches_json(request)


# ==================== LOGISTICS BULK LOGS TRASH ====================

@login_required
def logistics_bulk_logs_trash(request):
    """
    Unified Bulk Logs Trash - Shows soft-deleted NCM and PND bulk logs
    """
    from ncm.models import NCMBulkLog
    from pick_and_drop.models import PNDBulkLog
    from itertools import chain

    search_query = request.GET.get('search', '').strip()

    # Get deleted NCM logs
    ncm_logs = NCMBulkLog.objects.filter(is_deleted=True)
    if search_query:
        ncm_logs = ncm_logs.filter(
            Q(batch_number__icontains=search_query) |
            Q(created_by__username__icontains=search_query)
        )

    # Get deleted PND logs
    pnd_logs = PNDBulkLog.objects.filter(is_deleted=True)
    if search_query:
        pnd_logs = pnd_logs.filter(
            Q(batch_number__icontains=search_query) |
            Q(created_by__username__icontains=search_query)
        )

    ncm_list = list(ncm_logs)
    for log in ncm_list:
        log.log_provider = 'ncm'
        log.branch_display = log.from_branch

    pnd_list = list(pnd_logs)
    for log in pnd_list:
        log.log_provider = 'pnd'
        log.branch_display = log.destination_branch

    combined_logs = sorted(
        chain(ncm_list, pnd_list),
        key=lambda x: x.deleted_at or x.created_at,
        reverse=True
    )

    total_trashed = len(combined_logs)

    # Pagination
    paginator = Paginator(combined_logs, 25)
    page_number = request.GET.get('page', 1)
    logs = paginator.get_page(page_number)

    context = {
        'logs': logs,
        'total_trashed': total_trashed,
        'search_query': search_query,
    }
    return render(request, 'logistics_bulk_logs_trash.html', context)


@login_required
@require_http_methods(["POST"])
def logistics_bulk_log_restore(request, provider, log_id):
    """Restore a bulk log from trash"""
    from ncm.models import NCMBulkLog
    from pick_and_drop.models import PNDBulkLog

    try:
        if provider == 'pnd':
            log = get_object_or_404(PNDBulkLog, id=log_id, is_deleted=True)
        else:
            log = get_object_or_404(NCMBulkLog, id=log_id, is_deleted=True)

        log.is_deleted = False
        log.deleted_at = None
        log.save()
        messages.success(request, f'Batch "{log.batch_number}" restored successfully.')
    except Exception as e:
        messages.error(request, f'Error restoring batch: {str(e)}')

    return redirect('logistics_bulk_logs_trash')


@login_required
@require_http_methods(["POST"])
def logistics_bulk_log_permanent_delete(request, provider, log_id):
    """Permanently delete a bulk log (cannot be undone)"""
    from ncm.models import NCMBulkLog
    from pick_and_drop.models import PNDBulkLog

    try:
        if provider == 'pnd':
            log = get_object_or_404(PNDBulkLog, id=log_id, is_deleted=True)
        else:
            log = get_object_or_404(NCMBulkLog, id=log_id, is_deleted=True)

        batch_number = log.batch_number
        log.delete()
        messages.success(request, f'Batch "{batch_number}" permanently deleted.')
    except Exception as e:
        messages.error(request, f'Error deleting batch: {str(e)}')

    return redirect('logistics_bulk_logs_trash')


@login_required
@require_http_methods(["POST"])
def logistics_bulk_logs_trash_bulk_action(request):
    """Bulk actions on trashed bulk logs: restore or permanent delete"""
    from ncm.models import NCMBulkLog
    from pick_and_drop.models import PNDBulkLog

    action = request.POST.get('bulk_action')
    # IDs come as "ncm-123" or "pnd-456"
    item_keys = request.POST.getlist('item_ids')

    if not item_keys:
        messages.warning(request, 'No batches selected.')
        return redirect('logistics_bulk_logs_trash')

    if not action:
        messages.warning(request, 'No action selected.')
        return redirect('logistics_bulk_logs_trash')

    ncm_ids = []
    pnd_ids = []
    for key in item_keys:
        if key.startswith('pnd-'):
            pnd_ids.append(int(key.replace('pnd-', '')))
        elif key.startswith('ncm-'):
            ncm_ids.append(int(key.replace('ncm-', '')))

    count = 0
    if action == 'restore':
        if ncm_ids:
            updated = NCMBulkLog.objects.filter(id__in=ncm_ids, is_deleted=True).update(
                is_deleted=False, deleted_at=None
            )
            count += updated
        if pnd_ids:
            updated = PNDBulkLog.objects.filter(id__in=pnd_ids, is_deleted=True).update(
                is_deleted=False, deleted_at=None
            )
            count += updated
        messages.success(request, f'{count} batch(es) restored successfully.')

    elif action == 'permanent_delete':
        if ncm_ids:
            deleted, _ = NCMBulkLog.objects.filter(id__in=ncm_ids, is_deleted=True).delete()
            count += deleted
        if pnd_ids:
            deleted, _ = PNDBulkLog.objects.filter(id__in=pnd_ids, is_deleted=True).delete()
            count += deleted
        messages.success(request, f'{count} batch(es) permanently deleted.')

    return redirect('logistics_bulk_logs_trash')


@login_required
@require_http_methods(["POST"])
def logistics_bulk_logs_empty_trash(request):
    """Permanently delete ALL trashed bulk logs"""
    from ncm.models import NCMBulkLog
    from pick_and_drop.models import PNDBulkLog

    if not request.user.is_staff:
        messages.error(request, 'Admin access required.')
        return redirect('logistics_bulk_logs_trash')

    try:
        ncm_count, _ = NCMBulkLog.objects.filter(is_deleted=True).delete()
        pnd_count, _ = PNDBulkLog.objects.filter(is_deleted=True).delete()
        total = ncm_count + pnd_count

        if total == 0:
            messages.info(request, 'Trash is already empty.')
        else:
            messages.success(request, f'Trash emptied! {total} batch(es) permanently deleted.')
    except Exception as e:
        messages.error(request, f'Error emptying trash: {str(e)}')

    return redirect('logistics_bulk_logs_trash')
