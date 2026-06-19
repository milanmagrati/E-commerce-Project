from django.urls import path
from . import views

app_name = 'google_sheets'

urlpatterns = [
    # Main imports page
    path('', views.google_imports_page, name='google_imports_page'),

    # Connection management
    path('connections/add/', views.add_sheet_connection, name='add_sheet_connection'),
    path('connections/<int:connection_id>/edit/', views.edit_sheet_connection, name='edit_sheet_connection'),
    path('connections/<int:connection_id>/delete/', views.delete_sheet_connection, name='delete_sheet_connection'),
    path('connections/<int:connection_id>/get/', views.get_connection_data, name='get_connection_data'),

    # Sync
    path('connections/<int:connection_id>/sync/', views.trigger_sync, name='trigger_sync'),

    # Data & Preview
    path('connections/<int:connection_id>/preview/', views.preview_sheet_data, name='preview_sheet_data'),
    path('connections/<int:connection_id>/data/', views.get_sheet_data_table, name='get_sheet_data_table'),
    path('connections/<int:connection_id>/update-cell/', views.update_cell, name='update_cell'),
    path('connections/<int:connection_id>/logs/', views.sync_logs, name='sync_logs'),



    # Previews & UI actions
    path('preview-by-url/', views.preview_sheet_by_url, name='preview_sheet_by_url'),
]
