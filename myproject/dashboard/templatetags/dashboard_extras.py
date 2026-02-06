from django import template
from decimal import Decimal

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
