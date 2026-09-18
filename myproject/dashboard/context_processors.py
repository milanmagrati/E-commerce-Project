from django.db.models import F
from django.core.cache import cache
import re

_HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')

def _safe_color(val, fallback):
    """Only allow valid hex colors to prevent CSS injection."""
    return val if val and _HEX_RE.match(val) else fallback


def company_setup(request):
    """Inject CompanySetup settings into every template context.

    Cached globally for 30 seconds to avoid a DB read on every page load.
    """
    from dashboard.models import CompanySetup

    CACHE_KEY = 'ctx_company_setup'
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    try:
        cs = CompanySetup.get_settings()
    except Exception:
        cs = None

    if cs:
        # Safely build logo/favicon — only expose if the file actually exists on disk
        logo = None
        if cs.logo:
            try:
                if cs.logo.storage.exists(cs.logo.name):
                    logo = cs.logo
            except Exception:
                pass

        favicon = None
        if cs.favicon:
            try:
                if cs.favicon.storage.exists(cs.favicon.name):
                    favicon = cs.favicon
            except Exception:
                pass

        result = {
            'company_settings': cs,
            'COMPANY_NAME': cs.company_name,
            'COMPANY_LOGO': logo,
            'COMPANY_FAVICON': favicon,
            'COMPANY_TAGLINE': cs.tagline,
            'COMPANY_THEME': cs.theme,
            'COMPANY_PRIMARY': _safe_color(cs.primary_color, '#5e72e4'),
            'COMPANY_SECONDARY': _safe_color(cs.secondary_color, '#825ee4'),
        }
    else:
        result = {
            'COMPANY_NAME': 'Trendy Shopping',
            'COMPANY_LOGO': None,
            'COMPANY_FAVICON': None,
            'COMPANY_TAGLINE': '',
            'COMPANY_PRIMARY': '#5e72e4',
            'COMPANY_SECONDARY': '#825ee4',
            'COMPANY_THEME': 'default',
        }

    # Cache for 30 seconds — company settings rarely change
    try:
        cache.set(CACHE_KEY, result, 30)
    except Exception:
        pass  # If cache backend fails, return uncached result
    return result


def low_stock_notifications(request):
    """
    Provide low stock notification data for the global notification bar.

    Cached per-user for 60 seconds.  Without caching, this fires 4–6 DB
    queries on EVERY page load for every logged-in user with stock permissions,
    which under even modest load exhausts the MySQL connection pool → 500s.
    """
    if not request.user.is_authenticated:
        return {}

    # Check role-based permission
    user = request.user
    if not (getattr(user, 'is_administrator', False) or getattr(user, 'can_view_low_stock_alerts', False)):
        return {}

    # Per-user cache key so each user sees their own data
    CACHE_KEY = f'ctx_low_stock_{user.pk}'
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    from dashboard.models import Product, ProductVariation

    items = []

    # Check if any product has a custom threshold configured in Alert Settings
    has_custom_thresholds = Product.objects.filter(
        is_deleted=False,
        low_stock_threshold__gt=0,
    ).exists()

    has_var_thresholds = ProductVariation.objects.filter(
        product__is_deleted=False,
        low_stock_threshold__gt=0,
    ).exists()

    if has_custom_thresholds or has_var_thresholds:
        # --- Products matching Alert Settings thresholds ---

        # Out of stock products (threshold configured, stock = 0)
        out_of_stock_products = Product.objects.filter(
            is_deleted=False,
            low_stock_threshold__gt=0,
            stock=0,
        ).order_by('name')[:10]

        for p in out_of_stock_products:
            items.append({'name': p.name, 'stock': 0, 'status': 'out'})

        # Low stock products (threshold configured, stock <= threshold, stock > 0)
        low_stock_products = Product.objects.filter(
            is_deleted=False,
            low_stock_threshold__gt=0,
            stock__lte=F('low_stock_threshold'),
            stock__gt=0,
        ).order_by('stock')[:10]

        for p in low_stock_products:
            items.append({'name': p.name, 'stock': p.stock, 'status': 'low'})

        # --- Variations matching Alert Settings thresholds ---

        # Out of stock variations
        out_of_stock_variations = ProductVariation.objects.filter(
            product__is_deleted=False,
            low_stock_threshold__gt=0,
            stock=0,
        ).select_related('product').order_by('product__name')[:10]

        for v in out_of_stock_variations:
            vname = v.variation_name or v.sku
            items.append({'name': f"{v.product.name} ({vname})", 'stock': 0, 'status': 'out'})

        # Low stock variations
        low_stock_variations = ProductVariation.objects.filter(
            product__is_deleted=False,
            low_stock_threshold__gt=0,
            stock__lte=F('low_stock_threshold'),
            stock__gt=0,
        ).select_related('product').order_by('stock')[:10]

        for v in low_stock_variations:
            vname = v.variation_name or v.sku
            items.append({'name': f"{v.product.name} ({vname})", 'stock': v.stock, 'status': 'low'})
    else:
        # Fallback: no thresholds configured at all, show products with stock <= 10
        fallback_products = Product.objects.filter(
            is_deleted=False,
            stock__lte=10,
            stock__gt=0,
        ).order_by('stock')[:10]
        for p in fallback_products:
            items.append({'name': p.name, 'stock': p.stock, 'status': 'low'})

    # Sort: out-of-stock first, then by stock ascending; limit to 10
    items.sort(key=lambda x: (0 if x['status'] == 'out' else 1, x['stock']))
    items = items[:10]

    result = {
        'low_stock_notification_products': items,
        'low_stock_notification_count': len(items),
    }

    # Cache per-user for 60 seconds
    try:
        cache.set(CACHE_KEY, result, 60)
    except Exception:
        pass
    return result


