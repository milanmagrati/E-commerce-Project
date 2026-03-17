from django.urls import path
from . import views

app_name = 'hrm'

urlpatterns = [
    # HRM Dashboard
    path('', views.hrm_dashboard, name='dashboard'),

    # HR Management
    path('departments/', views.department_list, name='department_list'),
    path('designations/', views.designation_list, name='designation_list'),
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
