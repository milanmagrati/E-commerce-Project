from django.contrib import admin
from .models import Product, Order, OrderItem, Category, Customer, Branch, StaffPerformance
from .models import ProductAttribute, ProductAttributeValue, ProductVariation, VariationAttributeValue
from .models import ReturnRequest, ReturnItem, ReturnActivityLog
from .models import BundleComponent, ProductPurchase


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'created_at']
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ['name', 'city', 'phone', 'is_active', 'created_at']
    list_filter = ['is_active', 'city']
    search_fields = ['name', 'city']

class BundleComponentInline(admin.TabularInline):
    model = BundleComponent
    fk_name = 'bundle_product'
    extra = 1
    autocomplete_fields = ['component_product']


class ProductPurchaseInline(admin.TabularInline):
    model = ProductPurchase
    extra = 0
    readonly_fields = ['purchase_date']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'product_type', 'category', 'price', 'stock', 'display_available_stock',
                    'display_average_cost', 'stock_status', 'is_active', 'created_at']
    list_filter = ['is_active', 'stock_status', 'product_type', 'category']
    search_fields = ['name', 'description']
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ['display_average_cost', 'display_available_stock']
    inlines = [BundleComponentInline, ProductPurchaseInline]

    def display_average_cost(self, obj):
        return obj.average_cost
    display_average_cost.short_description = 'Avg Cost'

    def display_available_stock(self, obj):
        return obj.available_stock
    display_available_stock.short_description = 'Available Stock'

    def get_inlines(self, request, obj=None):
        """Only show BundleComponentInline for bundle products, ProductPurchaseInline for non-bundles."""
        if obj is None:
            return [BundleComponentInline, ProductPurchaseInline]
        if obj.is_bundle:
            return [BundleComponentInline]
        return [ProductPurchaseInline]

    class Media:
        js = ('admin/js/bundle_admin.js',)

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


class ReturnItemInline(admin.TabularInline):
    model = ReturnItem
    extra = 0
    readonly_fields = ['order_item', 'product', 'product_variation', 'product_name', 'product_sku',
                        'quantity', 'price', 'total', 'return_quantity', 'good_qty', 'damaged_qty',
                        'refund_amount', 'restocked', 'restocked_at', 'restocked_by']


class ReturnActivityLogInline(admin.TabularInline):
    model = ReturnActivityLog
    extra = 0
    readonly_fields = ['user', 'action_type', 'description', 'field_name', 'old_value', 'new_value', 'created_at']


@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    list_display = ['rma_number', 'order', 'customer_name', 'return_status', 'return_reason',
                    'refund_type', 'total_amount', 'refund_amount', 'created_by', 'created_at']
    list_filter = ['return_status', 'return_reason', 'refund_type', 'condition_received', 'is_deleted', 'created_at']
    search_fields = ['rma_number', 'customer_name', 'customer_phone', 'customer_email', 'order__order_number']
    readonly_fields = ['rma_number', 'created_at', 'updated_at', 'approved_at', 'refunded_at',
                        'quality_checked_at', 'deleted_at']
    inlines = [ReturnItemInline, ReturnActivityLogInline]


@admin.register(ReturnItem)
class ReturnItemAdmin(admin.ModelAdmin):
    list_display = ['return_request', 'product_name', 'product_sku', 'return_quantity',
                    'good_qty', 'damaged_qty', 'refund_amount', 'restocked', 'created_at']
    list_filter = ['restocked', 'created_at']
    search_fields = ['product_name', 'product_sku', 'return_request__rma_number']
    readonly_fields = ['created_at']


@admin.register(ReturnActivityLog)
class ReturnActivityLogAdmin(admin.ModelAdmin):
    list_display = ['return_request', 'user', 'action_type', 'description', 'created_at']
    list_filter = ['action_type', 'created_at']
    search_fields = ['return_request__rma_number', 'description']
    readonly_fields = ['created_at']