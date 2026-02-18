# Staff Performance Dashboard - Setup Guide

## Overview
A complete staff performance analytics dashboard has been implemented according to the `staff_performance.html` template design. The solution includes models, views, and route configurations.

## Files Created/Modified

### 1. **Model** - `dashboard/models.py`
Added `StaffPerformance` model to track staff metrics:

```python
class StaffPerformance(models.Model):
    staff_member = models.OneToOneField(User, on_delete=models.CASCADE)
    total_orders = models.IntegerField(default=0)
    successful_orders = models.IntegerField(default=0)
    return_count = models.IntegerField(default=0)
    total_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    success_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    period_start = models.DateField(auto_now_add=True)
    period_end = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**Key Methods:**
- `calculate_metrics()`: Calculates performance metrics based on orders created by staff member

### 2. **View Function** - `dashboard/views.py`
Added `staff_performance_view()` that provides:

**KPI Calculations:**
- Total successful deliveries count
- Total returns count
- Total revenue (sum of paid orders)
- Success rate percentage (delivered orders / total orders)

**Features:**
- **Period Filtering**: Today, This Month (default), Last 30 Days, YTD
- **Staff Filtering**: View metrics for all staff or individual team members
- **Performance Trends**: 8-day trend data with daily orders, returns, and revenue
- **Top Products**: List of top 3 products by units sold with revenue
- **Staff Rankings**: Team members ranked by success rate with order counts

**Context Variables Passed to Template:**
```python
{
    'total_orders': int,
    'successful_deliveries': int,
    'returns': int,
    'total_revenue': float,
    'success_rate': float,
    'staff_performance_data': list,  # Sorted by success rate
    'top_products': list,
    'performance_trends': JSON string,  # For chart
    'staff_members': QuerySet,
    'selected_period': str,
    'selected_staff': str,
    'date_range': str,
}
```

### 3. **URL Route** - `dashboard/urls.py`
Added route:
```python
path('staff-performance/', views.staff_performance_view, name='staff_performance'),
```

**Access URL:** `/dashboard/staff-performance/`

### 4. **Admin Interface** - `dashboard/admin.py`
Registered `StaffPerformance` model in Django admin with:
- Read-only performance metrics
- Staff member search
- Period management
- Organized fieldsets

### 5. **Template** - `dashboard/templates/staff_performance.html`
Updated existing template to use dynamic data:
- KPI cards display real data with proper formatting
- Performance chart uses JSON data from view
- Filter dropdowns populated with staff members
- Staff ranking table shows actual performance data
- Top products table displays real product metrics

## How It Works

### 1. **View Flow**
```
Request → staff_performance_view()
  ├─ Get filter parameters (period, staff)
  ├─ Calculate date range
  ├─ Query orders based on filters
  ├─ Calculate KPIs (successful orders, returns, revenue, success rate)
  ├─ Aggregate performance data per staff member
  ├─ Generate 8-day performance trends
  ├─ Get top products
  └─ Render template with context
```

### 2. **Data Aggregation**
The view uses Django ORM aggregations:
- `Sum()`: Calculate total revenue
- `Count()`: Count successful deliveries and returns
- `Filter()`: Apply date ranges and status filters
- Sorted performance data by success rate

### 3. **Filtering Logic**
- **Period**: Defines date range for query filtering
- **Staff Member**: Optional - if not specified, shows team-wide metrics

## To Deploy

### Step 1: Create Migration
```bash
python manage.py makemigrations
python manage.py migrate
```

### Step 2: Create Initial Staff Performance Records (Optional)
```python
# In Django shell or management command
from dashboard.models import User, StaffPerformance
for user in User.objects.filter(created_orders__isnull=False).distinct():
    sp, created = StaffPerformance.objects.get_or_create(staff_member=user)
    sp.calculate_metrics()
```

### Step 3: Access the Dashboard
Navigate to: `/dashboard/staff-performance/`

## Key Features

✅ **Real-time Metrics**: Data calculated from actual orders
✅ **Dynamic Filtering**: Period and staff member filters
✅ **Performance Trends**: Visual 8-day trend chart
✅ **Staff Rankings**: Automatic sorting by success rate
✅ **Top Products**: Identifies best-selling products
✅ **Responsive Design**: Mobile-friendly layout
✅ **Admin Interface**: Manage performance metrics
✅ **Decimal Safe**: Uses project's decimal utilities

## Data Requirements

The dashboard requires orders with:
- `created_by`: Staff member who created the order
- `order_status` or `status`: Order status (must include 'delivered')
- `payment_status`: Payment status (requires 'paid' status)
- `total_amount`: Order total in decimal format
- `created_at`: Order creation timestamp

## Permissions

The view requires:
- `@login_required`: User must be authenticated
- `@admin_only`: User must be admin (using project decorators)

If you have custom permission requirements, modify the decorators in the view.

## Template Customization

The template includes several customizable sections:
- Gradient color schemes (CSS variables)
- Chart configuration (update `initPerformanceChart()`)
- Filter options (in dropdown selects)
- Metric badges and icons

## Notes

- Performance data is calculated at request time (not cached)
- For large datasets, consider adding pagination or caching
- Success rate = (delivered orders / total orders) * 100
- Returns calculated from `ReturnRequest` model linked to orders
- Revenue includes only orders with 'paid' payment status
