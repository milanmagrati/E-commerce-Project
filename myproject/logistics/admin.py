from django.contrib import admin
from .models import LogisticsProvider, LogisticsOrder, StatusLog


@admin.register(LogisticsProvider)
class LogisticsProviderAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active', 'created_at']
    list_filter = ['is_active']


@admin.register(LogisticsOrder)
class LogisticsOrderAdmin(admin.ModelAdmin):
    list_display = ['order_reference', 'ncm_order_id', 'status', 'cod_amount', 'last_synced', 'created_at']  # Changed 'order' to 'order_reference'
    list_filter = ['status', 'provider', 'created_at']
    search_fields = ['order_reference', 'ncm_order_id']  # Changed here too
    readonly_fields = ['created_at', 'updated_at', 'last_synced']
    
    actions = ['sync_status_action']
    
    def sync_status_action(self, request, queryset):
        from .ncm_service import NCMService
        service = NCMService()
        
        count = 0
        for logistics_order in queryset:
            try:
                service.sync_status(logistics_order)
                count += 1
            except Exception as e:
                self.message_user(request, f"Error: {str(e)}", level='ERROR')
        
        self.message_user(request, f"Synced {count} orders successfully")
    
    sync_status_action.short_description = "🔄 Sync status from NCM"


@admin.register(StatusLog)
class StatusLogAdmin(admin.ModelAdmin):
    list_display = ['logistics_order', 'status', 'message', 'created_at']
    list_filter = ['status', 'created_at']
    readonly_fields = ['created_at']
    search_fields = ['logistics_order__ncm_order_id', 'message']
