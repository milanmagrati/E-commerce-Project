"""Presentation helpers for the vault templates."""

import json

from django import template
from django.utils import timezone
from django.utils.safestring import mark_safe

from dashboard.timezone_utils import convert_to_nepali
from ..models import DeviceKind

register = template.Library()


@register.filter
def sentinel_ago(value):
    """Compact relative time: 12s / 5m / 3h / 2d / 4w."""
    if not value:
        return '—'
    seconds = int((timezone.now() - value).total_seconds())
    if seconds < 0:
        return 'just now'
    if seconds < 60:
        return f'{seconds}s ago'
    if seconds < 3600:
        return f'{seconds // 60}m ago'
    if seconds < 86400:
        return f'{seconds // 3600}h ago'
    if seconds < 604800:
        return f'{seconds // 86400}d ago'
    return f'{seconds // 604800}w ago'


@register.filter
def sentinel_clock(value):
    if not value:
        return '—'
    return convert_to_nepali(value).strftime('%d %b %Y · %I:%M %p')


@register.filter
def sentinel_time(value):
    if not value:
        return '—'
    return convert_to_nepali(value).strftime('%I:%M:%S %p')


@register.filter
def device_icon(kind):
    return {
        DeviceKind.DESKTOP: 'fa-desktop',
        DeviceKind.MOBILE: 'fa-mobile-screen',
        DeviceKind.TABLET: 'fa-tablet-screen-button',
        DeviceKind.BOT: 'fa-robot',
        DeviceKind.API: 'fa-code',
    }.get(kind, 'fa-circle-question')


@register.filter
def browser_icon(name):
    return {
        'Chrome': 'fa-chrome', 'Firefox': 'fa-firefox-browser', 'Safari': 'fa-safari',
        'Edge': 'fa-edge', 'Opera': 'fa-opera', 'Internet Explorer': 'fa-internet-explorer',
    }.get(name, '')


@register.filter
def os_icon(name):
    for prefix, icon in (('Windows', 'fa-windows'), ('macOS', 'fa-apple'), ('iOS', 'fa-apple'),
                         ('Android', 'fa-android'), ('Ubuntu', 'fa-ubuntu'),
                         ('Linux', 'fa-linux'), ('Chrome OS', 'fa-chrome')):
        if (name or '').startswith(prefix):
            return icon
    return ''


@register.filter
def initials(value):
    parts = [p for p in str(value or '?').replace('.', ' ').replace('_', ' ').split() if p]
    if not parts:
        return '?'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@register.filter
def avatar_tone(value):
    """Deterministic accent per username so the same person is always the same colour."""
    palette = ['violet', 'sky', 'emerald', 'amber', 'rose', 'cyan', 'indigo', 'teal']
    return palette[sum(ord(c) for c in str(value or '?')) % len(palette)]


@register.filter
def percent_of(value, total):
    try:
        total = float(total)
        if not total:
            return 0
        return round((float(value) / total) * 100, 1)
    except (TypeError, ValueError):
        return 0


@register.filter
def as_json(value):
    return mark_safe(json.dumps(value, default=str))


@register.filter
def pretty_value(value):
    """Render a diff value for the changes table."""
    if value is None:
        return mark_safe('<em class="sv-null">empty</em>')
    if value is True:
        return mark_safe('<span class="sv-bool sv-bool-on">Yes</span>')
    if value is False:
        return mark_safe('<span class="sv-bool sv-bool-off">No</span>')
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)[:200]
    text = str(value)
    return text if text.strip() else mark_safe('<em class="sv-null">empty</em>')


@register.filter
def get_item(mapping, key):
    """dict lookup by variable key — Django templates can't do mapping[key]."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def strip_app(value):
    """'dashboard.Product' -> 'Product'."""
    return str(value or '').split('.')[-1]


@register.simple_tag
def query_replace(request, **kwargs):
    """Rebuild the querystring with overrides — keeps filters while changing page/sort."""
    params = request.GET.copy()
    for key, value in kwargs.items():
        if value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = value
    params.pop('page', None) if 'page' not in kwargs else None
    return params.urlencode()
