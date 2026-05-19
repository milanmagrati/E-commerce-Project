# Order Redirection Activity Log Implementation

## Overview
This document describes the implementation of displaying redirection events and old customer details in the order detail's activity log when orders are redirected from the **Possible Redirection** page.

## Problem Statement
When an order is redirected from `possible_redirection.html`:
1. The redirection status and logs were not properly displayed in the order detail's activity log
2. Old customer details were changed but not stored/displayed for reference
3. No history of what customer details were before redirection

## Solution Implemented

### 1. Database Model Enhancement
**File:** `/myproject/dashboard/models.py`

**Added to `OrderActivityLog` model:**
- New field: `metadata` (JSONField)
  - Stores detailed information about specific action types
  - For redirections: stores old customer details
  - Default value: empty dict `{}`

**Migration:** `0054_add_metadata_to_orderactivitylog.py`
- Applied successfully to database

### 2. Backend Logic Updates
**File:** `/myproject/dashboard/views.py`

#### Updated Functions:

**A. `redirect_rtv_save()` - Standalone RTV Redirection**
- Captures old customer details BEFORE updating the order
  - `customer_name`
  - `customer_phone`
  - `shipping_address`
  - `branch_city`
- Stores these in `metadata` field when creating activity logs
- Logs created for:
  1. Matched order (confirmed local order that was used for redirection)
  2. Linked local order (if RTV had a linked local order)

**B. `redirect_order_save()` - Direct Order Redirection**
- Captures old customer details at function start (before transaction)
- Passes old details through to `redirect_order_to_ncm()` function
- Creates activity logs with old customer details in metadata

**C. `redirect_order_to_ncm()` - NCM API Redirect**
- Function signature updated to accept `old_customer_details` parameter
- Stores old details in activity log metadata when redirect succeeds

### 3. Template Display Enhancement
**File:** `/myproject/dashboard/templates/order_detail.html`

#### Added Redirection Details Section:
- Only displays when activity log action_type is `'redirected'`
- Only displays when metadata contains old customer details
- Shows:
  - **Old Customer Name** - with user icon
  - **Old Phone** - with phone icon
  - **Old Shipping Address** - with map marker icon
  - **Old Branch City** - with city icon

#### Styling Features:
- Warning-themed styling (amber/yellow color scheme)
- Grid layout (2 columns on desktop, 1 on mobile)
- Each detail has its own card with left border highlight
- Icons for visual clarity
- Responsive design

### 4. Activity Flow Diagram

```
Redirection Workflow:
├── possible_redirection.html (User selects RTV to redirect)
│
├── redirect_rtv_save() or redirect_order_save()
│   ├── Capture Old Customer Details
│   ├── Update Order with New Details
│   └── Create Activity Log with Old Details in Metadata
│
├── Order Detail Page Displays:
│   ├── Redirected Status
│   ├── New Customer Details (in description)
│   └── OLD Customer Details (in metadata section - NEW!)
│
└── User Can See Complete History of Changes
```

## Data Structure

### Activity Log for Redirected Order:

```python
OrderActivityLog {
    order: Order,
    action_type: 'redirected',
    user: User,
    field_name: 'ncm_status',
    old_value: '',
    new_value: 'redirected',
    description: 'Order redirected via NCM API...',
    metadata: {
        'customer_name': 'Old Name',
        'customer_phone': 'Old Phone',
        'shipping_address': 'Old Address',
        'branch_city': 'Old Branch'
    },
    created_at: datetime
}
```

## Usage Example

### Scenario: Redirecting Order #T142

**Before Redirection:**
- Customer: Milan Magrati
- Phone: 9841234567
- Address: Kathmandu
- Branch: TINKUNE

**After Redirection via Possible Redirection Page:**
- Customer changed to: Gita Maharjan
- Phone changed to: 9801234567
- Address changed to: Bhaktapur

**In Activity Log:**
1. **Redirected Activity** badge appears
2. **Description** shows: "Order redirected via NCM API... New customer: Gita Maharjan, Phone: 9801234567, Address: Bhaktapur"
3. **Old Customer Details Section** (NEW) shows:
   - Name: Milan Magrati
   - Phone: 9841234567
   - Address: Kathmandu
   - Branch: TINKUNE

## Files Modified

1. **Models:** `/myproject/dashboard/models.py`
   - Added `metadata` field to `OrderActivityLog`

2. **Migrations:** `/myproject/dashboard/migrations/0054_add_metadata_to_orderactivitylog.py`
   - Created migration for new field

3. **Views:** `/myproject/dashboard/views.py`
   - Updated `redirect_rtv_save()` function
   - Updated `redirect_order_save()` function
   - Updated `redirect_order_to_ncm()` function signature and implementation

4. **Templates:** `/myproject/dashboard/templates/order_detail.html`
   - Added redirection details section in activity log
   - Added CSS styling for redirection details

## Features

✅ **Old Customer Details Captured** - Stored before any changes are made
✅ **Activity Log Integration** - Seamless display in existing activity log UI
✅ **Visual Distinction** - Warning/amber theme makes redirection events stand out
✅ **Responsive Design** - Works on desktop and mobile devices
✅ **Icon Indicators** - Clear icons for each field
✅ **Database Efficient** - Uses JSONField, no additional queries needed
✅ **Backward Compatible** - Existing activity logs unaffected

## API Response Fields

When viewing an order with redirected activity:
- `log.action_type` = `'redirected'`
- `log.metadata` = Dictionary containing old customer details
- `log.metadata.customer_name` = Previous customer name
- `log.metadata.customer_phone` = Previous phone number
- `log.metadata.shipping_address` = Previous address
- `log.metadata.branch_city` = Previous branch

## Testing Checklist

- [ ] Perform order redirection from possible_redirection.html
- [ ] Check activity log in order detail page
- [ ] Verify old customer details appear in redirection entry
- [ ] Test on mobile view - responsive layout works
- [ ] Test with various special characters in addresses
- [ ] Verify metadata persists after page refresh
- [ ] Check database - metadata field populated correctly
- [ ] Test with different user roles - all can see old details

## Future Enhancements

Possible improvements:
1. **Comparison View** - Side-by-side old vs new customer details
2. **Undo Redirection** - Ability to revert to previous customer details
3. **Redirection History** - Show multiple redirections if order redirected more than once
4. **Export/Print** - Include old customer details in order invoice/export
5. **Notifications** - Alert admin when orders are redirected

## Support & Documentation

For questions or issues:
- Check `possible_redirection.html` for how redirects are initiated
- Review `redirect_rtv_save()` in views.py for technical details
- See order_detail.html template for display logic
