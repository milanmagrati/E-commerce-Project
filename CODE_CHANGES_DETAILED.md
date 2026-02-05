# Detailed Code Changes

## File 1: accounts/models.py

### Added vendor_id field to CustomUser

**Location**: After `profile_picture` field (around line 18-26)

```python
# ✅ Vendor/Seller ID for logistics API integration
vendor_id = models.CharField(max_length=100, blank=True, null=True, unique=True, 
                            help_text="Unique vendor ID for logistics providers like NCM")
```

**Purpose**: Store unique vendor identifier for NCM and other logistics partners

---

## File 2: accounts/admin.py

### Complete CustomUserAdmin registration

```python
from django.contrib import admin
from .models import CustomUser

@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = ['username', 'email', 'role', 'vendor_id', 'is_active']
    list_filter = ['role', 'is_active', 'created_at']
    search_fields = ['username', 'email', 'vendor_id']
    
    fieldsets = (
        ('Personal Info', {
            'fields': ('username', 'email', 'first_name', 'last_name', 'phone', 'profile_picture')
        }),
        ('Vendor Info', {
            'fields': ('vendor_id',),
            'description': 'Set a unique vendor ID for logistics API integration (e.g., NCM)'
        }),
        ('Account Settings', {
            'fields': ('role', 'is_active', 'is_staff', 'is_superuser')
        }),
        # ... other fieldsets ...
    )
```

**Purpose**: Make vendor_id easily manageable in Django admin

---

## File 3: dashboard/views.py

### Updated send_single_order_to_ncm() function

#### 1. Vendor Reference ID Generation (around line 3353)

```python
# Generate Vendor Reference ID - ensure it's not empty
# Try to use vendor_id from user first, then order_number
vendor_ref_id = None

# Priority 1: Use vendor_id from the user who created the order
if order.created_by and hasattr(order.created_by, 'vendor_id') and order.created_by.vendor_id:
    vendor_ref_id = order.created_by.vendor_id

# Priority 2: Fallback to order_number
if not vendor_ref_id:
    vendor_ref_id = str(order.order_number or "").strip()

# Priority 3: If still no vendor_ref_id, create one using order ID
if not vendor_ref_id:
    vendor_ref_id = f"ORD-{order.id}"

# Append order number for uniqueness if using vendor_id alone
if order.created_by and hasattr(order.created_by, 'vendor_id') and order.created_by.vendor_id:
    vendor_ref_id = f"{vendor_ref_id}#{order.order_number}"
```

**Purpose**: Intelligently generate vendor reference ID with fallback support

#### 2. NCM Payload Update (around line 3370)

```python
payload = {
    "name": str(order.customer_name or "").strip(),
    "phone": phone,
    "phone2": "",
    "cod_charge": float(order.total_amount or 0),
    "address": str(order.shipping_address or "").strip(),
    "fbranch": from_branch,
    "branch": str(order.branch_city or "KATHMANDU").upper(),
    "package": str(product_name)[:100],
    "vrefid": vendor_ref_id,  # ✅ NOW PROPERLY SET
    "instruction": str(order.notes or "")[:100],
    "deliverytype": delivery_type,
    "weight": weight
}
```

**Purpose**: Include vendor reference ID in NCM API payload

#### 3. Debug Logging (around line 3385)

```python
# DEBUG: Log the payload being sent
print(f"DEBUG: NCM API Payload for Order {order.order_number}:")
print(f"  Vendor Ref ID: {payload.get('vrefid')}")
print(f"  Full Payload: {payload}")
print(f"Response Status: {response.status_code}")
if response.status_code != 200:
    print(f"Response Body: {response.text}")
```

**Purpose**: Provide detailed logging for troubleshooting

#### 4. Activity Log Update (around line 3408)

```python
# Log Activity
OrderActivityLog.objects.create(
    order=order,
    user=request.user,
    action_type='updated',
    description=f"Sent to NCM. NCM ID: {order.ncm_order_id}, Vendor Ref: {vendor_ref_id}"
)
```

**Purpose**: Track vendor reference ID in order activity logs

---

## Key Logic Flow

```
send_single_order_to_ncm() called
    ↓
Check if order.created_by exists
    ↓
Try to use order.created_by.vendor_id
    ↓
If not available, use order.order_number
    ↓
If still not available, generate ORD-{id}
    ↓
Create vrefid = "VENDOR_ID#ORDER_NUMBER"
    ↓
Include vrefid in NCM API payload
    ↓
Log debug information
    ↓
Send to NCM API
```

---

## Impact on Data Flow

### Before Fix
```
Order Creation
    ↓
Bulk Send to NCM
    ↓
NCM API Payload
    └─ vrefid: "ORD-12345"  ← Only order number
    ↓
NCM Portal
    └─ Vendor Ref ID: (empty or just order number)
```

### After Fix
```
Order Creation (with created_by user)
    ↓
Set vendor_id on User Account (Admin)
    ↓
Bulk Send to NCM
    ↓
NCM API Payload
    └─ vrefid: "VENDOR-ID#ORD-12345"  ← Vendor ID + Order number
    ↓
NCM Portal
    └─ Vendor Ref ID: VENDOR-ID#ORD-12345  ✅ Properly populated
```

---

## Migration Applied

### Migration: 0006_customuser_vendor_id.py
(Already exists and applied)

```python
migrations.AddField(
    model_name='customuser',
    name='vendor_id',
    field=models.CharField(
        blank=True,
        help_text='Unique vendor ID for logistics providers like NCM',
        max_length=100,
        null=True,
        unique=True
    ),
)
```

---

## Testing Checklist

- [ ] Vendor ID field visible in admin
- [ ] Can set vendor_id for user
- [ ] Vendor_id is unique (database constraint)
- [ ] Debug logs show correct vendor_ref_id
- [ ] NCM portal shows vendor reference ID
- [ ] Fallback to order_number works if vendor_id not set
- [ ] Activity log records vendor reference
- [ ] Bulk send works with multiple orders

---

## Environment Variables (if needed)

No new environment variables required. All existing NCM settings still apply:
- `NCM_API_BASE_URL`
- `NCM_API_KEY`

---

## Backward Compatibility

✅ All changes are backward compatible:
- Existing orders without vendor_id still work
- Falls back to order_number
- No database schema breaking changes
- Existing bulk send functionality unchanged
