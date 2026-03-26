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
    path('document-types/create/', views.document_type_create, name='document_type_create'),
    path('document-types/<int:pk>/', views.document_type_detail, name='document_type_detail'),
    path('document-types/<int:pk>/update/', views.document_type_update, name='document_type_update'),
    path('document-types/<int:pk>/delete/', views.document_type_delete, name='document_type_delete'),
    path('document-types/<int:pk>/toggle-status/', views.document_type_toggle_status, name='document_type_toggle_status'),
    path('employees/', views.employee_list, name='employee_list'),
    path('employees/create/', views.employee_create, name='employee_create'),
    path('employees/<int:employee_id>/', views.employee_detail, name='employee_detail'),
    path('employees/<int:employee_id>/edit/', views.employee_edit, name='employee_edit'),
    path('employees/<int:employee_id>/delete/', views.employee_delete, name='employee_delete'),
    path('employees/<int:employee_id>/toggle-status/', views.employee_toggle_status, name='employee_toggle_status'),
    path('employees/document/<int:doc_id>/delete/', views.employee_document_delete, name='employee_document_delete'),
    path('api/departments-by-branch/', views.get_departments_by_branch, name='get_departments_by_branch'),
    path('api/designations-by-department/', views.get_designations_by_department, name='get_designations_by_department'),
    path('award-types/', views.award_type_list, name='award_type_list'),
    path('award-types/create/', views.award_type_create, name='award_type_create'),
    path('award-types/<int:pk>/', views.award_type_detail, name='award_type_detail'),
    path('award-types/<int:pk>/update/', views.award_type_update, name='award_type_update'),
    path('award-types/<int:pk>/delete/', views.award_type_delete, name='award_type_delete'),
    path('award-types/<int:pk>/toggle-status/', views.award_type_toggle_status, name='award_type_toggle_status'),
    path('awards/', views.award_list, name='award_list'),
    path('awards/create/', views.award_create, name='award_create'),
    path('awards/<int:pk>/', views.award_detail, name='award_detail'),
    path('awards/<int:pk>/update/', views.award_update, name='award_update'),
    path('awards/<int:pk>/delete/', views.award_delete, name='award_delete'),
    path('promotions/', views.promotion_list, name='promotion_list'),
    path('promotions/create/', views.promotion_create, name='promotion_create'),
    path('promotions/<int:pk>/', views.promotion_detail, name='promotion_detail'),
    path('promotions/<int:pk>/update/', views.promotion_update, name='promotion_update'),
    path('promotions/<int:pk>/delete/', views.promotion_delete, name='promotion_delete'),
    path('promotions/<int:pk>/toggle-status/', views.promotion_toggle_status, name='promotion_toggle_status'),
    path('api/employee-designation/', views.get_employee_designation, name='get_employee_designation'),
    path('resignations/', views.resignation_list, name='resignation_list'),
    path('resignations/create/', views.resignation_create, name='resignation_create'),
    path('resignations/<int:pk>/', views.resignation_detail, name='resignation_detail'),
    path('resignations/<int:pk>/update/', views.resignation_update, name='resignation_update'),
    path('resignations/<int:pk>/delete/', views.resignation_delete, name='resignation_delete'),
    path('resignations/<int:pk>/toggle-status/', views.resignation_toggle_status, name='resignation_toggle_status'),
    path('terminations/', views.termination_list, name='termination_list'),
    path('terminations/create/', views.termination_create, name='termination_create'),
    path('terminations/<int:pk>/', views.termination_detail, name='termination_detail'),
    path('terminations/<int:pk>/update/', views.termination_update, name='termination_update'),
    path('terminations/<int:pk>/delete/', views.termination_delete, name='termination_delete'),
    path('terminations/<int:pk>/toggle-status/', views.termination_toggle_status, name='termination_toggle_status'),
    path('warnings/', views.warning_list, name='warning_list'),
    path('warnings/create/', views.warning_create, name='warning_create'),
    path('warnings/<int:pk>/', views.warning_detail, name='warning_detail'),
    path('warnings/<int:pk>/update/', views.warning_update, name='warning_update'),
    path('warnings/<int:pk>/delete/', views.warning_delete, name='warning_delete'),
    path('warnings/<int:pk>/toggle-status/', views.warning_toggle_status, name='warning_toggle_status'),
    path('complaints/', views.complaint_list, name='complaint_list'),
    path('complaints/create/', views.complaint_create, name='complaint_create'),
    path('complaints/<int:pk>/', views.complaint_detail, name='complaint_detail'),
    path('complaints/<int:pk>/update/', views.complaint_update, name='complaint_update'),
    path('complaints/<int:pk>/delete/', views.complaint_delete, name='complaint_delete'),
    path('complaints/<int:pk>/toggle-status/', views.complaint_toggle_status, name='complaint_toggle_status'),

    # Asset Management
    path('assets/types/', views.asset_type_list, name='asset_type_list'),
    path('assets/types/create/', views.asset_type_create, name='asset_type_create'),
    path('assets/types/<int:pk>/', views.asset_type_detail, name='asset_type_detail'),
    path('assets/types/<int:pk>/update/', views.asset_type_update, name='asset_type_update'),
    path('assets/types/<int:pk>/delete/', views.asset_type_delete, name='asset_type_delete'),
    path('assets/types/<int:pk>/toggle-status/', views.asset_type_toggle_status, name='asset_type_toggle_status'),
    path('assets/', views.asset_list, name='asset_list'),
    path('assets/create/', views.asset_create, name='asset_create'),
    path('assets/<int:pk>/', views.asset_detail, name='asset_detail'),
    path('assets/<int:pk>/update/', views.asset_update, name='asset_update'),
    path('assets/<int:pk>/delete/', views.asset_delete, name='asset_delete'),
    path('assets/<int:pk>/assign/', views.asset_assign, name='asset_assign'),
    path('assets/<int:pk>/checkin/', views.asset_checkin, name='asset_checkin'),
    path('assets/dashboard/', views.asset_dashboard, name='asset_dashboard'),
    path('assets/depreciation/', views.asset_depreciation, name='asset_depreciation'),

    # Contract Management
    path('contracts/', views.contract_list, name='contract_list'),

    # Document Management
    path('documents/', views.document_list, name='document_list'),

    # Attendance Records
    path('attendance/', views.attendance_list, name='attendance_list'),
    path('attendance/create/', views.attendance_create, name='attendance_create'),
    path('attendance/<int:pk>/update/', views.attendance_update, name='attendance_update'),
    path('attendance/<int:pk>/delete/', views.attendance_delete, name='attendance_delete'),

    # Shifts
    path('shifts/', views.shift_list, name='shift_list'),
    path('shifts/create/', views.shift_create, name='shift_create'),
    path('shifts/<int:pk>/update/', views.shift_update, name='shift_update'),
    path('shifts/<int:pk>/delete/', views.shift_delete, name='shift_delete'),
    path('shifts/<int:pk>/toggle-status/', views.shift_toggle_status, name='shift_toggle_status'),

    # Attendance Policies
    path('attendance-policies/', views.attendance_policy_list, name='attendance_policy_list'),
    path('attendance-policies/create/', views.attendance_policy_create, name='attendance_policy_create'),
    path('attendance-policies/<int:pk>/update/', views.attendance_policy_update, name='attendance_policy_update'),
    path('attendance-policies/<int:pk>/delete/', views.attendance_policy_delete, name='attendance_policy_delete'),
    path('attendance-policies/<int:pk>/toggle-status/', views.attendance_policy_toggle_status, name='attendance_policy_toggle_status'),

    # Attendance Regularizations
    path('attendance-regularizations/', views.attendance_regularization_list, name='attendance_regularization_list'),
    path('attendance-regularizations/create/', views.attendance_regularization_create, name='attendance_regularization_create'),
    path('attendance-regularizations/<int:pk>/update/', views.attendance_regularization_update, name='attendance_regularization_update'),
    path('attendance-regularizations/<int:pk>/delete/', views.attendance_regularization_delete, name='attendance_regularization_delete'),
    path('attendance-regularizations/<int:pk>/status/', views.attendance_regularization_update_status, name='attendance_regularization_update_status'),
    path('api/employee-attendance-records/', views.employee_attendance_records_api, name='employee_attendance_records_api'),

    # ZKTeco ADMS Push Endpoints (device pushes data here)
    path('iclock/cdata', views.iclock_cdata, name='iclock_cdata'),
    path('iclock/getrequest', views.iclock_getrequest, name='iclock_getrequest'),
    path('iclock/devicecmd', views.iclock_devicecmd, name='iclock_devicecmd'),

    # Biometric Attendance (admin views)
    path('biometric-attendance/', views.biometric_attendance, name='biometric_attendance'),
    path('biometric-attendance/<str:pin>/<str:date_str>/view/', views.biometric_attendance_view, name='biometric_attendance_view'),
    path('biometric-attendance/<str:pin>/<str:date_str>/delete/', views.biometric_attendance_delete, name='biometric_attendance_delete'),
    path('biometric-attendance/<str:pin>/<str:date_str>/sync/', views.biometric_sync_single, name='biometric_sync_single'),
    path('biometric-attendance/sync-all/', views.biometric_sync_all, name='biometric_sync_all'),

    # Zekto Settings (device dashboard)
    path('settings/zekto/', views.zekto_settings, name='zekto_settings'),
    path('settings/zekto/device/<int:pk>/detail/', views.zekto_device_detail, name='zekto_device_detail'),
    path('settings/zekto/device/<int:pk>/update/', views.zekto_device_update, name='zekto_device_update'),
    path('settings/zekto/device/<int:pk>/delete/', views.zekto_device_delete, name='zekto_device_delete'),
    path('settings/zekto/device/<int:pk>/sync/', views.zekto_device_sync, name='zekto_device_sync'),

    # Payroll Management
    path('payroll/', views.payroll_management, name='payroll_management'),
]
