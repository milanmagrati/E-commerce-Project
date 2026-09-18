from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid


class NCMBulkLog(models.Model):
    """Tracks each bulk send batch to NCM"""

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
    from_branch = models.CharField(max_length=100, default='TINKUNE')
    delivery_type = models.CharField(max_length=20, default='Door2Door')

    # The orders this batch was asked to send, captured before the first API
    # call. Without it a batch whose worker died knows only how MANY orders it
    # owed (total_orders) - the child rows below are written after each send, so
    # everything not yet reached is unrecoverable. See dashboard/bulk_batch.py.
    selected_order_ids = models.JSONField(null=True, blank=True)

    # The send parameters that only ever lived in the POST body: which API
    # account to use, the fallback package weight, whether to stamp
    # order.logistics. A resume has to reproduce them or it would send the rest
    # of the batch on the default account, at the default weight.
    send_options = models.JSONField(null=True, blank=True)

    # Cooperative cancellation. The send loop reads this between orders and
    # stops; there is no way to kill the worker outright.
    cancel_requested = models.BooleanField(default=False)

    # Last sign of life from whoever is processing this batch. A 'processing'
    # batch with a stale (or null) heartbeat is stalled, not running.
    worker_heartbeat_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ncm_bulk_logs'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'NCM Bulk Log'
        verbose_name_plural = 'NCM Bulk Logs'

    def __str__(self):
        return f"{self.batch_number} ({self.total_orders} orders)"

    @staticmethod
    def generate_batch_number():
        now = timezone.now()
        short_id = uuid.uuid4().hex[:6].upper()
        return f"NCM-{now.strftime('%Y%m%d')}-{short_id}"


class NCMBulkLogOrder(models.Model):
    """Individual order within a bulk send batch"""

    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
    ]

    batch = models.ForeignKey(
        NCMBulkLog,
        on_delete=models.CASCADE,
        related_name='orders'
    )
    order = models.ForeignKey(
        'dashboard.Order',
        on_delete=models.SET_NULL,
        null=True,
        related_name='ncm_bulk_log_entries'
    )
    order_number = models.CharField(max_length=50)
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    shipping_address = models.CharField(max_length=500, blank=True)
    cod_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    destination_branch = models.CharField(max_length=100, blank=True)

    ncm_order_id = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='success')
    ncm_status = models.CharField(max_length=100, blank=True, default='Pickup Order Created')
    message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.order_number} - {self.status}"


class NCMBulkLogDetail(models.Model):
    """Detailed log entries for bulk operations (API responses, errors, etc.)"""

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
        NCMBulkLog,
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


class WebhookLog(models.Model):
    """Track webhook events for debugging and idempotency"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    webhook_id = models.CharField(max_length=100, unique=True, db_index=True)  # External webhook ID
    event = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Webhook payload and response
    payload = models.JSONField()
    response_data = models.JSONField(null=True, blank=True)
    
    # Update counts
    updated_orders_count = models.IntegerField(default=0)
    failed_orders_count = models.IntegerField(default=0)
    
    # Error tracking
    error_message = models.TextField(blank=True)
    
    # IP and identification
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    signature = models.CharField(max_length=255, blank=True)  # Webhook signature verification
    
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-received_at']
        verbose_name = 'Webhook Log'
        verbose_name_plural = 'Webhook Logs'
    
    def __str__(self):
        return f"{self.webhook_id} - {self.status}"
