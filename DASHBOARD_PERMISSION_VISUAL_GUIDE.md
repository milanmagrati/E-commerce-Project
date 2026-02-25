# Dashboard Permission - Visual Guide

## 📺 User Create Page - Permissions Section

```
┌──────────────────────────────────────────────────────────────────────┐
│ ⚙️ Custom Permissions - Check to Enable                    [✓][✕]   │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐   │
│  │ 📊 Dashboard     │  │ 🛒 Orders        │  │ 📦 Products      │   │
│  │ Module           │  │ Module           │  │ Module           │   │
│  ├──────────────────┤  ├──────────────────┤  ├──────────────────┤   │
│  │ ☑ View Dashboard │  │ ☑ View Orders    │  │ ☑ View Products  │   │
│  │                  │  │ ☑ Create Orders  │  │ ☑ Create Prod.   │   │
│  │                  │  │ ☐ Edit Orders    │  │ ☑ Edit Products  │   │
│  │                  │  │ ☐ Delete Orders  │  │ ☐ Delete Prod.   │   │
│  │                  │  │ ☐ Cancel Orders  │  │                  │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘   │
│                                                                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐   │
│  │ 👥 Customers     │  │ 📋 Returns       │  │ 🎯 Staff Targets │   │
│  │ Module           │  │ Module           │  │ Module           │   │
│  ├──────────────────┤  ├──────────────────┤  ├──────────────────┤   │
│  │ ☐ View Cust.     │  │ ☐ View Returns   │  │ ☐ View All Targ. │   │
│  │ ☐ Create Cust.   │  │ ☐ Create Returns │  │ ☐ Set Targets    │   │
│  │ ☐ Edit Cust.     │  │ ☐ Edit Returns   │  │ ☐ Edit Targets   │   │
│  │ ☐ Delete Cust.   │  │ ☐ Delete Returns │  │ ☐ Delete Targets │   │
│  │                  │  │ ☐ Approve Ret.   │  │ ☑ View Own Targ. │   │
│  │                  │  │ ☐ Process Refund │  │                  │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘   │
│                                 [more modules...]                     │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘

KEY POINTS:
✓ Dashboard Module is FIRST card (top-left position)
✓ Purple gradient header distinguishes it
✓ Single "View Dashboard" checkbox
✓ Appears in same grid as other permission modules
```

---

## 🎨 Dashboard Module Card - Detailed View

```
NORMAL STATE (Unchecked):
┌─────────────────────────────────────────┐
│ 📊 Dashboard Module                     │  ← Purple Gradient Header
│ background: linear-gradient(135deg,     │
│   #667eea 0%, #764ba2 100%)            │
├─────────────────────────────────────────┤
│                                         │
│ ☐ View Dashboard                        │  ← Checkbox (unchecked)
│                                         │
│ (hover state shows slight shadow)       │
│                                         │
└─────────────────────────────────────────┘


CHECKED STATE:
┌─────────────────────────────────────────┐
│ 📊 Dashboard Module                     │
├─────────────────────────────────────────┤
│                                         │
│ ☑ View Dashboard                        │  ← Checkbox (checked)
│                                         │
│ (hover state shows shadow + lift)       │
│                                         │
└─────────────────────────────────────────┘


HOVERED STATE:
┏─────────────────────────────────────────┐
┃ 📊 Dashboard Module                     │  ← Shadow & slight Y-lift
┣─────────────────────────────────────────┤
┃                                         ┃
┃ ☑ View Dashboard                        ┃
┃                                         ┃
┗─────────────────────────────────────────┘
    box-shadow: 0 4px 12px rgba(0,0,0,0.1)
    transform: translateY(-2px)
```

---

## 📱 Responsive Breakpoints

### DESKTOP (xl: 1200px+)
```
┌────────────────┬────────────────┬────────────────┐
│   Dashboard    │    Orders      │   Products     │  ← col-xl-4
│   Module       │    Module      │    Module      │
├────────────────┼────────────────┼────────────────┤
│   Customers    │    Returns     │   Staff Targets│  ← 3 columns
├────────────────┼────────────────┼────────────────┤
│   Dispatch     │   Inventory    │    Reports     │
└────────────────┴────────────────┴────────────────┘
```

### TABLET (lg: 992px+)
```
┌───────────────────────┬───────────────────────┐
│   Dashboard Module    │    Orders Module      │  ← col-lg-6
├───────────────────────┼───────────────────────┤
│   Products Module     │   Customers Module    │  ← 2 columns
├───────────────────────┼───────────────────────┤
│   Returns Module      │  Staff Targets Module │
└───────────────────────┴───────────────────────┘
```

### MOBILE (< 768px)
```
┌──────────────────────────┐
│  Dashboard Module        │  ← col-12
├──────────────────────────┤
│  Orders Module           │  ← Full width
├──────────────────────────┤
│  Products Module         │  ← Stacked
├──────────────────────────┤
│  Customers Module        │
└──────────────────────────┘
```

---

## 🔄 User Create - Role Selection Flow

```
STEP 1: Select Role
┌─────────────────────────────────┐
│ Choose Role:                    │
│ [Administrator        ▼]        │
└─────────────────────────────────┘
                ↓
        Dashboard Permission
            NOT SHOWN
       (Admin has all perms)
                ↓
        [Create User Button]


STEP 2a: Select Warehouse Role
┌─────────────────────────────────┐
│ Choose Role:                    │
│ [Warehouse            ▼]        │
└─────────────────────────────────┘
                ↓
        Permissions Section
            SHOWN
                ↓
        Dashboard: ☑ (Auto-enabled)
        Orders: ☑ (Auto-enabled)
        Products: ☐ (Auto-disabled)
        ... (warehouse defaults)


STEP 2b: Select Sales Role
┌─────────────────────────────────┐
│ Choose Role:                    │
│ [Sales                ▼]        │
└─────────────────────────────────┘
                ↓
        Permissions Section
            SHOWN
                ↓
        Dashboard: ☑ (Auto-enabled)
        Orders: ☑ (Auto-enabled)
        Products: ☑ (Auto-enabled)
        Customers: ☑ (Auto-enabled)
        ... (sales defaults)
```

