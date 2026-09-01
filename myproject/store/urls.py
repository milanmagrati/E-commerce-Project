from django.urls import path
from . import views

app_name = 'store'

urlpatterns = [
    path('', views.landing_page, name='landing'),
    path('products/', views.product_list, name='product_list'),
    path('products/<slug:slug>/', views.product_detail, name='product_detail'),
    path('category/<slug:slug>/', views.category_products, name='category_products'),
    path('search/', views.search_results, name='search'),
    path('search/autocomplete/', views.search_autocomplete, name='search_autocomplete'),
    path('cart/', views.cart_view, name='cart'),
    path('cart/add/<int:product_id>/', views.add_to_cart, name='add_to_cart'),
    path('cart/remove/<int:item_id>/', views.remove_from_cart, name='remove_from_cart'),
    path('cart/update/', views.update_cart, name='update_cart'),
    path('wishlist/', views.wishlist_view, name='wishlist'),
    path('wishlist/toggle/<int:product_id>/', views.toggle_wishlist, name='toggle_wishlist'),
    path('notify-back-in-stock/<int:product_id>/', views.notify_back_in_stock, name='notify_back_in_stock'),
    path('checkout/', views.checkout_view, name='checkout'),

    # Order lookup stays open to guests: number + phone, no sign-in needed.
    path('track-order/', views.order_track, name='order_track'),
    path('orders/<str:order_number>/', views.order_detail, name='order_detail'),

    # Customer accounts. Optional everywhere — they save retyping details and
    # keep order history together, but nothing on the storefront demands one.
    path('account/login/', views.account_login, name='account_login'),
    path('account/register/', views.account_register, name='account_register'),
    path('account/logout/', views.account_logout, name='account_logout'),
    path('account/', views.account_home, name='account'),
    path('account/profile/', views.account_profile, name='account_profile'),
    path('account/password/', views.account_password, name='account_password'),

    path('review/add/<int:product_id>/', views.add_review, name='add_review'),
    path('quick-order/<int:product_id>/', views.quick_order, name='quick_order'),

    # Order-form data endpoints
    path('api/locations/', views.locations_json, name='locations'),
    path('api/quote/', views.quote_json, name='quote'),
    path('api/discount/', views.apply_discount, name='apply_discount'),

    path('api/load-more/', views.load_more_products, name='load_more'),
    path('p/<slug:slug>/', views.dynamic_page, name='dynamic_page'),
]
