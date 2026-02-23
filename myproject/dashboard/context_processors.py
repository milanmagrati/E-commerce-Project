from django.db.models import F


def low_stock_notifications(request):
    if not request.user.is_authenticated:
        return {}

    from dashboard.models import Product, ProductVariation

    # Low stock simple products (with custom threshold set)
    low_stock_products = list(Product.objects.filter(
        user=request.user,
        is_deleted=False,
        low_stock_threshold__gt=0,
        stock__lte=F('low_stock_threshold'),
        stock__gt=0,
    ).order_by('stock')[:10])

    # Low stock variations (with custom threshold set)
    low_stock_variations = ProductVariation.objects.filter(
        product__user=request.user,
        product__is_deleted=False,
        low_stock_threshold__gt=0,
        stock__lte=F('low_stock_threshold'),
        stock__gt=0,
    ).select_related('product').order_by('stock')[:10]

    # Build a uniform list with .name and .stock for template
    items = []
    for p in low_stock_products:
        items.append({'name': p.name, 'stock': p.stock})
    for v in low_stock_variations:
        vname = v.variation_name or v.sku
        items.append({'name': f"{v.product.name} ({vname})", 'stock': v.stock})

    # Fallback: if no thresholds are set, show products with stock <= 10
    if not items:
        fallback_products = Product.objects.filter(
            user=request.user,
            is_deleted=False,
            stock__lte=10,
            stock__gt=0,
        ).order_by('stock')[:10]
        for p in fallback_products:
            items.append({'name': p.name, 'stock': p.stock})

    # Sort combined list by stock ascending and limit to 10
    items.sort(key=lambda x: x['stock'])
    items = items[:10]

    return {
        'low_stock_notification_products': items,
        'low_stock_notification_count': len(items),
    }
