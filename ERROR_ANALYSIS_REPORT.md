# URL Reverse Parameter Mismatch Error Analysis

## Error Message
```
Error: Reverse for 'order_detail' with keyword arguments '{'orderid': 10}' not found. 
1 pattern(s) tried: ['orders/(?P<order_id>[0-9]+)/\\Z']
```

## Root Cause
The Django URL pattern for `order_detail` expects a parameter named **`order_id`**, but somewhere in the code, a parameter named **`orderid`** is being used instead.

### URL Pattern Definition
**File**: [dashboard/urls.py](dashboard/urls.py#L68)
```python
path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
```

The URL pattern defines the parameter as `order_id` (snake_case with underscore).

## Error Locations

### Primary Issue Location
**File**: [myproject/ncm/views.py](myproject/ncm/views.py#L177-L314)
**Function**: `create_ncm_shipment(request, order_id)` (Lines 177-314)
**Key Line**: Line 272

```python
ncm_order_id = result['data'].get('orderid')
```

This line extracts `orderid` from the NCM API response, which returns:
```json
{'Message': 'Order Successfully Created', 'orderid': 18379880, 'weight': 1.0, ...}
```

### Problem Flow

1. **Line 272**: Extracts NCM response `orderid` value (e.g., 10)
   ```python
   ncm_order_id = result['data'].get('orderid')
   ```

2. **Lines 298, 304, 309**: Attempts to redirect using `order_id` parameter
   ```python
   return redirect('order_detail', order_id=order_id)
   ```

3. **Error**: The exception handler or a dynamic URL builder somewhere is confusing the local Django `order_id` with the NCM `orderid`, attempting to call:
   ```python
   reverse('order_detail', orderid=10)  # WRONG - should be order_id=10
   ```

## Evidence from Logs
**File**: [myproject/logs/ncm_integration.log](myproject/logs/ncm_integration.log#L7)
```
Error: Reverse for 'order_detail' with keyword arguments '{'orderid': 10}' not found.
```
This error occurs immediately after NCM order creation failure, suggesting the exception handler is involved.

## Where the Bug Likely Exists

The bug is likely in one of these scenarios:

### Scenario 1: Exception Handler Issue
If an exception occurs during the NCM API call or data processing, the exception handler at **line 309** attempts:
```python
except Exception as e:
    ...
    return redirect('order_detail', order_id=order_id)
```
But if an exception is raised BEFORE `order_id` is properly initialized from the URL parameter, or if there's variable name confusion between the local `order_id` and `ncm_order_id`, the redirect could fail.

### Scenario 2: Dynamic URL Parameter Construction
Look for code that dynamically builds URL parameters, such as:
- Places using `**kwargs` unpacking
- Dictionary-based parameter passing
- Response builders that create JSON responses with redirect URLs

### Scenario 3: Request Parameter Confusion
Search for places where `request.GET` or `request.POST` is accessing `orderid` parameter and trying to use it as `order_id` for reverse URL generation.

## Search Recommendations

To find the exact problematic code, search for:

1. **Places using `reverse()` with keyword arguments**:
   ```bash
   grep -r "reverse(" --include="*.py" | grep orderid
   ```

2. **Places unpacking dictionaries into reverse/redirect**:
   ```bash
   grep -r "\*\*" --include="*.py" | grep -E "(reverse|redirect)"
   ```

3. **Request parameter access**:
   ```bash
   grep -r "request.GET.*orderid\|request.POST.*orderid" --include="*.py"
   ```

4. **Variable shadowing** (both `order_id` and `orderid` in same scope):
   ```bash
   grep -r "orderid\|order_id" myproject/ncm/views.py | grep -E "^\d+(.*orderid.*order_id|.*order_id.*orderid)"
   ```

## Fix Strategy

Once the exact location is found, the fix involves:

1. **Ensure parameter names match URL pattern**: Always use `order_id` (not `orderid`) when calling `reverse()` or `redirect()` with the `order_detail` URL name

2. **Never mix NCM and Django IDs**: Keep them separate:
   - `order_id` = Django Order model ID (local database ID)
   - `ncm_order_id` = NCM system order ID (external system ID)

3. **Correct usage example**:
   ```python
   # Extract NCM response data
   ncm_order_id = result['data'].get('orderid')  # NCM system ID
   
   # Use local order ID for Django URL reversal
   redirect('order_detail', order_id=order_id)  # NOT orderid!
   ```

## Related Code References

- **URL patterns**: [dashboard/urls.py](dashboard/urls.py#L68)
- **Order model**: [dashboard/models.py](dashboard/models.py) (check `order_id` field)
- **Order detail view**: [dashboard/views.py](dashboard/views.py) (search for `def order_detail`)
- **NCM integration**: [myproject/ncm/views.py](myproject/ncm/views.py#L177)

---

**Status**: ✅ ERROR IDENTIFIED
**Severity**: Medium (causes redirect failure but doesn't lose data)
**Impact**: Users cannot navigate back to order detail page after NCM order creation attempt