---

## ✏️ User Edit Page - Dashboard Permission

```
EDITING USER (Dashboard Enabled):
┌────────────────────────────────────────┐
│ Edit User: john_doe@example.com        │
├────────────────────────────────────────┤
│                                        │
│ Account Information                    │
│ ├─ Username: john_doe                  │
│ ├─ Email: john_doe@example.com         │
│ └─ Phone: +977-1234567890              │
│                                        │
│ Role Selection                         │
│ └─ Role: [Sales                ▼]     │
│                                        │
│ Custom Permissions                     │
│ ┌────────────────────────────────────┐ │
│ │ 📊 Dashboard Module                │ │
│ ├────────────────────────────────────┤ │
│ │ ☑ View Dashboard               ✓   │ │  ← Checked (shows as blue checkmark)
│ │                                    │ │
│ │ (status indicator on the left)      │ │
│ └────────────────────────────────────┘ │
│                                        │
│ ┌────────────────────────────────────┐ │
│ │ 🛒 Orders Module                   │ │
│ ├────────────────────────────────────┤ │
│ │ ☑ View Orders                      │ │
│ │ ☑ Create Orders                    │ │
│ └────────────────────────────────────┘ │
│                                        │
│ [...more permission modules...]        │
│                                        │
│ [Cancel]                [Update User]  │
└────────────────────────────────────────┘


EDITING USER (Dashboard Disabled):
┌────────────────────────────────────────┐
│ Edit User: jane_doe@example.com        │
├────────────────────────────────────────┤
│ ... (account info) ...                 │
│ Role: [Warehouse              ▼]       │
│                                        │
│ Custom Permissions                     │
│ ┌────────────────────────────────────┐ │
│ │ 📊 Dashboard Module                │ │
│ ├────────────────────────────────────┤ │
│ │ ☐ View Dashboard               ✗   │ │  ← Unchecked (permission disabled)
│ │                                    │ │
│ └────────────────────────────────────┘ │
│  ^This user cannot access dashboard!    │
│                                        │
│ [Cancel]                [Update User]  │
└────────────────────────────────────────┘
```

---

## 🚫 Access Denied Flow

```
USER ATTEMPTS DASHBOARD ACCESS:
         ↓
┌─────────────────────────────────┐
│ http://site.com/dashboard/      │
└─────────────────────────────────┘
         ↓
  Django View Checks:
  if not request.user.can_view_dashboard:
         ↓
  Dashboard Permission = FALSE
         ↓
┌─────────────────────────────────┐
│ ❌ Error Message:                │
│ You do not have permission to    │
│ view the dashboard.              │
└─────────────────────────────────┘
         ↓
┌─────────────────────────────────┐
│ Redirect: /login/                │
│ (No dashboard data shown)        │
└─────────────────────────────────┘


ADMIN GRANTS PERMISSION:
User Edit Page
        ↓
☑ View Dashboard
        ↓
[Update User]
        ↓
can_view_dashboard = TRUE
        ↓
User re-logs in
        ↓
Dashboard loads successfully! ✓
```

---

## 🎯 Color Scheme Reference

### Dashboard Module Header
```css
background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
color: white;
border-radius: 8px 8px 0 0;
padding: 12px 15px;
font-weight: 600;
```

### Comparison with Other Modules
```
Dashboard Module   │ 📊 Purple Gradient (#667eea → #764ba2)
Orders Module      │ 🛒 Blue (#0d6efd)
Products Module    │ 📦 Green (#198754)
Customers Module   │ 👥 Cyan (#0dcaf0)
Returns Module     │ 📋 Red (#dc3545)
Staff Targets      │ 🎯 Orange (#fd7e14)
Dispatch Module    │ 🚚 Yellow (#ffc107)
Inventory Module   │ 📦 Gray (#6c757d)
Reports Module     │ 📊 Dark (#212529)
```

---

## 💾 State Persistence

```
CREATE USER WITH DASHBOARD ENABLED:
Create Form → ☑ Dashboard → Submit
        ↓
Database: can_view_dashboard = TRUE
        ↓
User Login → Dashboard Accessible ✓


EDIT USER TO DISABLE DASHBOARD:
Edit Form → ☐ Dashboard → Update
        ↓
Database: can_view_dashboard = FALSE
        ↓
User Login → Dashboard Shows Error ✗
        ↓
Admin Re-enables → ☑ Dashboard → Update
        ↓
Database: can_view_dashboard = TRUE
        ↓
User Login → Dashboard Accessible ✓
```

---

## 📊 Permission Matrix

```
User Role        │ Dashboard │ Orders │ Products │ Customers │ ... │ Status
─────────────────┼───────────┼────────┼──────────┼───────────┼─────┼────────
Admin            │     ✓     │   ✓    │    ✓     │     ✓     │ ... │ All
Warehouse        │     ✓     │   ✓    │    ✓     │     ✓     │ ... │ Enabled
Sales            │     ✓     │   ✓    │    ✓     │     ✓     │ ... │ Enabled
John (Warehouse) │     ✓     │   ✓    │    ✗     │     ✗     │ ... │ Modified
Jane (Sales)     │     ✗     │   ✗    │    ✗     │     ✗     │ ... │ No Access
─────────────────┴───────────┴────────┴──────────┴───────────┴─────┴────────
```

---

**Visual Guide Complete** ✅
