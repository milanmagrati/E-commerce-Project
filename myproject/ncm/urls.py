# ncm/urls.py
"""
URL configuration for NCM integration
"""

from django.urls import path
from . import views
from . import order_recovery
from . import realtime_api

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

    # Same sync, started in the background and answered as JSON, so a list page
    # can trigger it without navigating away or waiting on the NCM round-trips.
    path('bulk-sync/start/',
         views.bulk_sync_ncm_orders_json,
         name='bulk_sync_json'),


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
    
    # Real-time API endpoints for auto-sync
    path('api/order/<int:order_id>/status/', 
         realtime_api.api_get_order_status, 
         name='api_order_status'),
    
    path('api/order/<int:order_id>/sync/', 
         realtime_api.api_sync_order_status, 
         name='api_sync_status'),
    
    path('api/orders/batch-status/', 
         realtime_api.api_get_orders_status_batch, 
         name='api_batch_status'),
    
    path('api/order/<int:order_id>/activity/',
         realtime_api.api_get_order_activity_log,
         name='api_activity_log'),

    path('api/order/<int:order_id>/comments/',
         realtime_api.api_get_order_comments,
         name='api_order_comments'),

    path('api/order/<int:order_id>/comments/add/',
         realtime_api.api_add_order_comment,
         name='api_add_comment'),
    
    # Drives the background bulk sync in the absence of cron (ncm/scheduler.py).
    # Polled by every authenticated page from base.html.
    path('api/heartbeat/',
         realtime_api.api_sync_heartbeat,
         name='api_sync_heartbeat'),


    # Order Recovery & Troubleshooting
    path('orders/<int:order_id>/clear-ncm-id/', 
         order_recovery.clear_ncm_order_id, 
         name='clear_ncm_id'),
    
    path('orders/<int:order_id>/verify-ncm/', 
         order_recovery.verify_ncm_order, 
         name='verify_ncm'),
    
]
