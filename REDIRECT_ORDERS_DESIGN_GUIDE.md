# Redirect Orders Page - Visual & Design Guide

## Color Scheme

### Gradient Colors
- **Primary Gradient**: #667eea (blue-purple) to #764ba2 (deep purple)
- **Success Gradient**: #11998e (teal) to #38ef7d (bright green)
- **Warning Orange**: #ffc107
- **Danger Red**: #dc3545
- **Info Blue**: #0d6efd

### Component Colors
- **Table Header**: Linear gradient from #667eea to #764ba2 (gradient)
- **Stat Cards**: Border-left with matching color (4px)
- **Hover Effects**: Subtle box-shadow and translate animations
- **Badges**: Various colors based on status

## Page Structure

```
┌─────────────────────────────────────────────────────────────┐
│ HEADER SECTION                                              │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ 📤 Redirect Orders                                       ││
│ │ View all orders that have been redirected to new        ││
│ │ customers                                                ││
│ │                                                          ││
│ │ [Possible Redirection]  [✅ Redirect Orders] [RTVs] ... ││
│ └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ STATISTICS SECTION (3 Cards)                                │
│ ┌──────────────┬──────────────┬──────────────┐             │
│ │ ✓ REDIRECTED │ 📋 THIS PAGE │ 💰 TOTAL     │             │
│ │ 45 orders    │ 12 orders    │ 12 entries   │             │
│ └──────────────┴──────────────┴──────────────┘             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ FILTERS SECTION                                             │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ Search: [_____] Branch: [dropdown] Start: [date]        ││
│ │ End Date: [date] [Filter] [Reset] Per Page: [50▼]       ││
│ └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ TABLE SECTION                                               │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ 📤 REDIRECTED ORDERS                                    ││
│ ├─────────────────────────────────────────────────────────┤│
│ │ Order # │ Old Customer │ Old Branch │ New Customer │... ││
│ ├─────────────────────────────────────────────────────────┤│
│ │ #12345  │ John Doe     │ TINKUNE   │ ✓ Jane Smith │... ││
│ │         │ 9841234567   │           │ 9847654321   │    ││
│ │         │ NCM: 98765   │           │              │    ││
│ ├─────────────────────────────────────────────────────────┤│
│ │ #12346  │ Alice Cooper │ KALIMATI  │ ✓ Bob Wilson │... ││
│ │         │ 9849876543   │           │ 9842468135   │    ││
│ │         │ NCM: 98766   │           │              │    ││
│ └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ PAGINATION SECTION                                          │
│ ◄ ‹ 1 [2] 3 › ►                                             │
└─────────────────────────────────────────────────────────────┘
```

## Modal Layout

```
┌──────────────────────────────────────────────────────────────┐
│ 📦 Order Details & Redirection History                   [✕] │
│ Order #12345  ✅ Redirected  📤 Redirected                   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│ ℹ️ ORDER OVERVIEW                                            │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ Order Number: #12345          NCM Order ID: 98765      │  │
│ │ Order Status: Processing      Total Amount: Rs.5000    │  │
│ │ Created At: Jan 10, 2025      Updated At: Jan 15, 2025 │  │
│ │ Items Count: 3 items                                    │  │
│ └────────────────────────────────────────────────────────┘  │
│                                                              │
│ 👤 ORIGINAL CUSTOMER DETAILS                                │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ Name: John Doe              Phone: 9841234567          │  │
│ │ Email: john@email.com       Branch: TINKUNE            │  │
│ │ Address: Thamel, Kathmandu, Nepal                      │  │
│ └────────────────────────────────────────────────────────┘  │
│                                                              │
│ ✓ REDIRECTED TO (NEW CUSTOMER)                              │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ Name: Jane Smith              Phone: 9847654321        │  │
│ │ Email: jane@email.com        Branch: KALIMATI          │  │
│ │ Address: Patan, Bhaktapur, Nepal                       │  │
│ └────────────────────────────────────────────────────────┘  │
│                                                              │
│ 📜 REDIRECTION HISTORY                                      │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ → Redirected by admin_user                             │  │
│ │   Jan 15, 2025 at 03:45 PM                             │  │
│ │   Reason: Customer requested different address         │  │
│ └────────────────────────────────────────────────────────┘  │
│                                                              │
│ 📦 PRODUCTS / ITEMS                                         │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ # │ Product        │ Qty │ Price    │ Total          │  │
│ ├───┼────────────────┼─────┼──────────┼────────────────┤  │
│ │ 1 │ Product Name   │  2  │ 1000.00  │ 2000.00        │  │
│ │ 2 │ Another Item   │  1  │ 2000.00  │ 2000.00        │  │
│ │ 3 │ Third Product  │  1  │ 1000.00  │ 1000.00        │  │
│ └────────────────────────────────────────────────────────┘  │
│                                                              │
│ 💰 FINANCIAL DETAILS                                        │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ Discount: Rs.0              Shipping: Rs.0             │  │
│ │ Tax %: 0%                   Total: Rs.5000             │  │
│ └────────────────────────────────────────────────────────┘  │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ [Close]  [Open Full Order ↗️]                               │
└──────────────────────────────────────────────────────────────┘
```

