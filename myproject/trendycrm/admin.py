from django.contrib import admin
from .models import CommentAutomation


@admin.register(CommentAutomation)
class CommentAutomationAdmin(admin.ModelAdmin):
    list_display = ['name', 'integration', 'match_type', 'trigger_keyword', 'is_active', 'trigger_count', 'last_triggered_at']
    list_filter = ['is_active', 'match_type', 'integration']
    search_fields = ['name', 'trigger_keyword', 'public_reply', 'dm_message']
    readonly_fields = ['trigger_count', 'last_triggered_at', 'created_at', 'updated_at']
    list_editable = ['is_active']
    ordering = ['-created_at']
