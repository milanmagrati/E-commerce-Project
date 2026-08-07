from django import template
from decimal import Decimal
from django.utils import timezone
from datetime import date, timedelta
import pytz

register = template.Library()

@register.filter(name='order_badge')
def order_badge(status):
    """Return Bootstrap badge color class based on order status"""
    badge_classes = {
        'pending': 'warning',
        'processing': 'info',
        'confirmed': 'primary',
        'packed': 'secondary',
        'shipped': 'info',
        'delivered': 'success',
        'cancelled': 'danger',
        'returned': 'dark',
        'return': 'dark',
        'return_processing': 'warning',
    }
    return badge_classes.get(status, 'secondary')


@register.filter(name='get_badge_class')
def get_badge_class(status):
    """Return Bootstrap badge color class for order status"""
    badge_classes = {
        'pending': 'warning',
        'processing': 'info',
        'confirmed': 'primary',
        'packed': 'secondary',
        'shipped': 'info',
        'delivered': 'success',
        'cancelled': 'danger',
        'returned': 'dark',
        'return': 'dark',
        'return_processing': 'warning',
        'in_stock': 'success',
        'low_stock': 'warning',
        'out_of_stock': 'danger',
    }
    return badge_classes.get(status, 'secondary')


@register.filter(name='get_payment_badge')
def get_payment_badge(status):
    """Return Bootstrap badge color class for payment status"""
    badge_classes = {
        'pending': 'warning',
        'paid': 'success',
        'failed': 'danger',
        'refunded': 'info',
        'cod': 'secondary',
    }
    return badge_classes.get(status, 'secondary')


@register.filter(name='stock_badge')
def stock_badge(stock):
    """Return Bootstrap badge color based on stock level"""
    try:
        stock = int(stock)
        if stock > 10:
            return 'success'
        elif stock > 0:
            return 'warning'
        else:
            return 'danger'
    except (ValueError, TypeError):
        return 'secondary'


@register.filter(name='currency')
def currency(value):
    """Format value as currency"""
    try:
        return f"रू {float(value):,.2f}"
    except (ValueError, TypeError):
        return "रू 0.00"


@register.filter(name='percentage')
def percentage(value, total):
    """Calculate percentage"""
    try:
        if float(total) == 0:
            return 0
        return round((float(value) / float(total)) * 100, 1)
    except (ValueError, TypeError, ZeroDivisionError):
        return 0


@register.filter(name='multiply')
def multiply(value, arg):
    """Multiply value by arg"""
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0


@register.filter(name='subtract')
def subtract(value, arg):
    """Subtract arg from value"""
    try:
        return float(value) - float(arg)
    except (ValueError, TypeError):
        return 0


@register.filter(name='status_icon')
def status_icon(status):
    """Return icon class for status"""
    icons = {
        'pending': 'fa-clock',
        'processing': 'fa-cog fa-spin',
        'confirmed': 'fa-check-circle',
        'packed': 'fa-box',
        'shipped': 'fa-shipping-fast',
        'delivered': 'fa-check-double',
        'cancelled': 'fa-times-circle',
        'returned': 'fa-undo',
        'return': 'fa-box-open',
        'return_processing': 'fa-truck',
    }
    return icons.get(status, 'fa-question-circle')


@register.filter(name='payment_icon')
def payment_icon(method):
    """Return icon class for payment method"""
    icons = {
        'cash': 'fa-money-bill-wave',
        'esewa': 'fa-mobile-alt',
        'khalti': 'fa-mobile-alt',
        'ime_pay': 'fa-mobile-alt',
        'bank_transfer': 'fa-university',
        'cod': 'fa-hand-holding-usd',
    }
    return icons.get(method, 'fa-credit-card')


@register.simple_tag
def get_order_status_color(status):
    """Return color for order status"""
    colors = {
        'pending': '#ffc107',
        'processing': '#17a2b8',
        'confirmed': '#007bff',
        'packed': '#6c757d',
        'shipped': '#17a2b8',
        'delivered': '#28a745',
        'cancelled': '#dc3545',
        'returned': '#343a40',
        'return': '#343a40',
        'return_processing': '#fd7e14',
    }
    return colors.get(status, '#6c757d')


@register.filter(name='range_filter')
def range_filter(value):
    """Create a range for iteration"""
    try:
        return range(int(value))
    except (ValueError, TypeError):
        return range(0)


