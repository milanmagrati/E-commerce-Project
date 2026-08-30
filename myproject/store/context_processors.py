from dashboard.models import Category
from store.customer_auth import get_customer
from store.models import Cart, Wishlist, Page


def store_context(request):
    # Only run expensive queries on store pages
    if not request.path.startswith('/store/'):
        return {}

    cart_count = 0
    cart_items = []
    wishlist_count = 0
    all_categories = Category.objects.all()

    # Cart and wishlist belong to the signed-in shopper when there is one and
    # to the browser session otherwise. The `request.user` branch is only for
    # staff browsing the shop while signed into the admin.
    customer = get_customer(request)
    if customer:
        cart = Cart.objects.filter(customer=customer).first()
        wishlist_count = Wishlist.objects.filter(customer=customer).count()
    elif request.user.is_authenticated:
        cart = Cart.objects.filter(user=request.user, customer__isnull=True).first()
        wishlist_count = Wishlist.objects.filter(
            user=request.user, customer__isnull=True).count()
    else:
        customer = None
        session_key = request.session.session_key
        cart = Cart.objects.filter(
            session_key=session_key, customer__isnull=True).first() if session_key else None
        if session_key:
            wishlist_count = Wishlist.objects.filter(
                user__isnull=True, customer__isnull=True, session_key=session_key
            ).count()

    if cart:
        cart_count = cart.total_items
        cart_items = cart.items.select_related('product').all()[:5]

    footer_pages = Page.objects.filter(is_published=True).order_by('created_at')

    return {
        'store_customer': customer,
        'store_cart_count': cart_count,
        'store_wishlist_count': wishlist_count,
        'store_all_categories': all_categories,
        'store_cart_items': cart_items,
        'footer_pages': footer_pages,
    }
