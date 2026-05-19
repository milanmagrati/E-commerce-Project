# Order Redirection Implementation - Developer Quick Reference

## Overview
This implementation captures and displays old customer details when orders are redirected via the NCM logistics system.

## Key Files

| File | Purpose | Key Lines |
|------|---------|-----------|
| `dashboard/models.py` | OrderActivityLog model with metadata field | 602-640 |
| `dashboard/views.py` | Redirect functions that capture old details | 4844-5660 |
| `dashboard/templates/order_detail.html` | Display old customer details in Activity Log | 1301-1345, 3537-3600 |
| `dashboard/migrations/0054_add_metadata_to_orderactivitylog.py` | Database migration | All |

## Database Schema

```
OrderActivityLog.metadata (JSONField)
├── customer_name (string, optional)
├── customer_phone (string, optional)
├── shipping_address (string, optional)
└── branch_city (string, optional)
```

## How It Works

### 1. Capturing Old Customer Details

```python
# Before updating order
_old_customer_details = {
    'customer_name': order.customer_name,
    'customer_phone': order.customer_phone,
    'shipping_address': order.shipping_address,
    'branch_city': order.branch_city,
}

# Update order with new values
order.customer_name = new_value
...order.save()

# Create activity log with old details
OrderActivityLog.objects.create(
    order=order,
    action_type='redirected',
    metadata=_old_customer_details,  # Store old values
    ...
)
```

### 2. Displaying in Template

```django
{% if log.action_type == 'redirected' and log.metadata %}
    <div class="activity-redirection-details">
        <h6>Old Customer Details (Before Redirection)</h6>
        {% if log.metadata.customer_name %}
            <span>{{ log.metadata.customer_name }}</span>
        {% endif %}
        <!-- Similar for phone, address, branch -->
    </div>
{% endif %}
```

## Redirect Functions & Their Implementations

### Function 1: redirect_order_save()
**Location:** `dashboard/views.py:4844`
**Purpose:** Edit order details and optionally redirect via NCM API
**Captures Old Details:** YES (Line 4860)
**Passes to:** redirect_order_to_ncm() with old_customer_details parameter

### Function 2: redirect_order_to_ncm()
**Location:** `dashboard/views.py:5480`
**Purpose:** Core function that calls NCM v2 redirect API
**Parameter:** old_customer_details dict
**Creates Activity Log:** YES (Line 5613-5625)

### Function 3: redirect_rtv_save()
**Location:** `dashboard/views.py:5250`
**Purpose:** Redirect RTV (Return To Vendor) orders via NCM API
**Captures Old Details:** YES (Line 5365 for local order, Line 5398 for matched order)
**Creates Activity Logs:** YES (2 logs: one for local, one for matched order)

### Function 4: redirect_rtv_get()
**Location:** `dashboard/views.py:5079`
**Purpose:** Fetch RTV order data for redirect modal
**Note:** Does NOT capture old details (this is the GET function, not the redirect action)

## Adding New Fields to Capture

If you need to capture additional fields during redirection:

### Step 1: Update Model
```python
# In dashboard/models.py - OrderActivityLog class
# (Already has metadata JSONField, no need to change)
```

### Step 2: Update Capture Logic
```python
# In redirect functions
_old_customer_details = {
    'customer_name': order.customer_name,
    'customer_phone': order.customer_phone,
    'shipping_address': order.shipping_address,
    'branch_city': order.branch_city,
    'new_field': order.new_field,  # ADD HERE
}
```

### Step 3: Update Template Display
```django
{% if log.metadata.new_field %}
<div class="detail-row">
    <span class="detail-label"><i class="fas fa-icon me-1"></i> Field Label:</span>
    <span class="detail-value">{{ log.metadata.new_field }}</span>
</div>
{% endif %}
```

## Testing

### Manual Test Checklist
- [ ] Create order with customer details
- [ ] Redirect order via Possible Redirection page
- [ ] Go to order detail page
- [ ] Verify Activity Log shows:
  - [ ] "Order Redirected" action type
  - [ ] "Old Customer Details" section
  - [ ] Previous customer name
  - [ ] Previous customer phone
  - [ ] Previous shipping address
  - [ ] Previous branch/city

### Unit Test Example
```python
from dashboard.models import OrderActivityLog, Order

# Get a redirected order's activity log
log = OrderActivityLog.objects.filter(
    action_type='redirected',
    order=order
).first()

# Verify metadata
assert log.metadata is not None
assert log.metadata['customer_name'] == 'Old Name'
assert log.metadata['customer_phone'] == '9841234567'
```

## CSS Classes for Styling

| Class | Purpose | Properties |
|-------|---------|------------|
| `.activity-redirection-details` | Main container | light yellow bg, amber border, 8px radius |
| `.redirection-section` | Section wrapper | Padding, margins |
| `.old-customer-details` | Grid container | 2-col desktop, 1-col mobile |
| `.detail-row` | Individual row | White bg, left border, flexbox |
| `.detail-label` | Field label | Bold, colored text |
| `.detail-value` | Field value | Gray text, word-break |

## Common Issues & Solutions

### Issue: Metadata is empty or None
**Solution:** Always initialize with `_old_customer_details or {}` when creating activity log

### Issue: Old values not showing in template
**Solution:** Check `{% if log.metadata %}` condition - ensure metadata is not NULL

### Issue: Mobile design broken
**Solution:** The CSS includes `@media (max-width: 768px)` for responsive grid. Verify this is in the template.

### Issue: JSON serialization error
**Solution:** Ensure all values in metadata dict are JSON-serializable (strings, numbers, booleans, dicts, lists, None)

## Performance Considerations

- **Metadata Storage:** JSON column is indexed by PostgreSQL - lookups are fast
- **Query Performance:** No additional queries needed beyond standard OrderActivityLog queries
- **Display Performance:** Template rendering time is negligible (simple dict access)
- **Database Size:** Average 100-200 bytes per redirected order activity log entry

## Security Notes

- ✅ Metadata is stored in database (not in URLs or cookies)
- ✅ Customer data is only visible to authenticated users
- ✅ Activity log requires proper permissions
- ✅ No sensitive data exposed in error messages
- ✅ XSS protection through Django template escaping

## Related Documentation

- [ORDER_REDIRECTION_ACTIVITY_LOG.md](ORDER_REDIRECTION_ACTIVITY_LOG.md) - Technical Details
- [REDIRECTION_QUICK_REFERENCE.md](REDIRECTION_QUICK_REFERENCE.md) - User Guide
- [FINAL_SOLUTION_SUMMARY.md](FINAL_SOLUTION_SUMMARY.md) - Implementation Summary

## Support

For issues or questions:
1. Check the INSPECTION_REPORT.md for comprehensive verification
2. Review the test_order_redirection.py script for testing examples
3. Check Django logs for any errors during redirect operations
