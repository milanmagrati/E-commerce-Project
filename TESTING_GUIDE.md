# Two-Way Order Synchronization - Complete Testing Guide

## Pre-Testing Setup

Make sure your Django application is running and you have admin access.

```bash
# In your project root
cd /home/milan-magrati/Desktop/EcommerceAdmin
source .venv/bin/activate
cd myproject
python manage.py runserver 0.0.0.0:8000
```

---

## Test Suite 1: order_detail Page Updates

### Test 1.1: Update Order Status in order_detail
**Objective**: Verify status changes in order_detail are reflected in order_edit

**Steps**:
1. Navigate to an order's detail page: `/admin/orders/{order_id}/`
2. Scroll to "Update Order Status" section
3. Click the "Order Status" dropdown
4. Select a different status (e.g., "shipped")
5. Click "Update" or submit button
6. Verify success message appears
7. Navigate to order edit page: `/admin/orders/{order_id}/edit/`
8. Look at the "Order Status" dropdown on the edit page
9. **Expected Result**: The new status should be selected/highlighted

**Verification**:
- [ ] Success message shows status was updated
- [ ] order_detail shows new status in the status card/badge
- [ ] order_edit page loads and shows new status in dropdown
- [ ] Activity log shows the status change

---

### Test 1.2: Update Payment Status in order_detail
**Objective**: Verify payment status changes are synchronized

**Steps**:
1. On order detail page, find "Payment Status" dropdown
2. Select a different payment status (e.g., "paid" or "partial")
3. Click Update
4. Verify success message
5. Navigate to order edit page
6. Check the "Payment Status" dropdown

**Expected Result**: New payment status should be selected on edit page

**Verification**:
- [ ] Payment status card on detail page shows new value
- [ ] order_edit dropdown shows new selection
- [ ] Activity log records the change

---

### Test 1.3: Update Payment Method in order_detail
**Objective**: Verify payment method changes are synchronized

**Steps**:
1. On order detail page, find "Payment Method" dropdown
2. Select a different payment method (e.g., "khalti" or "esewa")
3. Click Update
4. Go to order edit page
5. Check the "Payment Method" (Payment Setup) dropdown

**Expected Result**: New payment method should be selected on edit page

**Verification**:
- [ ] Payment method badge shows new value on detail page
- [ ] order_edit dropdown reflects new selection
- [ ] No dropdown shows wrong value after reload

---

### Test 1.4: Update Multiple Fields Together in order_detail
**Objective**: Verify multiple simultaneous changes are synchronized

**Steps**:
1. On order detail page, update:
   - Order Status: e.g., "confirmed"
   - Payment Status: e.g., "pending"
   - Payment Method: e.g., "cod"
2. Click Update
3. Go to order edit page
4. Verify all three fields show correct selections

**Expected Result**: All updated fields maintain correct values

**Verification**:
- [ ] All three dropdowns show correct selections
- [ ] Activity log shows all changes
- [ ] Values persist after page refresh

---

## Test Suite 2: order_edit Page Updates

### Test 2.1: Update Order Status in order_edit
**Objective**: Verify status changes in order_edit are reflected in order_detail

**Steps**:
1. Navigate to order edit page: `/admin/orders/{order_id}/edit/`
2. Find "Order Status" dropdown in the form
3. Select a different status
4. Click "Save" or "Update Order" button
5. System should redirect to order detail page
6. Verify success message on detail page
7. Check the status card/badge on detail page
8. Navigate back to order edit page
9. Verify the status dropdown shows the new selection

**Expected Result**: Order status updated and synchronized correctly

**Verification**:
- [ ] Edit page saved successfully (no error)
- [ ] Redirected to order_detail (not back to edit)
- [ ] Status badge on detail page shows new value
- [ ] order_edit dropdown shows new selection after navigating back
- [ ] Activity log shows the change

---

### Test 2.2: Update Payment Status in order_edit
**Objective**: Verify payment status updates in edit page sync to detail

