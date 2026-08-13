from django.db import models


class WooCommerceOrder(models.Model):
    """
    Stores WooCommerce orders received via API.
    Maps to the existing store.Order model via woo_order_id as the external reference.
    """
    woo_order_id = models.PositiveBigIntegerField(unique=True, db_index=True)
    order = models.OneToOneField(
        'store.Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='woo_link',
    )
    status = models.CharField(max_length=50, default='pending')
    currency = models.CharField(max_length=10, default='NPR')
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    customer_name = models.CharField(max_length=255, blank=True)
    customer_email = models.EmailField(blank=True)
    billing_phone = models.CharField(max_length=30, blank=True)
    billing_data = models.JSONField(default=dict, blank=True)
    shipping_data = models.JSONField(default=dict, blank=True)
    line_items_json = models.JSONField(default=list, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    sync_source = models.CharField(max_length=50, default='woocommerce_plugin')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'WooCommerce Order'
        verbose_name_plural = 'WooCommerce Orders'

    def __str__(self):
        return f'WOO-{self.woo_order_id} ({self.status})'

    @property
    def is_synced(self):
        """True once this WooCommerce order is linked to an internal store.Order.
        Only false if that internal order was later deleted (order FK is SET_NULL)."""
        return self.order_id is not None
