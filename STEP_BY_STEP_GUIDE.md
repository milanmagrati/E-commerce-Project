# Step-by-Step Implementation Guide

## ✅ Status: COMPLETE

All code changes have been implemented and are ready to use.

---

## Phase 1: Setup Your Vendor IDs (5 minutes)

### Step 1.1: Access Admin Panel
```
URL: http://yoursite.com/admin/
Login with admin credentials
```

### Step 1.2: Navigate to Users
```
Left Sidebar → Authentication and Authorization → Users
Click on "Customize >" or directly find CustomUser
```

### Step 1.3: Select Your First User
```
Click on a user you want to assign vendor ID
Example: Click on "sales_manager" or your username
```

### Step 1.4: Set Vendor ID
```
Find "Vendor Info" section (below Personal Info)
In "Vendor ID" field, enter a unique identifier:

Examples:
- V001
- TRENDY-SHOP-001
- VENDOR-ABC
- SHOP-001

Rules:
- Must be unique
- Can include letters, numbers, hyphens
- Max 100 characters
- No spaces recommended
```

### Step 1.5: Save Changes
```
Click "Save and continue editing" button
You should see: "The CustomUser was changed successfully."
```

### Step 1.6: Repeat for Other Users
```
Go back to Users list
Repeat steps 1.3-1.5 for each user who sends orders
```

---

## Phase 2: Test the Fix (5 minutes)

### Step 2.1: Create Test Orders
```
Admin Panel → Dashboard → Orders
Create 2-3 test orders
Make sure they're created by a user with vendor_id set
```

### Step 2.2: Select Orders for Bulk Send
```
Orders Management Page
Check the checkboxes for your test orders
"Bulk Send to NCM" button appears
```

### Step 2.3: Open Bulk Send Modal
```
Click "Bulk Send to NCM" button
Modal opens with options
```

### Step 2.4: Configure NCM Settings
```
From Branch: TINKUNE (or your branch)
Delivery Type: Door2Door
Default Weight: 1.0
Check "Auto-set Logistics to NCM" (recommended)
```

### Step 2.5: Send Orders
```
Click "Send to NCM Now" button
Orders are processed
```

### Step 2.6: Check Debug Output
```
Look at Django console/terminal where you're running:
python manage.py runserver

You should see:
DEBUG: NCM API Payload for Order ORD-12345:
  Vendor Ref ID: V001#ORD-12345
  Full Payload: {...}
Response Status: 200
```

### Step 2.7: Verify in NCM Portal
```
1. Login to NCM portal: https://portal.nepalcanmove.com
2. Go to Orders section
3. Find your test orders
4. Check "Vendor Ref ID" column
5. Should show: "V001#ORD-12345" ✅
```

---

## Phase 3: Verify Integration (5 minutes)

### Check 1: Admin Interface
```
✓ Can see vendor_id field in user edit form
✓ Can set/update vendor_id
✓ Can search users by vendor_id
```

### Check 2: Order Activity Log
```
✓ Orders show vendor reference in activity logs
Example: "Sent to NCM. NCM ID: 12345, Vendor Ref: V001#ORD-12345"
```

### Check 3: Database
```
Open Django shell:
python manage.py shell

>>> from accounts.models import CustomUser
>>> user = CustomUser.objects.get(username='your_username')
>>> print(user.vendor_id)
# Should print: V001
```

### Check 4: NCM Integration
```
✓ Orders showing in NCM portal
✓ Vendor Ref ID populated correctly
✓ No errors in NCM response
```

---

## Phase 4: Production Deployment (10 minutes)

### Step 4.1: Set All User Vendor IDs
```
For each user account in the system:
Admin → Users
Edit each user
Set unique vendor_id
Save
```

### Step 4.2: Enable Debug Logging (Optional)
```
Edit settings.py and ensure logging is configured:

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG',
    },
}
```

### Step 4.3: Test with Real Orders
```
Create orders with realistic data
Bulk send 5-10 orders to NCM
Monitor console for debug output
Check NCM portal for proper vendor identification
```

### Step 4.4: Monitor First Week
```
✓ Track bulk sends to NCM
✓ Verify vendor ref IDs in NCM portal
✓ Check for any errors in debug logs
✓ Confirm customers receive correct tracking
```

---

## Troubleshooting Guide

### Issue 1: Vendor Ref ID Shows Empty in NCM
**Solution**:
1. Check Django console for debug output
2. Verify vendor_id is set for the user
3. Check if user created the order (order.created_by)
4. Review NCM API response in debug logs

**Debug Command**:
```
python manage.py shell
>>> from accounts.models import CustomUser
>>> user = CustomUser.objects.get(username='your_user')
>>> print(f"Vendor ID: {user.vendor_id}")
```