**Steps**:
1. On order edit page, find "Payment Status Setup" dropdown
2. Select a different payment status
3. Save the order
4. Redirect happens to order_detail
5. Check the payment status card on detail page
6. Go back to order_edit
7. Verify dropdown shows new selection

**Expected Result**: Payment status correctly synchronized

---

### Test 2.3: Update Payment Method in order_edit
**Objective**: Verify payment method updates sync correctly

**Steps**:
1. On order edit page, find "Payment Setup" (Payment Method) dropdown
2. Select different payment method
3. Save the order
4. On detail page, check payment method badge
5. Go back to order_edit
6. Verify dropdown shows new selection

**Expected Result**: Payment method synchronized across pages

---

### Test 2.4: Update Partial Payment in order_edit
**Objective**: Verify complex partial payment updates sync correctly

**Steps**:
1. On order edit page, check "Is Partial Payment" checkbox
2. Enter "Partial Amount Paid": e.g., 5000
3. "Remaining Amount" should auto-calculate
4. Save the order
5. On detail page, check partial payment display
6. Go back to order_edit
7. Verify partial payment checkbox and amounts are correct

**Expected Result**: Partial payment data fully synchronized

**Verification**:
- [ ] Partial payment badge shows on detail page
- [ ] Amount values display correctly
- [ ] Progress bar (if shown) reflects correct percentage
- [ ] Activity log shows payment change

---

## Test Suite 3: Cross-Page Navigation & Synchronization

### Test 3.1: Rapid Navigation Between Pages
**Objective**: Verify data consistency during rapid page switching

**Steps**:
1. Update status in order_detail
2. Immediately navigate to order_edit (within 2 seconds)
3. Check if new status is reflected
4. Go back to order_detail
5. Update payment method
6. Quickly navigate to order_edit
7. Verify payment method is shown

**Expected Result**: Data always in sync regardless of navigation speed

**Verification**:
- [ ] No stale data ever displayed
- [ ] Dropdowns always show correct selections
- [ ] No race conditions occur

---

### Test 3.2: Browser Back Button Test
**Objective**: Verify browser cache doesn't show stale data

**Steps**:
1. Update status in order_detail
2. Navigate to order_edit page
3. Use browser back button to go back to order_detail
4. Check if updated status is still displayed
5. Or: Manually refresh page (F5)
6. Verify status still shows latest value

**Expected Result**: Updated data persists, no stale cache shown

**Verification**:
- [ ] Hard refresh (Ctrl+F5) shows latest data
- [ ] Browser back button doesn't show old data
- [ ] Page refresh always fetches fresh data from server

---

### Test 3.3: Multiple Browser Tabs
**Objective**: Verify synchronization across multiple tabs

**Steps**:
1. Open order detail page in Tab 1
2. Open same order edit page in Tab 2
3. In Tab 1, update order status
4. In Tab 2, refresh the page
5. Check if Tab 2 shows updated status

**Expected Result**: Updated data appears in both tabs after refresh

**Verification**:
- [ ] Manual refresh in Tab 2 shows latest data
- [ ] Both tabs never show conflicting information
- [ ] Activity log has only one entry for the change

---

## Test Suite 4: Edge Cases

### Test 4.1: Null/Empty Status Setup
**Objective**: Verify handling when status_setup is None

**Steps**:
1. Try to update order where status_setup might be None
2. Select a valid status in dropdown
3. Save and verify it updates correctly
4. Navigate to other page
5. Verify new status is shown

**Expected Result**: System handles None values gracefully

---

### Test 4.2: Invalid Setup ID
**Objective**: Verify error handling for invalid setup IDs

**Steps**:
1. Try to submit form with invalid setup ID (via browser DevTools)
2. System should reject and show error
3. Or gracefully handle it by setting to None

**Expected Result**: No crash, graceful error handling

**Verification**:
- [ ] User sees error message
- [ ] Order not left in inconsistent state
- [ ] Activity log not created for failed attempt

---

### Test 4.3: Concurrent Updates (if applicable)
**Objective**: Verify handling of simultaneous updates from different users

