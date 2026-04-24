"""
Backorder / Stock Reservation Service
======================================
All four public functions run inside ``transaction.atomic()`` and use
``select_for_update()`` to prevent race conditions on concurrent requests.

Usage:
    from inventory.services import allocate_order, restock_product, ship_order_item, cancel_order_item
"""
import logging
from django.db import transaction

logger = logging.getLogger(__name__)


def _allocate_bundle(item, product):
    """
    Handle stock reservation for a bundle product.

    Reserves stock on each *component* product, proportional to the
    bundle's quantity_required.  Backorder tracking stays on the
    top-level bundle product for visibility.
    """
    from dashboard.models import Product

    components = product.bundle_components.select_related('component_product').all()
    if not components.exists():
        logger.warning(f"allocate_order: bundle '{product.name}' has no components")
        return

    total_requested = item.quantity
    # Figure out how many full bundles can be reserved
    max_full_bundles = total_requested
    for comp in components:
        cp = Product.objects.select_for_update().get(pk=comp.component_product.pk)
        available = cp.stock - cp.reserved_qty
        possible = max(available, 0) // comp.quantity_required
        max_full_bundles = min(max_full_bundles, possible)

    can_reserve = max_full_bundles
    backorder = total_requested - can_reserve

    if backorder > 0 and not product.backorders_allowed:
        backorder = 0
        can_reserve = min(total_requested, max_full_bundles)

    # Reserve component stock
    for comp in components:
        cp = Product.objects.select_for_update().get(pk=comp.component_product.pk)
        reserve_units = can_reserve * comp.quantity_required
        cp.reserved_qty += reserve_units
        cp.save(update_fields=['reserved_qty'])

    # Track on the OrderItem and bundle product
    item.reserved_qty = can_reserve
    item.backordered_qty = backorder
    item.save(update_fields=['reserved_qty', 'backordered_qty'])

    product.reserved_qty += can_reserve
    product.backordered_qty += backorder
    product.save(update_fields=['reserved_qty', 'backordered_qty'])

    logger.info(
        f"allocate_order(bundle): order={item.order.order_number} "
        f"bundle={product.name} reserved={can_reserve} backordered={backorder}"
    )


def allocate_order(order):
    """
    Called when a store order is confirmed.

    For each OrderItem:
      • reserve up to available_stock from the product
      • any excess goes to backordered_qty (only if product.backorders_allowed)

    Handles simple, variable, AND bundle products.

    Args:
        order: store.models.Order instance (status just moved to 'confirmed')
    """
    from dashboard.models import Product

    with transaction.atomic():
        for item in order.items.select_related('product').all():
            product = item.product
            if product is None:
                continue

            # Lock the product row for the duration of this transaction
            product = Product.objects.select_for_update().get(pk=product.pk)

            if product.is_bundle:
                _allocate_bundle(item, product)
                continue

            available = product.stock - product.reserved_qty
            can_reserve = min(item.quantity, max(available, 0))
            backorder = item.quantity - can_reserve

            if backorder > 0 and not product.backorders_allowed:
                # If backorders are not allowed, cap to what's available
                backorder = 0
                can_reserve = min(item.quantity, max(available, 0))

            # Update OrderItem
            item.reserved_qty = can_reserve
            item.backordered_qty = backorder
            item.save(update_fields=['reserved_qty', 'backordered_qty'])

            # Update Product counters
            product.reserved_qty += can_reserve
            product.backordered_qty += backorder
            product.save(update_fields=['reserved_qty', 'backordered_qty'])

            logger.info(
                f"allocate_order: order={order.order_number} product={product.name} "
                f"reserved={can_reserve} backordered={backorder}"
            )