### Issue 2: Can't Set Vendor ID in Admin
**Solution**:
1. Check accounts/admin.py is properly updated
2. Restart Django server
3. Clear browser cache
4. Verify you're editing CustomUser, not auth.User

### Issue 3: Duplicate Vendor ID Error
**Solution**:
1. vendor_id must be unique across all users
2. Use different vendor_id for each user
3. Example: "SHOP-001", "SHOP-002", etc.

### Issue 4: Orders Not Sending to NCM
**Solution**:
1. Check debug logs for error messages
2. Verify NCM API credentials in settings
3. Check vendor_ref_id is properly generated
4. Look for NCM API response errors

---

## Verification Commands

### Check Vendor ID Field Exists
```bash
python manage.py shell
>>> from accounts.models import CustomUser
>>> print(CustomUser._meta.get_field('vendor_id'))
# Should print: vendor_id field details
```

### Check User Vendor ID
```bash
python manage.py shell
>>> from accounts.models import CustomUser
>>> user = CustomUser.objects.first()
>>> print(f"User: {user.username}, Vendor ID: {user.vendor_id}")
```

### Check Order Created By
```bash
python manage.py shell
>>> from dashboard.models import Order
>>> order = Order.objects.first()
>>> print(f"Order: {order.order_number}, Created by: {order.created_by}")
>>> print(f"Vendor ID: {order.created_by.vendor_id if order.created_by else 'None'}")
```

---

## Quick Reference

### What Gets Sent to NCM

```json
{
  "vrefid": "VENDOR_ID#ORDER_NUMBER",
  "name": "Customer Name",
  "phone": "9841234567",
  "cod_charge": 2500.00,
  "address": "Full Address",
  "fbranch": "Your Branch",
  "branch": "Destination",
  "package": "Product Name",
  "instruction": "Special notes",
  "deliverytype": "Door2Door",
  "weight": 1.0
}
```

### Vendor Reference Format

```
IF user has vendor_id:
  → "VENDOR_ID#ORDER_NUMBER"
  → "V001#ORD-12345"

ELSE IF order has order_number:
  → "ORDER_NUMBER"
  → "ORD-12345"

ELSE:
  → "ORD-{id}"
  → "ORD-9876"
```

---

## Rollback Instructions (If Needed)

### If Something Goes Wrong
```bash
# Revert changes to views.py
git checkout dashboard/views.py

# Revert changes to admin.py
git checkout accounts/admin.py

# Restart Django
python manage.py runserver
```

**Note**: The vendor_id field will remain in the database (harmless if not used)

---

## FAQ

**Q: Do I have to set vendor_id for all users?**
A: No. If not set, system falls back to order_number. Setting it improves tracking.

**Q: Can multiple users share the same vendor_id?**
A: No. vendor_id is unique. Each user/vendor needs their own.

**Q: Does this affect existing orders already sent to NCM?**
A: No. Only new bulk sends will include the vendor reference ID.

**Q: What if vendor_id is not set when sending?**
A: System uses order_number as fallback, then auto-generates if needed.

**Q: Will this break existing functionality?**
A: No. All changes are backward compatible.

**Q: How do I check if vendor_ref_id was sent correctly?**
A: Check Django console for "DEBUG: NCM API Payload" messages.

---

## Support Documentation

- **Full Details**: See `NCM_VENDOR_ID_FIX.md`
- **Code Changes**: See `CODE_CHANGES_DETAILED.md`
- **Quick Setup**: See `VENDOR_ID_QUICK_SETUP.md`
- **Summary**: See `SOLUTION_SUMMARY.md`

---

## Timeline

- **Setup Vendor IDs**: 5 minutes
- **Test the Fix**: 5 minutes
- **Verify Integration**: 5 minutes
- **Production Ready**: Immediately

**Total Time**: ~15 minutes

---

## Checklist

### Before Going Live
- [ ] Vendor IDs set for all users
- [ ] Test orders created and sent to NCM
- [ ] Debug logs reviewed for proper vendor ref IDs
- [ ] NCM portal shows correct vendor references
- [ ] No errors in API responses

### After Going Live
- [ ] Monitor first batch of orders
- [ ] Check NCM portal regularly
- [ ] Review debug logs for issues
- [ ] Confirm customer tracking works
- [ ] Validate vendor identification

---

**Status**: ✅ READY TO USE

All code is in place. Just follow Phase 1 to set vendor IDs, then start using!

---

**Questions?** Check the documentation files or review the debug console output.

**Need Help?** Look at the debug logs in Django console - they show exactly what's being sent to NCM.

---

**Happy shipping!** 🚀
