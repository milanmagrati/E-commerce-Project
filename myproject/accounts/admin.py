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
        ('Order Permissions', {
            'fields': ('can_view_orders', 'can_create_orders', 'can_edit_orders', 'can_delete_orders', 'can_cancel_orders'),
            'classes': ('collapse',)
        }),
        ('Product Permissions', {
            'fields': ('can_view_products', 'can_create_products', 'can_edit_products', 'can_delete_products'),
            'classes': ('collapse',)
        }),
        ('Other Permissions', {
            'fields': ('can_view_customers', 'can_create_customers', 'can_edit_customers', 'can_delete_customers',
                      'can_view_dispatch', 'can_manage_dispatch', 'can_scan_barcodes',
                      'can_view_inventory', 'can_manage_inventory', 'can_adjust_stock',
                      'can_view_reports', 'can_view_sales_reports', 'can_view_financial_reports', 'can_export_data',
                      'can_edit_prices', 'can_give_discounts', 'max_discount_percent',
                      'can_view_returns', 'can_create_returns', 'can_edit_returns', 'can_delete_returns',
                      'can_approve_returns', 'can_process_refunds'),
            'classes': ('collapse',)
        }),
        ('Dates', {
            'fields': ('last_login', 'date_joined', 'created_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('last_login', 'date_joined', 'created_at')
