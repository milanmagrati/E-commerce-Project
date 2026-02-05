from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Order, OrderActivityLog

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
