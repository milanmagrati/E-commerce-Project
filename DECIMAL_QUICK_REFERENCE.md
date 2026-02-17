# Decimal Fixes - Quick Reference

## 🚀 Quick Start

### 1. Check for corrupted orders
```bash
python manage.py diagnose_decimals
```

### 2. Fix all corrupted decimal values
```bash
python manage.py cleanup_decimals
```

### 3. Verify the fix worked
```bash
python manage.py diagnose_decimals
# Expected output: ✅ No invalid decimal values found!
```

---

## 📊 Diagnostic Command

### See all invalid decimals
```bash
python manage.py diagnose_decimals
```
**Output:** Shows each order with invalid decimal fields

### Generate detailed JSON report
```bash
python manage.py diagnose_decimals --report
```
**Output:** Saves detailed report to `/tmp/decimal_diagnosis_TIMESTAMP.json`

### Auto-fix without seeing details
```bash
python manage.py diagnose_decimals --fix-invalid
```
**Output:** Fixes all invalid values immediately (still shows summary)

---

## 🧹 Cleanup Command

### Preview what will be fixed (safe)
```bash
python manage.py cleanup_decimals --dry-run
```
**Safe to run** - Shows what will change without making changes

### Fix all corrupted decimal values
```bash
python manage.py cleanup_decimals
```
**Fixes** all invalid decimal values in all orders at once

### Fix a specific order
```bash
python manage.py cleanup_decimals --order-id 42
```
**Fixes** only order with ID 42

### Fix and show details
```bash
python manage.py cleanup_decimals --order-id 42
# Shows each field that was fixed
```

---

## 🔒 Using safe_decimal() in Code

### In Python Views
```python
from dashboard.decimal_utils import safe_decimal

# Convert user input safely
user_price = request.POST.get('price')  # Could be "invalid", None, etc.
safe_price = safe_decimal(user_price, max_digits=10, decimal_places=2)
# Returns: Decimal('0') if invalid, or the clamped valid value

product.price = safe_price
product.save()  # Automatically validated by Order.save()
```

### In API Handlers
```python
import json
from dashboard.decimal_utils import safe_decimal

data = json.loads(request.body)
order_data = {
    'discount_amount': safe_decimal(data.get('discount'), 10, 2),
    'shipping_charge': safe_decimal(data.get('shipping'), 10, 2),
    'total_amount': safe_decimal(data.get('total'), 10, 2),
}
```

### In CSV Imports
```python
import csv
from dashboard.decimal_utils import safe_decimal

with open('orders.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        order = Order(
            total_amount=safe_decimal(row['total'], 10, 2),
            discount_amount=safe_decimal(row['discount'], 10, 2),
        )
        order.save()  # Decimal fields auto-validated
```

---

## ✅ When Everything Works

### All views should work without errors
```python
# This should never raise decimal.InvalidOperation anymore
orders = Order.objects.all()
for order in orders:
    print(order.total_amount)  # ✅ Safe
    print(order.discount_amount)  # ✅ Safe
```

### Dashboard aggregations work
```python
# This should work without errors
total = Order.objects.filter(
    payment_status='paid'
).aggregate(Sum('total_amount'))['total']
print(f"Total Revenue: {total}")  # ✅ Safe
```

### Exports work
```python
# This should work without errors
orders.xlsx  # ✅ Export without decimal errors
orders_list.html  # ✅ Template rendering works
```

---

## 🐛 Troubleshooting

### Still getting decimal.InvalidOperation?

**Step 1:** Run diagnostic
```bash
python manage.py diagnose_decimals --report
```

**Step 2:** Check the report
```bash
cat /tmp/decimal_diagnosis_*.json
```

**Step 3:** Fix the issues
```bash
python manage.py cleanup_decimals
```

**Step 4:** Verify
```bash
python manage.py diagnose_decimals
```

### Orders disappearing after cleanup?

The cleanup command sets invalid values to `Decimal('0')`, not deletes orders.
```bash
# They're still there, just with 0 values
Order.objects.first().total_amount  # → Decimal('0.00')
```

### Want to see which orders were fixed?

```bash
python manage.py cleanup_decimals --order-id <id>
# Shows exactly which fields were fixed for that order
```

---

## 📋 Decimal Fields Managed

These fields are automatically validated:

| Model | Field | Max Digits | Decimal Places |
|-------|-------|-----------|-----------------|
| Order | total_amount | 10 | 2 |
| Order | discount_amount | 10 | 2 |
| Order | shipping_charge | 10 | 2 |
| Order | delivery_charge | 10 | 2 |
| Order | expense_amount | 10 | 2 |
| Order | tax_percent | 5 | 2 |
| Order | partial_amount_paid | 10 | 2 |
| Order | remaining_amount | 10 | 2 |
| Order | cod_collected | 10 | 2 |
| Order | package_weight | 5 | 2 |
| OrderItem | price | 10 | 2 |
| OrderItem | total | 10 | 2 |

---

## 🎯 Best Practices

### ✅ DO
```python
# Validate all external input
safe_price = safe_decimal(user_input, 10, 2)

# Use ORM queries
orders = Order.objects.filter(status='pending')

# Let Model.save() validate
order.save()  # Automatic decimal validation
```

### ❌ DON'T
```python
# Don't trust user input directly
order.price = Decimal(user_input)  # Could fail

# Don't use raw SQL for decimals
with connection.cursor() as cursor:
    cursor.execute("SELECT * FROM orders WHERE...")

# Don't manually validate if model does it
order = Order(decimal_field=value)
# order.save() already validates!
```

---

## 📞 Questions?

Refer to: `DECIMAL_FIXES_IMPLEMENTATION.md` for full documentation
