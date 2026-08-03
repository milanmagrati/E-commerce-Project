from django.contrib import admin

from .models import AuditEvent, DeviceSession, KnownDevice, SecurityAlert, VaultSettings


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor_username', 'event_type', 'severity',
                    'risk_score', 'module', 'action')
    list_filter = ('event_type', 'severity', 'module', 'device_kind', 'created_at')
    search_fields = ('actor_username', 'action', 'object_label', 'path', 'ip_address')
    date_hierarchy = 'created_at'
    readonly_fields = [f.name for f in AuditEvent._meta.fields]

    # The trail is append-only. Leaving delete enabled would let anyone with admin
    # access quietly remove the single row that records what they did — retention
    # is enforced in bulk by `manage.py sentinel_prune`, which is auditable and
    # scheduled, not by hand-picking rows here.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DeviceSession)
class DeviceSessionAdmin(admin.ModelAdmin):
    list_display = ('username_snapshot', 'ip_address', 'browser', 'operating_system',
                    'device_kind', 'started_at', 'is_active')
    list_filter = ('is_active', 'device_kind', 'end_reason', 'started_at')
    search_fields = ('username_snapshot', 'ip_address', 'user_agent')
    date_hierarchy = 'started_at'


@admin.register(KnownDevice)
class KnownDeviceAdmin(admin.ModelAdmin):
    list_display = ('user', 'label', 'device_kind', 'login_count',
                    'is_trusted', 'is_blocked', 'last_seen')
    list_filter = ('is_trusted', 'is_blocked', 'device_kind')
    search_fields = ('user__username', 'label', 'fingerprint')


@admin.register(SecurityAlert)
class SecurityAlertAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'kind', 'severity', 'status',
                    'subject_username', 'occurrence_count')
    list_filter = ('kind', 'severity', 'status', 'created_at')
    search_fields = ('subject_username', 'title', 'detail', 'ip_address')


@admin.register(VaultSettings)
class VaultSettingsAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'retention_days', 'capture_page_views', 'updated_at')

    def has_add_permission(self, request):
        return not VaultSettings.objects.exists()
