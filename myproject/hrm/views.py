from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def hrm_dashboard(request):
    context = {
        'page_title': 'HRM Dashboard',
    }
    return render(request, 'hrm/dashboard.html', context)


# ==================== HR Management ====================

@login_required
def department_list(request):
    context = {
        'page_title': 'Departments',
    }
    return render(request, 'hrm/department_list.html', context)


@login_required
def designation_list(request):
    context = {
        'page_title': 'Designations',
    }
    return render(request, 'hrm/designation_list.html', context)


@login_required
def document_type_list(request):
    context = {
        'page_title': 'Document Types',
    }
    return render(request, 'hrm/document_type_list.html', context)


@login_required
def employee_list(request):
    context = {
        'page_title': 'Employees',
    }
    return render(request, 'hrm/employee_list.html', context)


@login_required
def award_type_list(request):
    context = {
        'page_title': 'Award Types',
    }
    return render(request, 'hrm/award_type_list.html', context)


@login_required
def award_list(request):
    context = {
        'page_title': 'Awards',
    }
    return render(request, 'hrm/award_list.html', context)


@login_required
def promotion_list(request):
    context = {
        'page_title': 'Promotions',
    }
    return render(request, 'hrm/promotion_list.html', context)


@login_required
def resignation_list(request):
    context = {
        'page_title': 'Resignations',
    }
    return render(request, 'hrm/resignation_list.html', context)


@login_required
def termination_list(request):
    context = {
        'page_title': 'Terminations',
    }
    return render(request, 'hrm/termination_list.html', context)


@login_required
def warning_list(request):
    context = {
        'page_title': 'Warnings',
    }
    return render(request, 'hrm/warning_list.html', context)


@login_required
def complaint_list(request):
    context = {
        'page_title': 'Complaints',
    }
    return render(request, 'hrm/complaint_list.html', context)


# ==================== Asset Management ====================

@login_required
def asset_type_list(request):
    context = {
        'page_title': 'Asset Types',
    }
    return render(request, 'hrm/asset_type_list.html', context)


@login_required
def asset_list(request):
    context = {
        'page_title': 'Assets',
    }
    return render(request, 'hrm/asset_list.html', context)


@login_required
def asset_dashboard(request):
    context = {
        'page_title': 'Asset Dashboard',
    }
    return render(request, 'hrm/asset_dashboard.html', context)


@login_required
def asset_depreciation(request):
    context = {
        'page_title': 'Asset Depreciation',
    }
    return render(request, 'hrm/asset_depreciation.html', context)


# ==================== Contract Management ====================

@login_required
def contract_list(request):
    context = {
        'page_title': 'Contract Management',
    }
    return render(request, 'hrm/contract_list.html', context)


# ==================== Document Management ====================

@login_required
def document_list(request):
    context = {
        'page_title': 'Document Management',
    }
    return render(request, 'hrm/document_list.html', context)


# ==================== Attendance ====================

@login_required
def attendance_dashboard(request):
    context = {
        'page_title': 'Attendance Dashboard',
    }
    return render(request, 'hrm/attendance_dashboard.html', context)


@login_required
def attendance_list(request):
    context = {
        'page_title': 'Attendance',
    }
    return render(request, 'hrm/attendance_list.html', context)


# ==================== Biometric Attendance ====================

@login_required
def biometric_attendance(request):
    context = {
        'page_title': 'Biometric Attendance',
    }
    return render(request, 'hrm/biometric_attendance.html', context)


# ==================== Payroll Management ====================

@login_required
def payroll_management(request):
    context = {
        'page_title': 'Payroll Management',
    }
    return render(request, 'hrm/payroll_management.html', context)