**Steps**:
1. User A opens order edit page
2. User B opens same order edit page
3. User A updates status and saves
4. User B updates payment method and saves
5. Navigate to order detail
6. Verify BOTH changes are present

**Expected Result**: Last write wins, both changes may be captured

---

## Test Suite 5: Activity Logging

### Test 5.1: Verify Status Change Log
**Objective**: Ensure activity logs correctly record status changes

**Steps**:
1. Update order status in order_detail
2. Navigate to activity logs section
3. Check if most recent log shows status change
4. Verify it shows: old status → new status
5. Check timestamp and user

**Expected Result**: Activity log contains accurate change record

**Verification**:
- [ ] Log entry exists for the change
- [ ] Shows correct old and new values
- [ ] Current logged-in user is recorded
- [ ] Timestamp is recent

---

### Test 5.2: Verify Payment Change Log
**Objective**: Ensure payment changes are logged

**Steps**:
1. Update payment status and payment method
2. Check activity logs
3. Verify both changes are recorded
4. Check they show correct old/new values

**Expected Result**: All payment changes logged accurately

---

### Test 5.3: Verify Multiple Changes in Single Update
**Objective**: Ensure all changes in one save are logged together

**Steps**:
1. Update status, payment method, and payment status together
2. Save
3. Check activity log
4. Verify one log entry shows all changes

**Expected Result**: Single activity log entry with all changes listed

---

## Test Suite 6: Performance & Database

### Test 6.1: Check Query Count
**Objective**: Verify no N+1 query problems

**Steps**:
1. Use Django Debug Toolbar or `django-extensions` shell
2. Load order edit page
3. Check number of database queries
4. Expected: ~10-15 queries (not 50+)
5. Check select_related is working

**Expected Result**: Efficient queries with select_related

```python
# In Django shell
from django.db import connection
from dashboard.models import Order

order = Order.objects.select_related(
    'status_setup',
    'payment_setup',
    'payment_status_setup',
    'customer',
    'created_by'
).get(id=1)

print(len(connection.queries))  # Should be low
```

---

### Test 6.2: Verify Cache Deletion
**Objective**: Ensure cache is properly cleared after updates

**Steps**:
1. Monitor Django cache during update
2. Verify `cache.delete(f'order_{order.id}')` is called
3. Check that next page load fetches fresh data

**Expected Result**: Cache cleared appropriately after updates

---

## Test Suite 7: Data Integrity

### Test 7.1: Verify String Fields Sync With FK
**Objective**: Ensure order_status matches status_setup.name

**Steps**:
1. Update order status in detail page
2. Check database:

```python
from dashboard.models import Order
order = Order.objects.get(id=1)
print(f"order_status: {order.order_status}")
print(f"status_setup: {order.status_setup.name}")
print(f"Match: {order.order_status == order.status_setup.name.lower().replace(' ', '_')}")
```

3. Verify they match

**Expected Result**: String and FK fields are always synchronized

---

### Test 7.2: Verify Payment Data Consistency
**Objective**: Ensure payment fields are consistent

**Steps**:
1. Update order with partial payment
2. Check the following are consistent:
   - `is_partial_payment` boolean
   - `payment_status` (should be "partial")
   - `partial_amount_paid` (should have value)
   - `remaining_amount` (calculated correctly)

```python
from dashboard.models import Order
order = Order.objects.get(id=1)
if order.is_partial_payment:
    assert order.payment_status == 'partial'
    assert order.partial_amount_paid > 0
    expected_remaining = order.total_amount - order.partial_amount_paid
    assert order.remaining_amount == expected_remaining
```

**Expected Result**: All payment fields are logically consistent

---

## Test Suite 8: User Experience

### Test 8.1: Success Messages
**Objective**: Verify users see appropriate success feedback

**Steps**:
1. Update order in detail page
2. Verify success message shows: "✅ Order updated! Changed: Status"
3. Update in edit page
4. Verify success message shows details
5. Check message persists across redirect

**Expected Result**: Clear, informative success messages shown

