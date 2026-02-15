from django.contrib import admin
from .models import NCMBulkLog, NCMBulkLogOrder, NCMBulkLogDetail, WebhookLog
import json


@admin.register(WebhookLog)
class WebhookLogAdmin(admin.ModelAdmin):
    """Admin interface for webhook logs"""
    list_display = (
        'webhook_id', 'event', 'status', 'updated_orders_count', 
        'failed_orders_count', 'received_at', 'processed_at'
    )
    list_filter = ('status', 'event', 'received_at')
    search_fields = ('webhook_id', 'event')
    readonly_fields = (
        'webhook_id', 'received_at', 'processed_at', 'payload_display',
        'response_display'
    )
    fieldsets = (
        ('Webhook Info', {
            'fields': ('webhook_id', 'event', 'status', 'source_ip', 'signature')
        }),
        ('Processing', {
            'fields': ('updated_orders_count', 'failed_orders_count', 'error_message')
        }),
        ('Payload & Response', {
            'fields': ('payload_display', 'response_display'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('received_at', 'processed_at')
        }),
    )
    
    def payload_display(self, obj):
        """Display payload as formatted JSON"""
        return json.dumps(obj.payload, indent=2, ensure_ascii=False) if obj.payload else '-'
    payload_display.short_description = 'Payload'
    
    def response_display(self, obj):
        """Display response as formatted JSON"""
        return json.dumps(obj.response_data, indent=2, ensure_ascii=False) if obj.response_data else '-'
    response_display.short_description = 'Response'
    
    def has_add_permission(self, request):
        """Prevent manual webhook creation"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """Allow deletion for cleanup"""
        return True


@admin.register(NCMBulkLog)
class NCMBulkLogAdmin(admin.ModelAdmin):
    list_display = ('batch_number', 'total_orders', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('batch_number',)


# Register your models here.
