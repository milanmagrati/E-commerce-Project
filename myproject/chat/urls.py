from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    path('', views.chat_inbox, name='inbox'),
    path('thread/<int:thread_id>/', views.chat_thread, name='thread'),
    path('start/<int:user_id>/', views.start_chat, name='start'),

    # API endpoints - messaging
    path('api/create-group/', views.create_group, name='create_group'),
    path('api/send/', views.api_send_message, name='api_send'),
    path('api/messages/<int:thread_id>/', views.api_get_messages, name='api_messages'),
    path('api/unread-count/', views.api_unread_count, name='api_unread_count'),
    path('api/mark-read/<int:thread_id>/', views.api_mark_read, name='api_mark_read'),

    # API endpoints - message actions
    path('api/message/<int:message_id>/delete/', views.api_delete_message, name='api_delete_message'),
    path('api/message/<int:message_id>/edit/', views.api_edit_message, name='api_edit_message'),

    # API endpoints - group management
    path('api/thread/<int:thread_id>/remove-member/', views.api_remove_member, name='api_remove_member'),
    path('api/thread/<int:thread_id>/add-members/', views.api_add_members, name='api_add_members'),
    path('api/thread/<int:thread_id>/leave/', views.api_leave_group, name='api_leave_group'),
    path('api/thread/<int:thread_id>/delete-group/', views.api_delete_group, name='api_delete_group'),
    path('api/thread/<int:thread_id>/delete/', views.api_delete_thread, name='api_delete_thread'),
    path('api/thread/<int:thread_id>/rename/', views.api_rename_group, name='api_rename_group'),
]
