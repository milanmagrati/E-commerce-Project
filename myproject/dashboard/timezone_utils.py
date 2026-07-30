"""
Nepali Timezone Utilities
Handles timezone conversion and formatting for Nepali Time (UTC+5:45)
"""

from django.utils import timezone
from datetime import datetime, timedelta, timezone as dt_timezone
import logging
import pytz

# Nepali Timezone Configuration
NEPALI_TIMEZONE = pytz.timezone('Asia/Kathmandu')

logger = logging.getLogger('ncm')

# Sanity bounds for provider-supplied timestamps (see parse_ncm_datetime).
NCM_MIN_YEAR = 2015
NCM_MAX_FUTURE = timedelta(days=2)


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

    The naive == UTC assumption below is deliberate and must stay: DATABASES
    sets no per-connection 'TIME_ZONE', so connection.timezone is UTC and
    Django interprets naive datetimes written to / read from the DB as UTC
    too. Changing it would silently shift every value already stored.

    Strings coming from an external provider are a different matter — NCM
    sends Nepal wall-clock time when it omits an offset. Run those through
    parse_ncm_datetime() first; never hand a raw provider string here.
    """
    if dt is None:
        return None
    
    # If naive, assume it's UTC.
    # dt_timezone.utc, not timezone.utc: Django 5.0 removed the latter, so the
    # old spelling raised AttributeError here and every naive datetime passed
    # to format_nepali_datetime() silently rendered as the '—' placeholder
    # (that function catches AttributeError).
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)

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


def format_nepali_datetime_or_none(dt, format_string='%b %d, %Y %I:%M %p'):
    """
    Like format_nepali_datetime(), but returns None instead of the '—'
    placeholder when there is no datetime.

    Use this when building context dicts: the template should decide how to
    render "missing" ({% if %}) rather than the view baking in a glyph that
    the template then has to string-compare against.
    """
    if dt is None:
        return None
    try:
        return convert_to_nepali(dt).strftime(format_string)
    except (AttributeError, TypeError):
        return None


def _is_sane_provider_datetime(dt):
    """
    Reject provider timestamps that can't be real.

    A junk value is worse than a missing one here: RTVOrder.Meta.ordering is
    ['-rtv_marked_at', ...], so a single year-2099 timestamp would pin that
    row to the top of the RTV list forever, and provenance-based repair can't
    tell "absurd" apart from "merely wrong". NCM_MAX_FUTURE leaves room for
    clock skew between NCM's servers and ours instead of demanding <= now.
    """
    if dt is None:
        return False
    if dt.year < NCM_MIN_YEAR:
        return False
    if dt > timezone.now() + NCM_MAX_FUTURE:
        return False
    return True


def parse_ncm_datetime(value):
    """
    Parse a timestamp as sent by NCM (or any logistics provider) into an
    aware datetime, or None if it can't be trusted.

    NCM's real payloads are ISO-8601 *with* the Nepal offset, e.g.
    "2026-02-20T11:19:53.209447+05:45" (comment added_time, order/status
    added_time). Their webhook docs also show a 'Z' (UTC) form. Both are
    honoured exactly as sent — an aware value is never re-localized.

    A naive value (no offset, e.g. "2026-07-20 13:52:00") is interpreted as
    **Nepal wall-clock time**, because that is what the provider means. This
    is the one place that assumption lives; see convert_to_nepali() for why
    the DB-facing helpers assume the opposite.

    Args:
        value: str / datetime / None (anything else returns None)
    Returns:
        Aware datetime, or None for empty / unparseable / absurd input.
    """
    from django.utils.dateparse import parse_date, parse_datetime

    if value is None:
        return None

    # Already a datetime (e.g. re-parsing a value we produced earlier).
    if isinstance(value, datetime):
        dt = value if timezone.is_aware(value) else NEPALI_TIMEZONE.localize(value)
        return dt if _is_sane_provider_datetime(dt) else None

    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    dt = None
    try:
        # Handles both offset-bearing ISO and space-separated naive forms.
        dt = parse_datetime(raw)
    except ValueError:
        dt = None

    if dt is None:
        # Date-only ("2026-07-20") -> midnight Nepal time.
        try:
            d = parse_date(raw)
        except ValueError:
            d = None
        if d is not None:
            dt = datetime.combine(d, datetime.min.time())

    if dt is None:
        # Last resort: forms parse_datetime rejects but fromisoformat accepts.
        try:
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            dt = None

    if dt is None:
        logger.warning(f"parse_ncm_datetime: unparseable timestamp {value!r}")
        return None

    if timezone.is_naive(dt):
        dt = NEPALI_TIMEZONE.localize(dt)

    if not _is_sane_provider_datetime(dt):
        logger.warning(f"parse_ncm_datetime: out-of-range timestamp {value!r}")
        return None

    return dt


def format_ncm_datetime(value, format_string='%b %d, %Y %I:%M %p'):
    """
    Parse a raw NCM timestamp and format it in Nepal time, in one call.
    Returns '—' when the value is missing or unparseable.
    """
    return format_nepali_datetime(parse_ncm_datetime(value), format_string)


# Example usage in views:
# from dashboard.timezone_utils import get_nepali_now, format_nepali_datetime
#
# # Get current time in Nepali timezone
# now = get_nepali_now()
#
# # Format datetime
# formatted = format_nepali_datetime(some_datetime_field)
