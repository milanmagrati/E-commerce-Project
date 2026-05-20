# Redirect Orders Page - Implementation Guide

## Overview
The Redirect Orders page displays all orders that have been redirected to new customers from their original recipients. This page provides a complete view of the redirection history with old and new customer details.

## Key Features

### 1. **Professional Statistics Cards**
- **Total Redirected**: Shows the total count of redirected orders
- **This Page**: Shows count of orders on current page
- **Total Value**: Shows number of entries with detailed redirection info

### 2. **Advanced Filtering System**
- **Search**: Search by Order #, NCM ID, Customer Name, or Phone Number
- **Branch Filter**: Filter orders by delivery branch/city
- **Date Range**: Filter by redirection date (Start & End Date)
- **Per Page**: Choose pagination size (50, 100, or 200 records)
- **Reset Button**: Quickly clear all filters

### 3. **Comprehensive Data Display**
Each redirected order row shows:
- **Order #**: Order number with NCM ID reference
- **Old Customer**: Original customer details (Name & Phone)
- **Old Branch**: Original delivery branch
- **New Customer**: Redirected to customer (Name & Phone) with ✓ indicator
- **New Branch**: Redirected to branch/city
- **Redirect Date**: When and what time the redirection occurred
- **By**: Username of the person who performed the redirection
- **Amount**: Total order amount in Rs.

### 4. **Action Buttons**
- **View Details**: Opens detailed modal with complete order information
- **Open Full Order**: Opens the complete order detail page in new tab

### 5. **Detailed Order Modal**
When clicking "View Details", a comprehensive modal shows:

#### Order Overview Section
- Order number and NCM ID
- Order status and total amount
- Created and updated timestamps
- Item count

#### Original Customer Details Section
- Name, Phone, Email
- Original branch/city
- Original shipping address
- Complete original delivery information

#### Redirected To (New Customer) Section
- New customer name (highlighted in green)
- New phone and email
- New branch/city
- New shipping address

#### Redirection History Section
- Redirect action details
- User who performed redirection
- Timestamp of redirection
- Reason for redirection (if provided)

#### Products/Items Section
- Complete item list from the order
- Product names with quantities
- Unit price and total price
- Full item breakdown

#### Financial Details Section
- Discount amount
- Shipping charges
- Tax percentage
- Total order amount

### 6. **Professional Design Elements**
- **Gradient Headers**: Modern gradient background for table headers
- **Hover Effects**: Subtle animations on table rows
- **Color-Coded Badges**: Different colors for status indicators
- **Responsive Design**: Works seamlessly on all screen sizes (Desktop, Tablet, Mobile)
- **Info Boxes**: Highlighted sections for old and new customer data

## URL Routes

### Main Page
```
/orders/redirect-orders/
```
Route name: `redirect_orders_list`

### API Endpoints
```
/api/orders/<order_id>/get-redirect-details/
```
Route name: `get_redirect_order_details`

## Data Structure

### Database Queries
The view efficiently fetches:
1. All orders with `ncm_status='redirected'` and `is_deleted=False`
2. Related OrderActivityLog entries with action_type='redirected'
3. Metadata stored in activity logs containing old customer information
4. Order items prefetched to reduce database queries
5. Redirection history with user information

### Metadata Storage
Old customer information is stored in `OrderActivityLog.metadata` as a JSON object:
```json
{
    "old_customer_name": "Original Customer Name",
    "old_customer_phone": "9841234567",
    "old_customer_email": "old@email.com",
    "old_shipping_address": "Original Address",
    "old_branch_city": "TINKUNE",
    "old_landmark": "Original Landmark",
    "old_in_out": "in",
    "old_delivery_type": "Door2Door"
}
```

## Features & Functionality

### 1. Pagination
- Supports 50, 100, or 200 orders per page
- Easy navigation with first, previous, next, last buttons
- Shows current page number out of total pages

