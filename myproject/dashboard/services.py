import logging
from django.core.exceptions import ValidationError
from django.db import transaction

logger = logging.getLogger(__name__)


@transaction.atomic
def deduct_stock(product, quantity):
    """Deduct stock for a product when an order item is placed.

    For bundle products: loops through each BundleComponent and deducts
    the required quantity from each component product's stock.
    For simple/variable products: deducts directly from the product's stock.

    Also decrements reserved_qty where applicable so that available_stock
    (stock - reserved_qty) stays accurate.

    Args:
        product: A Product instance to deduct stock for.
        quantity: Number of units ordered (multiplied by component qty for bundles).

    Raises:
        ValidationError: If any product has insufficient stock.
    """
    if product is None:
        return

    if product.is_bundle:
        components = product.bundle_components.select_related('component_product').all()
        if not components.exists():
            logger.warning(f"Bundle product '{product.name}' has no components configured.")
            return

        # Pre-check all component stock before deducting anything
        insufficient = []
        for comp in components:
            required = comp.quantity_required * quantity
            if comp.component_product.stock < required:
                insufficient.append(
                    f"{comp.component_product.name} (need {required}, have {comp.component_product.stock})"
                )

        if insufficient:
            raise ValidationError(
                f"Insufficient stock for bundle '{product.name}': {', '.join(insufficient)}"
            )

        # All checks passed — deduct stock from each component
        for comp in components:
            required = comp.quantity_required * quantity
            comp.component_product.stock -= required
            comp.component_product.reserved_qty = max(0, comp.component_product.reserved_qty - required)
            comp.component_product.save(update_fields=['stock', 'reserved_qty'])
            logger.info(
                f"Deducted {required} from '{comp.component_product.name}' "
                f"(bundle: {product.name}), remaining: {comp.component_product.stock}"
            )
    else:
        if product.stock < quantity:
            raise ValidationError(
                f"Insufficient stock for '{product.name}' (need {quantity}, have {product.stock})"
            )
        product.stock -= quantity
        product.reserved_qty = max(0, product.reserved_qty - quantity)
        product.save(update_fields=['stock', 'reserved_qty'])
        logger.info(
            f"Deducted {quantity} from '{product.name}', remaining: {product.stock}"
        )