## Responsive Breakpoints

### Desktop (≥992px)
- All columns visible
- Full table width
- Hover effects on rows
- Modals centered on screen

### Tablet (768px - 992px)
- Hidden columns: Old Branch, New Branch, Redirect Date, By
- Compact table layout
- Full-width modals
- Stacked filter inputs

### Mobile (<768px)
- Hidden columns: Most columns hidden
- Essential info: Order #, New Customer, Amount
- Vertical table layout
- Full-width inputs
- Touch-friendly buttons (larger tap areas)

## Animation Effects

### Table Row Hover
```css
transform: translateY(-2px);
box-shadow: inset 3px 0 0 #0d6efd;
background-color: #f8f9ff;
```

### Button Hover
```css
transform: translateY(-2px);
box-shadow: 0 4px 12px rgba(color, 0.4);
```

### Card Hover
```css
transform: translateY(-2px);
box-shadow: 0 4px 12px rgba(0,0,0,0.15);
```

## Typography

### Headers
- Font Size: 2rem (h2)
- Font Weight: 700
- Color: #2c3e50

### Table Headers
- Font Size: 0.9rem
- Font Weight: 600
- Color: White
- Text Transform: UPPERCASE
- Letter Spacing: 0.5px

### Labels
- Font Size: 0.85rem
- Font Weight: 600
- Color: #6c757d
- Text Transform: UPPERCASE
- Letter Spacing: 0.5px

### Values
- Font Size: 1rem
- Font Weight: 400
- Color: #2c3e50

## Spacing & Layout

### Padding
- Card Body: 1.5rem
- Table Cell: 1rem (1.25rem on header)
- Modal: 2rem
- Section: 1.5rem

### Margins
- Statistics Row: 3rem (bottom)
- Filter Card: 1rem (bottom)
- Table Card: 1rem (bottom)
- Pagination: 2rem (top)

### Gaps
- Button Groups: 0.5rem
- Field Groups: 1rem
- Row Elements: 3rem

## Border Radius
- Cards: 8px
- Buttons: 6px
- Input Fields: 6px
- Modals: 10px
- Badges: 4px

## Shadows

### Light Shadow (Default)
```css
box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
```

### Medium Shadow (Cards)
```css
box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
```

### Heavy Shadow (Modals)
```css
box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
```

## Icons Used

- 📤 `fas fa-exchange-alt` - Redirect/Exchange
- 👤 `fas fa-user` - Customer
- 📋 `fas fa-list` - List
- ✓ `fas fa-check-circle` - Completed/Success
- 💰 `fas fa-rupee-sign` - Currency
- 🔍 `fas fa-search` - Search
- 📅 `fas fa-calendar` - Date
- ⚙️ `fas fa-cog` - Settings/Options
- 👁️ `fas fa-eye` - View
- 🔗 `fas fa-external-link-alt` - External link
- 📊 `fas fa-chart-bar` - Statistics
- ⏱️ `fas fa-history` - History
- 📦 `fas fa-boxes` - Products
- 💳 `fas fa-calculator` - Financial
- ⬅️ `fas fa-arrow-left` - Back
- ➡️ `fas fa-arrow-right` - Forward

## Badge Styles

```
Success (Green): bg-success text-white
Warning (Yellow): bg-warning text-dark
Danger (Red): bg-danger text-white
Info (Blue): bg-info text-dark
Primary (Purple): bg-primary text-white
Secondary (Gray): bg-secondary text-white
Light: bg-light text-dark
```

## Input & Form Styling

### Text Inputs
- Border: 1px solid #e0e0e0
- Border Radius: 6px
- Focus: Border #667eea, Shadow 0 0 0 0.2rem rgba(102, 126, 234, 0.15)

### Selects (Dropdowns)
- Border: 1px solid #e0e0e0
- Border Radius: 6px
- Same focus styling as inputs

### Labels
- Font Size: 0.875rem
- Color: #6c757d
- Font Weight: 500
- Margin Bottom: 0.5rem

## Accessibility Features

1. **Keyboard Navigation**: All buttons keyboard accessible
2. **Screen Reader Support**: Proper ARIA labels
3. **Color Contrast**: WCAG AA compliant
4. **Focus Indicators**: Visible focus on interactive elements
5. **Form Labels**: Associated with inputs via for/id

## Browser Support

- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+
- Mobile browsers (Chrome, Safari, Firefox)

## Performance Optimizations

- CSS Grid/Flexbox for layout
- CSS transforms for animations (GPU accelerated)
- Hardware acceleration for smooth transitions
- Lazy loading of images if any
- Minified CSS in production
