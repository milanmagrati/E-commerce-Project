from django.db.models import F


def low_stock_notifications(request):
    if not request.user.is_authenticated:
        return {}

    from dashboard.models import Product

    low_stock_products = Product.objects.filter(
        user=request.user,
        is_deleted=False,
        low_stock_threshold__gt=0,
        stock__lte=F('low_stock_threshold'),
        stock__gt=0,
    ).order_by('stock')[:10]

    if not low_stock_products.exists():
        low_stock_products = Product.objects.filter(
            user=request.user,
            is_deleted=False,
            stock__lte=10,
            stock__gt=0,
        ).order_by('stock')[:10]

    count = low_stock_products.count()

    return {
        'low_stock_notification_products': low_stock_products,
        'low_stock_notification_count': count,
    }
