from django.contrib import admin

from .models import Resource, ResourceFile, ResourceRead, ResourceTopic


class ResourceFileInline(admin.TabularInline):
    model = ResourceFile
    extra = 0
    readonly_fields = ('file_type', 'original_name', 'file_size', 'uploaded_by', 'uploaded_at')


@admin.register(ResourceTopic)
class ResourceTopicAdmin(admin.ModelAdmin):
    list_display = ('name', 'icon', 'color', 'article_count', 'order', 'created_by')
    search_fields = ('name',)


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ('title', 'topic', 'version', 'created_by', 'file_count', 'created_at')
    search_fields = ('title', 'content', 'subtitle')
    list_filter = ('topic', 'created_at')
    inlines = [ResourceFileInline]


@admin.register(ResourceRead)
class ResourceReadAdmin(admin.ModelAdmin):
    list_display = ('resource', 'user', 'read_at')
    list_filter = ('read_at',)
