from django.contrib import admin
from .models import WooCommerceOrder


@admin.register(WooCommerceOrder)
class WooCommerceOrderAdmin(admin.ModelAdmin):
    list_display = ('woo_order_id', 'status', 'customer_name', 'total', 'currency', 'sync_source', 'created_at', 'updated_at')
    list_filter = ('status', 'currency', 'sync_source')
    search_fields = ('=woo_order_id', 'customer_name', 'customer_email', 'billing_phone')
    readonly_fields = ('raw_payload', 'created_at', 'updated_at')
    ordering = ('-created_at',)
