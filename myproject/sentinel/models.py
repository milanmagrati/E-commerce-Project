"""
Sentinel Vault — audit trail, session forensics and access intelligence.

Three concerns, three tables:
  * DeviceSession — one row per authenticated browser/device session (who, where, what hardware, how long).
  * AuditEvent    — one row per meaningful thing that happened (login, create, edit, delete, export, denial...).
  * SecurityAlert — one row per anomaly worth a human's attention, with an acknowledge workflow.

Plus KnownDevice (per-user device memory, so "first seen on a new machine" is answerable)
and VaultSettings (a singleton control panel: retention, capture depth, alert thresholds).

Everything an actor did survives that actor being deleted: usernames/roles/labels are
snapshotted onto the row at write time, and FKs are SET_NULL. An audit trail that
disappears when you delete the suspect is not an audit trail.
"""

from datetime import time as datetime_time

from django.conf import settings
from django.db import models
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# Vocabulary
# ─────────────────────────────────────────────────────────────────────────────

class EventType(models.TextChoices):
    LOGIN = 'login', 'Sign In'
    LOGIN_FAILED = 'login_failed', 'Failed Sign In'
    LOGOUT = 'logout', 'Sign Out'
    SESSION_KILLED = 'session_killed', 'Session Revoked'
    CREATE = 'create', 'Created'
    UPDATE = 'update', 'Updated'
    DELETE = 'delete', 'Deleted'
    VIEW = 'view', 'Viewed'
    EXPORT = 'export', 'Exported'
    IMPORT = 'import', 'Imported'
    DENIED = 'denied', 'Access Denied'
    PASSWORD = 'password', 'Credential Change'
    PERMISSION = 'permission', 'Permission Change'
    JOB = 'job', 'Background Job'
    INTEGRATION = 'integration', 'Integration Call'
    ERROR = 'error', 'System Error'
    SYSTEM = 'system', 'System Event'


class Severity(models.TextChoices):
    INFO = 'info', 'Info'
    NOTICE = 'notice', 'Notice'
    WARNING = 'warning', 'Warning'
    CRITICAL = 'critical', 'Critical'


class DeviceKind(models.TextChoices):
    DESKTOP = 'desktop', 'Desktop'
    MOBILE = 'mobile', 'Mobile'
    TABLET = 'tablet', 'Tablet'
    BOT = 'bot', 'Bot / Crawler'
    API = 'api', 'API Client'
    UNKNOWN = 'unknown', 'Unknown'


# Presentation metadata kept next to the vocabulary it describes, so a new event
# type can never render as an unstyled grey blob in the UI.
EVENT_STYLE = {
    EventType.LOGIN: ('fa-right-to-bracket', 'emerald'),
    EventType.LOGIN_FAILED: ('fa-triangle-exclamation', 'amber'),
    EventType.LOGOUT: ('fa-right-from-bracket', 'slate'),
    EventType.SESSION_KILLED: ('fa-plug-circle-xmark', 'rose'),
    EventType.CREATE: ('fa-circle-plus', 'emerald'),
    EventType.UPDATE: ('fa-pen-to-square', 'sky'),
    EventType.DELETE: ('fa-trash-can', 'rose'),
    EventType.VIEW: ('fa-eye', 'slate'),
    EventType.EXPORT: ('fa-file-arrow-down', 'violet'),
    EventType.IMPORT: ('fa-file-arrow-up', 'violet'),
    EventType.DENIED: ('fa-ban', 'amber'),
    EventType.PASSWORD: ('fa-key', 'amber'),
    EventType.PERMISSION: ('fa-user-shield', 'violet'),
    EventType.JOB: ('fa-gears', 'cyan'),
    EventType.INTEGRATION: ('fa-tower-broadcast', 'cyan'),
    EventType.ERROR: ('fa-bug', 'rose'),
    EventType.SYSTEM: ('fa-server', 'slate'),
}


# ─────────────────────────────────────────────────────────────────────────────
# Settings singleton
# ─────────────────────────────────────────────────────────────────────────────

