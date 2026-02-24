from django.contrib import admin
from .models import ChatThread, ChatMessage


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ('sender', 'content', 'is_read', 'created_at')


@admin.register(ChatThread)
class ChatThreadAdmin(admin.ModelAdmin):
    list_display = ('id', 'get_participants', 'is_group', 'group_name', 'created_by', 'updated_at', 'created_at')
    list_filter = ('is_group', 'created_at')
    search_fields = ('group_name',)
    inlines = [ChatMessageInline]

    def get_participants(self, obj):
        return ', '.join(p.username for p in obj.participants.all())
    get_participants.short_description = 'Participants'


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'thread', 'sender', 'short_content', 'is_read', 'created_at')
    list_filter = ('is_read', 'created_at')
    search_fields = ('content', 'sender__username')

    def short_content(self, obj):
        return obj.content[:80]
    short_content.short_description = 'Message'
