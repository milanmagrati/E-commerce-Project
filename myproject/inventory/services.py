"""
Backorder / Stock Reservation Service
======================================
All public functions run inside ``transaction.atomic()`` and use
``select_for_update()`` to prevent race conditions on concurrent requests.

Public API:
    allocate_order(order)                     — Reserve stock when a store order is confirmed
    restock_product(product, qty)             — Fill backorders FIFO when new stock arrives
    ship_order_item(item)                     — Deduct stock + release reservation on shipment
    cancel_order_item(item)                   — Roll back reservation on cancellation
    clear_reservation_on_dispatch(product, q) — Clear counters when dashboard dispatches stock
    release_order_reservations(dashboard_order) — Release all reservations for a cancelled order
    reset_stale_counters()                    — Maintenance: recalculate all counters from DB

Usage:
    from inventory.services import allocate_order, restock_product
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
                # If backorders are not allowed, just drop the backorder
                backorder = 0

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

        from dashboard.models import OrderItem as DashboardOrderItem

        # Find all backordered line-items for this product
        store_backordered = list(
            OrderItem.objects
            .filter(product=product, backordered_qty__gt=0)
            .select_related('order')
            .select_for_update()
        )
        
        dash_backordered = list(
            DashboardOrderItem.objects
            .filter(product=product, backordered_qty__gt=0)
            .select_related('order')
            .select_for_update()
        )

        # Combine and sort oldest first (FIFO by order created_at)
        all_backordered = store_backordered + dash_backordered
        all_backordered.sort(key=lambda x: (x.order.created_at, x.pk))

        for item in all_backordered:
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
    Resets the item's reserved_qty to 0. Also deducts stock from ProductBatches
    in FIFO order (oldest expiry first) if they exist.

    Args:
        item: store.models.OrderItem instance
    """
    from dashboard.models import Product

    with transaction.atomic():
        if item.product is None:
            return

        product = Product.objects.select_for_update().get(pk=item.product.pk)

        shipped = item.reserved_qty
        
        # 1. Deduct from batches (FIFO)
        _deduct_from_batches_fifo(product, shipped)
        
        # 2. Update product counters
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


