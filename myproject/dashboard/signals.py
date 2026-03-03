import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Order, OrderItem, OrderActivityLog

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Order)
def log_order_creation(sender, instance, created, **kwargs):
    """Log when an order is created"""
    if created:
        description = f'Order #{instance.order_number} was created with total amount रू {instance.total_amount}'
        if instance.is_partial_payment:
            description += f' | Partial Payment: रू {instance.partial_amount_paid} paid, रू {instance.remaining_amount} remaining'

        OrderActivityLog.objects.create(
            order=instance,
            action_type='created',
            user=instance.created_by,
            description=description
        )


@receiver(post_save, sender=OrderItem)
def deduct_stock_on_order_item_create(sender, instance, created, **kwargs):
    """Automatically deduct stock when a new OrderItem is created."""
    if created and instance.product:
        from .services import deduct_stock
        try:
            deduct_stock(instance.product, instance.quantity)
        except Exception as e:
            logger.error(f"Stock deduction failed for OrderItem {instance}: {e}")
