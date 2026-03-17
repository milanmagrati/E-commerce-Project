import json
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils import timezone


@login_required
def hrm_dashboard(request):
    now = timezone.now()

    # -- Stats cards data --
    stats = {
        'total_employees': 10,
        'employees_this_month': 10,
        'branches': 9,
        'departments': 24,
        'attendance_rate': 85.5,
        'present_today': 45,
        'pending_leaves': 0,
        'on_leave_today': 0,
        'active_jobs': 11,
        'jobs_this_month': 15,
        'total_candidates': 31,
        'candidates_this_month': 31,
    }

    # -- Chart data --
    department_distribution = {
        'labels': ['Engineering', 'Marketing', 'HR', 'Finance', 'Operations', 'Sales'],
        'data': [8, 5, 4, 3, 6, 5],
        'colors': ['#6366f1', '#f97316', '#ec4899', '#ef4444', '#3b82f6', '#10b981'],
    }

    hiring_trend = {
        'labels': ['Oct 2025', 'Nov 2025', 'Dec 2025', 'Jan 2026', 'Feb 2026', 'Mar 2026'],
        'data': [8, 12, 10, 14, 18, 14],
    }

    candidate_status = {
        'labels': ['Interview', 'New', 'Offer', 'Screening'],
        'data': [8, 5, 14, 4],
        'colors': ['#8b5cf6', '#3b82f6', '#f97316', '#06b6d4'],
    }

    leave_types = {
        'labels': ['Annual Leave', 'Bereavement Leave', 'Compensatory Leave', 'Emergency Leave',
                    'Marriage Leave', 'Maternity Leave', 'Paternity Leave', 'Personal Leave',
                    'Sick Leave', 'Study Leave'],
        'data': [15, 2, 5, 3, 1, 8, 3, 4, 12, 2],
        'colors': ['#10b981', '#6b7280', '#06b6d4', '#f97316', '#ef4444',
                    '#ec4899', '#3b82f6', '#22c55e', '#ef4444', '#8b5cf6'],
    }

    employee_growth = {
        'labels': ['January', 'February', 'March', 'April', 'May', 'June',
                    'July', 'August', 'September', 'October', 'November', 'December'],
        'data': [15, 5, 22, 10, 28, 31, 35, 50, 42, 45, 47, 52],
    }

    # -- Recent leave applications --
    recent_leaves = [
        {'employee': 'Amie Jerde', 'status': 'Approved', 'leave_type': 'Sick Leave', 'start_date': '2026-03-10', 'end_date': '2026-03-12'},
        {'employee': 'Amie Jerde', 'status': 'Approved', 'leave_type': 'Personal Leave', 'start_date': '2026-03-05', 'end_date': '2026-03-06'},
        {'employee': 'Amie Jerde', 'status': 'Approved', 'leave_type': 'Annual Leave', 'start_date': '2026-02-20', 'end_date': '2026-02-25'},
        {'employee': 'Amie Jerde', 'status': 'Approved', 'leave_type': 'Annual Leave', 'start_date': '2026-02-10', 'end_date': '2026-02-15'},
        {'employee': 'Amie Jerde', 'status': 'Approved', 'leave_type': 'Annual Leave', 'start_date': '2026-01-15', 'end_date': '2026-01-20'},
    ]

    # -- Recent candidates --
    recent_candidates = [
        {'name': 'Geeta Devi', 'status': 'Offer', 'position': 'Senior Software Engineer', 'date': '2026-03-15'},
        {'name': 'Nisha Agarwal', 'status': 'Interview', 'position': 'Content Writer', 'date': '2026-03-14'},
        {'name': 'Ramesh Babu', 'status': 'Screening', 'position': 'Network Administrator', 'date': '2026-03-13'},
        {'name': 'Tarun Malhotra', 'status': 'Offer', 'position': 'Customer Support Representative', 'date': '2026-03-12'},
    ]

    # -- Recent announcements --
    recent_announcements = [
        {'title': 'Updated Employee Handbook and Policies', 'priority': 'High', 'category': 'Policy Updates', 'date': '2026-03-15'},
        {'title': 'Annual Performance Review Process', 'priority': 'High', 'category': 'HR Updates', 'date': '2026-03-14'},
        {'title': 'New Employee Benefits Program Launch', 'priority': 'Medium', 'category': 'Benefits', 'date': '2026-03-13'},
        {'title': 'IT Department System Maintenance', 'priority': 'Low', 'category': 'IT Updates', 'date': '2026-03-12'},
        {'title': 'Company Town Hall Meeting', 'priority': 'Medium', 'category': 'Events', 'date': '2026-03-10'},
    ]

    # -- Recent meetings --
    recent_meetings = [
        {'title': 'Daily Scrum Meeting', 'status': 'Scheduled', 'date': '2026-03-17'},
        {'title': 'Daily Scrum Meeting', 'status': 'Scheduled', 'date': '2026-03-16'},
        {'title': 'Daily Scrum Meeting', 'status': 'Scheduled', 'date': '2026-03-15'},
        {'title': 'Daily Scrum Meeting', 'status': 'Scheduled', 'date': '2026-03-14'},
        {'title': 'Weekly Sprint Review', 'status': 'Completed', 'date': '2026-03-13'},
    ]

    context = {
        'page_title': 'Dashboard',
        'stats': stats,
        'department_distribution': json.dumps(department_distribution),
        'hiring_trend': json.dumps(hiring_trend),
        'candidate_status': json.dumps(candidate_status),
        'leave_types': json.dumps(leave_types),
        'employee_growth': json.dumps(employee_growth),
        'recent_leaves': recent_leaves,
        'total_leaves': 60,
        'recent_candidates': recent_candidates,
        'total_candidates_list': 5,
        'recent_announcements': recent_announcements,
        'total_announcements': 5,
        'recent_meetings': recent_meetings,
        'total_meetings': 5,
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