def _deduct_from_batches_fifo(product, qty_to_deduct):
    """
    Helper to deduct quantity from ProductBatches in FIFO order
    (oldest expiry date first, then oldest created_at).
    """
    if qty_to_deduct <= 0:
        return
        
    from django.db.models import F
        
    # Get batches with available stock, ordered by FIFO rules
    batches = list(product.batches.filter(quantity__gt=0).order_by(
        F('expiry_date').asc(nulls_last=True),
        'created_at'
    ).select_for_update())
    
    remaining_to_deduct = qty_to_deduct
    
    for batch in batches:
        if remaining_to_deduct <= 0:
            break
            
        deduct_from_batch = min(batch.quantity, remaining_to_deduct)
        batch.quantity -= deduct_from_batch
        batch.save(update_fields=['quantity'])
        
        remaining_to_deduct -= deduct_from_batch
        
        logger.info(
            f"FIFO batch deduction: product={product.name} batch={batch.batch_number} "
            f"deducted={deduct_from_batch} remaining_in_batch={batch.quantity}"
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


def restore_order_stock(order):
    """
    Restores stock for a dispatched order that is being cancelled, deleted, or moved to trash.
    Correctly handles simple, variable, and bundle products.
    """
    from dashboard.models import Product, ProductVariation
    
    with transaction.atomic():
        for item in order.items.all():
            if item.product_variation:
                variation = ProductVariation.objects.select_for_update().get(pk=item.product_variation.pk)
                variation.stock += item.quantity
                if variation.stock > 0:
                    variation.status = 'active'
                variation.save(update_fields=['stock', 'status'])
            elif item.product:
                product = Product.objects.select_for_update().get(pk=item.product.pk)
                
                if product.is_bundle:
                    # Restore stock for bundle components
                    components = product.bundle_components.select_related('component_product').all()
                    for comp in components:
                        comp_product = Product.objects.select_for_update().get(pk=comp.component_product.pk)
                        restored_qty = comp.quantity_required * item.quantity
                        comp_product.stock += restored_qty
                        
                        threshold = comp_product.low_stock_threshold or 0
                        if comp_product.stock <= 0:
                            comp_product.stock_status = 'out_of_stock'
                        elif threshold > 0 and comp_product.stock <= threshold:
                            comp_product.stock_status = 'low_stock'
                        else:
                            comp_product.stock_status = 'in_stock'
                            
                        comp_product.save(update_fields=['stock', 'stock_status'])
                else:
                    # Restore stock for simple product
                    product.stock += item.quantity
                    threshold = product.low_stock_threshold or 0
                    if product.stock <= 0:
                        product.stock_status = 'out_of_stock'
                    elif threshold > 0 and product.stock <= threshold:
                        product.stock_status = 'low_stock'
                    else:
                        product.stock_status = 'in_stock'
                    product.save(update_fields=['stock', 'stock_status'])

        logger.info(f"restore_order_stock: Restored stock for dispatched order={order.order_number}")


def clear_reservation_on_dispatch(product, quantity):
    """
    Called by the dashboard dispatch view when stock is deducted on dispatch.

    The dispatch view already handles the main stock deduction itself, so this
    function decrements reserved_qty and backordered_qty to keep the
    counters accurate. Also deducts from FIFO batches.

    Args:
        product: dashboard.models.Product instance
        quantity: int – number of units being dispatched
    """
    from dashboard.models import Product

    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=product.pk)

        # 1. Deduct from FIFO batches
        _deduct_from_batches_fifo(product, quantity)

        # 2. Clear reserved_qty (these are filled units)
        reserved_to_clear = min(product.reserved_qty, quantity)
        product.reserved_qty = max(0, product.reserved_qty - reserved_to_clear)

        # If quantity exceeds what was reserved, also clear backordered_qty
        # (handles edge cases where items were dispatched while still backordered)
        remaining = quantity - reserved_to_clear
        if remaining > 0 and product.backordered_qty > 0:
            backorder_to_clear = min(product.backordered_qty, remaining)
            product.backordered_qty = max(0, product.backordered_qty - backorder_to_clear)

        product.save(update_fields=['reserved_qty', 'backordered_qty'])

        # Also clean up store and dashboard OrderItem records for this product
        # that belong to dispatched/delivered orders
        _cleanup_order_items_for_product(product)


def _cleanup_order_items_for_product(product):
    """
    Zero out reserved_qty and backordered_qty on both store and dashboard 
    OrderItem records for orders that have been dispatched or delivered, 
    since these counters are no longer relevant once the order is fulfilled.
    """
    from store.models import OrderItem as StoreOrderItem
    from dashboard.models import OrderItem as DashboardOrderItem
    from django.db.models import Q

    try:
        # Cleanup Store Orders
        stale_store_items = (
            StoreOrderItem.objects
            .filter(
                product=product,
                order__status__in=('delivered', 'shipped', 'cancelled'),
            )
            .filter(
                Q(reserved_qty__gt=0) | Q(backordered_qty__gt=0)
            )
        )
        stale_store_items.update(reserved_qty=0, backordered_qty=0)

        # Cleanup Dashboard Orders
        stale_dash_items = (
            DashboardOrderItem.objects
            .filter(
                product=product,
                order__order_status__in=('delivered', 'cancelled'),
            )
            .filter(
                Q(reserved_qty__gt=0) | Q(backordered_qty__gt=0)
            )
        )
        stale_dash_items.update(reserved_qty=0, backordered_qty=0)

    except Exception as e:
        logger.warning(f"_cleanup_order_items_for_product: {e}")


def release_order_reservations(dashboard_order):
    """
    Called when a dashboard Order is cancelled.

    Finds the matching store.Order by order_number and releases all
    reserved_qty and backordered_qty on each OrderItem back to the
    product. Also directly decrements product-level counters.

    Args:
        dashboard_order: dashboard.models.Order instance
    """
    from dashboard.models import Product
    from store.models import Order as StoreOrder

    try:
        # Try to find matching store order by order_number
        store_order = StoreOrder.objects.filter(
            order_number=dashboard_order.order_number
        ).first()

        if store_order:
            for item in store_order.items.select_related('product').all():
                if item.product is None:
                    continue
                if item.reserved_qty == 0 and item.backordered_qty == 0:
                    continue

                cancel_order_item(item)  # each call is already atomic

            logger.info(
                f"release_order_reservations: released all reservations for "
                f"store order {store_order.order_number}"
            )
        else:
            # No matching store order — release directly from dashboard order items
            # This handles orders created from the dashboard (not the store)
            _release_product_counters_from_dashboard_order(dashboard_order)

    except Exception as e:
        logger.error(
            f"release_order_reservations: error releasing reservations for "
            f"order {dashboard_order.order_number}: {e}"
        )