class VaultSettings(models.Model):
    """Runtime control panel — editable from the UI, no redeploy needed to dial capture up or down."""

    capture_page_views = models.BooleanField(
        default=True, help_text='Record read-only page visits (GET). Turn off to log writes only.')
    capture_field_diffs = models.BooleanField(
        default=True, help_text='Record before/after values on every tracked model change.')
    capture_anonymous = models.BooleanField(
        default=False, help_text='Record activity from signed-out visitors (storefront traffic).')

    retention_days = models.PositiveIntegerField(
        default=180, help_text='Events older than this are removed by the sentinel_prune command.')
    page_view_retention_days = models.PositiveIntegerField(
        default=30, help_text='Low-value page-view rows expire sooner than write events.')

    # Anomaly thresholds
    failed_login_threshold = models.PositiveIntegerField(
        default=5, help_text='Failed sign-ins from one IP within the window before an alert is raised.')
    failed_login_window_minutes = models.PositiveIntegerField(default=15)
    bulk_delete_threshold = models.PositiveIntegerField(
        default=10, help_text='Deletions by one user within an hour before an alert is raised.')
    denial_threshold = models.PositiveIntegerField(
        default=8, help_text='Permission denials by one user within an hour before an alert is raised.')

    # Real `time` objects, not '07:00' strings. A string default survives a DB
    # round-trip (Django parses it on load) but NOT on an unsaved instance — and
    # get_settings() falls back to an unsaved VaultSettings() whenever the table
    # is unreachable. Comparing str to time then raises TypeError inside the risk
    # scorer, which is swallowed by log_event's guard and silently drops the event.
    office_hours_start = models.TimeField(
        default=datetime_time(7, 0),
        help_text='Activity outside this window is flagged as off-hours (Nepal time).')
    office_hours_end = models.TimeField(default=datetime_time(21, 0))
    alert_on_off_hours = models.BooleanField(default=True)
    alert_on_new_device = models.BooleanField(default=True)

    # Defaults exclude this project's background pollers. base.html refreshes the
    # unread-chat count, the global notice and the incomplete-attendance badge on a
    # timer from *every* open tab — without these lines each idle browser would
    # write a row a minute, forever, and bury the actions a human actually took.
    excluded_paths = models.TextField(
        default=(
            '/static/\n/media/\n/iclock/\n/favicon.ico\n'
            '/chat/api/unread-count/\n'
            '/api/active-notice/\n'
            '/hrm/attendance/incomplete/?count_only=1'
        ),
        blank=True,
        help_text=('One URL prefix per line. Requests starting with these are never recorded. '
                   'Include a ?query fragment to match only that variant of a path.'),
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sentinel_settings_updates')

    class Meta:
        verbose_name = 'Vault Settings'
        verbose_name_plural = 'Vault Settings'

    def __str__(self):
        return 'Sentinel Vault Settings'

    @classmethod
    def load(cls):
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj

    @property
    def excluded_prefixes(self):
        return [p.strip() for p in (self.excluded_paths or '').splitlines() if p.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# Sessions & devices
# ─────────────────────────────────────────────────────────────────────────────

class KnownDevice(models.Model):
    """A device fingerprint this user has signed in from before.

    Existence of a row is what makes "signed in from an unrecognised device"
    a question we can answer; `is_trusted` lets an admin silence a device
    they've confirmed is legitimate.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sentinel_devices')
    fingerprint = models.CharField(max_length=64, db_index=True)

    label = models.CharField(max_length=160, blank=True, default='')
    browser = models.CharField(max_length=60, blank=True, default='')
    operating_system = models.CharField(max_length=60, blank=True, default='')
    device_kind = models.CharField(max_length=12, choices=DeviceKind.choices, default=DeviceKind.UNKNOWN)

    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    login_count = models.PositiveIntegerField(default=0)

    is_trusted = models.BooleanField(default=False)
    is_blocked = models.BooleanField(default=False, help_text='Sign-ins from this device raise a critical alert.')

    class Meta:
        unique_together = ('user', 'fingerprint')
        ordering = ['-last_seen']
        indexes = [models.Index(fields=['user', '-last_seen'])]

    def __str__(self):
        return f'{self.label or self.fingerprint[:12]} — {self.user_id}'


class DeviceSession(models.Model):
    """One authenticated session: the device, the network, and its whole lifetime."""

    class EndReason(models.TextChoices):
        ACTIVE = 'active', 'Active'
        LOGOUT = 'logout', 'Signed Out'
        EXPIRED = 'expired', 'Expired'
        REVOKED = 'revoked', 'Revoked by Admin'
        REPLACED = 'replaced', 'Replaced by New Session'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='sentinel_sessions')
    username_snapshot = models.CharField(max_length=150, db_index=True)
    role_snapshot = models.CharField(max_length=50, blank=True, default='')

    session_key = models.CharField(max_length=64, blank=True, default='', db_index=True)
    device = models.ForeignKey(KnownDevice, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='sessions')
    fingerprint = models.CharField(max_length=64, blank=True, default='', db_index=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    forwarded_chain = models.CharField(max_length=255, blank=True, default='')
    network_label = models.CharField(max_length=80, blank=True, default='',
                                     help_text='Private LAN / loopback / public, derived from the IP.')

    user_agent = models.TextField(blank=True, default='')
    browser = models.CharField(max_length=60, blank=True, default='')
    browser_version = models.CharField(max_length=30, blank=True, default='')
    operating_system = models.CharField(max_length=60, blank=True, default='')
    os_version = models.CharField(max_length=30, blank=True, default='')
    device_kind = models.CharField(max_length=12, choices=DeviceKind.choices, default=DeviceKind.UNKNOWN)
    device_brand = models.CharField(max_length=40, blank=True, default='')
    screen = models.CharField(max_length=24, blank=True, default='', help_text='Reported by the browser, e.g. 1920x1080.')
    timezone_name = models.CharField(max_length=64, blank=True, default='')
    language = models.CharField(max_length=32, blank=True, default='')

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_activity = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=12, choices=EndReason.choices, default=EndReason.ACTIVE)

    is_active = models.BooleanField(default=True, db_index=True)
    is_new_device = models.BooleanField(default=False)
    request_count = models.PositiveIntegerField(default=0)
    write_count = models.PositiveIntegerField(default=0)
    risk_score = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['-last_activity']
        indexes = [
            models.Index(fields=['is_active', '-last_activity']),
            models.Index(fields=['user', '-started_at']),
        ]

    def __str__(self):
        return f'{self.username_snapshot} @ {self.ip_address or "?"} ({self.started_at:%Y-%m-%d %H:%M})'

    @property
    def duration(self):
        return (self.ended_at or timezone.now()) - self.started_at

    @property
    def duration_display(self):
        total = int(self.duration.total_seconds())
        if total < 60:
            return f'{total}s'
        if total < 3600:
            return f'{total // 60}m {total % 60}s'
        hours, rem = divmod(total, 3600)
        if hours < 24:
            return f'{hours}h {rem // 60}m'
        return f'{hours // 24}d {hours % 24}h'

    @property
    def is_idle(self):
        """Still flagged active, but nothing has happened for 15 minutes."""
        return self.is_active and (timezone.now() - self.last_activity).total_seconds() > 900

    @property
    def device_display(self):
        parts = [p for p in (self.browser, self.operating_system) if p]
        return ' on '.join(parts) or 'Unknown device'


# ─────────────────────────────────────────────────────────────────────────────
# The audit trail itself
# ─────────────────────────────────────────────────────────────────────────────

class AuditEventQuerySet(models.QuerySet):
    def writes(self):
        return self.exclude(event_type__in=[EventType.VIEW, EventType.LOGIN, EventType.LOGOUT])

    def since(self, when):
        return self.filter(created_at__gte=when)

    def for_actor(self, user):
        return self.filter(actor=user)


class AuditEvent(models.Model):
    """A single recorded action. Append-only by convention — nothing in the UI edits these."""

    # Who
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sentinel_events')
    actor_username = models.CharField(max_length=150, blank=True, default='anonymous', db_index=True)
    actor_role = models.CharField(max_length=50, blank=True, default='')
    session = models.ForeignKey(DeviceSession, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='events')

    # What
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.SYSTEM, db_index=True)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.INFO, db_index=True)
    module = models.CharField(max_length=50, blank=True, default='', db_index=True,
                              help_text='Owning app/area, e.g. dashboard, hrm, ncm.')
    action = models.CharField(max_length=255, help_text='Human-readable summary shown in the stream.')

    # On what
    object_type = models.CharField(max_length=100, blank=True, default='', db_index=True,
                                   help_text='app_label.ModelName')
    object_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    object_label = models.CharField(max_length=255, blank=True, default='')
    changes = models.JSONField(default=dict, blank=True,
                               help_text='{field: {"old": ..., "new": ...}} for updates.')

    # Where from
    path = models.CharField(max_length=512, blank=True, default='')
    method = models.CharField(max_length=10, blank=True, default='')
    view_name = models.CharField(max_length=160, blank=True, default='')
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    user_agent = models.TextField(blank=True, default='')
    device_kind = models.CharField(max_length=12, choices=DeviceKind.choices, default=DeviceKind.UNKNOWN)
    browser = models.CharField(max_length=60, blank=True, default='')
    operating_system = models.CharField(max_length=60, blank=True, default='')

    # Judgement
    risk_score = models.PositiveSmallIntegerField(default=0, db_index=True,
                                                  help_text='0–100. Drives the risk column and alerting.')
    risk_flags = models.JSONField(default=list, blank=True)
    context = models.JSONField(default=dict, blank=True, help_text='Free-form extras from the caller.')

    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['-created_at', 'event_type']),
            models.Index(fields=['actor', '-created_at']),
            models.Index(fields=['object_type', 'object_id']),
            models.Index(fields=['module', '-created_at']),
            models.Index(fields=['-risk_score', '-created_at']),
        ]
        verbose_name = 'Audit Event'
        verbose_name_plural = 'Audit Events'

    def __str__(self):
        return f'[{self.created_at:%Y-%m-%d %H:%M}] {self.actor_username}: {self.action}'

    @property
    def icon(self):
        return EVENT_STYLE.get(self.event_type, ('fa-circle-dot', 'slate'))[0]

    @property
    def tone(self):
        return EVENT_STYLE.get(self.event_type, ('fa-circle-dot', 'slate'))[1]

    @property
    def risk_band(self):
        if self.risk_score >= 70:
            return 'critical'
        if self.risk_score >= 40:
            return 'elevated'
        if self.risk_score >= 20:
            return 'low'
        return 'minimal'

    @property
    def change_count(self):
        return len(self.changes) if isinstance(self.changes, dict) else 0


# ─────────────────────────────────────────────────────────────────────────────
# Alerts
# ─────────────────────────────────────────────────────────────────────────────

class SecurityAlert(models.Model):
    """An anomaly a human should look at, with an explicit acknowledge/resolve trail."""

    class Kind(models.TextChoices):
        NEW_DEVICE = 'new_device', 'Unrecognised Device'
        BRUTE_FORCE = 'brute_force', 'Repeated Failed Sign-ins'
        OFF_HOURS = 'off_hours', 'Off-hours Access'
        BULK_DELETE = 'bulk_delete', 'Bulk Deletion'
        DENIAL_STORM = 'denial_storm', 'Repeated Access Denials'
        PERMISSION_CHANGE = 'permission_change', 'Permission Escalation'
        CONCURRENT = 'concurrent', 'Concurrent Sessions'
        BLOCKED_DEVICE = 'blocked_device', 'Sign-in from Blocked Device'
        DATA_EXPORT = 'data_export', 'Large Data Export'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        ACKNOWLEDGED = 'ack', 'Acknowledged'
        RESOLVED = 'resolved', 'Resolved'
        DISMISSED = 'dismissed', 'Dismissed'

    kind = models.CharField(max_length=24, choices=Kind.choices, db_index=True)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.WARNING, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)

    subject_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sentinel_alerts')
    subject_username = models.CharField(max_length=150, blank=True, default='')
    session = models.ForeignKey(DeviceSession, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='alerts')
    event = models.ForeignKey(AuditEvent, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='alerts')

    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True, default='')
    evidence = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    # Repeats of the same condition fold into one row instead of flooding the list.
    occurrence_count = models.PositiveIntegerField(default=1)
    dedupe_key = models.CharField(max_length=120, blank=True, default='', db_index=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sentinel_resolved_alerts')
    resolution_note = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-last_seen_at']
        indexes = [
            models.Index(fields=['status', '-last_seen_at']),
            models.Index(fields=['kind', '-created_at']),
        ]
        verbose_name = 'Security Alert'
        verbose_name_plural = 'Security Alerts'

    def __str__(self):
        return f'{self.get_kind_display()} — {self.subject_username or "system"}'

    @property
    def is_open(self):
        return self.status in (self.Status.OPEN, self.Status.ACKNOWLEDGED)

    @property
    def tone(self):
        return {
            Severity.CRITICAL: 'rose',
            Severity.WARNING: 'amber',
            Severity.NOTICE: 'sky',
            Severity.INFO: 'slate',
        }.get(self.severity, 'slate')
