from django.urls import path

from . import views

app_name = 'resources'

urlpatterns = [
    path('', views.resource_list, name='list'),
    path('create/', views.resource_create, name='create'),
    path('<int:pk>/', views.resource_detail, name='detail'),
    path('<int:pk>/edit/', views.resource_edit, name='edit'),
    path('<int:pk>/delete/', views.resource_delete, name='delete'),
    path('<int:pk>/mark-read/', views.resource_mark_read, name='mark_read'),
    path('<int:pk>/file/<int:file_id>/delete/', views.resource_file_delete, name='file_delete'),

    path('topics/create/', views.topic_create, name='topic_create'),
    path('topics/<int:pk>/edit/', views.topic_edit, name='topic_edit'),
    path('topics/<int:pk>/delete/', views.topic_delete, name='topic_delete'),
]
