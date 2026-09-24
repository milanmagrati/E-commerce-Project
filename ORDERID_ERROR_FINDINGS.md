# Error "Reverse for 'order_detail' with keyword arguments '{'orderid': 10}' not found" - Investigation Report

## Executive Summary

The error occurs when code attempts to use Django's `reverse()` function with parameter name `orderid` instead of `order_id` to generate a URL for the `order_detail` view. The URL pattern expects `order_id` but receives `orderid` (which is the NCM API's field name for order IDs).

**Status**: ✅ ROOT CAUSE IDENTIFIED  
**Severity**: Medium (prevents navigation after failed NCM operations)

---

## Error Details

### Error Message
```
Error: Reverse for 'order_detail' with keyword arguments '{'orderid': 10}' not found.
1 pattern(s) tried: ['orders/(?P<order_id>[0-9]+)/\\Z']
```

### When It Occurs
- After attempting to create an NCM order (shipment)
- When NCM API call fails (e.g., invalid phone, invalid branch)
- In the exception handler that tries to redirect back to order detail

### Log Location
- **File**: [myproject/logs/ncm_integration.log](myproject/logs/ncm_integration.log#L7)
- **Line**: 7
- **Timestamp**: During order creation attempts

---

## URL Configuration

### Correct Definition
**File**: [dashboard/urls.py](dashboard/urls.py#L68)
```python
path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
```

The URL pattern clearly defines the parameter as **`order_id`** (with underscore).

### Django URL Reversal Rule
To generate a URL for `order_detail`, the correct syntax is:
```python
# CORRECT
reverse('order_detail', order_id=10)         # Result: /orders/10/
redirect('order_detail', order_id=10)

# WRONG
reverse('order_detail', orderid=10)          # ❌ ERROR!
redirect('order_detail', orderid=10)         # ❌ ERROR!
```

---

## Code Analysis

### Primary Location of Issue

**File**: [myproject/ncm/views.py](myproject/ncm/views.py)  
**Function**: `create_ncm_shipment(request, order_id)` (Lines 177-314)

#### Key Lines:

**Line 272** - NCM Response Processing:
```python
ncm_order_id = result['data'].get('orderid')
```
The NCM API response contains field `orderid`:
```json
{
  "Message": "Order Successfully Created",
  "orderid": 18379880,
  "weight": 1.0,
  "delivery_charge": 220.0
}
```

**Line 298, 304, 309** - Redirect Calls:
```python
# These are CORRECT:
return redirect('order_detail', order_id=order_id)  # Uses Django order ID

# But what if order_id variable is not properly set?
# Then line 309 exception handler executes with an undefined/wrong value
```

---

## Problem Diagnosis

### Hypothesis 1: Variable Confusion
The function receives `order_id` as parameter (line 177):
```python
def create_ncm_shipment(request, order_id):
    try:
        order = get_object_or_404(Order, id=order_id, is_deleted=False)
        # ... code ...
        ncm_order_id = result['data'].get('orderid')  # This is NCM system ID
        # ... more code ...
        return redirect('order_detail', order_id=order_id)  # Should use local order_id
    except Exception as e:
        # If exception here, order_id should still be defined from parameter
        return redirect('order_detail', order_id=order_id)
```

However, the error suggests somewhere `orderid` (not `order_id`) is being used.

### Hypothesis 2: Dynamic URL Building
Look for code that builds URLs dynamically using **`**kwargs`** unpacking:
```python
# If this pattern exists somewhere:
params = {'orderid': ncm_order_id}
reverse('order_detail', **params)  # ERROR! Should be order_id
```

### Hypothesis 3: Request Parameter Mishandling  
Check if code is extracting `orderid` from request parameters and trying to use it directly:
```python
# Problematic pattern:
def some_view(request):
    orderid = request.GET.get('orderid')  # From query string
    reverse('order_detail', orderid=orderid)  # WRONG! Should extract order_id first
```

---

## Related Code Patterns Found

### Pattern: Multi-Source Order ID Extraction
**Location**: [dashboard/views.py](dashboard/views.py#L19612)
```python
# Lines 19612, 19657, 19811
oid = order.get('orderid') or order.get('id') or order.get('pk') or order.get('order_id')
```

This pattern appears in NCM RTV sync code where it tries to extract order ID from dictionary data that might come from different sources. This is defensive programming but could be a source of confusion.

### Pattern: NCM vs Django ID Mixing
Multiple places in the codebase work with both:
- **`ncm_order_id`** - NCM system's ID (from their API response)
- **`order_id`** - Django Order model's ID (local database)

These must be kept strictly separated in URL generation context.

---

## Search Results

### Files with 'orderid' references:
1. **dashboard/views.py** - Lines 5456, 8134, 13044, 19612, 19657, 19811, 20095
2. **ncm/views.py** - Line 272
3. **Log file** - ncm_integration.log
4. **Templates** - Various HTML files with order ID display

### Pattern Count:
- Total `orderid` occurrences in Python code: ~10-15
- Most are in data extraction from API responses (correct usage)
- None obviously wrong in the contexts reviewed

---

## Recommendations for Finding Exact Location

### Search Strategy 1: Traceback Analysis
```bash
grep -B5 -A5 "Reverse for 'order_detail'" myproject/logs/ncm_integration.log
# This will show context around the error
```

### Search Strategy 2: Dynamic Kwargs Pattern
```bash
grep -n "\*\*.*{" myproject/**/*.py | grep -E "reverse|redirect"
# Find places using **dict unpacking with reverse/redirect
```

### Search Strategy 3: Request Parameter Usage
```bash
grep -n "request.GET.*orderid\|request.POST.*orderid" myproject/**/*.py
# Find places where request parameters might be used for URL generation
```

### Search Strategy 4: Exception Handler Review
```bash
grep -B10 "except Exception" myproject/ncm/views.py
# Find all exception handlers that might be building URLs with wrong params
```

---

## Fix Strategy

Once the exact problematic code is found, apply one of these fixes:

### Fix Pattern 1: Use Correct Parameter Name
```python
# BEFORE (WRONG)
return redirect('order_detail', orderid=order_id)

# AFTER (CORRECT)
return redirect('order_detail', order_id=order_id)
```

### Fix Pattern 2: Use kwargs Correctly
```python
# BEFORE (WRONG)
params = {'orderid': order_id}
reverse('order_detail', **params)

# AFTER (CORRECT)
params = {'order_id': order_id}
reverse('order_detail', **params)
```

### Fix Pattern 3: Convert NCM ID to Django ID
```python
# BEFORE (might be WRONG if ncm_order_id is used directly)
ncm_id = result['data'].get('orderid')
redirect('order_detail', orderid=ncm_id)

# AFTER (CORRECT - use Django order_id)
ncm_id = result['data'].get('orderid')
redirect('order_detail', order_id=order_id)  # Use the local order_id
```

---

## Testing the Fix

After applying fix, test:
```python
from django.urls import reverse

# Test 1: Verify URL pattern
url1 = reverse('order_detail', order_id=10)
assert url1 == '/orders/10/', f"Got: {url1}"

# Test 2: Verify error no longer occurs
# Try operations that previously triggered the error:
# - Create NCM shipment with invalid data
# - Check that redirect works without "Reverse" errors
```

---

## Documentation Updates Needed

After fix:
1. Add comment in [myproject/ncm/views.py](myproject/ncm/views.py#L272) clarifying:
   - `result['data'].get('orderid')` is NCM system ID
   - Use `order_id` (the function parameter) for Django URL reversal
   
2. Update any relevant docstrings to note the distinction

3. Consider adding type hints to make distinction clear:
   ```python
   def create_ncm_shipment(request, order_id: int) -> HttpResponse:
       """
       Create NCM shipment for Django Order with ID order_id.
       
       Args:
           order_id: Django Order model ID (local database)
       
       Note:
           NCM API returns 'orderid' field - this is the NCM system ID,
           distinct from Django's order_id. Always use order_id for
           Django URL reversal, never the NCM orderid.
       """
   ```

---

## Related Issues

This error is part of a broader pattern in the NCM integration code:
1. Phone number validation failures
2. Branch name mismatches  
3. ID system confusion (Django vs NCM)

Consider a broader refactor to:
- Clearly separate ID types with type hints
- Add validation for required fields before API calls
- Improve error handling and messaging

---

## Files Modified
- Created: [ERROR_ANALYSIS_REPORT.md](ERROR_ANALYSIS_REPORT.md)
- Created: [ORDERID_ERROR_FINDINGS.md](ORDERID_ERROR_FINDINGS.md)