def _release_product_counters_from_dashboard_order(dashboard_order):
    """
    Fallback: For orders that don't have a matching store.Order,
    release product-level reserved_qty/backordered_qty based on
    the dashboard order items quantities.

    This is a best-effort approach since dashboard OrderItems
    don't track reserved/backordered individually.
    """
    from dashboard.models import Product

    with transaction.atomic():
        for item in dashboard_order.items.select_related('product').all():
            if item.product is None:
                continue

            try:
                product = Product.objects.select_for_update().get(pk=item.product.pk)

                # Release reserved units (up to what's tracked)
                release_qty = min(item.quantity, product.reserved_qty)
                if release_qty > 0:
                    product.reserved_qty = max(0, product.reserved_qty - release_qty)

                # If there's remaining quantity beyond reserved, release from backordered
                remaining = item.quantity - release_qty
                if remaining > 0 and product.backordered_qty > 0:
                    backorder_release = min(remaining, product.backordered_qty)
                    product.backordered_qty = max(0, product.backordered_qty - backorder_release)

                product.save(update_fields=['reserved_qty', 'backordered_qty'])

                logger.info(
                    f"_release_product_counters: order={dashboard_order.order_number} "
                    f"product={product.name} released_reserved={release_qty}"
                )
            except Product.DoesNotExist:
                continue


def reset_stale_counters():
    """
    Maintenance utility: Recalculates reserved_qty and backordered_qty
    on all products from the actual store.OrderItem records.

    Call this periodically or after discovering counter drift.
    Safe to run at any time — uses atomic transactions.

    Usage:
        from inventory.services import reset_stale_counters
        reset_stale_counters()
    """
    from dashboard.models import Product
    from store.models import OrderItem
    from django.db.models import Sum, Q

    with transaction.atomic():
        products = Product.objects.select_for_update().filter(
            Q(reserved_qty__gt=0) | Q(backordered_qty__gt=0)
        )

        fixed_count = 0
        for product in products:
            # Sum from store.OrderItem
            store_active_items = OrderItem.objects.filter(
                product=product,
                order__status__in=['pending', 'confirmed']
            )
            store_reserved = store_active_items.aggregate(total=Sum('reserved_qty'))['total'] or 0
            store_backordered = store_active_items.aggregate(total=Sum('backordered_qty'))['total'] or 0

            # Sum from dashboard.models.OrderItem
            from dashboard.models import OrderItem as DashboardOrderItem
            dash_active_items = DashboardOrderItem.objects.filter(
                product=product,
                order__order_status__in=['pending', 'processing', 'confirmed', 'dispatched']
            )
            dash_reserved = dash_active_items.aggregate(total=Sum('reserved_qty'))['total'] or 0
            dash_backordered = dash_active_items.aggregate(total=Sum('backordered_qty'))['total'] or 0

            actual_reserved = store_reserved + dash_reserved
            actual_backordered = store_backordered + dash_backordered

            if (product.reserved_qty != actual_reserved or
                    product.backordered_qty != actual_backordered):
                old_reserved = product.reserved_qty
                old_backordered = product.backordered_qty

                product.reserved_qty = actual_reserved
                product.backordered_qty = actual_backordered
                product.save(update_fields=['reserved_qty', 'backordered_qty'])

                fixed_count += 1
                logger.info(
                    f"reset_stale_counters: {product.name} "
                    f"reserved {old_reserved}->{actual_reserved} "
                    f"backordered {old_backordered}->{actual_backordered}"
                )

        logger.info(f"reset_stale_counters: fixed {fixed_count} products")
        return fixed_count
