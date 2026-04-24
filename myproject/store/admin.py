from django.contrib import admin
from .models import (
    ProductReview, Cart, CartItem, Order, OrderItem, Wishlist
)


@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    list_display = ('product', 'user', 'rating', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = ('product__name', 'user__username')


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'session_key', 'created_at')
    inlines = [CartItemInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('reserved_qty', 'backordered_qty')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'user', 'order_type', 'status', 'total_price', 'created_at')
    list_filter = ('order_type', 'status', 'created_at')
    search_fields = ('order_number', 'user__username')
    inlines = [OrderItemInline]


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ('user', 'product', 'created_at')
    search_fields = ('user__username', 'product__name')


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product', 'quantity', 'price', 'reserved_qty', 'backordered_qty')
    list_filter = ('order__status',)
    search_fields = ('order__order_number', 'product__name')
    readonly_fields = ('reserved_qty', 'backordered_qty')
