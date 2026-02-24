from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    path('', views.chat_inbox, name='inbox'),
    path('thread/<int:thread_id>/', views.chat_thread, name='thread'),
    path('start/<int:user_id>/', views.start_chat, name='start'),

    # API endpoints
    path('api/create-group/', views.create_group, name='create_group'),
    path('api/send/', views.api_send_message, name='api_send'),
    path('api/messages/<int:thread_id>/', views.api_get_messages, name='api_messages'),
    path('api/unread-count/', views.api_unread_count, name='api_unread_count'),
    path('api/mark-read/<int:thread_id>/', views.api_mark_read, name='api_mark_read'),
]
