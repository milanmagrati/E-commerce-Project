# Order Redirection Activity Log - Quick Reference

## What Was Implemented

When an order is redirected from the **Possible Redirection** page, the old customer details are now captured and displayed in the Activity Log.

## How It Works

### 1. Order Redirection Process

```
User navigates to Possible Redirection page
    ↓
Selects an RTV order to redirect
    ↓
Fills new customer details
    ↓
Clicks redirect button
    ↓
System captures OLD customer details first
    ↓
Updates order with NEW customer details
    ↓
Creates activity log entry with both old and new info
    ↓
User views order detail page
    ↓
Sees redirection event in Activity Log with old customer details
```

### 2. Activity Log Display

In **Order Detail** → **Activity Log** section:

```
┌─────────────────────────────────────────────────────────┐
│ 📌 Activity Log                      5 Activities        │
├─────────────────────────────────────────────────────────┤
│                                                          │
│ ⏳ 🔄 ORDER REDIRECTED   May 19, 2026 03:45 PM         │
│                                                          │
│ Description: Order redirected via NCM API (NCM Order    │
│ #123456). New customer: Gita Maharjan, Phone:          │
│ 9801234567, Address: Bhaktapur                         │
│                                                          │
│ User: Milan Magrati                                     │
│                                                          │
│ ⚠️ OLD CUSTOMER DETAILS (BEFORE REDIRECTION)            │
│                                                          │
│ [👤 Name: Milan Magrati]  [📞 Phone: 9841234567]        │
│ [📍 Address: Kathmandu]    [🏢 Branch: TINKUNE]         │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## Database Changes

### New Field in OrderActivityLog

```python
metadata = JSONField(
    default=dict,
    blank=True,
    null=True,
    help_text="Additional data for specific action types"
)
```

### Data Stored in metadata for Redirections

```json
{
  "customer_name": "Old Name",
  "customer_phone": "Old Phone Number",
  "shipping_address": "Old Address",
  "branch_city": "Old Branch Name"
}
```

## Files Modified Summary

| File | Changes | Lines |
|------|---------|-------|
| models.py | Added metadata field | +3 lines |
| views.py | Capture old details in 3 functions | +60 lines |
| order_detail.html | Display old details + CSS | +70 lines |
| Migration 0054 | Created for metadata field | Generated |

## Where Old Customer Details Are Shown

### Location in Order Detail Page:
1. Scroll to **"Activity Log"** card at bottom
2. Find the entry with **"🔄 ORDER REDIRECTED"** badge
3. Scroll down within that activity item
4. See section: **"⚠️ OLD CUSTOMER DETAILS (BEFORE REDIRECTION)"**

### What Information is Shown:
- ✅ Customer Name (before redirect)
- ✅ Customer Phone (before redirect)
- ✅ Shipping Address (before redirect)
- ✅ Branch City (before redirect)

## Template Code Structure

```html
<!-- In activity log item -->
{% if log.action_type == 'redirected' and log.metadata %}
<div class="activity-redirection-details">
    <h6>⚠️ Old Customer Details (Before Redirection)</h6>

    <!-- Shows old customer info from log.metadata -->
    {% if log.metadata.customer_name %}
        <div class="detail-row">
            <span class="detail-label">Name:</span>
            <span class="detail-value">{{ log.metadata.customer_name }}</span>
        </div>
    {% endif %}

    <!-- More details... -->
</div>
{% endif %}
```

## Backend Functions Updated

### 1. `redirect_rtv_save(ncm_order_id)`
- Captures old customer details before updating
- Creates activity log with metadata

### 2. `redirect_order_save(order_id)`
- Captures old customer details at start
- Passes to redirect_order_to_ncm
- Creates activity logs with metadata

### 3. `redirect_order_to_ncm(order, ..., old_customer_details)`
- Accepts old customer details as parameter
- Stores in activity log metadata

## Usage Scenarios

### Scenario 1: Direct Redirection
```
Order #T142 (Original: Milan Magrati, Kathmandu)
    ↓
Admin redirects via Possible Redirection page
    ↓
New customer: Gita Maharjan, Bhaktapur
    ↓
Activity Log shows:
  - New customer in description
  - Old customer in metadata section ✨
```

### Scenario 2: Branch Change
```
Order #T145 (Original: TINKUNE branch)
    ↓
Redirected to KATHMANDU branch
    ↓
Activity Log shows:
  - Old branch: TINKUNE
  - New branch: KATHMANDU
```

## Testing the Implementation

### Manual Test Steps:

1. **Go to Possible Redirection page:**
   - Navigate to: `/orders/possible-redirection/`

2. **Select an RTV order:**
   - Click the redirect button (🔄) on any order

3. **Fill new customer details:**
   - Change customer name, phone, address

4. **Submit redirection:**
   - Click "Confirm Redirect"

5. **Check Activity Log:**
   - Go to redirected order's detail page
   - Scroll to Activity Log
   - Find the redirected entry
   - Verify old customer details appear

## Styling Features

### Visual Design:
- **Color Scheme:** Amber/Yellow (warning theme)
- **Icons:** Font Awesome icons for each field
- **Layout:** Grid layout (2 columns desktop, 1 mobile)
- **Border:** Left border highlight in amber color
- **Background:** Light amber background

### Responsive:
- Desktop: 2-column grid for old customer details
- Mobile: 1-column layout for better readability
- Properly wrapped long addresses

## Performance Impact

✅ **Minimal Impact:**
- No additional database queries
- Metadata stored as JSON
- Query count same as before
- Template renders efficiently

## Backward Compatibility

✅ **Fully Compatible:**
- Old activity logs without metadata still display correctly
- Metadata field is optional (nullable)
- No breaking changes to existing code
- Existing redirects that don't have metadata still work

## Security Considerations

✅ **Data Protection:**
- Old customer details stored only in activity logs
- Accessible only to users who can view orders
- Follows existing permission model
- No additional security risks

## Future Enhancements (Optional)

1. **Side-by-Side Comparison View**
   - Display old and new details in columns

2. **Undo Functionality**
   - Quick button to revert to old customer details

3. **Email Notifications**
   - Notify admin of redirections with old/new details

4. **Audit Export**
   - Export redirection history with old/new details

5. **Change Tracking**
   - Track which admin performed redirection

## Common Questions

**Q: Where does the old customer data come from?**
A: Captured from the order object before any modifications are made.

**Q: Is the old data stored permanently?**
A: Yes, in the activity log's metadata field. Permanent storage.

**Q: Can I see old data for redirects that happened before this update?**
A: No, only redirects after this implementation will have old data.

**Q: Does this slow down the order detail page?**
A: No, minimal impact - just displaying stored JSON data.

**Q: Can I delete the old customer details?**
A: Not recommended - they're part of the audit trail. They persist with the activity log.

## Support & Debugging

**If old customer details don't show:**
1. Verify migration was applied: `python manage.py migrate dashboard`
2. Check activity log metadata in database
3. Ensure action_type is exactly 'redirected'
4. Clear browser cache and reload

**Check Database:**
```sql
SELECT id, action_type, metadata FROM dashboard_orderactivitylog
WHERE action_type = 'redirected' LIMIT 1;
```

**Expected output:**
```
| id  | action_type | metadata                                     |
|-----|-------------|----------------------------------------------|
| 123 | redirected  | {"customer_name": "...", "customer_phone": "..."} |
```
