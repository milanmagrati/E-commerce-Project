from django.contrib import admin
from .models import PNDBulkLog, PNDBulkLogOrder, PNDBulkLogDetail


@admin.register(PNDBulkLog)
class PNDBulkLogAdmin(admin.ModelAdmin):
    list_display = ('batch_number', 'total_orders', 'success_count', 'failed_count', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('batch_number',)


@admin.register(PNDBulkLogOrder)
class PNDBulkLogOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'customer_name', 'pnd_order_id', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('order_number', 'customer_name', 'pnd_order_id')


@admin.register(PNDBulkLogDetail)
class PNDBulkLogDetailAdmin(admin.ModelAdmin):
    list_display = ('action', 'order_number', 'timestamp')
    list_filter = ('action',)
    search_fields = ('order_number',)