def maintenance_mode(request):
    """Inject maintenance mode state into every template context.

    Cached globally for 30 seconds.
    """
    if not request.user.is_authenticated:
        return {}

    CACHE_KEY = 'ctx_maintenance_mode'
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        # Still need to compute IS_ADMIN_USER per-request (not cached)
        is_admin = (
            getattr(request.user, 'is_superuser', False) or
            getattr(request.user, 'role', '') == 'administrator'
        )
        return {**cached, 'IS_ADMIN_USER': is_admin}

    try:
        from dashboard.models import MaintenanceMode
        mm = MaintenanceMode.get_settings()
        base = {
            'MAINTENANCE_MODE': mm.is_enabled,
            'MAINTENANCE_MESSAGE': mm.message,
        }
    except Exception:
        base = {
            'MAINTENANCE_MODE': False,
            'MAINTENANCE_MESSAGE': '',
        }

    try:
        cache.set(CACHE_KEY, base, 30)
    except Exception:
        pass

    is_admin = (
        getattr(request.user, 'is_superuser', False) or
        getattr(request.user, 'role', '') == 'administrator'
    )
    return {**base, 'IS_ADMIN_USER': is_admin}


def incomplete_attendance_alert(request):
    """
    Site-wide Incomplete Attendance alert (toast + review/fix modal).

    Previously this was computed only inside the HRM Attendance Policies view,
    so the alert only ever appeared on that one page. As a context processor
    it's available in every template that extends base.html, matching the
    permission checks already used by the HRM views for this feature
    (`can_view_hrm_incomplete_attendance` / `can_view_dashboard_incomplete_attendance`).
    """
    if not request.user.is_authenticated:
        return {}

    user = request.user
    can_view = (
        getattr(user, 'is_superuser', False) or getattr(user, 'role', '') == 'administrator'
        or getattr(user, 'can_view_hrm_incomplete_attendance', False)
        or getattr(user, 'can_view_dashboard_incomplete_attendance', False)
    )
    if not can_view:
        return {}

    can_fix = (
        getattr(user, 'is_superuser', False) or getattr(user, 'role', '') == 'administrator'
        or (getattr(user, 'can_fix_hrm_incomplete_attendance', False) and getattr(user, 'can_view_hrm_incomplete_attendance', False))
    )

    CACHE_KEY = 'ctx_inc_att_alert_settings'
    alert_settings = cache.get(CACHE_KEY)
    if alert_settings is None:
        from hrm.models import AttendanceAlertSettings
        try:
            s = AttendanceAlertSettings.get_settings()
            alert_settings = {'mode': s.mode, 'interval_minutes': s.interval_minutes}
        except Exception:
            alert_settings = {'mode': 'refresh', 'interval_minutes': 60}
        try:
            cache.set(CACHE_KEY, alert_settings, 30)
        except Exception:
            pass

    from dashboard.timezone_utils import get_nepali_now

    return {
        'can_view_incomplete_attendance_alert': True,
        'can_fix_incomplete_attendance': can_fix,
        'incomplete_attendance_today': get_nepali_now().date().isoformat(),
        'INC_ATT_ALERT_MODE': alert_settings['mode'],
        'INC_ATT_ALERT_INTERVAL_MINUTES': alert_settings['interval_minutes'],
    }


def expiry_notifications(request):
    """
    Provide global expiry notifications for products expiring within 30 days.

    Cached per-user for 60 seconds to avoid repeated DB queries on every page load.
    """
    if not request.user.is_authenticated:
        return {}

    # Only show to users with permission to view products/inventory
    user = request.user
    if not (getattr(user, 'is_superuser', False) or
            getattr(user, 'role', '') in ['administrator', 'warehouse']):
        return {}

    CACHE_KEY = f'ctx_expiry_{user.pk}'
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    from dashboard.models import Product
    from django.utils import timezone
    from datetime import timedelta

    today = timezone.now().date()
    warning_date = today + timedelta(days=30)

    items = []

    # 1. Check direct product expiry dates
    expiring_products = Product.objects.filter(
        is_deleted=False,
        expiry_date__isnull=False,
        expiry_date__lte=warning_date
    ).order_by('expiry_date')[:15]

    for p in expiring_products:
        days = (p.expiry_date - today).days
        status = 'expired' if days < 0 else ('critical' if days <= 7 else 'warning')
        items.append({
            'id': p.id,
            'name': p.name,
            'days': days,
            'date': p.expiry_date,
            'status': status,
            'type': 'product'
        })

    # Sort: expired first, then ascending by days left
    items.sort(key=lambda x: x['days'])
    items = items[:10]  # Limit to top 10 most urgent

    result = {
        'expiry_notification_items': items,
        'expiry_notification_count': len(items),
        'has_expired_items': any(item['status'] == 'expired' for item in items),
        'has_critical_items': any(item['status'] in ['expired', 'critical'] for item in items)
    }

    # Cache per-user for 60 seconds
    try:
        cache.set(CACHE_KEY, result, 60)
    except Exception:
        pass
    return result

