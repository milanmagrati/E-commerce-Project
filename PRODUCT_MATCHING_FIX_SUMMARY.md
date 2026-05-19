# Product Matching Logic Fix - Possible Redirection Page

## Issue Identified
In the **Possible Redirection** page, the product matching logic was **backwards**, causing incorrect product matches:

### Example: Order T5470
- **RTV Product (being returned)**: "Hair removal spray"
- **Linked Local Order Product**: "Hair growth serum"
- **System showed**: "✓ Product matched"
- **But**: These are completely different products!

## Root Cause
The matching logic was **prioritizing linked local order items** over the **RTV's actual product_description**:

```python
# OLD (WRONG) LOGIC:
if _item_kws:  # ← Keywords from LINKED LOCAL ORDER items
    if any(kw == _pn for kw in _item_kws):
        _match_found = True  # ← WRONG! Matching wrong products
elif _desc and _desc == _pn:  # ← RTV's product_description (fallback only)
    _match_found = True
```

### Why This Was Wrong:
1. RTVs represent products **being returned** (not the same as original order)
2. Linked local orders may have **different products** than the RTV
3. The matching should verify candidate orders have the **exact RTV product**, not the original order product

## Solution Applied
Fixed both the **pre-filter logic** and **per-entry matching logic** to:

```python
# NEW (CORRECT) LOGIC:
# Use ONLY RTV's product_description for matching
_desc = (entry['rtv'].product_description or '').lower().strip()

if not _desc:
    # No product info → branch-only match (with warning)
    _matched = list(_branch_candidates)
    entry['is_branch_only_match'] = True
else:
    # Match candidate orders ONLY if products match EXACTLY
    for _o in _branch_candidates:
        for _item in _o.items.all():
            _pn = _item.product_name.lower().strip()
            if _desc == _pn:  # ← Exact match required!
                _matched.append(_o)
                break
```

## Key Changes

### 1. Pre-filter Logic (Line ~4600)
- **Removed**: `_item_kws` (linked local order keywords) from matching
- **Added**: Direct comparison of RTV's `product_description` against candidate orders
- **Effect**: Only RTVs with exact product matches appear in the list

### 2. Per-entry Matching (Line ~4710)
- **Removed**: Logic that prioritized local order items
- **Added**: Exclusive use of RTV's `product_description`
- **Effect**: Each RTV's matches are correctly filtered by product

### 3. Display Handling
- Linked local order items are **only for display/reference** (template shows what was in original NCM order)
- **Not used for matching logic** anymore

## Result
Now when viewing the Possible Redirection page:

✅ **T5470 with "Hair removal spray"**
- Will **NOT** match orders with "Hair growth serum"
- Will **ONLY** match orders with "Hair removal spray"
- Badge shows "✓ Product matched" **only when products are identical**

✅ **Branch-only matches** (RTVs without product_description)
- Show warning badge: "⚠️ Branch match only — no product info"
- This alerts admin that matching is by branch location only

## Files Modified
- `/myproject/dashboard/views.py` - Function: `possible_redirection_list()`
  - Line ~4600-4627: Pre-filter matching logic
  - Line ~4710-4735: Per-entry matching logic

## Testing Required
1. Go to **Possible Redirection** page
2. Verify **T5470** no longer shows in the list (or shows as branch-only match if no product_description)
3. Verify RTVs with exact product matches display "✓ Product matched"
4. Verify RTVs without product info show "⚠️ Branch match only" warning

---
**Status**: ✅ Fixed and ready for testing