def restock_product(product, qty):
    """
    Called when new stock is received (e.g. stock-in / purchase).

    1. Fills oldest backordered OrderItems first (FIFO by order created_at).
    2. All incoming units are added to product.stock.

    Args:
        product: dashboard.models.Product instance
        qty:     int – number of units received
    """
    from dashboard.models import Product
    from store.models import OrderItem

    with transaction.atomic():
        # Lock the product row
        product = Product.objects.select_for_update().get(pk=product.pk)

        remaining = qty

        # Find all backordered line-items for this product, oldest first
        backordered_items = (
            OrderItem.objects
            .filter(product=product, backordered_qty__gt=0)
            .select_related('order')
            .order_by('order__created_at', 'pk')
            .select_for_update()
        )

        for item in backordered_items:
            if remaining <= 0:
                break

            fill = min(item.backordered_qty, remaining)

            # Move units from backordered → reserved
            item.reserved_qty += fill
            item.backordered_qty -= fill
            item.save(update_fields=['reserved_qty', 'backordered_qty'])

            # Update product-level counters
            product.reserved_qty += fill
            product.backordered_qty -= fill

            remaining -= fill

            logger.info(
                f"restock_product: filled backorder order={item.order.order_number} "
                f"product={product.name} filled={fill} remaining_backorder={item.backordered_qty}"
            )

        # Add all incoming units to physical stock
        product.stock += qty
        product.save(update_fields=['stock', 'reserved_qty', 'backordered_qty'])

        logger.info(
            f"restock_product: product={product.name} added={qty} "
            f"stock_now={product.stock} reserved={product.reserved_qty} "
            f"backordered={product.backordered_qty}"
        )


def ship_order_item(item):
    """
    Called when an order item is shipped / dispatched.

    Decreases both product.stock and product.reserved_qty by item.reserved_qty.
    Resets the item's reserved_qty to 0.

    Args:
        item: store.models.OrderItem instance
    """
    from dashboard.models import Product

    with transaction.atomic():
        if item.product is None:
            return

        product = Product.objects.select_for_update().get(pk=item.product.pk)

        shipped = item.reserved_qty
        product.stock = max(0, product.stock - shipped)
        product.reserved_qty = max(0, product.reserved_qty - shipped)
        product.save(update_fields=['stock', 'reserved_qty'])

        # Clear the item's reservation (it's now shipped)
        item.reserved_qty = 0
        item.save(update_fields=['reserved_qty'])

        logger.info(
            f"ship_order_item: order={item.order.order_number} product={product.name} "
            f"shipped={shipped} stock_now={product.stock}"
        )


def cancel_order_item(item):
    """
    Called when an order (or individual line item) is cancelled.

    Rolls back reserved_qty and/or backordered_qty on both the item
    and the product.

    Args:
        item: store.models.OrderItem instance
    """
    from dashboard.models import Product

    with transaction.atomic():
        if item.product is None:
            return

        product = Product.objects.select_for_update().get(pk=item.product.pk)

        # Release reserved units back to available pool
        released = item.reserved_qty
        product.reserved_qty = max(0, product.reserved_qty - released)

        # Remove backordered units
        cancelled_backorder = item.backordered_qty
        product.backordered_qty = max(0, product.backordered_qty - cancelled_backorder)

        product.save(update_fields=['reserved_qty', 'backordered_qty'])

        # Zero out the item
        item.reserved_qty = 0
        item.backordered_qty = 0
        item.save(update_fields=['reserved_qty', 'backordered_qty'])

        logger.info(
            f"cancel_order_item: order={item.order.order_number} product={product.name} "
            f"released_reserved={released} cancelled_backorder={cancelled_backorder}"
        )


def clear_reservation_on_dispatch(product, quantity):
    """
    Called by the dashboard dispatch view when stock is deducted on dispatch.

    The dispatch view already handles the stock deduction itself, so this
    function only decrements reserved_qty to keep the counter accurate.

    Args:
        product: dashboard.models.Product instance
        quantity: int – number of units being dispatched
    """
    from dashboard.models import Product

    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=product.pk)
        product.reserved_qty = max(0, product.reserved_qty - quantity)
        product.save(update_fields=['reserved_qty'])
