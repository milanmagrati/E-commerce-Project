from django.contrib import admin
from .models import (
    ProductReview, Cart, CartItem, Order, OrderItem, Wishlist, DiscountCode,
    StoreCustomer, DeliveryCharge, DeliverySetting,
    ProductPageTheme, ProductThemeOverride, ReviewVote,
)


@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    list_display = ('product', 'display_name', 'rating', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = ('product__name', 'guest_name', 'user__username')


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
    list_display = ('order_number', 'full_name', 'phone', 'district', 'courier_branch',
                    'order_type', 'status', 'total_price', 'created_at')
    list_filter = ('order_type', 'status', 'district', 'created_at')
    search_fields = ('order_number', 'full_name', 'phone', 'email')
    inlines = [OrderItemInline]


@admin.register(DiscountCode)
class DiscountCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'discount_type', 'value', 'min_order_amount',
                    'free_delivery', 'is_active', 'used_count', 'usage_limit', 'valid_to')
    list_filter = ('discount_type', 'is_active', 'free_delivery')
    search_fields = ('code', 'description')
    readonly_fields = ('used_count',)


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ('product', 'user', 'session_key', 'created_at')
    search_fields = ('user__username', 'product__name')


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product', 'quantity', 'price', 'reserved_qty', 'backordered_qty')
    list_filter = ('order__status',)
    search_fields = ('order__order_number', 'product__name')
    readonly_fields = ('reserved_qty', 'backordered_qty')


@admin.register(StoreCustomer)
class StoreCustomerAdmin(admin.ModelAdmin):
    """Read-mostly view of storefront shoppers. These are NOT staff users —
    they live in their own table and have no dashboard access at all."""
    list_display = ('full_name', 'email', 'phone', 'district', 'is_active', 'created_at', 'last_login')
    list_filter = ('is_active', 'created_at')
    search_fields = ('full_name', 'email', 'phone')
    readonly_fields = ('password', 'created_at', 'last_login')
    ordering = ('-created_at',)


@admin.register(DeliveryCharge)
class DeliveryChargeAdmin(admin.ModelAdmin):
    """Fallback editor. The day-to-day surface is Setup -> Delivery Charge Setup
    in the dashboard, which also busts the storefront's delivery cache."""
    list_display = ('district', 'scope_label', 'charge', 'free_above', 'delivery_time', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('district', 'branch_name', 'branch_code', 'covered_areas')


@admin.register(DeliverySetting)
class DeliverySettingAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'inside_valley_charge', 'default_charge',
                    'free_delivery_threshold', 'updated_at')


# The product page theme is edited at Setup → Product Page Theme; these are
# here only so the rows are inspectable when something needs checking.

@admin.register(ProductPageTheme)
class ProductPageThemeAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'buy_action', 'ship_fee', 'updated_at')


@admin.register(ProductThemeOverride)
class ProductThemeOverrideAdmin(admin.ModelAdmin):
    list_display = ('product', 'layout', 'updated_at')
    search_fields = ('product__name',)


@admin.register(ReviewVote)
class ReviewVoteAdmin(admin.ModelAdmin):
    list_display = ('review', 'value', 'customer', 'created_at')
    list_filter = ('value',)
