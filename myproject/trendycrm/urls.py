from django.urls import path
from . import views

app_name = 'trendycrm'

urlpatterns = [
    path('', views.crm_home, name='home'),
    path('conversations/', views.crm_conversations, name='conversations'),
    path('conversations/create/', views.crm_create_conversation, name='create_conversation'),
    path('conversations/<int:conv_id>/send/', views.crm_send_message, name='send_message'),
    path('chatbot/', views.crm_chatbot, name='chatbot'),
    path('chatbot/toggle/', views.crm_chatbot_toggle, name='chatbot_toggle'),
    path('quick-replies/', views.crm_quick_replies, name='quick_replies'),
    path('quick-replies/create/', views.crm_quick_reply_create, name='quick_reply_create'),
    path('quick-replies/<int:pk>/delete/', views.crm_quick_reply_delete, name='quick_reply_delete'),
    path('integrations/', views.crm_integrations, name='integrations'),
    path('integrations/<str:channel_key>/connect/', views.crm_integration_connect, name='integration_connect'),
    path('integrations/<str:channel_key>/oauth/callback/', views.crm_integration_oauth_callback, name='integration_oauth_callback'),
    path('contacts/', views.crm_contacts, name='contacts'),
    path('analytics/', views.crm_analytics, name='analytics'),
    path('tickets/', views.crm_tickets, name='tickets'),
]
