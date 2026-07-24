"""
Nepali Timezone Utilities
Handles timezone conversion and formatting for Nepali Time (UTC+5:45)
"""

from django.utils import timezone
from datetime import datetime, timedelta
import pytz

# Nepali Timezone Configuration
NEPALI_TIMEZONE = pytz.timezone('Asia/Kathmandu')


def nepali_day_start(date_obj):
    """
    UTC-aware datetime for 00:00:00 Nepal time on date_obj.

    Use this (with `field__gte=...`) instead of Django's `field__date__gte=...`
    lookup on DateTimeField columns. With USE_TZ=True and TIME_ZONE='Asia/Kathmandu',
    that lookup compiles to `DATE(CONVERT_TZ(field, 'UTC', 'Asia/Kathmandu')) >= ...`
    on MySQL, and CONVERT_TZ() silently returns NULL here because this server's
    mysql.time_zone_name tables aren't loaded — so the filter matches zero rows,
    every time, with no error. Comparing against a plain UTC datetime avoids
    CONVERT_TZ entirely.
    """
    return NEPALI_TIMEZONE.localize(datetime.combine(date_obj, datetime.min.time()))


def nepali_day_end_exclusive(date_obj):
    """
    UTC-aware datetime for the start of the day AFTER date_obj, Nepal time.
    Use with `field__lt=...` for an inclusive "through end of date_obj" filter —
    see nepali_day_start() for why `field__date__lte=...` must be avoided here.
    """
    return nepali_day_start(date_obj) + timedelta(days=1)


def get_nepali_now():
    """
    Get current datetime in Nepali timezone
    Returns a timezone-aware datetime object in Asia/Kathmandu timezone
    """
    return timezone.now().astimezone(NEPALI_TIMEZONE)


def convert_to_nepali(dt):
    """
    Convert any datetime to Nepali timezone
    Args:
        dt: datetime object (naive or aware)
    Returns:
        Timezone-aware datetime in Asia/Kathmandu
    """
    if dt is None:
        return None
    
    # If naive, assume it's UTC
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    
    return dt.astimezone(NEPALI_TIMEZONE)


def format_nepali_datetime(dt, format_string='%b %d, %Y %I:%M %p'):
    """
    Format datetime in Nepali timezone
    Args:
        dt: datetime object
        format_string: strftime format string
    Returns:
        Formatted string
    """
    if dt is None:
        return '—'
    
    try:
        nepali_dt = convert_to_nepali(dt)
        return nepali_dt.strftime(format_string)
    except (AttributeError, TypeError):
        return '—'


def format_nepali_date(dt):
    """Format date in Nepali timezone (e.g., Feb 09, 2026)"""
    return format_nepali_datetime(dt, '%b %d, %Y')


def format_nepali_time(dt):
    """Format time in Nepali timezone (e.g., 10:01 AM)"""
    return format_nepali_datetime(dt, '%I:%M %p')


def format_nepali_short_datetime(dt):
    """Format short datetime in Nepali timezone (e.g., Feb 09 10:01 AM)"""
    return format_nepali_datetime(dt, '%b %d %I:%M %p')


# Example usage in views:
# from dashboard.timezone_utils import get_nepali_now, format_nepali_datetime
#
# # Get current time in Nepali timezone
# now = get_nepali_now()
#
# # Format datetime
# formatted = format_nepali_datetime(some_datetime_field)