@register.filter(name='get_item')
def get_item(dictionary, key):
    """Get item from dictionary"""
    if dictionary:
        return dictionary.get(key)
    return None


@register.filter(name='replace')
def replace(value, arg):
    """Replace substring in string - usage: {{ value|replace:"old:new" }}"""
    try:
        if ':' not in arg:
            return value
        old, new = arg.split(':', 1)
        return str(value).replace(old, new)
    except (ValueError, AttributeError):
        return value

# Nepali Timezone Filters
NEPALI_TZ = pytz.timezone('Asia/Kathmandu')

@register.filter(name='nepali_datetime')
def nepali_datetime(dt_value):
    """Format datetime in Nepali timezone (Asia/Kathmandu) with full date and time"""
    if not dt_value:
        return "—"
    try:
        # Convert to Nepali timezone
        if hasattr(dt_value, 'astimezone'):
            nepali_dt = dt_value.astimezone(NEPALI_TZ)
            return nepali_dt.strftime('%b %d, %Y %I:%M %p')
        return str(dt_value)
    except (AttributeError, TypeError):
        return "—"


@register.filter(name='nepali_date')
def nepali_date(dt_value):
    """Format date in Nepali timezone format (e.g., Feb 09, 2026)"""
    if not dt_value:
        return "—"
    try:
        if hasattr(dt_value, 'astimezone'):
            nepali_dt = dt_value.astimezone(NEPALI_TZ)
            return nepali_dt.strftime('%b %d, %Y')
        return str(dt_value)
    except (AttributeError, TypeError):
        return "—"


@register.filter(name='nepali_time')
def nepali_time(dt_value):
    """Format time in Nepali timezone format (e.g., 10:01 AM)"""
    if not dt_value:
        return "—"
    try:
        if hasattr(dt_value, 'astimezone'):
            nepali_dt = dt_value.astimezone(NEPALI_TZ)
            return nepali_dt.strftime('%I:%M %p')
        return str(dt_value)
    except (AttributeError, TypeError):
        return "—"


@register.filter(name='nepali_short_datetime')
def nepali_short_datetime(dt_value):
    """Format datetime in short Nepali timezone format (e.g., Feb 09 10:01 AM)"""
    if not dt_value:
        return "—"
    try:
        if hasattr(dt_value, 'astimezone'):
            nepali_dt = dt_value.astimezone(NEPALI_TZ)
            return nepali_dt.strftime('%b %d %I:%M %p')
        return str(dt_value)
    except (AttributeError, TypeError):
        return "—"


@register.simple_tag
def current_nepali_time():
    """Get current time in Nepali timezone"""
    now = timezone.now().astimezone(NEPALI_TZ)
    return now.strftime('%I:%M %p')


@register.simple_tag
def current_nepali_datetime():
    """Get current date and time in Nepali timezone"""
    now = timezone.now().astimezone(NEPALI_TZ)
    return now.strftime('%b %d, %Y %I:%M %p')


@register.filter(name='followup_urgency')
def followup_urgency(date_value):
    """Return CSS class based on follow-up date urgency vs today."""
    if not date_value:
        return 'not-set'
    today = date.today()
    if isinstance(date_value, str):
        return 'not-set'
    delta = (date_value - today).days
    if delta < 0:
        return 'overdue'
    elif delta == 0:
        return 'today'
    elif delta == 1:
        return 'tomorrow'
    else:
        return 'upcoming'


@register.filter(name='followup_type_icon')
def followup_type_icon(ftype):
    """Return emoji icon for follow-up type."""
    icons = {
        'call': '\U0001F4DE',
        'whatsapp': '\U0001F4AC',
        'email': '\U0001F4E7',
        'visit': '\U0001F3EA',
    }
    return icons.get(ftype, '')


@register.filter(name='logistics_badge_class')
def logistics_badge_class_filter(status, logistics=None):
    """Badge classes for an NCM/PND status.

    Thin wrapper over dashboard.logistics_status so the template and the AJAX
    endpoint that repaints these badges share one colour table - see that
    module for why it isn't duplicated in JavaScript.

    Usage: {{ order.ncm_status|logistics_badge_class:order.logistics }}
    """
    from dashboard.logistics_status import logistics_badge_class
    return logistics_badge_class(status, logistics)


@register.filter(name='logistics_status_text')
def logistics_status_text_filter(status, logistics=None):
    """The displayed status string, with the provider's default for a blank value."""
    from dashboard.logistics_status import logistics_status_text
    return logistics_status_text(status, logistics)