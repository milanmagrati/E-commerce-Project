from django.urls import path
from . import views

app_name = 'trendycrm'

urlpatterns = [
    path('', views.crm_home, name='home'),
    path('conversations/', views.crm_conversations, name='conversations'),
    path('conversations/ajax/', views.crm_conversations_ajax, name='conversations_ajax'),
    path('conversations/create/', views.crm_create_conversation, name='create_conversation'),
    path('conversations/<int:conv_id>/delete/', views.crm_delete_conversation, name='delete_conversation'),
    path('conversations/<int:conv_id>/send/', views.crm_send_message, name='send_message'),
    
    path('social/', views.crm_social_posts, name='social_posts'),
    path('social/action/<int:comment_id>/', views.crm_social_action, name='social_action'),
    path('social/post-action/<int:post_id>/', views.crm_social_post_action, name='social_post_action'),
    path('chatbot/', views.crm_chatbot, name='chatbot'),
    path('chatbot/toggle/', views.crm_chatbot_toggle, name='chatbot_toggle'),
    path('quick-replies/', views.crm_quick_replies, name='quick_replies'),
    path('quick-replies/create/', views.crm_quick_reply_create, name='quick_reply_create'),
    path('quick-replies/<int:pk>/delete/', views.crm_quick_reply_delete, name='quick_reply_delete'),
    path('integrations/', views.crm_integrations, name='integrations'),
    path('integrations/facebook/connect/', views.connect_facebook, name='facebook_connect'),
    path('integrations/facebook/callback/', views.facebook_callback, name='facebook_callback'),
    path('integrations/instagram/connect/', views.connect_instagram, name='instagram_connect'),
    path('integrations/instagram/callback/', views.instagram_callback, name='instagram_callback'),
    path('integrations/tiktok/connect/', views.connect_tiktok, name='tiktok_connect'),
    path('integrations/tiktok/callback/', views.tiktok_callback, name='tiktok_callback'),
    path('integrations/meta/webhook/', views.meta_webhook, name='meta_webhook'),
    path('integrations/<str:channel_key>/connect/', views.crm_integration_connect, name='integration_connect'),
    path('integrations/<int:pk>/disconnect/', views.crm_integration_disconnect, name='integration_disconnect'),
    path('contacts/', views.crm_contacts, name='contacts'),
    path('analytics/', views.crm_analytics, name='analytics'),
    path('tickets/', views.crm_tickets, name='tickets'),
]
