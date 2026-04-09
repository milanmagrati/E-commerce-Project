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
    path('checkout/', views.checkout_view, name='checkout'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/<str:order_number>/', views.order_detail, name='order_detail'),
    path('login/', views.customer_login, name='login'),
    path('register/', views.customer_register, name='register'),
    path('logout/', views.customer_logout, name='logout'),
    path('profile/', views.customer_profile, name='profile'),
    path('review/add/<int:product_id>/', views.add_review, name='add_review'),
    path('api/load-more/', views.load_more_products, name='load_more'),
]
