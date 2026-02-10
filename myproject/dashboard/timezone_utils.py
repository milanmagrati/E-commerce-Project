"""
Nepali Timezone Utilities
Handles timezone conversion and formatting for Nepali Time (UTC+5:45)
"""

from django.utils import timezone
from datetime import datetime
import pytz

# Nepali Timezone Configuration
NEPALI_TIMEZONE = pytz.timezone('Asia/Kathmandu')


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
