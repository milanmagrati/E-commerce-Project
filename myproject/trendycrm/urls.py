from django.urls import path
from . import views

app_name = 'trendycrm'

urlpatterns = [
    path('', views.crm_home, name='home'),
    path('conversations/', views.crm_conversations, name='conversations'),
    path('conversations/ajax/', views.crm_conversations_ajax, name='conversations_ajax'),
    path('conversations/list-ajax/', views.crm_conversations_list_ajax, name='conversations_list_ajax'),
    path('conversations/create/', views.crm_create_conversation, name='create_conversation'),
    path('conversations/<int:conv_id>/delete/', views.crm_delete_conversation, name='delete_conversation'),
    path('conversations/<int:conv_id>/send/', views.crm_send_message, name='send_message'),
    
    # Conversation Sidebar Endpoints
    path('conversations/<int:conv_id>/link-contact/', views.crm_link_contact, name='link_contact'),
    path('conversations/<int:conv_id>/add-label/', views.crm_add_label, name='add_label'),
    path('conversations/<int:conv_id>/remove-label/', views.crm_remove_label, name='remove_label'),
    path('conversations/<int:conv_id>/add-note/', views.crm_add_note, name='add_note'),
    path('conversations/<int:conv_id>/assign/', views.crm_assign_conversation, name='assign_conversation'),
    path('conversations/<int:conv_id>/resolve/', views.crm_resolve_conversation, name='resolve_conversation'),
    path('conversations/<int:conv_id>/toggle-ai/', views.crm_toggle_conversation_ai, name='toggle_conversation_ai'),
    path('conversations/<int:conv_id>/take-over/', views.crm_take_over_conversation, name='take_over_conversation'),

    # AI failure alerts inside a thread — retry the reply the bot missed, or
    # dismiss the alert once a human has handled it.
    path('messages/<int:msg_id>/retry-ai/', views.crm_retry_ai_reply, name='retry_ai_reply'),
    path('messages/<int:msg_id>/dismiss-alert/', views.crm_dismiss_ai_alert, name='dismiss_ai_alert'),

    # Cross-page poller for unresolved lead/complaint chips (see base_crm.html).
    path('alerts/poll/', views.crm_important_alerts_poll, name='important_alerts_poll'),

    path('labels/create/', views.crm_create_label_global, name='create_label_global'),

    path('social/', views.crm_social_posts, name='social_posts'),
    path('social/action/<int:comment_id>/', views.crm_social_action, name='social_action'),
    path('social/post-action/<int:post_id>/', views.crm_social_post_action, name='social_post_action'),
    path('chatbots/', views.crm_chatbot_list, name='chatbot_list'),
    path('chatbot/create/', views.crm_chatbot_create, name='chatbot_create'),
    path('chatbot/<int:bot_id>/', views.crm_chatbot, name='chatbot'),
    path('chatbot/<int:bot_id>/delete/', views.crm_chatbot_delete, name='chatbot_delete'),
    path('chatbot/<int:bot_id>/toggle/', views.crm_chatbot_toggle, name='chatbot_toggle'),
    path('chatbot/<int:bot_id>/save-knowledge/', views.crm_chatbot_save_knowledge, name='chatbot_save_knowledge'),
    path('chatbot/<int:bot_id>/save-agent/', views.crm_chatbot_save_agent, name='chatbot_save_agent'),
    path('chatbot/<int:bot_id>/save-triage/', views.crm_chatbot_save_triage, name='chatbot_save_triage'),
    path('chatbot/<int:bot_id>/triage-preview/', views.crm_triage_preview, name='triage_preview'),
    path('chatbot/<int:bot_id>/toggle-channel/', views.crm_chatbot_toggle_channel, name='chatbot_toggle_channel'),
    path('chatbot/<int:bot_id>/credit-history/', views.crm_credit_history, name='credit_history'),
    path('quick-replies/', views.crm_quick_replies, name='quick_replies'),
    path('quick-replies/create/', views.crm_quick_reply_create, name='quick_reply_create'),
    path('quick-replies/search/', views.crm_quick_replies_search, name='quick_replies_search'),
    path('quick-replies/<int:pk>/edit/', views.crm_quick_reply_edit, name='quick_reply_edit'),
    path('quick-replies/<int:pk>/delete/', views.crm_quick_reply_delete, name='quick_reply_delete'),

    path('integrations/', views.crm_integrations, name='integrations'),
    path('integrations/facebook/connect/', views.connect_facebook, name='facebook_connect'),
    path('integrations/facebook/callback/', views.facebook_callback, name='facebook_callback'),
    path('integrations/instagram/connect/', views.connect_instagram, name='instagram_connect'),
    path('integrations/instagram/callback/', views.instagram_callback, name='instagram_callback'),
    path('integrations/whatsapp/connect/', views.connect_whatsapp, name='whatsapp_connect'),
    path('integrations/whatsapp/callback/', views.whatsapp_callback, name='whatsapp_callback'),
    path('integrations/tiktok/connect/', views.connect_tiktok, name='tiktok_connect'),
    path('integrations/tiktok/callback/', views.tiktok_callback, name='tiktok_callback'),
    path('integrations/meta/webhook/', views.meta_webhook, name='meta_webhook'),
    path('integrations/<str:channel_key>/connect/', views.crm_integration_connect, name='integration_connect'),
    path('integrations/<int:pk>/disconnect/', views.crm_integration_disconnect, name='integration_disconnect'),
    path('contacts/', views.crm_contacts, name='contacts'),
    path('analytics/', views.crm_analytics, name='analytics'),
    path('tickets/', views.crm_tickets, name='tickets'),

    # Page Profiles — Centralized Knowledge Core
    path('page-profiles/', views.crm_page_profiles, name='page_profiles'),
    path('page-profiles/<int:integration_id>/save/', views.crm_page_profile_save, name='page_profile_save'),

    # AI Test endpoint (Dashboard use only)
    path('ai/test/', views.crm_ai_test, name='ai_test'),

    # Comment Automations (ManyChat-style)
    path('comment-automations/', views.crm_comment_automations, name='comment_automations'),
    path('comment-automations/save/', views.crm_comment_automation_save, name='comment_automation_save'),
    path('comment-automations/<int:pk>/delete/', views.crm_comment_automation_delete, name='comment_automation_delete'),
    path('comment-automations/<int:pk>/toggle/', views.crm_comment_automation_toggle, name='comment_automation_toggle'),
    path('comment-automations/<int:pk>/get/', views.crm_comment_automation_get, name='comment_automation_get'),
    # Fire automation manually on an existing comment
    path('social/comment/<int:comment_id>/fire-automation/', views.crm_fire_automation_on_comment, name='fire_automation_on_comment'),
]

