import json
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import JsonResponse
from django.db.models import Q
from django.core.paginator import Paginator

from .models import Branch, Department, Designation


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
def branch_list(request):
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')

    branches = Branch.objects.all()

    if search_query:
        branches = branches.filter(
            Q(name__icontains=search_query) |
            Q(address__icontains=search_query) |
            Q(city__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(email__icontains=search_query)
        )

    paginator = Paginator(branches, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Branches',
        'branches': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'total_branches': paginator.count,
    }
    return render(request, 'hrm/branch_list.html', context)


@login_required
def branch_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Branch name is required.'})

        branch = Branch(
            name=name,
            address=request.POST.get('address', '').strip(),
            city=request.POST.get('city', '').strip(),
            state=request.POST.get('state', '').strip(),
            country=request.POST.get('country', '').strip(),
            zip_code=request.POST.get('zip_code', '').strip(),
            phone=request.POST.get('phone', '').strip(),
            email=request.POST.get('email', '').strip(),
            status=request.POST.get('status', 'active'),
        )
        branch.save()
        return JsonResponse({'success': True, 'message': f'Branch "{branch.name}" created successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def branch_detail(request, branch_id):
    branch = get_object_or_404(Branch, id=branch_id)
    return JsonResponse({
        'success': True,
        'branch': {
            'id': branch.id,
            'name': branch.name,
            'address': branch.address,
            'city': branch.city,
            'state': branch.state,
            'country': branch.country,
            'zip_code': branch.zip_code,
            'phone': branch.phone,
            'email': branch.email,
            'status': branch.status,
            'created_at': branch.created_at.strftime('%Y-%m-%d'),
            'updated_at': branch.updated_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def branch_update(request, branch_id):
    branch = get_object_or_404(Branch, id=branch_id)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Branch name is required.'})

        branch.name = name
        branch.address = request.POST.get('address', '').strip()
        branch.city = request.POST.get('city', '').strip()
        branch.state = request.POST.get('state', '').strip()
        branch.country = request.POST.get('country', '').strip()
        branch.zip_code = request.POST.get('zip_code', '').strip()
        branch.phone = request.POST.get('phone', '').strip()
        branch.email = request.POST.get('email', '').strip()
        branch.status = request.POST.get('status', branch.status)
        branch.save()
        return JsonResponse({'success': True, 'message': f'Branch "{branch.name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def branch_delete(request, branch_id):
    branch = get_object_or_404(Branch, id=branch_id)

    if request.method == 'POST':
        branch_name = branch.name
        branch.delete()
        return JsonResponse({'success': True, 'message': f'Branch "{branch_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def branch_toggle_status(request, branch_id):
    branch = get_object_or_404(Branch, id=branch_id)

    if request.method == 'POST':
        branch.status = 'inactive' if branch.status == 'active' else 'active'
        branch.save()
        return JsonResponse({'success': True, 'message': f'Branch "{branch.name}" is now {branch.get_status_display()}.'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def department_list(request):
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')
    status_filter = request.GET.get('status', '')
    branch_filter = request.GET.get('branch', '')

    departments = Department.objects.select_related('branch').all()

    if search_query:
        departments = departments.filter(
            Q(name__icontains=search_query) |
            Q(branch__name__icontains=search_query) |
            Q(description__icontains=search_query)
        )

    if status_filter:
        departments = departments.filter(status=status_filter)

    if branch_filter:
        departments = departments.filter(branch_id=branch_filter)

    paginator = Paginator(departments, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Departments',
        'departments': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'status_filter': status_filter,
        'branch_filter': branch_filter,
        'total_departments': paginator.count,
        'branches': Branch.objects.filter(status='active').order_by('name'),
    }
    return render(request, 'hrm/department_list.html', context)


@login_required
def department_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        branch_id = request.POST.get('branch', '').strip()

        if not name:
            return JsonResponse({'success': False, 'error': 'Department name is required.'})
        if not branch_id:
            return JsonResponse({'success': False, 'error': 'Branch is required.'})

        branch = get_object_or_404(Branch, id=branch_id)
        department = Department(
            name=name,
            branch=branch,
            description=request.POST.get('description', '').strip(),
            status=request.POST.get('status', 'active'),
        )
        department.save()
        return JsonResponse({'success': True, 'message': f'Department "{department.name}" created successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def department_detail(request, department_id):
    department = get_object_or_404(Department.objects.select_related('branch'), id=department_id)
    return JsonResponse({
        'success': True,
        'department': {
            'id': department.id,
            'name': department.name,
            'branch_id': department.branch.id,
            'branch_name': department.branch.name,
            'description': department.description,
            'status': department.status,
            'created_at': department.created_at.strftime('%Y-%m-%d'),
            'updated_at': department.updated_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def department_update(request, department_id):
    department = get_object_or_404(Department, id=department_id)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        branch_id = request.POST.get('branch', '').strip()

        if not name:
            return JsonResponse({'success': False, 'error': 'Department name is required.'})
        if not branch_id:
            return JsonResponse({'success': False, 'error': 'Branch is required.'})

        branch = get_object_or_404(Branch, id=branch_id)
        department.name = name
        department.branch = branch
        department.description = request.POST.get('description', '').strip()
        department.status = request.POST.get('status', department.status)
        department.save()
        return JsonResponse({'success': True, 'message': f'Department "{department.name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def department_delete(request, department_id):
    department = get_object_or_404(Department, id=department_id)

    if request.method == 'POST':
        dept_name = department.name
        department.delete()
        return JsonResponse({'success': True, 'message': f'Department "{dept_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def department_toggle_status(request, department_id):
    department = get_object_or_404(Department, id=department_id)

    if request.method == 'POST':
        department.status = 'inactive' if department.status == 'active' else 'active'
        department.save()
        return JsonResponse({'success': True, 'message': f'Department "{department.name}" is now {department.get_status_display()}.'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def designation_list(request):
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')
    status_filter = request.GET.get('status', '')
    department_filter = request.GET.get('department', '')

    designations = Designation.objects.select_related('department', 'department__branch').all()

    if search_query:
        designations = designations.filter(
            Q(name__icontains=search_query) |
            Q(department__name__icontains=search_query) |
            Q(description__icontains=search_query)
        )

    if status_filter:
        designations = designations.filter(status=status_filter)

    if department_filter:
        designations = designations.filter(department_id=department_filter)

    paginator = Paginator(designations, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Designations',
        'designations': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'status_filter': status_filter,
        'department_filter': department_filter,
        'total_designations': paginator.count,
        'departments': Department.objects.filter(status='active').select_related('branch').order_by('name'),
    }
    return render(request, 'hrm/designation_list.html', context)


@login_required
def designation_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        department_id = request.POST.get('department', '').strip()

        if not name:
            return JsonResponse({'success': False, 'error': 'Designation name is required.'})
        if not department_id:
            return JsonResponse({'success': False, 'error': 'Department is required.'})

        department = get_object_or_404(Department, id=department_id)
        designation = Designation(
            name=name,
            department=department,
            description=request.POST.get('description', '').strip(),
            status=request.POST.get('status', 'active'),
        )
        designation.save()
        return JsonResponse({'success': True, 'message': f'Designation "{designation.name}" created successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def designation_detail(request, designation_id):
    designation = get_object_or_404(Designation.objects.select_related('department', 'department__branch'), id=designation_id)
    return JsonResponse({
        'success': True,
        'designation': {
            'id': designation.id,
            'name': designation.name,
            'department_id': designation.department.id,
            'department_name': designation.department.name,
            'branch_name': designation.department.branch.name,
            'description': designation.description,
            'status': designation.status,
            'created_at': designation.created_at.strftime('%Y-%m-%d'),
            'updated_at': designation.updated_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def designation_update(request, designation_id):
    designation = get_object_or_404(Designation, id=designation_id)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        department_id = request.POST.get('department', '').strip()

        if not name:
            return JsonResponse({'success': False, 'error': 'Designation name is required.'})
        if not department_id:
            return JsonResponse({'success': False, 'error': 'Department is required.'})

        department = get_object_or_404(Department, id=department_id)
        designation.name = name
        designation.department = department
        designation.description = request.POST.get('description', '').strip()
        designation.status = request.POST.get('status', designation.status)
        designation.save()
        return JsonResponse({'success': True, 'message': f'Designation "{designation.name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def designation_delete(request, designation_id):
    designation = get_object_or_404(Designation, id=designation_id)

    if request.method == 'POST':
        desig_name = designation.name
        designation.delete()
        return JsonResponse({'success': True, 'message': f'Designation "{desig_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def designation_toggle_status(request, designation_id):
    designation = get_object_or_404(Designation, id=designation_id)

    if request.method == 'POST':
        designation.status = 'inactive' if designation.status == 'active' else 'active'
        designation.save()
        return JsonResponse({'success': True, 'message': f'Designation "{designation.name}" is now {designation.get_status_display()}.'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


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



