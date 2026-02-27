# Dynamic Bulk Actions - System Architecture

## System Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                       SETUP MANAGEMENT PAGE                          │
│  ┌──────────────────┐  ┌──────────────────┐                         │
│  │ Order Statuses   │  │ Payment Statuses │                         │
│  │ ────────────────  │  │ ──────────────── │                         │
│  │ • Pending        │  │ • Unpaid         │                         │
│  │ • Processing     │  │ • Partial        │                         │
│  │ • Shipped        │  │ • Paid           │                         │
│  │ • Delivered      │  │ • Refunded       │                         │
│  └────────┬─────────┘  └────────┬─────────┘                         │
└───────────┼──────────────────────┼──────────────────────────────────┘
            │                      │
            ↓                      ↓
        ┌──────────────────────────────────┐
        │      Setup Database Table        │
        │  ┌────────────────────────────┐  │
        │  │ id | setup_type | name | .. │  │
        │  ├────────────────────────────┤  │
        │  │ 1  | status     | Pending   │  │
        │  │ 2  | status     | Processing│  │
        │  │ 3  | status     | Shipped   │  │
        │  │ 4  | status     | Delivered │  │
        │  │ 5  | payment_.. | Unpaid    │  │
        │  │ 6  | payment_.. | Partial   │  │
        │  │ 7  | payment_.. | Paid      │  │
        │  └────────────────────────────┘  │
        └──────────┬───────────────────────┘
                   │
                   ↓
        ┌──────────────────────────────────┐
        │      orders_list View            │
        │      (Django Backend)            │
        │                                   │
        │  order_setups = Setup.objects.   │
        │    filter(setup_type='status')   │
        │                                   │
        │  order_status_bulk_options = [   │
        │    ('status_setup_1',            │
        │     'Mark as Pending', '📋'),    │
        │    ('status_setup_2',            │
        │     'Mark as Processing', '📋'),│
        │    ...                           │
        │  ]                               │
        └──────────┬───────────────────────┘
                   │
                   ↓ (Pass to template context)
        ┌──────────────────────────────────┐
        │   orders_list.html Template      │
        │                                   │
        │  <select name="bulk_action">      │
        │    <option>Export to Excel</option
        │    <option>Move to Trash</option │
        │                                   │
        │    <optgroup label="Order...">   │
        │  {% for opt in bulk_options %}   │
        │    <option value="{{ opt[0] }}"> │
        │      {{ opt[2] }}{{ opt[1] }}   │
        │    </option>                     │
        │  {% endfor %}                    │
        │                                   │
        │  </select>                       │
        └──────────┬───────────────────────┘
                   │
                   ↓ (User selects option)
        ┌──────────────────────────────────┐
        │  Form Submission (POST)          │
        │  ───────────────────────────────  │
        │  bulk_action: status_setup_2     │
        │  order_ids: [1, 5, 14]           │
        │  csrfmiddlewaretoken: ...        │
        └──────────┬───────────────────────┘
                   │
                   ↓
        ┌──────────────────────────────────┐
        │  orders_bulk_action() Handler    │
        │  (Django Backend)                │
        │                                   │
        │  1. Extract setup_id from action │
        │     setup_id = 2                 │
        │                                   │
        │  2. Fetch Setup object           │
        │     status_setup = Setup.objects.│
        │       get(id=2, type='status')   │
        │     # Gets "Processing"          │
        │                                   │
        │  3. Update Order records         │
        │     for order in orders:         │
        │       order.status_setup = setup │
        │       order.order_status = name  │
        │       order.save()               │
        │                                   │
        │  4. Create Activity Logs         │
        │     OrderActivityLog.create(     │
        │       action: 'status_changed',  │
        │       description: '..setup...'  │
        │     )                            │
        │                                   │
        │  5. Show Success Message         │
        │     "3 order(s) marked as       │
        │      Processing!"               │
        └──────────┬───────────────────────┘
                   │
                   ↓
        ┌──────────────────────────────────┐
        │      Order Database              │
        │  ┌────────────────────────────┐  │
        │  │ id | status_setup | order_ │  │
        │  │    |      id      | status │  │
        │  ├────────────────────────────┤  │
        │  │ 1  │     2       │ Proces .│  │ ← Updated
        │  │ 5  │     2       │ Proces .│  │ ← Updated
        │  │ 14 │     2       │ Proces .│  │ ← Updated
        │  └────────────────────────────┘  │
        └──────────────────────────────────┘
```

---

## Data Flow: Detailed Steps

### Step 1: Setup Management Entry
```
User Action: Setup Management → Order Statuses → Add "Expedited"
Result: INSERT INTO Setup (setup_type='status', name='Expedited', is_active=True) → id=8
```

### Step 2: Page Load (orders_list view)
```python
# Django Backend (views.py:2390)
order_setups = Setup.objects.filter(
    setup_type='status', 
    is_active=True
).order_by('name')

# Result: QuerySet [Setup(id=1, name='Pending'), 
#                   Setup(id=2, name='Processing'),
#                   Setup(id=3, name='Shipped'),
#                   Setup(id=4, name='Delivered'),
#                   Setup(id=8, name='Expedited')]

# Create dropdown options
order_status_bulk_options = [
    ('status_setup_1', 'Mark as Pending', '📋'),
    ('status_setup_2', 'Mark as Processing', '📋'),
    ('status_setup_3', 'Mark as Shipped', '📋'),
    ('status_setup_4', 'Mark as Delivered', '📋'),
    ('status_setup_8', 'Mark as Expedited', '📋'),  # NEW!
]

