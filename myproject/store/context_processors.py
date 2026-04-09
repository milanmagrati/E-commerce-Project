from dashboard.models import Category
from store.models import Cart, Wishlist


def store_context(request):
    # Only run expensive queries on store pages
    if not request.path.startswith('/store/'):
        return {}

    cart_count = 0
    cart_items = []
    wishlist_count = 0
    all_categories = Category.objects.all()

    if request.user.is_authenticated:
        cart = Cart.objects.filter(user=request.user).first()
        if cart:
            cart_count = cart.total_items
            cart_items = cart.items.select_related('product').all()[:5]
        wishlist_count = Wishlist.objects.filter(user=request.user).count()
    else:
        session_key = request.session.session_key
        if session_key:
            cart = Cart.objects.filter(session_key=session_key).first()
            if cart:
                cart_count = cart.total_items
                cart_items = cart.items.select_related('product').all()[:5]

    return {
        'store_cart_count': cart_count,
        'store_wishlist_count': wishlist_count,
        'store_all_categories': all_categories,
        'store_cart_items': cart_items,
    }
