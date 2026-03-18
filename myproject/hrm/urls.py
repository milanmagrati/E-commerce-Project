from django.urls import path
from . import views

app_name = 'hrm'

urlpatterns = [
    # HRM Dashboard
    path('', views.hrm_dashboard, name='dashboard'),

    # HR Management
    path('branches/', views.branch_list, name='branch_list'),
    path('branches/create/', views.branch_create, name='branch_create'),
    path('branches/<int:branch_id>/', views.branch_detail, name='branch_detail'),
    path('branches/<int:branch_id>/update/', views.branch_update, name='branch_update'),
    path('branches/<int:branch_id>/delete/', views.branch_delete, name='branch_delete'),
    path('branches/<int:branch_id>/toggle-status/', views.branch_toggle_status, name='branch_toggle_status'),
    path('departments/', views.department_list, name='department_list'),
    path('departments/create/', views.department_create, name='department_create'),
    path('departments/<int:department_id>/', views.department_detail, name='department_detail'),
    path('departments/<int:department_id>/update/', views.department_update, name='department_update'),
    path('departments/<int:department_id>/delete/', views.department_delete, name='department_delete'),
    path('departments/<int:department_id>/toggle-status/', views.department_toggle_status, name='department_toggle_status'),
    path('designations/', views.designation_list, name='designation_list'),
    path('designations/create/', views.designation_create, name='designation_create'),
    path('designations/<int:designation_id>/', views.designation_detail, name='designation_detail'),
    path('designations/<int:designation_id>/update/', views.designation_update, name='designation_update'),
    path('designations/<int:designation_id>/delete/', views.designation_delete, name='designation_delete'),
    path('designations/<int:designation_id>/toggle-status/', views.designation_toggle_status, name='designation_toggle_status'),
    path('document-types/', views.document_type_list, name='document_type_list'),
    path('employees/', views.employee_list, name='employee_list'),
    path('award-types/', views.award_type_list, name='award_type_list'),
    path('awards/', views.award_list, name='award_list'),
    path('promotions/', views.promotion_list, name='promotion_list'),
    path('resignations/', views.resignation_list, name='resignation_list'),
    path('terminations/', views.termination_list, name='termination_list'),
    path('warnings/', views.warning_list, name='warning_list'),
    path('complaints/', views.complaint_list, name='complaint_list'),

    # Asset Management
    path('assets/types/', views.asset_type_list, name='asset_type_list'),
    path('assets/', views.asset_list, name='asset_list'),
    path('assets/dashboard/', views.asset_dashboard, name='asset_dashboard'),
    path('assets/depreciation/', views.asset_depreciation, name='asset_depreciation'),

    # Contract Management
    path('contracts/', views.contract_list, name='contract_list'),

    # Document Management
    path('documents/', views.document_list, name='document_list'),

    # Attendance
    path('attendance/dashboard/', views.attendance_dashboard, name='attendance_dashboard'),
    path('attendance/', views.attendance_list, name='attendance_list'),

    # Biometric Attendance
    path('biometric-attendance/', views.biometric_attendance, name='biometric_attendance'),

    # Payroll Management
    path('payroll/', views.payroll_management, name='payroll_management'),
]
