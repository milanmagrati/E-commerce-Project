# pick_and_drop/urls.py
"""
URL configuration for Pick and Drop integration
"""

from django.urls import path
from . import views

app_name = 'pick_and_drop'

urlpatterns = [
    # Order Management with Pick and Drop
    path('orders/<int:order_id>/create/',
         views.create_pnd_shipment,
         name='create_shipment'),

    path('orders/<int:order_id>/sync/',
         views.sync_pnd_status,
         name='sync_status'),

    path('orders/<int:order_id>/track/',
         views.track_pnd_order,
         name='track_order'),

    path('orders/<int:order_id>/cancel/',
         views.cancel_pnd_order,
         name='cancel_order'),

    # Bulk Operations
    path('bulk-sync/',
         views.bulk_sync_pnd_orders,
         name='bulk_sync'),
]
