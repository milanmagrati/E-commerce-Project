from django.contrib import admin
from .models import Product, Order, OrderItem, Category, Customer, Branch, StaffPerformance
from .models import ProductAttribute, ProductAttributeValue, ProductVariation, VariationAttributeValue


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'created_at']
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ['name', 'city', 'phone', 'is_active', 'created_at']
    list_filter = ['is_active', 'city']
    search_fields = ['name', 'city']

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'stock', 'stock_status', 'is_active', 'created_at']
    list_filter = ['is_active', 'stock_status', 'category']
    search_fields = ['name', 'description']
    prepopulated_fields = {'slug': ('name',)}

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'customer_name', 'total_amount', 'order_status', 'payment_status', 'created_at', 'ncm_order_id', 'delivery_charge']
    list_filter = ['order_status', 'payment_status', 'created_at', 'ncm_order_id']
    search_fields = ['order_number', 'customer_name', 'customer_email', 'ncm_order_id']
    inlines = [OrderItemInline]
    actions = ['fetch_delivery_charges']
    readonly_fields = ['ncm_order_id', 'ncm_status', 'delivery_charge']

    def fetch_delivery_charges(self, request, queryset):
        """Admin action to fetch delivery charges from NCM API for selected orders"""
        from services.ncm_service import NCMService
        from decimal import Decimal
        import logging
        
        logger = logging.getLogger(__name__)
        ncm_service = NCMService()
        
        updated = 0
        failed = 0
        
        for order in queryset.filter(ncm_order_id__isnull=False):
            try:
                details_result = ncm_service.get_order_details(order.ncm_order_id)
                
                if details_result.get('success'):
                    details_data = details_result.get('data', {})
                    
                    # Try multiple possible field names for delivery charge
                    delivery_charge = (
                        details_data.get('chargeDetail') or 
                        details_data.get('deliveryCharge') or 
                        details_data.get('deliverycharge') or 
                        details_data.get('delivery_charge') or 
                        details_data.get('chargedetail') or 
                        details_data.get('shippingCharge') or 
                        details_data.get('shipping_charge') or 
                        details_data.get('charge') or 
                        details_data.get('amount') or 
                        0
                    )
                    
                    if delivery_charge and float(delivery_charge) > 0:
                        order.delivery_charge = Decimal(str(delivery_charge))
                        order.save(update_fields=['delivery_charge'])
                        updated += 1
                        logger.info(f"✅ Updated delivery charge for {order.order_number}: Rs. {delivery_charge}")
                else:
                    failed += 1
                    logger.error(f"Failed to fetch details for {order.order_number}: {details_result.get('error')}")
            except Exception as e:
                failed += 1
                logger.error(f"Error fetching delivery charge for {order.order_number}: {str(e)}")
        
        self.message_user(request, f'✅ Updated {updated} orders with delivery charges. Failed: {failed}')
    
    fetch_delivery_charges.short_description = "Fetch delivery charges from NCM API"

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'phone', 'created_at']
    search_fields = ['name', 'email', 'phone']

@admin.register(ProductAttribute)
class ProductAttributeAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']

@admin.register(ProductAttributeValue)
class ProductAttributeValueAdmin(admin.ModelAdmin):
    list_display = ['attribute', 'value']
    list_filter = ['attribute']

@admin.register(ProductVariation)
class ProductVariationAdmin(admin.ModelAdmin):
    list_display = ['sku', 'product', 'price', 'stock']
    list_filter = ['product']
    search_fields = ['sku', 'product__name']


@admin.register(StaffPerformance)
class StaffPerformanceAdmin(admin.ModelAdmin):
    list_display = ['staff_member', 'total_orders', 'successful_orders', 'success_rate', 'total_revenue', 'return_count']
    list_filter = ['success_rate', 'created_at']
    search_fields = ['staff_member__username', 'staff_member__first_name', 'staff_member__last_name']
    readonly_fields = ['total_orders', 'successful_orders', 'success_rate', 'total_revenue', 'return_count', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Staff Member', {
            'fields': ('staff_member',)
        }),
        ('Performance Metrics', {
            'fields': ('total_orders', 'successful_orders', 'return_count', 'success_rate', 'total_revenue')
        }),
        ('Period', {
            'fields': ('period_start', 'period_end')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )