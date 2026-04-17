from django.urls import path
from .views import WooCommerceOrderReceiveView

app_name = 'integrations'

urlpatterns = [
    path('woocommerce/orders/', WooCommerceOrderReceiveView.as_view(), name='woo-order-receive'),
]
