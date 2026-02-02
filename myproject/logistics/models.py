from django.db import models
from django.conf import settings


class LogisticsProvider(models.Model):
    """Stores logistics provider info like NCM"""
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)
    api_url = models.URLField()
    api_token = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'logistics_provider'
    
    def __str__(self):
        return self.name


class LogisticsOrder(models.Model):
    """Links your order to NCM order - WITHOUT importing Order model"""
    
    # Instead of ForeignKey to Order, we'll use a generic CharField to store order reference
    # You can change this later to ForeignKey when you create your Order model
    order_reference = models.CharField(max_length=100, help_text="Order ID or reference")
    
    provider = models.ForeignKey(LogisticsProvider, on_delete=models.PROTECT)
    ncm_order_id = models.CharField(max_length=100, blank=True)
    
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('CREATED', 'Created in NCM'),
        ('IN_TRANSIT', 'In Transit'),
        ('DELIVERED', 'Delivered'),
        ('RETURNED', 'Returned'),
    ]
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='PENDING')
    
    # Customer details (stored directly for now)
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    customer_address = models.TextField(blank=True)
    
    # Charges
    cod_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    last_synced = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'logistics_order'
    
    def __str__(self):
        return f"Order {self.order_reference} - NCM #{self.ncm_order_id}"


class StatusLog(models.Model):
    """Tracks all status changes"""
    logistics_order = models.ForeignKey(LogisticsOrder, on_delete=models.CASCADE, related_name='logs')
    status = models.CharField(max_length=100)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'status_log'
        ordering = ['-created_at']