# Pass to template
context = {
    ...
    'order_status_bulk_options': order_status_bulk_options,
}
```

### Step 3: Template Rendering
```html
<!-- Template (orders_list.html:175) -->
<select name="bulk_action">
    ...
    <optgroup label="Order Status">
        <option value="status_setup_1">📋 Mark as Pending</option>
        <option value="status_setup_2">📋 Mark as Processing</option>
        <option value="status_setup_3">📋 Mark as Shipped</option>
        <option value="status_setup_4">📋 Mark as Delivered</option>
        <option value="status_setup_8">📋 Mark as Expedited</option>
    </optgroup>
</select>

<!-- Result: Dropdown shows ALL 5 options including new "Expedited" -->
```

### Step 4: User Action & Form Submission
```
User: Selects orders [5, 7, 12]
      Chooses "Mark as Expedited" from dropdown
      Clicks "Apply Action"

POST Data:
  bulk_action: "status_setup_8"
  order_ids: ["5", "7", "12"]
  csrfmiddlewaretoken: "..."
```

### Step 5: Processing & Update
```python
# Handler (views.py:4585)
action = "status_setup_8"
order_ids = [5, 7, 12]

# Parse action
if action.startswith('status_setup_'):
    setup_id = int(action.split('_')[-1])  # setup_id = 8
    
    # Fetch Setup
    status_setup = Setup.objects.get(id=8, setup_type='status')
    # Gets: Setup(id=8, name='Expedited')
    
    # Update orders
    for order in Order.objects.filter(id__in=[5, 7, 12]):
        order.status_setup = status_setup  # FK points to Setup id=8
        order.order_status = 'Expedited'   # String field for backward compat
        order.save()
        
        # Create log entry
        OrderActivityLog.create(
            order_id=order.id,
            user_id=request.user.id,
            action_type='status_changed',
            description='Order status changed to Expedited by admin'
        )
```

### Step 6: Results
```
Database Changes:
┌─────────────────────────────────────────┐
│ Order Table (BEFORE)                    │
├─────────────────────────────────────────┤
│ id | status_setup_id | order_status     │
├─────────────────────────────────────────┤
│ 5  | NULL            | Pending          │
│ 7  | 1               | Pending          │
│ 12 | 2               | Processing       │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│ Order Table (AFTER)                     │
├─────────────────────────────────────────┤
│ id | status_setup_id | order_status     │
├─────────────────────────────────────────┤
│ 5  | 8               | Expedited        │ ← Updated
│ 7  | 8               | Expedited        │ ← Updated
│ 12 | 8               | Expedited        │ ← Updated
└─────────────────────────────────────────┘

Activity Logs Created:
- Order 5: "Order status changed to Expedited by admin"
- Order 7: "Order status changed to Expedited by admin"
- Order 12: "Order status changed to Expedited by admin"

User Feedback:
✅ 3 order(s) marked as Expedited!
```

---

## Key Implementation Points

### 1. Action Value Format
```python
# Format: {action_prefix}_{setup_id}
status_setup_8        # Order status with id=8
payment_status_setup_7 # Payment status with id=7
```

### 2. Parsing Action Value
```python
if action.startswith('status_setup_'):
    setup_id = int(action.split('_')[-1])
    # "status_setup_8" → setup_id = 8
    
elif action.startswith('payment_status_setup_'):
    setup_id = int(action.split('_')[-1])
    # "payment_status_setup_7" → setup_id = 7
```

### 3. Dual Field Update
```python
# Both fields updated to stay synchronized
order.status_setup = status_setup  # FK relationship
order.order_status = status_setup.name  # String field (backward compat)
order.save()
```

### 4. Error Handling
```python
try:
    setup_id = int(action.split('_')[-1])
    status_setup = Setup.objects.get(
        id=setup_id, 
        setup_type='status'
    )
except (ValueError, Setup.DoesNotExist):
    messages.error(request, 'Invalid status selected!')
    return redirect('orders_list')
```

---

## Performance Considerations

| Operation | Complexity | Notes |
|-----------|-----------|-------|
| Load setups      | O(n) | Single query, n = active setups |
| Render dropdown  | O(n) | Template iteration, cached |
| Parse action     | O(1) | Simple string split |
| Fetch setup      | O(1) | Single PK lookup |
| Update orders    | O(m*k) | m = orders, k = items (nested) |
| Create logs      | O(m) | One log per order |

**Optimization:** Use bulk_update() for large order counts to reduce queries.

---

## Backward Compatibility

Old action values still work:
```python
# These still function correctly
'mark_delivered'  → Update order_status='delivered'
'mark_processing' → Update order_status='processing'
'mark_shipped'    → Update order_status='shipped'
'mark_cancelled'  → Update order_status='cancelled'
'mark_paid'       → Update payment_status='paid'
'mark_pending'    → Update payment_status='pending'
```

---

## Future Enhancement: Bulk Update Optimization

```python
# Current approach (safe, creates logs)
for order in orders:
    order.status_setup = status_setup
    order.order_status = name
    order.save()  # ← One query per order

# Future approach (faster, no logs)
orders.update(
    status_setup=status_setup,
    order_status=name
)  # ← Single query for all orders
```

---

## Security Considerations

✅ **CSRF Protection:** Form includes CSRF token
✅ **SQL Injection:** Uses Django ORM (parameterized queries)
✅ **Input Validation:** Setup id is extracted and re-validated
✅ **Permissions:** Depends on user_permissions in order_bulk_action
✅ **Audit Trail:** All changes logged with user info
✅ **Type Checking:** setup_type validated before update

---

**Architecture Design:** DRY, Scalable, Maintainable
**Status:** ✅ Production Ready
