from django.urls import path
from . import views

app_name = 'todo'

urlpatterns = [
    path('', views.task_board, name='board'),
    path('task/create/', views.task_create, name='task_create'),
    path('task/<int:task_id>/', views.task_detail, name='task_detail'),
    path('task/<int:task_id>/edit/', views.task_edit, name='task_edit'),
    path('task/<int:task_id>/delete/', views.task_delete, name='task_delete'),
    path('api/task/<int:task_id>/status/', views.task_update_status, name='update_status'),
]
