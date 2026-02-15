# ncm/urls.py
"""
URL configuration for NCM integration
"""

from django.urls import path
from . import views
from . import order_recovery

app_name = 'ncm'

urlpatterns = [
    # Order Management with NCM
    path('orders/<int:order_id>/create/', 
         views.create_ncm_shipment, 
         name='create_shipment'),
    
    path('orders/<int:order_id>/sync/', 
         views.sync_ncm_status, 
         name='sync_status'),
    
    path('orders/<int:order_id>/track/', 
         views.track_ncm_order, 
         name='track_order'),
    
    # Bulk Operations
    path('bulk-sync/', 
         views.bulk_sync_ncm_orders, 
         name='bulk_sync'),
    
    # NCM Information
    path('branches/', 
         views.ncm_branches_list, 
         name='branches'),
    
    path('branches/json/', 
         views.branches_json, 
         name='branches_json'),
    
    # Webhook endpoint (NCM will POST here)
    path('webhook/', 
         views.ncm_webhook, 
         name='webhook'),
    
    # Order Recovery & Troubleshooting
    path('orders/<int:order_id>/clear-ncm-id/', 
         order_recovery.clear_ncm_order_id, 
         name='clear_ncm_id'),
    
    path('orders/<int:order_id>/verify-ncm/', 
         order_recovery.verify_ncm_order, 
         name='verify_ncm'),
    
]
