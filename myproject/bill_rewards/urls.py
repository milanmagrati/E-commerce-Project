from django.urls import path
from . import views

app_name = 'bill_rewards'

urlpatterns = [
    # Dashboard
    path('', views.bill_rewards_dashboard, name='dashboard'),

    # Upload
    path('upload/', views.bill_upload, name='bill_upload'),

    # Bill list & detail
    path('bills/', views.bill_list, name='bill_list'),
    path('bills/<uuid:bill_id>/', views.bill_detail, name='bill_detail'),

    # Admin review actions
    path('bills/<uuid:bill_id>/approve/', views.bill_approve, name='bill_approve'),
    path('bills/<uuid:bill_id>/reject/', views.bill_reject, name='bill_reject'),
    path('bills/<uuid:bill_id>/reprocess/', views.bill_reprocess, name='bill_reprocess'),

    # Line item product matching
    path('line-item/<int:item_id>/set-product/', views.line_item_set_product, name='line_item_set_product'),

    # Product aliases
    path('aliases/', views.alias_list, name='alias_list'),
    path('aliases/<int:alias_id>/delete/', views.alias_delete, name='alias_delete'),

    # Rewards
    path('rewards/', views.reward_transactions, name='reward_transactions'),
    path('rewards/config/', views.reward_config_view, name='reward_config'),

    # Analytics / Trending
    path('trending/', views.trending_analytics, name='trending_analytics'),

    # API endpoints
    path('api/bill/<uuid:bill_id>/status/', views.api_bill_status, name='api_bill_status'),
    path('api/search-products/', views.api_search_products_for_match, name='api_search_products'),
    path('api/bill/<uuid:bill_id>/update-field/', views.api_bill_update_field, name='api_bill_update_field'),
]
