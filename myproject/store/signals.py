"""Back-in-stock alerts.

When a Product (or one of its ProductVariations) is saved with stock on hand
again, any shopper waiting on it is messaged once and the notice is stamped so
it never fires twice. Sending must never break the save that triggered it, so
the whole flush is wrapped and logged.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger('store')

# How many waiting shoppers to clear per restock event, so one save can't fan
# out into thousands of sends synchronously.
_BATCH = 200


@receiver(post_save, sender='dashboard.Product', dispatch_uid='store.backinstock.product')
def _product_saved(sender, instance, created, **kwargs):
    if created or not (instance.stock and instance.stock > 0):
        return
    _flush(Q(product_id=instance.pk, variation__isnull=True))


@receiver(post_save, sender='dashboard.ProductVariation', dispatch_uid='store.backinstock.variation')
def _variation_saved(sender, instance, created, **kwargs):
    if created or not (instance.stock and instance.stock > 0):
        return
    # A variation coming back also satisfies product-level notices on its parent.
    _flush(Q(variation_id=instance.pk) |
           Q(product_id=instance.product_id, variation__isnull=True))


def _flush(where):
    try:
        from .models import BackInStockNotice

        pending = list(
            BackInStockNotice.objects
            .filter(where, notified_at__isnull=True)
            .select_related('product', 'variation')[:_BATCH]
        )
        if not pending:
            return

        done_ids = []
        for notice in pending:
            try:
                _send(notice)
            except Exception:
                logger.exception('back-in-stock: send failed for notice %s', notice.pk)
            # Stamp regardless of channel outcome: a restock is a one-shot
            # event, and retrying on every later save would spam the shopper.
            done_ids.append(notice.pk)

        BackInStockNotice.objects.filter(pk__in=done_ids).update(
            notified_at=timezone.now())
        logger.info('back-in-stock: cleared %d notice(s)', len(done_ids))
    except Exception:
        logger.exception('back-in-stock: flush failed')


def _send(notice):
    product = notice.product
    target = notice.target_label
    try:
        path = reverse('store:product_detail', kwargs={'slug': product.slug})
    except Exception:
        path = '/'
    url = f"{getattr(settings, 'SITE_URL', '').rstrip('/')}{path}"
    if notice.variation_id:
        url = f'{url}?variation={notice.variation_id}'

    line = f'Good news — {target} is back in stock.'

    if notice.email:
        send_mail(
            subject=f'{product.name} is back in stock',
            message=f'{line}\n\nShop it here: {url}\n\n— {product.name}',
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            recipient_list=[notice.email],
            fail_silently=True,
        )
        return

    if notice.phone:
        try:
            from services.sms_service import SMSService
            SMSService().send_text(notice.phone, f'{line} {url}', ref='back-in-stock')
        except Exception:
            logger.exception('back-in-stock: SMS path failed for %s', notice.pk)
