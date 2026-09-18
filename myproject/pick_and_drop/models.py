from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid


class PNDBulkLog(models.Model):
    """Tracks each bulk send batch to Pick and Drop"""

    STATUS_CHOICES = [
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('partial', 'Partial Success'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    batch_number = models.CharField(max_length=50, unique=True, db_index=True)
    total_orders = models.IntegerField(default=0)
    success_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    skipped_count = models.IntegerField(default=0)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='processing')
    destination_branch = models.CharField(max_length=100, default='KATHMANDU VALLEY')

    # See the matching fields on ncm.NCMBulkLog for what these are for.
    selected_order_ids = models.JSONField(null=True, blank=True)
    send_options = models.JSONField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False)
    worker_heartbeat_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='pnd_bulk_logs'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Pick and Drop Bulk Log'
        verbose_name_plural = 'Pick and Drop Bulk Logs'

    def __str__(self):
        return f"{self.batch_number} ({self.total_orders} orders)"

    @staticmethod
    def generate_batch_number():
        now = timezone.now()
        short_id = uuid.uuid4().hex[:6].upper()
        return f"PND-{now.strftime('%Y%m%d')}-{short_id}"


class PNDBulkLogOrder(models.Model):
    """Individual order within a bulk send batch"""

    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
    ]

    batch = models.ForeignKey(
        PNDBulkLog,
        on_delete=models.CASCADE,
        related_name='orders'
    )
    order = models.ForeignKey(
        'dashboard.Order',
        on_delete=models.SET_NULL,
        null=True,
        related_name='pnd_bulk_log_entries'
    )
    order_number = models.CharField(max_length=50)
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    shipping_address = models.CharField(max_length=500, blank=True)
    cod_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    destination_branch = models.CharField(max_length=100, blank=True)

    pnd_order_id = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='success')
    pnd_status = models.CharField(max_length=100, blank=True, default='Order Created')
    message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.order_number} - {self.status}"


class PNDBulkLogDetail(models.Model):
    """Detailed log entries for bulk operations"""

    ACTION_CHOICES = [
        ('batch_started', 'Batch Started'),
        ('order_sent', 'Order Sent'),
        ('order_failed', 'Order Failed'),
        ('order_skipped', 'Order Skipped'),
        ('batch_completed', 'Batch Completed'),
        ('status_synced', 'Status Synced'),
        ('error', 'Error'),
    ]

    batch = models.ForeignKey(
        PNDBulkLog,
        on_delete=models.CASCADE,
        related_name='details'
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    order_number = models.CharField(max_length=50, blank=True)
    message = models.TextField(blank=True)
    response_data = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.action} - {self.order_number or self.batch.batch_number}"
