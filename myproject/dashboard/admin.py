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
    list_display = ['order_number', 'customer_name', 'total_amount', 'order_status', 'payment_status', 'created_at']
    list_filter = ['order_status', 'payment_status', 'created_at']
    search_fields = ['order_number', 'customer_name', 'customer_email']
    inlines = [OrderItemInline]

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