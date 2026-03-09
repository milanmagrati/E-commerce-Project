from django.db.models import F


def low_stock_notifications(request):
    """
    Provide low stock notification data for the global notification bar.
    Uses the SAME query logic as the low_stock_alerts view (no user filter)
    so the notification accurately reflects what the alerts page shows.
    """
    if not request.user.is_authenticated:
        return {}

    from dashboard.models import Product, ProductVariation

    items = []

    # Check if any product has a custom threshold configured in Alert Settings
    has_custom_thresholds = Product.objects.filter(
        is_deleted=False,
        low_stock_threshold__gt=0,
    ).exists()

    has_var_thresholds = ProductVariation.objects.filter(
        product__is_deleted=False,
        low_stock_threshold__gt=0,
    ).exists()

    if has_custom_thresholds or has_var_thresholds:
        # --- Products matching Alert Settings thresholds ---

        # Out of stock products (threshold configured, stock = 0)
        out_of_stock_products = Product.objects.filter(
            is_deleted=False,
            low_stock_threshold__gt=0,
            stock=0,
        ).order_by('name')[:10]

        for p in out_of_stock_products:
            items.append({'name': p.name, 'stock': 0, 'status': 'out'})

        # Low stock products (threshold configured, stock <= threshold, stock > 0)
        low_stock_products = Product.objects.filter(
            is_deleted=False,
            low_stock_threshold__gt=0,
            stock__lte=F('low_stock_threshold'),
            stock__gt=0,
        ).order_by('stock')[:10]

        for p in low_stock_products:
            items.append({'name': p.name, 'stock': p.stock, 'status': 'low'})

        # --- Variations matching Alert Settings thresholds ---

        # Out of stock variations
        out_of_stock_variations = ProductVariation.objects.filter(
            product__is_deleted=False,
            low_stock_threshold__gt=0,
            stock=0,
        ).select_related('product').order_by('product__name')[:10]

        for v in out_of_stock_variations:
            vname = v.variation_name or v.sku
            items.append({'name': f"{v.product.name} ({vname})", 'stock': 0, 'status': 'out'})

        # Low stock variations
        low_stock_variations = ProductVariation.objects.filter(
            product__is_deleted=False,
            low_stock_threshold__gt=0,
            stock__lte=F('low_stock_threshold'),
            stock__gt=0,
        ).select_related('product').order_by('stock')[:10]

        for v in low_stock_variations:
            vname = v.variation_name or v.sku
            items.append({'name': f"{v.product.name} ({vname})", 'stock': v.stock, 'status': 'low'})
    else:
        # Fallback: no thresholds configured at all, show products with stock <= 10
        fallback_products = Product.objects.filter(
            is_deleted=False,
            stock__lte=10,
            stock__gt=0,
        ).order_by('stock')[:10]
        for p in fallback_products:
            items.append({'name': p.name, 'stock': p.stock, 'status': 'low'})

    # Sort: out-of-stock first, then by stock ascending; limit to 10
    items.sort(key=lambda x: (0 if x['status'] == 'out' else 1, x['stock']))
    items = items[:10]

    return {
        'low_stock_notification_products': items,
        'low_stock_notification_count': len(items),
    }
