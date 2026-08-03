from django.urls import path

from . import views

app_name = 'sentinel'

urlpatterns = [
    path('', views.command_center, name='home'),
    path('stream/', views.activity_stream, name='stream'),
    path('stream/export/', views.export_events, name='export'),

    path('sessions/', views.session_monitor, name='sessions'),
    path('sessions/<int:session_id>/', views.session_detail, name='session_detail'),
    path('sessions/<int:session_id>/revoke/', views.revoke_session, name='revoke_session'),
    path('users/<int:user_id>/revoke-all/', views.revoke_all_sessions, name='revoke_all'),
    path('devices/<int:device_id>/action/', views.device_action, name='device_action'),

    path('alerts/', views.alert_console, name='alerts'),
    path('alerts/<int:alert_id>/action/', views.alert_action, name='alert_action'),
    path('alerts/bulk/', views.bulk_alert_action, name='bulk_alert_action'),

    path('people/', views.people_index, name='people'),
    path('people/<int:user_id>/', views.user_dossier, name='dossier'),

    path('settings/', views.vault_settings, name='settings'),

    # Polled by the UI — the middleware never records these (see HARD_EXCLUDED).
    path('api/event/<int:event_id>/', views.event_detail, name='api_event'),
    path('api/pulse/', views.api_pulse, name='api_pulse'),
    path('api/sessions/', views.api_live_sessions, name='api_sessions'),
]