### 2. Search & Filter
- Search functionality preserves across pagination
- Filter parameters maintained when navigating pages
- Date range filtering with start and end dates
- Branch-based filtering for location-specific views

### 3. Responsive Design
- Desktop: Full table with all columns visible
- Tablet: Hides some columns (Old Branch, New Branch hidden)
- Mobile: Shows essential information with hidden columns marked as hide-sm/hide-md

### 4. Real-Time Data
- Order details fetched via AJAX modal
- Loading spinner while fetching data
- Error handling with user-friendly messages

## JavaScript Functions

### openOrderDetailModal(orderId)
- Fetches order details from API
- Populates modal with order information
- Shows loading state during fetch
- Handles errors gracefully

### populateOrderDetails(order)
- Populates all modal fields with order data
- Formats dates and currency
- Handles missing data gracefully
- Updates badges and status indicators

### formatDateTime(dateStr)
- Converts ISO datetime to user-friendly format
- Handles null/undefined values
- Uses local timezone for display

### changePerPage(value)
- Updates per_page parameter
- Submits filter form
- Reloads page with new pagination size

## Security Features

1. **Login Required**: Page requires user authentication
2. **Permission Check**: Only users with 'can_view_orders' permission
3. **User-Specific View**: Shows redirection user information
4. **Error Logging**: All errors logged for debugging

## Performance Optimization

1. **Single Database Query**: Uses single database query to fetch orders
2. **Prefetch Related**: Pre-fetches related objects (items, activity logs)
3. **Select Related**: Uses select_related for foreign key relationships
4. **AJAX Loading**: Loads order details on-demand via AJAX
5. **Pagination**: Limits data displayed per page

## Integration Points

### Navigation
- Button visible in "Possible Redirection" page header
- Accessible from main navigation menu (Orders section)
- Links to main orders list and other order management pages

### Data Sources
- **OrderActivityLog**: For redirection history and metadata
- **Order**: For current order details
- **OrderItem**: For product information
- **Customer**: For customer information (if linked)

## Usage Workflow

1. Navigate to "Possible Redirection" page
2. Click the "Redirect Orders" button (green gradient button)
3. View all redirected orders with old and new customer details
4. Use search/filter to find specific orders
5. Click "View Details" to see complete redirection history
6. Click "Open Full Order" to manage the order

## Customization Options

### Styling
- Colors: Gradient from #667eea to #764ba2 for headers
- Success Green: #11998e to #38ef7d for redirect indicators
- Hover Effects: Translatey(-2px) with shadow
- Border Radius: 8px for modern look

### Field Display
- Customize which columns are hidden on mobile/tablet
- Add/remove fields in modal sections
- Adjust badge colors and styles
- Modify table layout

## Troubleshooting

### Modal Not Loading
- Check browser console for JavaScript errors
- Verify API endpoint is accessible
- Check user permissions

### Filter Not Working
- Clear browser cache
- Verify date format (YYYY-MM-DD)
- Check filter parameter values

### Performance Issues
- Reduce per_page value if loading slowly
- Check database indexes on ncm_status field
- Verify OrderActivityLog has proper indexing

## Future Enhancements

1. **Bulk Actions**: Revert redirections for multiple orders
2. **Export**: Export redirect report to CSV/Excel
3. **Analytics**: Charts showing redirection trends
4. **Notifications**: Alert system for redirections
5. **Auto-Redirect**: Automatic redirection suggestions
6. **Redirection Rules**: Set up rules for automatic redirections

## Related Pages

- [Possible Redirection](/orders/possible-redirection/)
- [All Orders](/orders/)
- [NCM RTVs](/orders/rtvs/)
- [Return Orders](/orders/returns/)

## Support & Maintenance

For issues or questions about the Redirect Orders feature, contact the development team with:
- Page URL and filters applied
- Browser console errors (if any)
- Time of issue occurrence
- Expected vs actual behavior
