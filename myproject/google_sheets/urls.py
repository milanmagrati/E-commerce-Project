from django.urls import path
from . import views

app_name = 'google_sheets'

urlpatterns = [
    # Main page
    path('', views.google_imports_page, name='google_imports_page'),

    # Connection CRUD
    path('connections/add/', views.add_sheet_connection, name='add_sheet_connection'),
    path('connections/<int:connection_id>/edit/', views.edit_sheet_connection, name='edit_sheet_connection'),
    path('connections/<int:connection_id>/delete/', views.delete_sheet_connection, name='delete_sheet_connection'),
    path('connections/<int:connection_id>/get/', views.get_connection_data, name='get_connection_data'),

    # Sync
    path('connections/<int:connection_id>/sync/', views.trigger_sync, name='trigger_sync'),

    # Data table & logs
    path('connections/<int:connection_id>/data/', views.get_sheet_data_table, name='get_sheet_data_table'),
    path('connections/<int:connection_id>/logs/', views.sync_logs, name='sync_logs'),

    # URL validation (called before saving)
    path('test-connection/', views.test_connection, name='test_connection'),

    # Full-page spreadsheet view
    path('connections/<int:connection_id>/sheet/', views.view_connection_sheet, name='view_connection_sheet'),
    path('connections/<int:connection_id>/update-cell/', views.update_sheet_cell, name='update_sheet_cell'),
    path('connections/<int:connection_id>/update-dimension/', views.update_sheet_dimension, name='update_sheet_dimension'),
    path('connections/<int:connection_id>/update-structure/', views.update_sheet_structure, name='update_sheet_structure'),
]
