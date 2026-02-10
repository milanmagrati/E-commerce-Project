# Nepali Timezone Implementation Guide

## Overview
The entire e-commerce admin system has been configured to display and handle all dates and times in **Nepali Time (Asia/Kathmandu - UTC+5:45)**.

## Configuration Changes

### 1. Django Settings (settings.py)
```python
TIME_ZONE = 'Asia/Kathmandu'  # Nepali Time (UTC+5:45)
USE_TZ = True  # Enable timezone support
```

**What this does:**
- All datetimes in the database are stored in UTC
- When retrieved from the database, they are automatically converted to Nepali timezone
- All date/time display in templates will show Nepali time

### 2. Custom Template Filters
Added new Nepali timezone filters in `dashboard/templatetags/dashboard_extras.py`:

- `nepali_datetime` - Full date and time (e.g., Feb 09, 2026 10:01 AM)
- `nepali_date` - Date only (e.g., Feb 09, 2026)
- `nepali_time` - Time only (e.g., 10:01 AM)
- `nepali_short_datetime` - Compact format (e.g., Feb 09 10:01 AM)
- `current_nepali_time` - Current time tag
- `current_nepali_datetime` - Current date and time tag

### 3. Timezone Utility Functions
Created `dashboard/timezone_utils.py` with helper functions:

```python
from dashboard.timezone_utils import get_nepali_now, format_nepali_datetime

# Get current time in Nepali timezone
now = get_nepali_now()

# Format datetime
formatted = format_nepali_datetime(some_datetime_field)
```

## Updated Templates

The following templates have been updated to use Nepali timezone filters:

1. **dashboard/templates/orders_list.html** - Order creation dates
2. **accounts/templates/accounts/user_trash.html** - User deletion dates
3. **accounts/templates/accounts/profile_page.html** - User join and login dates
4. **accounts/templates/accounts/user_edit.html** - User dates
5. **accounts/templates/accounts/user_list.html** - User join dates
6. **dashboard/templates/stock_in_detail.html** - Stock creation dates
7. **dashboard/templates/customer_form.html** - Customer creation dates
8. **dashboard/templates/customer_detail.html** - Customer dates
9. **dashboard/templates/ncm_orders_trash.html** - Order deletion dates
10. **dashboard/templates/dispatch_list.html** - Dispatch creation dates
11. **dashboard/templates/returns/create.html** - Return order dates

## How It Works

### Database Storage
- All datetime data is stored in UTC in the database (standard Django practice)
- This ensures data consistency and makes backups and migrations easier

### Display Layer
- When datetime data is retrieved from the database in views or templates, Django automatically converts it to the configured timezone (Asia/Kathmandu)
- All template filters now format the datetime according to Nepali timezone

### Backend Views
- All `timezone.now()` calls automatically use Nepali timezone for comparisons
- All model `DateTimeField` with `auto_now_add=True` or `auto_now=True` use Nepali timezone

## Usage Examples

### In Templates
```html
<!-- Display order creation date in Nepali timezone -->
{{ order.created_at|nepali_datetime }}

<!-- Display just the date -->
{{ order.created_at|nepali_date }}

<!-- Display just the time -->
{{ order.created_at|nepali_time }}

<!-- Display current time -->
{% current_nepali_time %}
```

### In Views
```python
from django.utils import timezone
from dashboard.timezone_utils import get_nepali_now, format_nepali_datetime

# Get current time in Nepali timezone
now = get_nepali_now()

# Format a datetime field
formatted_date = format_nepali_datetime(order.created_at)

# All timezone.now() calls automatically use Nepali timezone
current_time = timezone.now()  # This is already in Nepali time for display purposes
```

### In Python Management Commands
```python
from dashboard.timezone_utils import get_nepali_now

def handle(self, *args, **options):
    now = get_nepali_now()
    print(f"Current Nepali Time: {now}")
```

## Important Notes

1. **Database Storage**: All datetimes remain UTC in the database. This is intentional and best practice.

2. **Timezone Conversions**: Django automatically handles timezone conversion when USE_TZ=True. You don't need to manually convert datetimes in most cases.

3. **Timezone-aware Objects**: Always use `timezone.now()` instead of `datetime.now()` to ensure timezone awareness.

4. **Template Filters**: The existing Django `|date` filter also respects the TIME_ZONE setting, so it will display Nepali time automatically.

5. **Consistency**: All new date/time displays should use the `nepali_*` filters for consistency.

## Testing Timezone

To verify the Nepali timezone is working:

1. Create a new order or user in the system
2. Check the displayed timestamp in the UI
3. It should show Nepal Standard Time (UTC+5:45)

### Example:
- UTC Time: 2026-02-09 04:16:00
- Nepali Time: 2026-02-09 09:01:00 (UTC+5:45 = +5 hours 45 minutes)

## Troubleshooting

### If times are not displaying correctly:

1. **Check Django Settings**: Ensure `TIME_ZONE = 'Asia/Kathmandu'` in settings.py
2. **Check USE_TZ**: Ensure `USE_TZ = True` in settings.py
3. **Clear Cache**: Clear browser cache and Django template cache
4. **Restart Server**: Restart Django development server
5. **Check Database**: Ensure datetimes are stored correctly in UTC

### If timezone.now() returns wrong time:

This is usually normal. When you call `timezone.now()` in Python code, it returns the current UTC time. Django automatically converts it to the configured timezone (Nepali) only when it's displayed in templates or when compared with timezone-aware datetimes.

## Additional Resources

- [Django Timezone Documentation](https://docs.djangoproject.com/en/6.0/topics/i18n/timezones/)
- [IANA Timezone List](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)
- [Nepal Standard Time Info](https://en.wikipedia.org/wiki/Nepal_Standard_Time)

---

**All times in your e-commerce admin system now display in Nepali Time (UTC+5:45)!**
