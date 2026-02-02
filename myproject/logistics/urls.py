from django.urls import path
from . import views


app_name = 'logistics'


urlpatterns = [
    # Webhook
    path('webhook/ncm/', views.ncm_webhook, name='ncm-webhook'),
    
    # Dashboard
    path('', views.logistics_dashboard, name='dashboard'),
    path('orders/', views.logistics_orders_list, name='orders_list'),
    path('branches/', views.ncm_branches_list, name='branches_list'),
    
    # Actions
    path('send/<int:order_id>/', views.send_order_to_ncm, name='send_to_ncm'),
    path('sync/<int:order_id>/', views.sync_ncm_status_view, name='sync_status'),
    path('bulk-send/', views.bulk_send_to_ncm, name='bulk_send'),
    path('bulk-sync/', views.bulk_sync_ncm_status, name='bulk_sync'),
    
    
    
]