**Verification**:
- [ ] Messages appear immediately
- [ ] Messages list changed fields
- [ ] Messages persist on redirect target page

---

### Test 8.2: Error Messages
**Objective**: Verify error handling is user-friendly

**Steps**:
1. Try to set invalid values (if possible)
2. Try with missing required fields
3. Check error messages are clear

**Expected Result**: Clear error messages guide user to fix issues

---

## Test Suite 9: Mobile/Responsive

### Test 9.1: Mobile View Synchronization
**Objective**: Verify synchronization works on mobile devices

**Steps**:
1. Open order_detail on mobile (or DevTools mobile view)
2. Update order status
3. Open order_edit on mobile
4. Verify sync works correctly

**Expected Result**: Synchronization works on all screen sizes

---

## Troubleshooting Guide

### Issue: Changes not appearing on other page
**Diagnostics**:
1. Check browser cache (Ctrl+Shift+Delete)
2. Hard refresh the page (Ctrl+F5)
3. Check Django cache settings
4. Verify `cache.delete()` is being called
5. Check database directly:
   ```python
   order = Order.objects.get(id=1)
   print(order.status_setup)  # Should show latest
   ```

### Issue: Dropdown shows wrong value selected
**Diagnostics**:
1. Check if `order.status_setup` is None
2. Verify ForeignKey relationship:
   ```python
   from dashboard.models import Order, Setup
   order = Order.objects.select_related('status_setup').get(id=1)
   if order.status_setup:
       print(f"ForeignKey set: {order.status_setup.id}")
   else:
       print("status_setup is None!")
   ```
3. Check template for correct condition:
   ```html
   {% if order.status_setup and order.status_setup.id == setup.id %}selected{% endif %}
   ```

### Issue: Data inconsistent across pages
**Diagnostics**:
1. Check if `sync_order_status_setup()` is being called
2. Verify re-fetch from database after sync:
   ```python
   order = sync_order_status_setup(order)
   order = Order.objects.select_related('status_setup').get(id=order.id)
   ```
3. Check activity log to see what was actually saved

### Issue: N+1 Query Problems
**Diagnostics**:
1. Use Django Debug Toolbar
2. Check select_related is used:
   ```python
   order = Order.objects.select_related(
       'status_setup',
       'payment_setup',
       'payment_status_setup'
   ).get(id=1)
   ```
3. Count queries:
   ```python
   from django.db import connection
   print(len(connection.queries))
   ```

---

## Sign-Off Checklist

- [ ] All Test Suite 1 tests pass (order_detail updates)
- [ ] All Test Suite 2 tests pass (order_edit updates)
- [ ] All Test Suite 3 tests pass (cross-page sync)
- [ ] All Test Suite 4 tests pass (edge cases)
- [ ] Activity logging working correctly
- [ ] Performance queries are optimized
- [ ] Data integrity verified in database
- [ ] User experience is smooth
- [ ] Mobile/responsive working

---

## Deployment Checklist

Before deploying to production:

- [ ] Run full test suite
- [ ] Check for N+1 queries with Django Debug Toolbar
- [ ] Verify cache settings are correct
- [ ] Test with production data (staging environment)
- [ ] Monitor error logs for first 24 hours
- [ ] Have rollback plan ready
- [ ] Run database integrity checks:
  ```bash
  python manage.py check
  python manage.py migrate --check
  ```

---

## Performance Benchmarks

**Expected metrics** (per page load):
- Order detail page: ~150-300ms (initial load)
- Order edit page: ~150-300ms (initial load)  
- Status update: ~100-200ms (POST to redirect)
- Database queries: 10-15 per page load (not 50+)

**Cache metrics**:
- Cache hit rate: >80% for repeated loads
- Cache miss: Forces fresh DB fetch (desired)
- Cleanup: Automatic after updates

---

## Notes

- All changes are backward compatible
- No migration needed if fields already exist
- Django QuerySet is not cached within a request
- Cache clearing uses Django's cache framework
- Activity logging is comprehensive and auditable

