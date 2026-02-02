from django.contrib import admin
from .models import Product, Order, OrderItem, Category, Customer
from .models import ProductAttribute, ProductAttributeValue, ProductVariation, VariationAttributeValue


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'created_at']
    prepopulated_fields = {'slug': ('name',)}

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