# NCM Vendor ID Integration Fix

## Overview
This fix ensures that when you send orders in bulk to NCM (Nepal Can Move) logistics, the **Vendor Reference ID** is properly populated and transmitted to the NCM API.

## Problem Statement
Previously, when sending orders in bulk to NCM, the "Vendor Ref ID" field was appearing empty on the NCM portal because:
1. The vendor reference ID wasn't being properly generated from available data
2. There was no mechanism to associate orders with their vendor/creator
3. The system wasn't utilizing the vendor_id field from the user account

## Solution Implemented

### 1. **Added Vendor ID Support to User Accounts**
- **File Modified**: `accounts/models.py`
- **Change**: Added `vendor_id` field to `CustomUser` model
  ```python
  vendor_id = models.CharField(max_length=100, blank=True, null=True, unique=True, 
                              help_text="Unique vendor ID for logistics providers like NCM")
  ```
- **Migration**: Already applied as `0006_customuser_vendor_id.py`

### 2. **Enhanced Vendor Reference ID Generation**
- **File Modified**: `dashboard/views.py` (in `send_single_order_to_ncm` function)
- **Change**: Implemented intelligent vendor reference ID generation with priority fallback:

  ```python
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

### 3. **Updated NCM API Payload**
- **File Modified**: `dashboard/views.py`
- **Change**: The `vrefid` field in the NCM API payload now receives:
  - Vendor-specific reference ID (format: `VENDOR_ID#ORDER_NUMBER`)
  - Or fallback to `order_number`
  - Or auto-generated reference ID

### 4. **Added Debug Logging**
- **File Modified**: `dashboard/views.py`
- **Change**: Added comprehensive debug logging to track what's being sent:
  ```python
  print(f"DEBUG: NCM API Payload for Order {order.order_number}:")
  print(f"  Vendor Ref ID: {payload.get('vrefid')}")
  print(f"  Full Payload: {payload}")
  ```

### 5. **Enhanced Admin Interface**
- **File Modified**: `accounts/admin.py`
- **Change**: Added `CustomUserAdmin` with:
  - Vendor ID field management
  - Easy access to set vendor IDs for users
  - Organized fieldsets for better UX

## How to Use

### Setting Vendor IDs for Users

1. **Navigate to Django Admin**: `http://your-admin-url/admin/accounts/customuser/`
2. **Select a User**: Click on the user account you want to edit
3. **Add Vendor ID**: In the "Vendor Info" section, enter a unique vendor ID (e.g., `VENDOR-001`, `TrendyShopping`, etc.)
4. **Save**: Click save

### How the System Works Now

When you bulk send orders to NCM:

1. **Order Created**: User creates an order (associated with their account)
2. **Bulk Send**: User selects multiple orders and clicks "Send to NCM"
3. **Vendor Reference Generated**: System checks:
   - Does the creator have a vendor_id? → Use it (e.g., `VENDOR-001#ORD-12345`)
   - No vendor_id? → Use order_number (e.g., `ORD-12345`)
   - No order_number? → Generate one (e.g., `ORD-9876`)
4. **API Call**: NCM receives the full payload including the vendor reference ID:
   ```json
   {
     "vrefid": "VENDOR-001#ORD-12345",
     "name": "Customer Name",
     "phone": "9841234567",
     "address": "Customer Address",
     ...
   }
   ```
5. **NCM Portal**: The Vendor Ref ID field is now populated with the correct reference

## NCM API Payload Structure

The complete payload sent to NCM includes:

```python
{
    "name": "Customer Name",
    "phone": "9841234567",
    "phone2": "",
    "cod_charge": 2500.00,
    "address": "Shipping Address",
    "fbranch": "TINKUNE",  # Your warehouse branch
    "branch": "KATHMANDU",  # Destination branch
    "package": "Product Name",
    "vrefid": "VENDOR-ID#ORDER-NUMBER",  # ✅ NOW PROPERLY SET
    "instruction": "Special instructions",
    "deliverytype": "Door2Door",
    "weight": 1.0
}
```

## Testing the Fix

1. **Set Vendor IDs in Admin**: 
   - Go to `/admin/accounts/customuser/`
   - Edit a user and add `vendor_id` (e.g., `TEST-VENDOR-001`)

2. **Create Test Orders**: 
   - Create a few orders and ensure they're created by the user with vendor_id set

3. **Bulk Send to NCM**:
   - Select 1-3 orders
   - Click "Bulk Send to NCM" button
   - Check the Django terminal for debug output showing the vendor_ref_id

4. **Verify in NCM Portal**:
   - Log in to NCM portal
   - Check the "Vendor Ref ID" column
   - Should now show values like `TEST-VENDOR-001#ORD-12345`

## Debug Output Example

When you send an order, you'll see debug logs like:

```
DEBUG: NCM API Payload for Order ORD-12345:
  Vendor Ref ID: TEST-VENDOR-001#ORD-12345
  Full Payload: {
    'name': 'John Doe',
    'phone': '9841234567',
    'cod_charge': 2500.0,
    'address': '123 Main St',
    'fbranch': 'TINKUNE',
    'branch': 'KATHMANDU',
    'package': 'Product Name',
    'vrefid': 'TEST-VENDOR-001#ORD-12345',
    'instruction': '',
    'deliverytype': 'Door2Door',
    'weight': 1.0
  }
Response Status: 200
```

## Files Modified

1. ✅ `accounts/models.py` - Added vendor_id field
2. ✅ `accounts/admin.py` - Enhanced admin interface
3. ✅ `dashboard/views.py` - Updated send_single_order_to_ncm function
4. ✅ Migrations already exist (0006_customuser_vendor_id.py)

## Benefits

✅ **Vendor ID now properly sent to NCM** - No more empty "Vendor Ref ID" fields
✅ **Flexible identification** - Supports vendor_id, order_number, or auto-generated IDs
✅ **Better tracking** - Vendor reference ID helps NCM track orders back to you
✅ **Debug support** - Console logging helps troubleshoot issues
✅ **Admin interface** - Easy vendor ID management
✅ **Backward compatible** - Works even if vendor_id is not set (falls back to order_number)

## Next Steps

1. **Set vendor IDs** for all your users in the admin panel
2. **Test bulk send** with a few orders to verify the vendor reference ID is being sent
3. **Monitor NCM portal** to confirm orders now have vendor references
4. **Check debug logs** if any issues arise (visible in Django console/logs)

## Support

If you encounter any issues:

1. Check the Django console for debug output showing the payload sent
2. Verify vendor_id is set for the user in admin
3. Ensure the NCM API credentials are correct in settings.py
4. Check NCM API response for specific error messages
