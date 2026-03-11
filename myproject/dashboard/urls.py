
from django.urls import path
from . import views


urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Product URLs
    path('products/', views.products_view, name='products'),
    path('products/trash/', views.products_trash, name='products_trash'),
    path('products/<int:product_id>/move-to-trash/', views.product_move_to_trash, name='product_move_to_trash'),
    path('products/<int:product_id>/restore/', views.product_restore, name='product_restore'),
    path('products/<int:product_id>/permanent-delete/', views.product_permanent_delete, name='product_permanent_delete'),
    path('products/trash/bulk-action/', views.products_trash_bulk_action, name='products_trash_bulk_action'),
    path('products/trash/empty/', views.empty_trash, name='empty_trash'),    
    path('products/bulk-action/', views.products_bulk_action, name='products_bulk_action'),
    path('products/export-excel/', views.export_products_excel, name='export_products_excel'),
    path('products/add/', views.product_add, name='product_add'),
    path('products/<int:product_id>/', views.product_detail, name='product_detail'),
    path('products/<int:product_id>/edit/', views.product_edit, name='product_edit'),

    
    # Order URLs
    # Order URLs (use consolidated orders_list and order_detail below)
    
    # Customer URLs (use consolidated customers_list and customer_detail below)
    
    # API
    path('api/chart-data/', views.chart_data, name='chart_data'),
    path('api/order-sources-data/', views.order_sources_data, name='order_sources_data'),
    
    # Category URLs
    path('categories/', views.category_list, name='category_list'),
    path('categories/<int:category_id>/delete/', views.category_delete, name='category_delete'),
    path('categories/<int:category_id>/edit/', views.category_edit, name='category_edit'),
    path('add-category/', views.add_category_ajax, name='add_category_ajax'),
    
      path('products/<int:product_id>/variations/', views.product_variations, name='product_variations'),
    path('variations/<int:variation_id>/delete/', views.variation_delete, name='variation_delete'),
    
        # Product Images (NEW - Add these lines)
    path('products/<int:product_id>/upload-images/', views.upload_product_images, name='upload_product_images'),
    path('product-images/<int:image_id>/delete/', views.delete_product_image, name='delete_product_image'),
    path('product-images/<int:image_id>/set-featured/', views.set_featured_image, name='set_featured_image'),
    path('products/<int:product_id>/reorder-images/', views.reorder_product_images, name='reorder_product_images'),
    
      # Customers
    path('customers/', views.customers_list, name='customers_list'),
    path('customers/add/', views.customer_add, name='customer_add'),
    path('customers/<int:customer_id>/', views.customer_detail, name='customer_detail'),
    path('customers/<int:customer_id>/edit/', views.customer_edit, name='customer_edit'),
    path('customers/<int:customer_id>/delete/', views.customer_delete, name='customer_delete'),
    path('customers/bulk-action/', views.customers_bulk_action, name='customers_bulk_action'),

    
    # Orders
    path('orders/', views.orders_list, name='orders_list'),
    path('orders/create/', views.order_create, name='order_create'),
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
    path('orders/<int:order_id>/edit/', views.order_edit, name='order_edit'), 
    path('orders/<int:order_id>/delete/', views.order_delete, name='order_delete'),
    path('orders/<int:order_id>/invoice/', views.order_invoice, name='order_invoice'),
    path('orders/bulk-action/', views.orders_bulk_action, name='orders_bulk_action'),
    path('orders/bulk-ncm-send/', views.orders_bulk_ncm_send, name='orders_bulk_ncm_send'),
    
    path('orders/trash/', views.orders_trash, name='orders_trash'),
    path('orders/<int:order_id>/restore/', views.order_restore, name='order_restore'),
    path('orders/<int:order_id>/move-to-trash/', views.order_move_to_trash, name='order_move_to_trash'),
    path('orders/<int:order_id>/permanent-delete/', views.order_permanent_delete, name='order_permanent_delete'),
    path('orders/trash/bulk-action/', views.orders_trash_bulk_action, name='orders_trash_bulk_action'),
    path('orders/trash/empty/', views.empty_orders_trash, name='empty_orders_trash'),
    
    # Return Orders
    path('orders/returns/', views.return_orders_list, name='return_orders_list'),

    # On Hold Orders
    path('orders/on-hold/', views.on_hold_orders_list, name='on_hold_orders_list'),

    # Order Follow-Up System
    path('orders/<int:order_id>/followup/add/', views.add_order_followup, name='add_order_followup'),
    path('orders/<int:order_id>/followups/', views.get_order_followups, name='get_order_followups'),
    path('orders/<int:order_id>/next-followup/', views.update_order_next_followup, name='update_order_next_followup'),
    
    
    
    # API Endpoints
    path('api/customer/<int:customer_id>/', views.api_get_customer, name='api_get_customer'),
    path('api/search-products/', views.api_search_products, name='api_search_products'),
    path('api/product/<int:product_id>/', views.api_get_product, name='api_get_product'),
    path('api/product/<int:product_id>/variations/', views.api_get_product_variations, name='api_get_product_variations'),
    path('api/bestselling-products/', views.api_bestselling_products, name='api_bestselling_products'),
     # Export URLs
    path('orders/export/selected/', views.export_selected_orders_excel, name='export_selected_orders_excel'),
    path('orders/<int:order_id>/export/', views.export_order_details, name='export_order_details'),
    
    
       # Gallery uploads/deletes
    path('products/<int:product_id>/gallery/upload/', views.product_gallery_upload, name='product_gallery_upload'),
    path('product-image/<int:image_id>/delete/', views.delete_product_image, name='delete_product_image'),

    # Variations CRUD
    path('products/<int:product_id>/variations/create/', views.variation_create, name='variation_create'),
    path('variations/<int:variation_id>/update/', views.variation_update, name='variation_update'),
    path('variations/<int:variation_id>/delete/', views.variation_delete, name='variation_delete'),
    
    # dispatch
    path('dispatch/', views.dispatch_management, name='dispatch_management'),
     # List all dispatches
    path('dispatch/list/', views.dispatch_list, name='dispatch_list'),
    
    # View single dispatch detail
    path('dispatch/<int:pk>/', views.dispatch_detail, name='dispatch_detail'),
    
    # Move dispatch to trash (soft delete)
    path('dispatch/<int:pk>/trash/', views.dispatch_move_to_trash, name='dispatch_move_to_trash'),
    
    # Trash management
    path('dispatch/trash/', views.dispatch_trash, name='dispatch_trash'),
    path('dispatch/<int:pk>/restore/', views.dispatch_restore, name='dispatch_restore'),
    path('dispatch/<int:pk>/permanent-delete/', views.dispatch_permanent_delete, name='dispatch_permanent_delete'),
    
    # Bulk actions
    path('dispatch/bulk-action/', views.dispatch_bulk_action, name='dispatch_bulk_action'),
    path('dispatch/trash/bulk-action/', views.dispatch_trash_bulk_action, name='dispatch_trash_bulk_action'),
    path('dispatch/trash/empty/', views.empty_dispatch_trash, name='empty_dispatch_trash'),
    
    # inventory
    path('inventory-dashboard/', views.inventory_dashboard, name='inventory_dashboard'),
    # Stock In URLs
    path('inventory/stock-in/create/', views.stock_in_create, name='stock_in_create'),
    path('inventory/stock-in/<int:stock_in_id>/', views.stock_in_detail, name='stock_in_detail'),       # API for Stock In
    path('api/product/<int:product_id>/stock-in/', views.api_get_product_for_stockin, name='api_get_product_for_stockin'),

    # Low Stock Alert Settings
    path('inventory/low-stock-settings/', views.low_stock_settings, name='low_stock_settings'),
    path('inventory/low-stock-settings/save/', views.save_low_stock_thresholds, name='save_low_stock_thresholds'),
    path('inventory/low-stock-alerts/', views.low_stock_alerts, name='low_stock_alerts'),
    
    
     # City Management URLs
    path('cities/', views.city_management, name='city_management'),
    path('cities/edit/<int:city_id>/', views.city_edit, name='city_edit'),
    path('cities/delete/<int:city_id>/', views.city_delete, name='city_delete'),
    path('api/cities/quick_add/', views.city_quick_add, name='city_quick_add'),
    path('api/cities/bulk_add/', views.city_bulk_add, name='city_bulk_add'),
    path('api/cities/', views.api_get_cities, name='api_get_cities'),
    path('api/cities/get-valley-status/', views.api_get_city_valley_status, name='get_city_valley_status'),
    
    # ✅ NEW: Setup Management URLs
    path('setup/', views.setup_management, name='setup_management'),
    path('setup/add/', views.setup_add, name='setup_add'),
    path('setup/<int:setup_id>/edit/', views.setup_edit, name='setup_edit'),
    path('setup/<int:setup_id>/delete/', views.setup_delete, name='setup_delete'),
    
    
       # ==================== 🆕 RETURN MANAGEMENT ====================
    # Dashboard & List
    path('returns/', views.returns_dashboard, name='returns_dashboard'),
    path('returns/list/', views.returns_list, name='returns_list'),
    
    # Create & Detail
    path('returns/create/', views.return_create, name='return_create'),
    path('returns/bulk-create/', views.bulk_return_create, name='bulk_return_create'),
    path('returns/<int:return_id>/', views.return_detail, name='return_detail'),
    path('api/order-by-barcode/', views.api_get_order_by_barcode, name='api_get_order_by_barcode'),
    
    # Trash Management
    path('returns/<int:return_id>/trash/', views.return_trash, name='return_trash'),
    path('returns/trash/', views.returns_trash_list, name='returns_trash_list'),
    path('returns/<int:return_id>/restore/', views.return_restore, name='return_restore'),
    path('returns/<int:return_id>/permanent-delete/', views.return_permanent_delete, name='return_permanent_delete'),
    path('returns/empty-trash/', views.returns_empty_trash, name='returns_empty_trash'),
    
    # Bulk Actions
    path('returns/bulk-action/', views.returns_bulk_action, name='returns_bulk_action'),
    path('returns/batch-bulk-action/', views.returns_batch_bulk_action, name='returns_batch_bulk_action'),
    path('returns/trash/bulk-action/', views.returns_trash_bulk_action, name='returns_trash_bulk_action'),

    # API Endpoint to search customer by phone number
    path('api/search-customer-by-phone/', views.search_customer_by_phone, name='search_customer_by_phone'),

    # API Endpoint to check duplicate orders
    path('api/check-duplicate-order/', views.check_duplicate_order, name='check_duplicate_order'),

    # add custom product
    path('api/create-custom-product/', views.create_custom_product, name='create_custom_product'),

    # ==================== SALES REPORTS ====================
    path('reports/sales/', views.sales_report, name='sales_report'),
    path('reports/daily-sales/', views.daily_sales_report, name='daily_sales_report'),
    path('reports/product-sales/', views.product_sales_report, name='product_sales_report'),

      # ✅ NCM ORDERS MANAGEMENT (already exists in your code)
    path('ncm-orders/', views.ncm_orders_list, name='ncm_orders_list'),
    path('ncm-orders/<int:order_id>/', views.ncm_order_detail, name='ncm_order_detail'),
    path('ncm-orders/track/<int:order_id>/', views.ncm_track_order, name='ncm_track_order'),
    path('ncm-orders/sync-all/', views.ncm_sync_all_statuses, name='ncm_sync_all_statuses'),
    
    # ✅ NCM TRASH MANAGEMENT (NEW - ADD THESE)
    path('ncm-orders/trash/', views.ncm_orders_trash, name='ncm_orders_trash'),
    path('ncm-orders/move-to-trash/<int:order_id>/', views.ncm_order_move_to_trash, name='ncm_order_move_to_trash'),
    path('ncm-orders/restore/<int:order_id>/', views.ncm_order_restore, name='ncm_order_restore'),
    path('ncm-orders/permanent-delete/<int:order_id>/', views.ncm_order_permanent_delete, name='ncm_order_permanent_delete'),
    path('ncm-orders/bulk-trash-action/', views.ncm_orders_bulk_trash_action, name='ncm_orders_bulk_trash_action'),
    path('ncm-orders/empty-trash/', views.ncm_orders_empty_trash, name='ncm_orders_empty_trash'),
    
    # ✅ NCM API ENDPOINTS
    path('api/ncm-branches/', views.ncm_branches_json, name='ncm_branches_json'),

    # ✅ NCM BULK ORDER LOGS
    path('ncm-bulk-logs/', views.ncm_bulk_logs_list, name='ncm_bulk_logs_list'),
    path('ncm-bulk-logs/<int:log_id>/', views.ncm_bulk_log_detail, name='ncm_bulk_log_detail'),
    path('ncm-bulk-logs/<int:log_id>/trash/', views.ncm_bulk_log_trash, name='ncm_bulk_log_trash'),
    path('ncm-bulk-logs/bulk-action/', views.ncm_bulk_logs_bulk_action, name='ncm_bulk_logs_bulk_action'),

    # Financial Report
    path('financial-report/', views.financial_report, name='financial_report'),
    path('financial-report/data/', views.financial_report_data, name='financial_report_data'),
    
    # Staff Performance
    path('staff-performance/', views.staff_performance_analytics, name='staff_performance_analytics'),

    # ==================== PURCHASE MANAGEMENT ====================
    path('purchases/dashboard/', views.purchase_dashboard, name='purchase_dashboard'),
    path('purchases/create/', views.purchase_create, name='purchase_create'),
    path('purchases/<int:purchase_id>/', views.purchase_detail, name='purchase_detail'),

    # Suppliers
    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/add/', views.supplier_add, name='supplier_add'),
    path('suppliers/<int:supplier_id>/', views.supplier_detail, name='supplier_detail'),
    path('suppliers/<int:supplier_id>/edit/', views.supplier_edit, name='supplier_edit'),

    # Supplier Payments
    path('suppliers/payment/add/', views.supplier_payment_add, name='supplier_payment_add'),

    # Product Purchase History
    path('products/<int:product_id>/purchase-history/', views.product_purchase_history, name='product_purchase_history'),

    # API
    path('api/supplier/<int:supplier_id>/purchases/', views.api_supplier_purchases, name='api_supplier_purchases'),

    # ==================== STAFF TARGETS ====================
    path('targets/manage/', views.manage_targets, name='manage_targets'),
    path('targets/my-targets/', views.my_targets, name='my_targets'),
    path('targets/set/', views.set_target, name='set_target'),
    path('targets/<int:target_id>/edit/', views.edit_target, name='edit_target'),
    path('targets/<int:target_id>/delete/', views.delete_target, name='delete_target'),
    path('api/targets/<int:target_id>/', views.api_target_detail, name='api_target_detail'),
]
    