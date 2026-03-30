from django import template

register = template.Library()


@register.filter
def smart_duration(hours):
    """Display hours as 'Xh Ym', 'Xh', or 'Ym' depending on value."""
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        return "--"
    if hours <= 0:
        return "0m"
    total_minutes = round(hours * 60)
    h = total_minutes // 60
    m = total_minutes % 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"
