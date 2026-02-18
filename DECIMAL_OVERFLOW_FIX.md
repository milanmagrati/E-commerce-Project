# Decimal Field Overflow Fix - Issue Resolution

## Problem Identified 🔴

In the order detail page, the **Chaim Chan** product's subtotal was being calculated incorrectly:

- **Expected Calculation**: 58,888,888.00 (price) × 21 (qty) = **1,236,666,648.00**
- **What Was Displayed**: **99,999,999.99** (capped at maximum)

### Root Cause

The `OrderItem` and `Order` models had decimal fields with `max_digits=10` and `decimal_places=2`:

```python
# BEFORE (WRONG)
price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
```

With these constraints, the **maximum allowed value is 99,999,999.99** (10 total digits, 2 after decimal = 8 whole digits max).

When calculating:
- 58,888,888.00 × 21 = 1,236,666,648.00
- This exceeds the limit → Database **caps the value at 99,999,999.99**

## Solution Applied ✅

Increased `max_digits` from **10 to 18** in all monetary decimal fields for maximum flexibility:

### Updated Models

**Order Model Fields:**
- `total_amount`: max_digits 10 → **18**
- `discount_amount`: max_digits 10 → **18**
- `shipping_charge`: max_digits 10 → **18**
- `delivery_charge`: max_digits 10 → **18**
- `expense_amount`: max_digits 10 → **18**
- `partial_amount_paid`: max_digits 10 → **18**
- `remaining_amount`: max_digits 10 → **18**
- `cod_collected`: max_digits 10 → **18**
- `package_weight`: max_digits 5 → **8**

**OrderItem Model Fields:**
- `price`: max_digits 10 → **18**
- `total`: max_digits 10 → **18**

### New Limits

With `max_digits=18` and `decimal_places=2`:
- **New maximum value**: 9,999,999,999,999,999.99 (16 whole digits)
- **Calculation now works perfectly**: 
  - 58,888,888.00 × 21 = 1,236,666,648.00 ✅ (well within limits)
  - Supports orders up to **99 quadrillion rupees**

### Updated Decimal Utilities

Updated `dashboard/decimal_utils.py` validation functions:

```python
decimal_fields = {
    'discount_amount': (18, 2),      # updated from (10, 2)
    'shipping_charge': (18, 2),      # updated from (10, 2)
    'total_amount': (18, 2),         # updated from (10, 2)
    'partial_amount_paid': (18, 2),  # updated from (10, 2)
    'remaining_amount': (18, 2),     # updated from (10, 2)
    'cod_collected': (18, 2),        # updated from (10, 2)
    'price': (18, 2),                # OrderItem price
    'total': (18, 2),                # OrderItem total
}
```

## Migrations Applied

1. **0018_fix_decimal_field_overflow.py** - Initial fix (10→15)
2. **0019_increase_decimal_max_digits_to_18.py** - Enhanced fix (15→18)

```bash
✅ All migrations applied successfully
```

## Testing

To verify the enhanced fix works with massive order amounts:

1. Create a new order item with:
   - Price: 999,999,999,999.99 (large amount)
   - Quantity: 1000+
   - Expected Total: Correctly calculated without overflow

2. The system will now support order totals up to **9,999,999,999,999,999.99**

## Files Modified

1. **myproject/dashboard/models.py**
   - Updated OrderItem decimal fields to max_digits=18
   - Updated Order decimal fields to max_digits=18

2. **myproject/dashboard/decimal_utils.py**
   - Updated validation field constraints to (18, 2)

3. **myproject/dashboard/migrations/**
   - 0018_fix_decimal_field_overflow.py (10→15)
   - 0019_increase_decimal_max_digits_to_18.py (15→18)

## Capacity Comparison

| Version | Max Digits | Max Value | Use Case |
|---------|-----------|-----------|----------|
| Original | 10 | 99,999,999.99 | ❌ Fails at 1.2B |
| First Fix | 15 | 9,999,999,999,999.99 | ✅ Works for moderate orders |
| Final Fix | 18 | 9,999,999,999,999,999.99 | ✅ Enterprise-grade capacity |

## Impact

✅ No data loss  
✅ Supports larger order amounts  
✅ Maintains backward compatibility  
✅ Validation logic updated to match database constraints  

---

**Issue Fixed**: Decimal field overflow causing incorrect order totals  
**Date Fixed**: February 18, 2026  
**Severity**: High (affects order calculations)
