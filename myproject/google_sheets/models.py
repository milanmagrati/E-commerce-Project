from django.db import models
from django.conf import settings
from django.utils import timezone


class GoogleSheetConnection(models.Model):
    """Stores a link to a Google Sheet with field mapping configuration."""

    SYNC_MODEL_CHOICES = [
        ('orders', 'Orders'),
        ('products', 'Products'),
        ('customers', 'Customers'),
        ('custom', 'Custom / Any Sheet'),
    ]

    CONNECTION_TYPE_CHOICES = [
        ('csv', 'Published CSV URL (Read Only)'),
        ('apps_script', 'Apps Script Web App (Two-Way Sync)'),
    ]

    name = models.CharField(max_length=255, help_text="Friendly name for this connection")
    connection_type = models.CharField(max_length=20, choices=CONNECTION_TYPE_CHOICES, default='csv')
    
    # Optional fields depending on connection type
    csv_url = models.URLField(max_length=2000, blank=True, help_text="Published CSV URL")
    apps_script_url = models.URLField(max_length=2000, blank=True, help_text="Apps Script Web App URL")
    
    spreadsheet_url = models.URLField(max_length=1000, blank=True, help_text="Link to view the sheet")
    sheet_name = models.CharField(max_length=255, default='Sheet1', help_text="Tab/sheet name")
    sync_model = models.CharField(max_length=50, choices=SYNC_MODEL_CHOICES, default='orders')
    field_mapping = models.JSONField(
        default=dict, blank=True,
        help_text='Mapping: {"A": "order_number", "B": "customer_name", ...}'
    )
    header_row = models.PositiveIntegerField(default=1, help_text="Row number containing headers")
    is_active = models.BooleanField(default=True)
    auto_sync_enabled = models.BooleanField(default=False, help_text="Enable periodic auto-sync")
    auto_sync_interval_minutes = models.PositiveIntegerField(default=60)


    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_direction = models.CharField(
        max_length=20, blank=True,
        choices=[('to_sheet', 'To Sheet'), ('from_sheet', 'From Sheet'), ('both', 'Both')],
    )
    last_sync_status = models.CharField(
        max_length=20, blank=True,
        choices=[('success', 'Success'), ('partial', 'Partial'), ('failed', 'Failed'), ('', 'Never synced')]
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sheet_connections'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Google Sheet Connection'
        verbose_name_plural = 'Google Sheet Connections'

    def __str__(self):
        return f"{self.name} ({self.sheet_name})"

    def get_last_sync_ago(self):
        if not self.last_synced_at:
            return 'Never'
        delta = timezone.now() - self.last_synced_at
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        elif seconds < 3600:
            return f"{seconds // 60}m ago"
        elif seconds < 86400:
            return f"{seconds // 3600}h ago"
        else:
            return f"{seconds // 86400}d ago"

    @property
    def spreadsheet_link(self):
        if self.spreadsheet_url:
            return self.spreadsheet_url
        if self.connection_type == 'csv' and self.csv_url:
            # Try to construct the base sheet URL from CSV URL
            import re
            match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', self.csv_url)
            if match:
                return f"https://docs.google.com/spreadsheets/d/{match.group(1)}"
        return ""


class GoogleSheetSyncLog(models.Model):
    """Records each sync operation for audit and debugging."""

    DIRECTION_CHOICES = [
        ('to_sheet', 'Django → Sheet'),
        ('from_sheet', 'Sheet → Django'),
        ('both', 'Two-Way Merge'),
    ]
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('partial', 'Partial'),
        ('failed', 'Failed'),
    ]

    connection = models.ForeignKey(
        GoogleSheetConnection, on_delete=models.CASCADE, related_name='sync_logs'
    )
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='success')
    rows_synced = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)
    rows_skipped = models.PositiveIntegerField(default=0)
    duration_seconds = models.FloatField(default=0)
    error_message = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='triggered_syncs'
    )
    synced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-synced_at']
        verbose_name = 'Sync Log'
        verbose_name_plural = 'Sync Logs'

    def __str__(self):
        return f"{self.connection.name} - {self.get_direction_display()} - {self.synced_at.strftime('%Y-%m-%d %H:%M')}"



