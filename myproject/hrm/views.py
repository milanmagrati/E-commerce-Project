import json
from datetime import date, timedelta
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, Sum, Count
from django.core.paginator import Paginator
from django.contrib import messages

from .models import Branch, Department, Designation, DocumentType, Employee, EmployeeDocument, AwardType, Award, Promotion, Resignation, Termination, Warning, Complaint, AssetType, Asset
from .forms import EmployeeForm, EmployeeDocumentForm


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
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')
    required_filter = request.GET.get('required', '')
    status_filter = request.GET.get('status', '')
    sort = request.GET.get('sort', '-created_at')

    allowed_sorts = ['name', '-name', 'created_at', '-created_at']
    if sort not in allowed_sorts:
        sort = '-created_at'

    document_types = DocumentType.objects.all()

    if search_query:
        document_types = document_types.filter(
            Q(name__icontains=search_query) |
            Q(description__icontains=search_query)
        )

    if required_filter == 'yes':
        document_types = document_types.filter(is_required=True)
    elif required_filter == 'no':
        document_types = document_types.filter(is_required=False)

    if status_filter == 'active':
        document_types = document_types.filter(is_active=True)
    elif status_filter == 'inactive':
        document_types = document_types.filter(is_active=False)

    document_types = document_types.order_by(sort)

    paginator = Paginator(document_types, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Document Types',
        'document_types': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'required_filter': required_filter,
        'status_filter': status_filter,
        'current_sort': sort,
        'total_document_types': paginator.count,
    }
    return render(request, 'hrm/document_type_list.html', context)


@login_required
def document_type_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Document type name is required.'})

        if DocumentType.objects.filter(name__iexact=name).exists():
            return JsonResponse({'success': False, 'error': f'Document type "{name}" already exists.'})

        doc_type = DocumentType(
            name=name,
            description=request.POST.get('description', '').strip(),
            is_required=request.POST.get('is_required') == 'on',
        )
        doc_type.save()
        return JsonResponse({'success': True, 'message': f'Document type "{doc_type.name}" created successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def document_type_detail(request, pk):
    doc_type = get_object_or_404(DocumentType, id=pk)
    return JsonResponse({
        'success': True,
        'document_type': {
            'id': doc_type.id,
            'name': doc_type.name,
            'description': doc_type.description,
            'is_required': doc_type.is_required,
            'is_active': doc_type.is_active,
            'created_at': doc_type.created_at.strftime('%Y-%m-%d'),
            'updated_at': doc_type.updated_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def document_type_update(request, pk):
    doc_type = get_object_or_404(DocumentType, id=pk)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Document type name is required.'})

        if DocumentType.objects.filter(name__iexact=name).exclude(id=pk).exists():
            return JsonResponse({'success': False, 'error': f'Document type "{name}" already exists.'})

        doc_type.name = name
        doc_type.description = request.POST.get('description', '').strip()
        doc_type.is_required = request.POST.get('is_required') == 'on'
        doc_type.save()
        return JsonResponse({'success': True, 'message': f'Document type "{doc_type.name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def document_type_delete(request, pk):
    doc_type = get_object_or_404(DocumentType, id=pk)

    if request.method == 'POST':
        doc_name = doc_type.name
        doc_type.delete()
        return JsonResponse({'success': True, 'message': f'Document type "{doc_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def document_type_toggle_status(request, pk):
    doc_type = get_object_or_404(DocumentType, id=pk)

    if request.method == 'POST':
        doc_type.is_active = not doc_type.is_active
        doc_type.save()
        status_text = 'Active' if doc_type.is_active else 'Inactive'
        return JsonResponse({'success': True, 'message': f'Document type "{doc_type.name}" is now {status_text}.'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def employee_list(request):
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')
    status_filter = request.GET.get('status', '')
    department_filter = request.GET.get('department', '')
    branch_filter = request.GET.get('branch', '')
    sort = request.GET.get('sort', 'full_name')

    allowed_sorts = ['full_name', '-full_name', 'employee_id', '-employee_id',
                     'date_of_joining', '-date_of_joining', 'created_at', '-created_at']
    if sort not in allowed_sorts:
        sort = 'full_name'

    employees = Employee.objects.select_related('branch', 'department', 'designation').all()

    if search_query:
        employees = employees.filter(
            Q(full_name__icontains=search_query) |
            Q(employee_id__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(department__name__icontains=search_query) |
            Q(designation__name__icontains=search_query)
        )

    if status_filter:
        employees = employees.filter(employee_status=status_filter)

    if department_filter:
        employees = employees.filter(department_id=department_filter)

    if branch_filter:
        employees = employees.filter(branch_id=branch_filter)

    employees = employees.order_by(sort)

    paginator = Paginator(employees, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Employees',
        'employees': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'status_filter': status_filter,
        'department_filter': department_filter,
        'branch_filter': branch_filter,
        'current_sort': sort,
        'total_employees': paginator.count,
        'branches': Branch.objects.filter(status='active').order_by('name'),
        'departments': Department.objects.filter(status='active').order_by('name'),
    }
    return render(request, 'hrm/employee_list.html', context)


@login_required
def employee_create(request):
    if request.method == 'POST':
        form = EmployeeForm(request.POST, request.FILES)
        if form.is_valid():
            employee = form.save()
            # Handle document uploads
            doc_titles = request.POST.getlist('doc_title')
            doc_files = request.FILES.getlist('doc_file')
            for title, file in zip(doc_titles, doc_files):
                if title and file:
                    EmployeeDocument.objects.create(
                        employee=employee,
                        title=title,
                        file=file,
                    )
            messages.success(request, f'Employee "{employee.full_name}" created successfully!')
            return redirect('hrm:employee_list')
    else:
        form = EmployeeForm(initial={'employee_id': Employee.generate_employee_id()})

    context = {
        'page_title': 'Create Employee',
        'form': form,
    }
    return render(request, 'hrm/employee_form.html', context)


@login_required
def employee_detail(request, employee_id):
    employee = get_object_or_404(
        Employee.objects.select_related('branch', 'department', 'designation'),
        id=employee_id
    )
    documents = employee.documents.all()
    context = {
        'page_title': f'Employee: {employee.full_name}',
        'employee': employee,
        'documents': documents,
    }
    return render(request, 'hrm/employee_detail.html', context)


@login_required
def employee_edit(request, employee_id):
    employee = get_object_or_404(Employee, id=employee_id)
    if request.method == 'POST':
        form = EmployeeForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            employee = form.save()
            # Handle new document uploads
            doc_titles = request.POST.getlist('doc_title')
            doc_files = request.FILES.getlist('doc_file')
            for title, file in zip(doc_titles, doc_files):
                if title and file:
                    EmployeeDocument.objects.create(
                        employee=employee,
                        title=title,
                        file=file,
                    )
            messages.success(request, f'Employee "{employee.full_name}" updated successfully!')
            return redirect('hrm:employee_list')
    else:
        form = EmployeeForm(instance=employee)

    documents = employee.documents.all()
    context = {
        'page_title': f'Edit Employee: {employee.full_name}',
        'form': form,
        'employee': employee,
        'documents': documents,
    }
    return render(request, 'hrm/employee_form.html', context)


@login_required
def employee_delete(request, employee_id):
    employee = get_object_or_404(Employee, id=employee_id)
    if request.method == 'POST':
        emp_name = employee.full_name
        employee.delete()
        return JsonResponse({'success': True, 'message': f'Employee "{emp_name}" deleted successfully!'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def employee_toggle_status(request, employee_id):
    employee = get_object_or_404(Employee, id=employee_id)
    if request.method == 'POST':
        employee.employee_status = 'inactive' if employee.employee_status == 'active' else 'active'
        employee.save()
        return JsonResponse({'success': True, 'message': f'Employee "{employee.full_name}" is now {employee.get_employee_status_display()}.'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def employee_document_delete(request, doc_id):
    doc = get_object_or_404(EmployeeDocument, id=doc_id)
    if request.method == 'POST':
        doc.delete()
        return JsonResponse({'success': True, 'message': 'Document deleted successfully!'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def get_departments_by_branch(request):
    branch_id = request.GET.get('branch_id')
    if branch_id:
        departments = Department.objects.filter(branch_id=branch_id, status='active').order_by('name')
        data = [{'id': d.id, 'name': d.name} for d in departments]
        return JsonResponse({'departments': data})
    return JsonResponse({'departments': []})


@login_required
def get_designations_by_department(request):
    department_id = request.GET.get('department_id')
    if department_id:
        designations = Designation.objects.filter(department_id=department_id, status='active').order_by('name')
        data = [{'id': d.id, 'name': d.name} for d in designations]
        return JsonResponse({'designations': data})
    return JsonResponse({'designations': []})


@login_required
def award_type_list(request):
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')
    status_filter = request.GET.get('status', '')

    award_types = AwardType.objects.all()

    if search_query:
        award_types = award_types.filter(
            Q(name__icontains=search_query) |
            Q(description__icontains=search_query)
        )

    if status_filter:
        award_types = award_types.filter(status=status_filter)

    paginator = Paginator(award_types, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Award Types',
        'award_types': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'status_filter': status_filter,
        'total_award_types': paginator.count,
    }
    return render(request, 'hrm/award_type_list.html', context)


@login_required
def award_type_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()

        if not name:
            return JsonResponse({'success': False, 'error': 'Award type name is required.'})

        award_type = AwardType(
            name=name,
            description=request.POST.get('description', '').strip(),
            status=request.POST.get('status', 'active'),
        )
        award_type.save()
        return JsonResponse({'success': True, 'message': f'Award type "{award_type.name}" created successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def award_type_detail(request, pk):
    award_type = get_object_or_404(AwardType, id=pk)
    return JsonResponse({
        'success': True,
        'award_type': {
            'id': award_type.id,
            'name': award_type.name,
            'description': award_type.description,
            'status': award_type.status,
            'created_at': award_type.created_at.strftime('%Y-%m-%d'),
            'updated_at': award_type.updated_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def award_type_update(request, pk):
    award_type = get_object_or_404(AwardType, id=pk)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()

        if not name:
            return JsonResponse({'success': False, 'error': 'Award type name is required.'})

        award_type.name = name
        award_type.description = request.POST.get('description', '').strip()
        award_type.status = request.POST.get('status', award_type.status)
        award_type.save()
        return JsonResponse({'success': True, 'message': f'Award type "{award_type.name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def award_type_delete(request, pk):
    award_type = get_object_or_404(AwardType, id=pk)

    if request.method == 'POST':
        type_name = award_type.name
        award_type.delete()
        return JsonResponse({'success': True, 'message': f'Award type "{type_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def award_type_toggle_status(request, pk):
    award_type = get_object_or_404(AwardType, id=pk)

    if request.method == 'POST':
        award_type.status = 'inactive' if award_type.status == 'active' else 'active'
        award_type.save()
        return JsonResponse({'success': True, 'message': f'Award type "{award_type.name}" is now {award_type.status}.'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def award_list(request):
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')
    award_type_filter = request.GET.get('award_type', '')
    employee_filter = request.GET.get('employee', '')

    awards = Award.objects.select_related('employee', 'award_type').all()

    if search_query:
        awards = awards.filter(
            Q(employee__full_name__icontains=search_query) |
            Q(award_type__name__icontains=search_query) |
            Q(gift__icontains=search_query) |
            Q(description__icontains=search_query)
        )

    if award_type_filter:
        awards = awards.filter(award_type_id=award_type_filter)

    if employee_filter:
        awards = awards.filter(employee_id=employee_filter)

    paginator = Paginator(awards, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Awards',
        'awards': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'award_type_filter': award_type_filter,
        'employee_filter': employee_filter,
        'total_awards': paginator.count,
        'employees': Employee.objects.filter(employee_status='active').order_by('full_name'),
        'award_types': AwardType.objects.filter(status='active').order_by('name'),
    }
    return render(request, 'hrm/award_list.html', context)


@login_required
def award_create(request):
    if request.method == 'POST':
        employee_id = request.POST.get('employee', '').strip()
        award_type_id = request.POST.get('award_type', '').strip()
        date = request.POST.get('date', '').strip()

        if not employee_id:
            return JsonResponse({'success': False, 'error': 'Employee is required.'})
        if not award_type_id:
            return JsonResponse({'success': False, 'error': 'Award type is required.'})
        if not date:
            return JsonResponse({'success': False, 'error': 'Award date is required.'})

        employee = get_object_or_404(Employee, id=employee_id)
        award_type = get_object_or_404(AwardType, id=award_type_id)

        monetary_value = request.POST.get('monetary_value', '').strip()

        award = Award(
            employee=employee,
            award_type=award_type,
            date=date,
            gift=request.POST.get('gift', '').strip(),
            monetary_value=monetary_value if monetary_value else None,
            description=request.POST.get('description', '').strip(),
        )

        if request.FILES.get('certificate'):
            award.certificate = request.FILES['certificate']
        if request.FILES.get('photo'):
            award.photo = request.FILES['photo']

        award.save()
        return JsonResponse({'success': True, 'message': f'Award for "{employee.full_name}" created successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def award_detail(request, pk):
    award = get_object_or_404(Award.objects.select_related('employee', 'award_type'), id=pk)
    return JsonResponse({
        'success': True,
        'award': {
            'id': award.id,
            'employee_id': award.employee.id,
            'employee_name': award.employee.full_name,
            'employee_code': award.employee.employee_id,
            'award_type_id': award.award_type.id,
            'award_type_name': award.award_type.name,
            'date': award.date.strftime('%Y-%m-%d'),
            'gift': award.gift,
            'monetary_value': str(award.monetary_value) if award.monetary_value else '',
            'description': award.description,
            'certificate_url': award.certificate.url if award.certificate else '',
            'photo_url': award.photo.url if award.photo else '',
            'created_at': award.created_at.strftime('%Y-%m-%d'),
            'updated_at': award.updated_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def award_update(request, pk):
    award = get_object_or_404(Award, id=pk)

    if request.method == 'POST':
        employee_id = request.POST.get('employee', '').strip()
        award_type_id = request.POST.get('award_type', '').strip()
        date = request.POST.get('date', '').strip()

        if not employee_id:
            return JsonResponse({'success': False, 'error': 'Employee is required.'})
        if not award_type_id:
            return JsonResponse({'success': False, 'error': 'Award type is required.'})
        if not date:
            return JsonResponse({'success': False, 'error': 'Award date is required.'})

        award.employee = get_object_or_404(Employee, id=employee_id)
        award.award_type = get_object_or_404(AwardType, id=award_type_id)
        award.date = date
        award.gift = request.POST.get('gift', '').strip()
        monetary_value = request.POST.get('monetary_value', '').strip()
        award.monetary_value = monetary_value if monetary_value else None
        award.description = request.POST.get('description', '').strip()

        if request.FILES.get('certificate'):
            award.certificate = request.FILES['certificate']
        if request.FILES.get('photo'):
            award.photo = request.FILES['photo']

        award.save()
        return JsonResponse({'success': True, 'message': f'Award for "{award.employee.full_name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def award_delete(request, pk):
    award = get_object_or_404(Award.objects.select_related('employee'), id=pk)

    if request.method == 'POST':
        emp_name = award.employee.full_name
        award.delete()
        return JsonResponse({'success': True, 'message': f'Award for "{emp_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def promotion_list(request):
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')
    status_filter = request.GET.get('status', '')
    employee_filter = request.GET.get('employee', '')

    promotions = Promotion.objects.select_related('employee', 'new_designation').all()

    if search_query:
        promotions = promotions.filter(
            Q(employee__full_name__icontains=search_query) |
            Q(previous_designation__icontains=search_query) |
            Q(new_designation__name__icontains=search_query) |
            Q(reason__icontains=search_query)
        )

    if status_filter:
        promotions = promotions.filter(status=status_filter)

    if employee_filter:
        promotions = promotions.filter(employee_id=employee_filter)

    paginator = Paginator(promotions, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Promotions',
        'promotions': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'status_filter': status_filter,
        'employee_filter': employee_filter,
        'total_promotions': paginator.count,
        'employees': Employee.objects.filter(employee_status='active').order_by('full_name'),
        'designations': Designation.objects.filter(status='active').order_by('name'),
    }
    return render(request, 'hrm/promotion_list.html', context)


@login_required
def promotion_create(request):
    if request.method == 'POST':
        employee_id = request.POST.get('employee', '').strip()
        previous_designation = request.POST.get('previous_designation', '').strip()
        new_designation_id = request.POST.get('new_designation', '').strip()
        promotion_date = request.POST.get('promotion_date', '').strip()
        effective_date = request.POST.get('effective_date', '').strip()

        if not employee_id:
            return JsonResponse({'success': False, 'error': 'Employee is required.'})
        if not previous_designation:
            return JsonResponse({'success': False, 'error': 'Previous designation is required.'})
        if not new_designation_id:
            return JsonResponse({'success': False, 'error': 'New designation is required.'})
        if not promotion_date:
            return JsonResponse({'success': False, 'error': 'Promotion date is required.'})
        if not effective_date:
            return JsonResponse({'success': False, 'error': 'Effective date is required.'})

        employee = get_object_or_404(Employee, id=employee_id)
        new_designation = get_object_or_404(Designation, id=new_designation_id)

        salary_adjustment = request.POST.get('salary_adjustment', '').strip()

        promotion = Promotion(
            employee=employee,
            previous_designation=previous_designation,
            new_designation=new_designation,
            promotion_date=promotion_date,
            effective_date=effective_date,
            salary_adjustment=salary_adjustment if salary_adjustment else None,
            reason=request.POST.get('reason', '').strip(),
        )

        if request.FILES.get('document'):
            promotion.document = request.FILES['document']

        promotion.save()
        return JsonResponse({'success': True, 'message': f'Promotion for "{employee.full_name}" created successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def promotion_detail(request, pk):
    promotion = get_object_or_404(Promotion.objects.select_related('employee', 'new_designation'), id=pk)
    return JsonResponse({
        'success': True,
        'promotion': {
            'id': promotion.id,
            'employee_id': promotion.employee.id,
            'employee_name': promotion.employee.full_name,
            'employee_code': promotion.employee.employee_id,
            'previous_designation': promotion.previous_designation,
            'new_designation_id': promotion.new_designation.id if promotion.new_designation else '',
            'new_designation_name': promotion.new_designation.name if promotion.new_designation else '',
            'promotion_date': promotion.promotion_date.strftime('%Y-%m-%d'),
            'effective_date': promotion.effective_date.strftime('%Y-%m-%d'),
            'salary_adjustment': str(promotion.salary_adjustment) if promotion.salary_adjustment else '',
            'reason': promotion.reason,
            'status': promotion.status,
            'document_url': promotion.document.url if promotion.document else '',
            'created_at': promotion.created_at.strftime('%Y-%m-%d'),
            'updated_at': promotion.updated_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def promotion_update(request, pk):
    promotion = get_object_or_404(Promotion, id=pk)

    if request.method == 'POST':
        employee_id = request.POST.get('employee', '').strip()
        previous_designation = request.POST.get('previous_designation', '').strip()
        new_designation_id = request.POST.get('new_designation', '').strip()
        promotion_date = request.POST.get('promotion_date', '').strip()
        effective_date = request.POST.get('effective_date', '').strip()

        if not employee_id:
            return JsonResponse({'success': False, 'error': 'Employee is required.'})
        if not previous_designation:
            return JsonResponse({'success': False, 'error': 'Previous designation is required.'})
        if not new_designation_id:
            return JsonResponse({'success': False, 'error': 'New designation is required.'})
        if not promotion_date:
            return JsonResponse({'success': False, 'error': 'Promotion date is required.'})
        if not effective_date:
            return JsonResponse({'success': False, 'error': 'Effective date is required.'})

        promotion.employee = get_object_or_404(Employee, id=employee_id)
        promotion.previous_designation = previous_designation
        promotion.new_designation = get_object_or_404(Designation, id=new_designation_id)
        promotion.promotion_date = promotion_date
        promotion.effective_date = effective_date
        salary_adjustment = request.POST.get('salary_adjustment', '').strip()
        promotion.salary_adjustment = salary_adjustment if salary_adjustment else None
        promotion.reason = request.POST.get('reason', '').strip()
        promotion.status = request.POST.get('status', promotion.status).strip()

        if request.FILES.get('document'):
            promotion.document = request.FILES['document']

        promotion.save()
        return JsonResponse({'success': True, 'message': f'Promotion for "{promotion.employee.full_name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def promotion_delete(request, pk):
    promotion = get_object_or_404(Promotion.objects.select_related('employee'), id=pk)

    if request.method == 'POST':
        emp_name = promotion.employee.full_name
        promotion.delete()
        return JsonResponse({'success': True, 'message': f'Promotion for "{emp_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def promotion_toggle_status(request, pk):
    promotion = get_object_or_404(Promotion, id=pk)

    if request.method == 'POST':
        new_status = request.POST.get('status', '').strip()
        if new_status not in ['pending', 'approved', 'rejected']:
            return JsonResponse({'success': False, 'error': 'Invalid status.'})
        promotion.status = new_status
        promotion.save()
        return JsonResponse({'success': True, 'message': f'Promotion status updated to "{promotion.get_status_display()}"!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def get_employee_designation(request):
    employee_id = request.GET.get('employee_id', '')
    if employee_id:
        try:
            employee = Employee.objects.select_related('designation').get(id=employee_id)
            designation_name = employee.designation.name if employee.designation else ''
            return JsonResponse({'success': True, 'designation': designation_name})
        except Employee.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Employee not found.'})
    return JsonResponse({'success': False, 'error': 'Employee ID is required.'})


@login_required
def resignation_list(request):
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')
    status_filter = request.GET.get('status', '')
    employee_filter = request.GET.get('employee', '')

    resignations = Resignation.objects.select_related('employee').all()

    if search_query:
        resignations = resignations.filter(
            Q(employee__full_name__icontains=search_query) |
            Q(reason__icontains=search_query) |
            Q(notice_period__icontains=search_query) |
            Q(description__icontains=search_query)
        )

    if status_filter:
        resignations = resignations.filter(status=status_filter)

    if employee_filter:
        resignations = resignations.filter(employee_id=employee_filter)

    paginator = Paginator(resignations, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Resignations',
        'resignations': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'status_filter': status_filter,
        'employee_filter': employee_filter,
        'total_resignations': paginator.count,
        'employees': Employee.objects.filter(employee_status='active').order_by('full_name'),
    }
    return render(request, 'hrm/resignation_list.html', context)


@login_required
def resignation_create(request):
    if request.method == 'POST':
        employee_id = request.POST.get('employee', '').strip()
        resignation_date = request.POST.get('resignation_date', '').strip()
        last_working_day = request.POST.get('last_working_day', '').strip()

        if not employee_id:
            return JsonResponse({'success': False, 'error': 'Employee is required.'})
        if not resignation_date:
            return JsonResponse({'success': False, 'error': 'Resignation date is required.'})
        if not last_working_day:
            return JsonResponse({'success': False, 'error': 'Last working day is required.'})

        employee = get_object_or_404(Employee, id=employee_id)

        resignation = Resignation(
            employee=employee,
            resignation_date=resignation_date,
            last_working_day=last_working_day,
            notice_period=request.POST.get('notice_period', '').strip(),
            reason=request.POST.get('reason', '').strip(),
            description=request.POST.get('description', '').strip(),
        )

        if request.FILES.get('document'):
            resignation.document = request.FILES['document']

        resignation.save()
        return JsonResponse({'success': True, 'message': f'Resignation for "{employee.full_name}" created successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def resignation_detail(request, pk):
    resignation = get_object_or_404(Resignation.objects.select_related('employee'), id=pk)
    return JsonResponse({
        'success': True,
        'resignation': {
            'id': resignation.id,
            'employee_id': resignation.employee.id,
            'employee_name': resignation.employee.full_name,
            'employee_code': resignation.employee.employee_id,
            'resignation_date': resignation.resignation_date.strftime('%Y-%m-%d'),
            'last_working_day': resignation.last_working_day.strftime('%Y-%m-%d'),
            'notice_period': resignation.notice_period,
            'reason': resignation.reason,
            'description': resignation.description,
            'status': resignation.status,
            'document_url': resignation.document.url if resignation.document else '',
            'created_at': resignation.created_at.strftime('%Y-%m-%d'),
            'updated_at': resignation.updated_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def resignation_update(request, pk):
    resignation = get_object_or_404(Resignation, id=pk)

    if request.method == 'POST':
        employee_id = request.POST.get('employee', '').strip()
        resignation_date = request.POST.get('resignation_date', '').strip()
        last_working_day = request.POST.get('last_working_day', '').strip()

        if not employee_id:
            return JsonResponse({'success': False, 'error': 'Employee is required.'})
        if not resignation_date:
            return JsonResponse({'success': False, 'error': 'Resignation date is required.'})
        if not last_working_day:
            return JsonResponse({'success': False, 'error': 'Last working day is required.'})

        resignation.employee = get_object_or_404(Employee, id=employee_id)
        resignation.resignation_date = resignation_date
        resignation.last_working_day = last_working_day
        resignation.notice_period = request.POST.get('notice_period', '').strip()
        resignation.reason = request.POST.get('reason', '').strip()
        resignation.description = request.POST.get('description', '').strip()
        resignation.status = request.POST.get('status', resignation.status).strip()

        if request.FILES.get('document'):
            resignation.document = request.FILES['document']

        resignation.save()
        return JsonResponse({'success': True, 'message': f'Resignation for "{resignation.employee.full_name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def resignation_delete(request, pk):
    resignation = get_object_or_404(Resignation.objects.select_related('employee'), id=pk)

    if request.method == 'POST':
        emp_name = resignation.employee.full_name
        resignation.delete()
        return JsonResponse({'success': True, 'message': f'Resignation for "{emp_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def resignation_toggle_status(request, pk):
    resignation = get_object_or_404(Resignation, id=pk)

    if request.method == 'POST':
        new_status = request.POST.get('status', '').strip()
        if new_status not in ['pending', 'approved', 'rejected', 'completed']:
            return JsonResponse({'success': False, 'error': 'Invalid status.'})
        resignation.status = new_status
        resignation.save()
        return JsonResponse({'success': True, 'message': f'Resignation status updated to "{resignation.get_status_display()}"!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def termination_list(request):
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')
    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')
    employee_filter = request.GET.get('employee', '')

    terminations = Termination.objects.select_related('employee').all()

    if search_query:
        terminations = terminations.filter(
            Q(employee__full_name__icontains=search_query) |
            Q(reason__icontains=search_query) |
            Q(notice_period__icontains=search_query) |
            Q(description__icontains=search_query)
        )

    if status_filter:
        terminations = terminations.filter(status=status_filter)

    if type_filter:
        terminations = terminations.filter(termination_type=type_filter)

    if employee_filter:
        terminations = terminations.filter(employee_id=employee_filter)

    paginator = Paginator(terminations, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Terminations',
        'terminations': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'employee_filter': employee_filter,
        'total_terminations': paginator.count,
        'employees': Employee.objects.filter(employee_status='active').order_by('full_name'),
    }
    return render(request, 'hrm/termination_list.html', context)


@login_required
def termination_create(request):
    if request.method == 'POST':
        employee_id = request.POST.get('employee', '').strip()
        termination_type = request.POST.get('termination_type', '').strip()
        notice_date = request.POST.get('notice_date', '').strip()
        termination_date = request.POST.get('termination_date', '').strip()

        if not employee_id:
            return JsonResponse({'success': False, 'error': 'Employee is required.'})
        if not termination_type:
            return JsonResponse({'success': False, 'error': 'Termination type is required.'})
        if not notice_date:
            return JsonResponse({'success': False, 'error': 'Notice date is required.'})
        if not termination_date:
            return JsonResponse({'success': False, 'error': 'Termination date is required.'})

        valid_types = [c[0] for c in Termination.TYPE_CHOICES]
        if termination_type not in valid_types:
            return JsonResponse({'success': False, 'error': 'Invalid termination type.'})

        employee = get_object_or_404(Employee, id=employee_id)

        termination = Termination(
            employee=employee,
            termination_type=termination_type,
            termination_date=termination_date,
            notice_date=notice_date,
            notice_period=request.POST.get('notice_period', '').strip(),
            reason=request.POST.get('reason', '').strip(),
            description=request.POST.get('description', '').strip(),
        )

        if request.FILES.get('document'):
            termination.document = request.FILES['document']

        termination.save()
        return JsonResponse({'success': True, 'message': f'Termination for "{employee.full_name}" created successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def termination_detail(request, pk):
    termination = get_object_or_404(Termination.objects.select_related('employee'), id=pk)
    return JsonResponse({
        'success': True,
        'termination': {
            'id': termination.id,
            'employee_id': termination.employee.id,
            'employee_name': termination.employee.full_name,
            'employee_code': termination.employee.employee_id,
            'termination_type': termination.termination_type,
            'termination_type_display': termination.get_termination_type_display(),
            'termination_date': termination.termination_date.strftime('%Y-%m-%d'),
            'notice_date': termination.notice_date.strftime('%Y-%m-%d'),
            'notice_period': termination.notice_period,
            'reason': termination.reason,
            'description': termination.description,
            'status': termination.status,
            'status_display': termination.get_status_display(),
            'document_url': termination.document.url if termination.document else '',
            'created_at': termination.created_at.strftime('%Y-%m-%d'),
            'updated_at': termination.updated_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def termination_update(request, pk):
    termination = get_object_or_404(Termination, id=pk)

    if request.method == 'POST':
        employee_id = request.POST.get('employee', '').strip()
        termination_type = request.POST.get('termination_type', '').strip()
        notice_date = request.POST.get('notice_date', '').strip()
        termination_date = request.POST.get('termination_date', '').strip()

        if not employee_id:
            return JsonResponse({'success': False, 'error': 'Employee is required.'})
        if not termination_type:
            return JsonResponse({'success': False, 'error': 'Termination type is required.'})
        if not notice_date:
            return JsonResponse({'success': False, 'error': 'Notice date is required.'})
        if not termination_date:
            return JsonResponse({'success': False, 'error': 'Termination date is required.'})

        valid_types = [c[0] for c in Termination.TYPE_CHOICES]
        if termination_type not in valid_types:
            return JsonResponse({'success': False, 'error': 'Invalid termination type.'})

        termination.employee = get_object_or_404(Employee, id=employee_id)
        termination.termination_type = termination_type
        termination.termination_date = termination_date
        termination.notice_date = notice_date
        termination.notice_period = request.POST.get('notice_period', '').strip()
        termination.reason = request.POST.get('reason', '').strip()
        termination.description = request.POST.get('description', '').strip()
        termination.status = request.POST.get('status', termination.status).strip()

        if request.FILES.get('document'):
            termination.document = request.FILES['document']

        termination.save()
        return JsonResponse({'success': True, 'message': f'Termination for "{termination.employee.full_name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def termination_delete(request, pk):
    termination = get_object_or_404(Termination.objects.select_related('employee'), id=pk)

    if request.method == 'POST':
        emp_name = termination.employee.full_name
        termination.delete()
        return JsonResponse({'success': True, 'message': f'Termination for "{emp_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def termination_toggle_status(request, pk):
    termination = get_object_or_404(Termination, id=pk)

    if request.method == 'POST':
        new_status = request.POST.get('status', '').strip()
        if new_status not in ['pending', 'in_progress', 'completed', 'revoked']:
            return JsonResponse({'success': False, 'error': 'Invalid status.'})
        termination.status = new_status
        termination.save()
        return JsonResponse({'success': True, 'message': f'Termination status updated to "{termination.get_status_display()}"!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def warning_list(request):
    warnings_qs = Warning.objects.select_related('employee', 'warning_by').all()
    employees = Employee.objects.filter(employee_status='active').order_by('full_name')

    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()
    type_filter = request.GET.get('type', '').strip()
    severity_filter = request.GET.get('severity', '').strip()
    employee_filter = request.GET.get('employee', '').strip()
    per_page = request.GET.get('per_page', '10')

    if search_query:
        warnings_qs = warnings_qs.filter(
            Q(employee__first_name__icontains=search_query) |
            Q(employee__last_name__icontains=search_query) |
            Q(employee__employee_id__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(description__icontains=search_query)
        )
    if status_filter:
        warnings_qs = warnings_qs.filter(status=status_filter)
    if type_filter:
        warnings_qs = warnings_qs.filter(warning_type=type_filter)
    if severity_filter:
        warnings_qs = warnings_qs.filter(severity=severity_filter)
    if employee_filter:
        warnings_qs = warnings_qs.filter(employee_id=employee_filter)

    paginator = Paginator(warnings_qs, int(per_page) if per_page.isdigit() else 10)
    page_number = request.GET.get('page', 1)
    warnings = paginator.get_page(page_number)

    context = {
        'page_title': 'Warnings',
        'warnings': warnings,
        'total_warnings': paginator.count,
        'employees': employees,
        'search_query': search_query,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'severity_filter': severity_filter,
        'employee_filter': employee_filter,
        'per_page': per_page,
    }
    return render(request, 'hrm/warning_list.html', context)


@login_required
def warning_create(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        employee_id = request.POST.get('employee', '').strip()
        warning_by_id = request.POST.get('warning_by', '').strip()
        warning_type = request.POST.get('warning_type', '').strip()
        subject = request.POST.get('subject', '').strip()
        severity = request.POST.get('severity', '').strip()
        warning_date = request.POST.get('warning_date', '').strip()
        description = request.POST.get('description', '').strip()
        improvement_plan = request.POST.get('improvement_plan') == 'on'
        document = request.FILES.get('document')

        if not all([employee_id, warning_type, subject, severity, warning_date]):
            return JsonResponse({'success': False, 'error': 'Please fill in all required fields.'})

        employee = get_object_or_404(Employee, id=employee_id)
        warning_by = None
        if warning_by_id:
            warning_by = get_object_or_404(Employee, id=warning_by_id)

        warning = Warning.objects.create(
            employee=employee,
            warning_by=warning_by,
            warning_type=warning_type,
            subject=subject,
            severity=severity,
            warning_date=warning_date,
            description=description,
            improvement_plan=improvement_plan,
            document=document,
        )
        return JsonResponse({'success': True, 'message': f'Warning for "{employee.full_name}" created successfully!'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def warning_detail(request, pk):
    warning = get_object_or_404(Warning.objects.select_related('employee', 'warning_by'), id=pk)
    data = {
        'success': True,
        'warning': {
            'id': warning.id,
            'employee_id': warning.employee_id,
            'employee_name': warning.employee.full_name,
            'employee_code': warning.employee.employee_id,
            'warning_by_id': warning.warning_by_id if warning.warning_by else '',
            'warning_by_name': warning.warning_by.full_name if warning.warning_by else '',
            'warning_type': warning.warning_type,
            'warning_type_display': warning.get_warning_type_display(),
            'subject': warning.subject,
            'severity': warning.severity,
            'severity_display': warning.get_severity_display(),
            'warning_date': str(warning.warning_date),
            'description': warning.description,
            'improvement_plan': warning.improvement_plan,
            'status': warning.status,
            'status_display': warning.get_status_display(),
            'document_url': warning.document.url if warning.document else '',
            'created_at': warning.created_at.strftime('%Y-%m-%d %H:%M'),
            'updated_at': warning.updated_at.strftime('%Y-%m-%d %H:%M'),
        }
    }
    return JsonResponse(data)


@login_required
def warning_update(request, pk):
    warning = get_object_or_404(Warning, id=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        employee_id = request.POST.get('employee', '').strip()
        warning_by_id = request.POST.get('warning_by', '').strip()
        warning_type = request.POST.get('warning_type', '').strip()
        subject = request.POST.get('subject', '').strip()
        severity = request.POST.get('severity', '').strip()
        warning_date = request.POST.get('warning_date', '').strip()
        description = request.POST.get('description', '').strip()
        improvement_plan = request.POST.get('improvement_plan') == 'on'
        status = request.POST.get('status', '').strip()
        document = request.FILES.get('document')

        if not all([employee_id, warning_type, subject, severity, warning_date]):
            return JsonResponse({'success': False, 'error': 'Please fill in all required fields.'})

        employee = get_object_or_404(Employee, id=employee_id)
        warning_by = None
        if warning_by_id:
            warning_by = get_object_or_404(Employee, id=warning_by_id)

        warning.employee = employee
        warning.warning_by = warning_by
        warning.warning_type = warning_type
        warning.subject = subject
        warning.severity = severity
        warning.warning_date = warning_date
        warning.description = description
        warning.improvement_plan = improvement_plan
        if status:
            warning.status = status
        if document:
            warning.document = document
        warning.save()
        return JsonResponse({'success': True, 'message': f'Warning for "{employee.full_name}" updated successfully!'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def warning_delete(request, pk):
    warning = get_object_or_404(Warning, id=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})
    name = warning.employee.full_name
    warning.delete()
    return JsonResponse({'success': True, 'message': f'Warning for "{name}" deleted successfully!'})


@login_required
def warning_toggle_status(request, pk):
    warning = get_object_or_404(Warning, id=pk)
    if request.method == 'POST':
        new_status = request.POST.get('status', '').strip()
        if new_status not in ['draft', 'issued', 'acknowledged', 'resolved', 'escalated']:
            return JsonResponse({'success': False, 'error': 'Invalid status.'})
        warning.status = new_status
        warning.save()
        return JsonResponse({'success': True, 'message': f'Warning status updated to "{warning.get_status_display()}"!'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def complaint_list(request):
    complaints_qs = Complaint.objects.select_related('complainant', 'against').all()
    employees = Employee.objects.filter(employee_status='active').order_by('full_name')

    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()
    type_filter = request.GET.get('type', '').strip()
    employee_filter = request.GET.get('employee', '').strip()
    per_page = request.GET.get('per_page', '10')

    if search_query:
        complaints_qs = complaints_qs.filter(
            Q(complainant__full_name__icontains=search_query) |
            Q(complainant__employee_id__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(assigned_to__icontains=search_query)
        )
    if status_filter:
        complaints_qs = complaints_qs.filter(status=status_filter)
    if type_filter:
        complaints_qs = complaints_qs.filter(complaint_type=type_filter)
    if employee_filter:
        complaints_qs = complaints_qs.filter(complainant_id=employee_filter)

    paginator = Paginator(complaints_qs, int(per_page) if per_page.isdigit() else 10)
    page_number = request.GET.get('page', 1)
    complaints = paginator.get_page(page_number)

    context = {
        'page_title': 'Complaints',
        'complaints': complaints,
        'total_complaints': paginator.count,
        'employees': employees,
        'search_query': search_query,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'employee_filter': employee_filter,
        'per_page': per_page,
    }
    return render(request, 'hrm/complaint_list.html', context)


@login_required
def complaint_create(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        complainant_id = request.POST.get('complainant', '').strip()
        against_id = request.POST.get('against', '').strip()
        complaint_type = request.POST.get('complaint_type', '').strip()
        subject = request.POST.get('subject', '').strip()
        complaint_date = request.POST.get('complaint_date', '').strip()
        description = request.POST.get('description', '').strip()
        assigned_to = request.POST.get('assigned_to', '').strip()
        is_anonymous = request.POST.get('is_anonymous') == 'on'
        document = request.FILES.get('document')

        if not all([complainant_id, complaint_type, subject, complaint_date]):
            return JsonResponse({'success': False, 'error': 'Please fill in all required fields.'})

        complainant = get_object_or_404(Employee, id=complainant_id)
        against = None
        if against_id:
            against = get_object_or_404(Employee, id=against_id)

        complaint = Complaint.objects.create(
            complainant=complainant,
            against=against,
            complaint_type=complaint_type,
            subject=subject,
            complaint_date=complaint_date,
            description=description,
            assigned_to=assigned_to,
            is_anonymous=is_anonymous,
            document=document,
        )
        return JsonResponse({'success': True, 'message': f'Complaint "{subject}" created successfully!'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def complaint_detail(request, pk):
    complaint = get_object_or_404(Complaint.objects.select_related('complainant', 'against'), id=pk)
    data = {
        'success': True,
        'complaint': {
            'id': complaint.id,
            'complainant_id': complaint.complainant_id,
            'complainant_name': 'Anonymous' if complaint.is_anonymous else complaint.complainant.full_name,
            'complainant_code': complaint.complainant.employee_id,
            'against_id': complaint.against_id if complaint.against else '',
            'against_name': complaint.against.full_name if complaint.against else '-',
            'complaint_type': complaint.complaint_type,
            'complaint_type_display': complaint.get_complaint_type_display(),
            'subject': complaint.subject,
            'complaint_date': str(complaint.complaint_date),
            'description': complaint.description,
            'assigned_to': complaint.assigned_to,
            'is_anonymous': complaint.is_anonymous,
            'status': complaint.status,
            'status_display': complaint.get_status_display(),
            'document_url': complaint.document.url if complaint.document else '',
            'created_at': complaint.created_at.strftime('%Y-%m-%d %H:%M'),
            'updated_at': complaint.updated_at.strftime('%Y-%m-%d %H:%M'),
        }
    }
    return JsonResponse(data)


@login_required
def complaint_update(request, pk):
    complaint = get_object_or_404(Complaint, id=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        complainant_id = request.POST.get('complainant', '').strip()
        against_id = request.POST.get('against', '').strip()
        complaint_type = request.POST.get('complaint_type', '').strip()
        subject = request.POST.get('subject', '').strip()
        complaint_date = request.POST.get('complaint_date', '').strip()
        description = request.POST.get('description', '').strip()
        assigned_to = request.POST.get('assigned_to', '').strip()
        is_anonymous = request.POST.get('is_anonymous') == 'on'
        status = request.POST.get('status', '').strip()
        document = request.FILES.get('document')

        if not all([complainant_id, complaint_type, subject, complaint_date]):
            return JsonResponse({'success': False, 'error': 'Please fill in all required fields.'})

        complainant = get_object_or_404(Employee, id=complainant_id)
        against = None
        if against_id:
            against = get_object_or_404(Employee, id=against_id)

        complaint.complainant = complainant
        complaint.against = against
        complaint.complaint_type = complaint_type
        complaint.subject = subject
        complaint.complaint_date = complaint_date
        complaint.description = description
        complaint.assigned_to = assigned_to
        complaint.is_anonymous = is_anonymous
        if status:
            complaint.status = status
        if document:
            complaint.document = document
        complaint.save()
        return JsonResponse({'success': True, 'message': f'Complaint "{subject}" updated successfully!'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def complaint_delete(request, pk):
    complaint = get_object_or_404(Complaint, id=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})
    subject = complaint.subject
    complaint.delete()
    return JsonResponse({'success': True, 'message': f'Complaint "{subject}" deleted successfully!'})


@login_required
def complaint_toggle_status(request, pk):
    complaint = get_object_or_404(Complaint, id=pk)
    if request.method == 'POST':
        new_status = request.POST.get('status', '').strip()
        if new_status not in ['submitted', 'under_review', 'resolved', 'dismissed']:
            return JsonResponse({'success': False, 'error': 'Invalid status.'})
        complaint.status = new_status
        complaint.save()
        return JsonResponse({'success': True, 'message': f'Complaint status updated to "{complaint.get_status_display()}"!'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


# ==================== Asset Management ====================

@login_required
def asset_type_list(request):
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')
    status_filter = request.GET.get('status', '')

    asset_types = AssetType.objects.all()

    if search_query:
        asset_types = asset_types.filter(
            Q(name__icontains=search_query) |
            Q(description__icontains=search_query)
        )

    if status_filter:
        asset_types = asset_types.filter(status=status_filter)

    paginator = Paginator(asset_types, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Asset Types',
        'asset_types': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'status_filter': status_filter,
        'total_asset_types': paginator.count,
    }
    return render(request, 'hrm/asset_type_list.html', context)


@login_required
def asset_type_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()

        if not name:
            return JsonResponse({'success': False, 'error': 'Asset type name is required.'})

        if AssetType.objects.filter(name__iexact=name).exists():
            return JsonResponse({'success': False, 'error': f'Asset type "{name}" already exists.'})

        asset_type = AssetType(
            name=name,
            description=request.POST.get('description', '').strip(),
            status=request.POST.get('status', 'active'),
        )
        asset_type.save()
        return JsonResponse({'success': True, 'message': f'Asset type "{asset_type.name}" created successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def asset_type_detail(request, pk):
    asset_type = get_object_or_404(AssetType, id=pk)
    return JsonResponse({
        'success': True,
        'asset_type': {
            'id': asset_type.id,
            'name': asset_type.name,
            'description': asset_type.description,
            'status': asset_type.status,
            'created_at': asset_type.created_at.strftime('%Y-%m-%d'),
            'updated_at': asset_type.updated_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def asset_type_update(request, pk):
    asset_type = get_object_or_404(AssetType, id=pk)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()

        if not name:
            return JsonResponse({'success': False, 'error': 'Asset type name is required.'})

        if AssetType.objects.filter(name__iexact=name).exclude(id=pk).exists():
            return JsonResponse({'success': False, 'error': f'Asset type "{name}" already exists.'})

        asset_type.name = name
        asset_type.description = request.POST.get('description', '').strip()
        asset_type.status = request.POST.get('status', asset_type.status)
        asset_type.save()
        return JsonResponse({'success': True, 'message': f'Asset type "{asset_type.name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def asset_type_delete(request, pk):
    asset_type = get_object_or_404(AssetType, id=pk)

    if request.method == 'POST':
        type_name = asset_type.name
        asset_type.delete()
        return JsonResponse({'success': True, 'message': f'Asset type "{type_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def asset_type_toggle_status(request, pk):
    asset_type = get_object_or_404(AssetType, id=pk)

    if request.method == 'POST':
        asset_type.status = 'inactive' if asset_type.status == 'active' else 'active'
        asset_type.save()
        return JsonResponse({'success': True, 'message': f'Asset type "{asset_type.name}" is now {asset_type.get_status_display()}.'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def asset_list(request):
    search_query = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '10')
    status_filter = request.GET.get('status', '')
    condition_filter = request.GET.get('condition', '')
    asset_type_filter = request.GET.get('asset_type', '')
    location_filter = request.GET.get('location', '')

    assets = Asset.objects.select_related('asset_type', 'assigned_to').all()

    if search_query:
        assets = assets.filter(
            Q(name__icontains=search_query) |
            Q(asset_code__icontains=search_query) |
            Q(serial_number__icontains=search_query) |
            Q(location__icontains=search_query) |
            Q(supplier__icontains=search_query) |
            Q(asset_type__name__icontains=search_query) |
            Q(assigned_to__full_name__icontains=search_query)
        )

    if status_filter:
        assets = assets.filter(status=status_filter)
    if condition_filter:
        assets = assets.filter(condition=condition_filter)
    if asset_type_filter:
        assets = assets.filter(asset_type_id=asset_type_filter)
    if location_filter:
        assets = assets.filter(location__icontains=location_filter)

    paginator = Paginator(assets, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Assets',
        'assets': page_obj,
        'search_query': search_query,
        'per_page': per_page,
        'status_filter': status_filter,
        'condition_filter': condition_filter,
        'asset_type_filter': asset_type_filter,
        'location_filter': location_filter,
        'total_assets': paginator.count,
        'asset_types': AssetType.objects.filter(status='active').order_by('name'),
        'employees': Employee.objects.filter(employee_status='active').order_by('full_name'),
    }
    return render(request, 'hrm/asset_list.html', context)


@login_required
def asset_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        asset_type_id = request.POST.get('asset_type', '').strip()
        asset_code = request.POST.get('asset_code', '').strip()
        status = request.POST.get('status', '').strip()

        errors = {}
        if not name:
            errors['name'] = 'Asset name is required.'
        if not asset_type_id:
            errors['asset_type'] = 'Asset type is required.'
        if not asset_code:
            errors['asset_code'] = 'Asset code is required.'
        elif Asset.objects.filter(asset_code__iexact=asset_code).exists():
            errors['asset_code'] = f'Asset code "{asset_code}" already exists.'
        if not status:
            errors['status'] = 'Status is required.'

        if errors:
            return JsonResponse({'success': False, 'errors': errors, 'error': list(errors.values())[0]})

        asset_type = get_object_or_404(AssetType, id=asset_type_id)

        purchase_cost = request.POST.get('purchase_cost', '').strip()
        salvage_value = request.POST.get('salvage_value', '').strip()
        useful_life = request.POST.get('useful_life_years', '').strip()
        assigned_to_id = request.POST.get('assigned_to', '').strip()

        asset = Asset(
            name=name,
            asset_type=asset_type,
            serial_number=request.POST.get('serial_number', '').strip(),
            asset_code=asset_code,
            purchase_date=request.POST.get('purchase_date', '').strip() or None,
            purchase_cost=purchase_cost if purchase_cost else None,
            status=status,
            condition=request.POST.get('condition', '').strip() or 'new',
            description=request.POST.get('description', '').strip(),
            location=request.POST.get('location', '').strip(),
            supplier=request.POST.get('supplier', '').strip(),
            warranty_info=request.POST.get('warranty_info', '').strip(),
            warranty_expiry=request.POST.get('warranty_expiry', '').strip() or None,
            depreciation_method=request.POST.get('depreciation_method', '').strip() or 'none',
            useful_life_years=int(useful_life) if useful_life else 5,
            salvage_value=salvage_value if salvage_value else None,
        )

        if assigned_to_id:
            asset.assigned_to = get_object_or_404(Employee, id=assigned_to_id)

        if request.FILES.get('image'):
            asset.image = request.FILES['image']
        if request.FILES.get('document'):
            asset.document = request.FILES['document']

        asset.save()
        return JsonResponse({'success': True, 'message': f'Asset "{asset.name}" created successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def asset_detail(request, pk):
    asset = get_object_or_404(Asset.objects.select_related('asset_type', 'assigned_to'), id=pk)
    return JsonResponse({
        'success': True,
        'asset': {
            'id': asset.id,
            'name': asset.name,
            'asset_type_id': asset.asset_type_id,
            'asset_type_name': asset.asset_type.name,
            'serial_number': asset.serial_number,
            'asset_code': asset.asset_code,
            'purchase_date': asset.purchase_date.strftime('%Y-%m-%d') if asset.purchase_date else '',
            'purchase_cost': str(asset.purchase_cost) if asset.purchase_cost else '',
            'status': asset.status,
            'status_display': asset.get_status_display(),
            'condition': asset.condition,
            'condition_display': asset.get_condition_display(),
            'description': asset.description,
            'location': asset.location,
            'assigned_to_id': asset.assigned_to_id or '',
            'assigned_to_name': asset.assigned_to.full_name if asset.assigned_to else '',
            'supplier': asset.supplier,
            'warranty_info': asset.warranty_info,
            'warranty_expiry': asset.warranty_expiry.strftime('%Y-%m-%d') if asset.warranty_expiry else '',
            'image_url': asset.image.url if asset.image else '',
            'document_url': asset.document.url if asset.document else '',
            'depreciation_method': asset.depreciation_method,
            'depreciation_method_display': asset.get_depreciation_method_display(),
            'useful_life_years': asset.useful_life_years,
            'salvage_value': str(asset.salvage_value) if asset.salvage_value else '',
            'created_at': asset.created_at.strftime('%Y-%m-%d'),
            'updated_at': asset.updated_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def asset_update(request, pk):
    asset = get_object_or_404(Asset, id=pk)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        asset_type_id = request.POST.get('asset_type', '').strip()
        asset_code = request.POST.get('asset_code', '').strip()
        status = request.POST.get('status', '').strip()

        errors = {}
        if not name:
            errors['name'] = 'Asset name is required.'
        if not asset_type_id:
            errors['asset_type'] = 'Asset type is required.'
        if not asset_code:
            errors['asset_code'] = 'Asset code is required.'
        elif Asset.objects.filter(asset_code__iexact=asset_code).exclude(id=pk).exists():
            errors['asset_code'] = f'Asset code "{asset_code}" already exists.'
        if not status:
            errors['status'] = 'Status is required.'

        if errors:
            return JsonResponse({'success': False, 'errors': errors, 'error': list(errors.values())[0]})

        asset.name = name
        asset.asset_type = get_object_or_404(AssetType, id=asset_type_id)
        asset.serial_number = request.POST.get('serial_number', '').strip()
        asset.asset_code = asset_code
        asset.purchase_date = request.POST.get('purchase_date', '').strip() or None
        purchase_cost = request.POST.get('purchase_cost', '').strip()
        asset.purchase_cost = purchase_cost if purchase_cost else None
        asset.status = status
        asset.condition = request.POST.get('condition', '').strip() or asset.condition
        asset.description = request.POST.get('description', '').strip()
        asset.location = request.POST.get('location', '').strip()
        asset.supplier = request.POST.get('supplier', '').strip()
        asset.warranty_info = request.POST.get('warranty_info', '').strip()
        asset.warranty_expiry = request.POST.get('warranty_expiry', '').strip() or None
        asset.depreciation_method = request.POST.get('depreciation_method', '').strip() or 'none'
        useful_life = request.POST.get('useful_life_years', '').strip()
        asset.useful_life_years = int(useful_life) if useful_life else 5
        salvage_value = request.POST.get('salvage_value', '').strip()
        asset.salvage_value = salvage_value if salvage_value else None

        assigned_to_id = request.POST.get('assigned_to', '').strip()
        asset.assigned_to = get_object_or_404(Employee, id=assigned_to_id) if assigned_to_id else None

        if request.FILES.get('image'):
            asset.image = request.FILES['image']
        if request.FILES.get('document'):
            asset.document = request.FILES['document']

        asset.save()
        return JsonResponse({'success': True, 'message': f'Asset "{asset.name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def asset_delete(request, pk):
    asset = get_object_or_404(Asset, id=pk)

    if request.method == 'POST':
        asset_name = asset.name
        asset.delete()
        return JsonResponse({'success': True, 'message': f'Asset "{asset_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def asset_assign(request, pk):
    asset = get_object_or_404(Asset, id=pk)

    if request.method == 'POST':
        assigned_to_id = request.POST.get('assigned_to', '').strip()
        if assigned_to_id:
            employee = get_object_or_404(Employee, id=assigned_to_id)
            asset.assigned_to = employee
            asset.status = 'assigned'
            asset.save()
            return JsonResponse({'success': True, 'message': f'Asset "{asset.name}" assigned to {employee.full_name}.'})
        else:
            asset.assigned_to = None
            asset.status = 'available'
            asset.save()
            return JsonResponse({'success': True, 'message': f'Asset "{asset.name}" unassigned and set to available.'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def asset_checkin(request, pk):
    asset = get_object_or_404(Asset, id=pk)

    if request.method == 'POST':
        asset.assigned_to = None
        asset.status = 'available'
        asset.save()
        return JsonResponse({'success': True, 'message': f'Asset "{asset.name}" checked in and is now available.'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def asset_dashboard(request):
    today = date.today()
    assets = Asset.objects.select_related('asset_type', 'assigned_to').all()

    total_assets = assets.count()
    available_count = assets.filter(status='available').count()
    assigned_count = assets.filter(status='assigned').count()
    maintenance_count = assets.filter(status='under_maintenance').count()
    retired_count = assets.filter(status='retired').count()
    disposed_count = assets.filter(status='disposed').count()

    # Percentages
    available_pct = round(available_count / total_assets * 100) if total_assets else 0
    assigned_pct = round(assigned_count / total_assets * 100) if total_assets else 0
    maintenance_pct = round(maintenance_count / total_assets * 100) if total_assets else 0

    # Asset value summary
    total_purchase_value = assets.aggregate(total=Sum('purchase_cost'))['total'] or Decimal('0.00')

    # Calculate depreciation for each asset
    total_depreciation = Decimal('0.00')
    for asset in assets:
        if asset.purchase_cost and asset.depreciation_method != 'none' and asset.purchase_date:
            years_elapsed = (today - asset.purchase_date).days / Decimal('365.25')
            salvage = asset.salvage_value or Decimal('0.00')
            depreciable_amount = asset.purchase_cost - salvage
            if depreciable_amount > 0 and asset.useful_life_years > 0:
                if asset.depreciation_method == 'straight_line':
                    annual_dep = depreciable_amount / asset.useful_life_years
                    dep = min(annual_dep * years_elapsed, depreciable_amount)
                elif asset.depreciation_method == 'declining_balance':
                    rate = Decimal('2.0') / asset.useful_life_years
                    remaining = asset.purchase_cost
                    full_years = int(years_elapsed)
                    for _ in range(full_years):
                        year_dep = remaining * rate
                        if remaining - year_dep < salvage:
                            year_dep = remaining - salvage
                        remaining -= year_dep
                    dep = asset.purchase_cost - remaining
                elif asset.depreciation_method == 'sum_of_years':
                    n = asset.useful_life_years
                    syd = n * (n + 1) / 2
                    dep = Decimal('0.00')
                    full_years = min(int(years_elapsed), n)
                    for yr in range(1, full_years + 1):
                        dep += depreciable_amount * Decimal(str((n - yr + 1) / syd))
                else:
                    dep = Decimal('0.00')
                total_depreciation += max(dep, Decimal('0.00'))

    total_current_value = total_purchase_value - total_depreciation
    depreciation_pct = round(total_depreciation / total_purchase_value * 100) if total_purchase_value else 0

    # Asset distribution by type
    asset_types_dist = (
        AssetType.objects.filter(status='active')
        .annotate(asset_count=Count('assets'))
        .order_by('-asset_count', 'name')
    )
    max_type_count = max((at.asset_count for at in asset_types_dist), default=1) or 1

    # Recent assignments (assigned assets, ordered by update)
    recent_assignments = (
        assets.filter(status='assigned', assigned_to__isnull=False)
        .order_by('-updated_at')[:5]
    )

    # Upcoming maintenance (under_maintenance assets)
    upcoming_maintenance = (
        assets.filter(status='under_maintenance')
        .order_by('-updated_at')[:5]
    )

    # Expiring warranties (within next 90 days)
    warranty_deadline = today + timedelta(days=90)
    expiring_warranties = (
        assets.filter(warranty_expiry__gte=today, warranty_expiry__lte=warranty_deadline)
        .order_by('warranty_expiry')[:5]
    )

    context = {
        'page_title': 'Asset Dashboard',
        'total_assets': total_assets,
        'available_count': available_count,
        'assigned_count': assigned_count,
        'maintenance_count': maintenance_count,
        'retired_count': retired_count,
        'disposed_count': disposed_count,
        'available_pct': available_pct,
        'assigned_pct': assigned_pct,
        'maintenance_pct': maintenance_pct,
        'total_purchase_value': total_purchase_value,
        'total_current_value': total_current_value,
        'total_depreciation': total_depreciation,
        'depreciation_pct': depreciation_pct,
        'asset_types_dist': asset_types_dist,
        'max_type_count': max_type_count,
        'recent_assignments': recent_assignments,
        'upcoming_maintenance': upcoming_maintenance,
        'expiring_warranties': expiring_warranties,
    }
    return render(request, 'hrm/asset_dashboard.html', context)


@login_required
def asset_depreciation(request):
    today = date.today()
    search_query = request.GET.get('search', '')
    method_filter = request.GET.get('method', '')
    per_page = request.GET.get('per_page', '10')

    assets = Asset.objects.select_related('asset_type').filter(
        depreciation_method__in=['straight_line', 'declining_balance', 'sum_of_years', 'units_of_production'],
        purchase_cost__isnull=False,
        purchase_date__isnull=False,
    )

    if search_query:
        assets = assets.filter(
            Q(name__icontains=search_query) |
            Q(asset_code__icontains=search_query) |
            Q(serial_number__icontains=search_query)
        )

    if method_filter:
        assets = assets.filter(depreciation_method=method_filter)

    # Calculate depreciation for each asset
    asset_data = []
    total_purchase = Decimal('0.00')
    total_current = Decimal('0.00')
    total_dep = Decimal('0.00')

    for asset in assets:
        years_elapsed = (today - asset.purchase_date).days / Decimal('365.25')
        salvage = asset.salvage_value or Decimal('0.00')
        depreciable_amount = asset.purchase_cost - salvage
        dep = Decimal('0.00')

        if depreciable_amount > 0 and asset.useful_life_years > 0:
            if asset.depreciation_method == 'straight_line':
                annual_dep = depreciable_amount / asset.useful_life_years
                dep = min(annual_dep * years_elapsed, depreciable_amount)
            elif asset.depreciation_method == 'declining_balance':
                rate = Decimal('2.0') / asset.useful_life_years
                remaining = asset.purchase_cost
                full_years = int(years_elapsed)
                for _ in range(full_years):
                    year_dep = remaining * rate
                    if remaining - year_dep < salvage:
                        year_dep = remaining - salvage
                    remaining -= year_dep
                dep = asset.purchase_cost - remaining
            elif asset.depreciation_method == 'sum_of_years':
                n = asset.useful_life_years
                syd = n * (n + 1) / 2
                full_years = min(int(years_elapsed), n)
                for yr in range(1, full_years + 1):
                    dep += depreciable_amount * Decimal(str((n - yr + 1) / syd))

        dep = max(dep, Decimal('0.00'))
        current_value = asset.purchase_cost - dep
        dep_pct = round(dep / asset.purchase_cost * 100, 2) if asset.purchase_cost else 0

        total_purchase += asset.purchase_cost
        total_current += current_value
        total_dep += dep

        asset_data.append({
            'id': asset.id,
            'name': asset.name,
            'asset_code': asset.asset_code,
            'purchase_date': asset.purchase_date,
            'purchase_cost': asset.purchase_cost,
            'depreciation_method': asset.get_depreciation_method_display(),
            'current_value': current_value,
            'depreciation': dep,
            'depreciation_pct': dep_pct,
        })

    total_dep_pct = round(total_dep / total_purchase * 100) if total_purchase else 0

    paginator = Paginator(asset_data, int(per_page) if per_page.isdigit() else 10)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    # Handle CSV export
    if request.GET.get('export') == 'csv':
        import csv
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="asset_depreciation_report.csv"'
        writer = csv.writer(response)
        writer.writerow(['Asset Name', 'Asset Code', 'Purchase Date', 'Purchase Cost', 'Depreciation Method', 'Current Value', 'Depreciation', 'Depreciation %'])
        for item in asset_data:
            writer.writerow([
                item['name'], item['asset_code'],
                item['purchase_date'].strftime('%Y-%m-%d') if item['purchase_date'] else '',
                f"{item['purchase_cost']:.2f}",
                item['depreciation_method'],
                f"{item['current_value']:.2f}",
                f"{item['depreciation']:.2f}",
                f"{item['depreciation_pct']:.2f}%",
            ])
        return response

    context = {
        'page_title': 'Asset Depreciation Report',
        'assets': page_obj,
        'search_query': search_query,
        'method_filter': method_filter,
        'per_page': per_page,
        'total_purchase': total_purchase,
        'total_current': total_current,
        'total_dep': total_dep,
        'total_dep_pct': total_dep_pct,
        'total_count': paginator.count,
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


# ==================== Attendance Records ====================


def _sync_biometric_to_attendance():
    """
    Aggregate raw BiometricAttendance punches into AttendanceRecord entries.
    For each unique (pin, date) group, finds the matching Employee by employee_code,
    computes clock_in (earliest punch) and clock_out (latest punch), calculates
    working hours, and creates or updates the AttendanceRecord.
    """
    from .models import BiometricAttendance, AttendanceRecord, Employee, Shift
    from django.db.models import Min, Max, Count
    from django.db.models.functions import TruncDate
    import pytz

    nst = pytz.timezone('Asia/Kathmandu')

    # Build PIN → Employee lookup
    emp_map = {}
    for emp in Employee.objects.all():
        if emp.employee_code:
            emp_map[emp.employee_code] = emp

    if not emp_map:
        return

    # Aggregate biometric punches by (pin, date)
    punch_groups = (
        BiometricAttendance.objects
        .annotate(punch_date=TruncDate('timestamp'))
        .values('pin', 'punch_date')
        .annotate(
            first_punch=Min('timestamp'),
            last_punch=Max('timestamp'),
            punch_count=Count('id'),
        )
    )

    for group in punch_groups:
        pin = group['pin']
        punch_date = group['punch_date']
        first_punch = group['first_punch']
        last_punch = group['last_punch']
        punch_count = group['punch_count']

        employee = emp_map.get(pin)
        if not employee:
            continue

        # Convert UTC-stored timestamps to Nepal time
        clock_in_time = first_punch.astimezone(nst).time() if first_punch else None
        clock_out_time = last_punch.astimezone(nst).time() if last_punch and punch_count > 1 else None

        # Calculate working hours
        working_hours = 0
        if clock_in_time and clock_out_time:
            from datetime import datetime, timedelta
            cin_dt = datetime.combine(punch_date, clock_in_time)
            cout_dt = datetime.combine(punch_date, clock_out_time)
            diff = (cout_dt - cin_dt).total_seconds() / 3600
            if diff < 0:
                diff += 24
            working_hours = round(diff, 2)

        # Compute shift-based overtime, late arrival, early departure
        overtime_hours = 0
        is_late = False
        is_early = False
        shift = None

        # Try to find an assigned shift for this employee
        # (use the first active shift as fallback if employee doesn't have one assigned)
        existing_record = AttendanceRecord.objects.filter(
            employee=employee, date=punch_date
        ).first()

        if existing_record and existing_record.shift:
            shift = existing_record.shift

        if shift and clock_in_time and clock_out_time:
            from datetime import datetime, timedelta
            shift_hours = float(shift.working_hours)
            if working_hours > shift_hours:
                overtime_hours = round(working_hours - shift_hours, 2)
            grace = shift.grace_period or 0
            shift_start = datetime.combine(punch_date, shift.start_time)
            shift_end = datetime.combine(punch_date, shift.end_time)
            cin_full = datetime.combine(punch_date, clock_in_time)
            cout_full = datetime.combine(punch_date, clock_out_time)
            if cin_full > shift_start + timedelta(minutes=grace):
                is_late = True
            if cout_full < shift_end - timedelta(minutes=grace):
                is_early = True

        # Create or update the AttendanceRecord
        record, created = AttendanceRecord.objects.update_or_create(
            employee=employee,
            date=punch_date,
            defaults={
                'clock_in': clock_in_time,
                'clock_out': clock_out_time,
                'status': 'present',
                'working_hours': working_hours,
                'overtime_hours': overtime_hours,
                'is_late_arrival': is_late,
                'is_early_departure': is_early,
            }
        )
        # Preserve shift if it was already set
        if not created and shift and not record.shift:
            record.shift = shift
            record.save(update_fields=['shift'])


@login_required
def attendance_list(request):
    from .models import AttendanceRecord, Employee, Shift
    from django.core.paginator import Paginator
    from django.db.models import Q, Count, Sum
    from datetime import date as dt_date

    # Sync biometric punches into attendance records before loading
    _sync_biometric_to_attendance()

    search = request.GET.get('search', '')
    per_page = request.GET.get('per_page', 9)
    try:
        per_page = int(per_page)
    except (ValueError, TypeError):
        per_page = 9

    qs = AttendanceRecord.objects.select_related('employee', 'shift').all()
    if search:
        qs = qs.filter(
            Q(employee__full_name__icontains=search) |
            Q(notes__icontains=search)
        )

    today = dt_date.today()
    all_records = AttendanceRecord.objects.all()
    total_records = all_records.count()
    present_today = all_records.filter(date=today, status='present').count()
    on_leave_today = all_records.filter(date=today, status='on_leave').count()
    late_today = all_records.filter(date=today, is_late_arrival=True).count()
    overtime_today = all_records.filter(date=today, overtime_hours__gt=0).count()

    paginator = Paginator(qs, per_page)
    page_num = request.GET.get('page', 1)
    records = paginator.get_page(page_num)

    employees = Employee.objects.filter(employee_status='active').order_by('full_name')
    shifts = Shift.objects.filter(is_active=True).order_by('name')

    context = {
        'page_title': 'Attendance Records',
        'records': records,
        'search': search,
        'per_page': per_page,
        'total_records': total_records,
        'present_today': present_today,
        'on_leave_today': on_leave_today,
        'late_today': late_today,
        'overtime_today': overtime_today,
        'employees': employees,
        'shifts': shifts,
    }
    return render(request, 'hrm/attendance_list.html', context)


@login_required
def attendance_create(request):
    from .models import AttendanceRecord, Employee, Shift, AttendancePolicy
    from datetime import datetime, timedelta

    if request.method == 'POST':
        employee_id = request.POST.get('employee')
        date_val = request.POST.get('date')
        clock_in = request.POST.get('clock_in') or None
        clock_out = request.POST.get('clock_out') or None
        shift_id = request.POST.get('shift') or None
        is_holiday = request.POST.get('is_holiday') == 'true'
        notes = request.POST.get('notes', '').strip()
        status = request.POST.get('status', 'present')

        if not employee_id or not date_val:
            return JsonResponse({'success': False, 'error': 'Employee and date are required.'})

        employee = Employee.objects.filter(pk=employee_id).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found.'})

        if AttendanceRecord.objects.filter(employee=employee, date=date_val).exists():
            return JsonResponse({'success': False, 'error': 'Attendance already exists for this employee on this date.'})

        shift = Shift.objects.filter(pk=shift_id).first() if shift_id else None

        working_hours = 0
        overtime_hours = 0
        is_late = False
        is_early = False

        if clock_in and clock_out:
            cin = datetime.strptime(clock_in, '%H:%M')
            cout = datetime.strptime(clock_out, '%H:%M')
            diff = (cout - cin).total_seconds() / 3600
            if diff < 0:
                diff += 24
            working_hours = round(diff, 2)

            if shift:
                shift_hours = float(shift.working_hours)
                if working_hours > shift_hours:
                    overtime_hours = round(working_hours - shift_hours, 2)
                shift_start = datetime.combine(datetime.today(), shift.start_time)
                shift_end = datetime.combine(datetime.today(), shift.end_time)
                cin_full = datetime.combine(datetime.today(), datetime.strptime(clock_in, '%H:%M').time())
                cout_full = datetime.combine(datetime.today(), datetime.strptime(clock_out, '%H:%M').time())
                grace = shift.grace_period or 0
                if cin_full > shift_start + timedelta(minutes=grace):
                    is_late = True
                if cout_full < shift_end - timedelta(minutes=grace):
                    is_early = True

        record = AttendanceRecord.objects.create(
            employee=employee,
            date=date_val,
            clock_in=clock_in if clock_in else None,
            clock_out=clock_out if clock_out else None,
            shift=shift,
            status=status,
            working_hours=working_hours,
            overtime_hours=overtime_hours,
            is_holiday=is_holiday,
            notes=notes,
            is_early_departure=is_early,
            is_late_arrival=is_late,
        )
        return JsonResponse({'success': True, 'id': record.id})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def attendance_update(request, pk):
    from .models import AttendanceRecord, Shift
    from datetime import datetime, timedelta

    record = get_object_or_404(AttendanceRecord, pk=pk)

    if request.method == 'POST':
        clock_in = request.POST.get('clock_in') or None
        clock_out = request.POST.get('clock_out') or None
        shift_id = request.POST.get('shift') or None
        is_holiday = request.POST.get('is_holiday') == 'true'
        notes = request.POST.get('notes', '').strip()
        status = request.POST.get('status', 'present')

        shift = Shift.objects.filter(pk=shift_id).first() if shift_id else record.shift

        working_hours = 0
        overtime_hours = 0
        is_late = False
        is_early = False

        if clock_in and clock_out:
            cin = datetime.strptime(clock_in, '%H:%M')
            cout = datetime.strptime(clock_out, '%H:%M')
            diff = (cout - cin).total_seconds() / 3600
            if diff < 0:
                diff += 24
            working_hours = round(diff, 2)

            if shift:
                shift_hours = float(shift.working_hours)
                if working_hours > shift_hours:
                    overtime_hours = round(working_hours - shift_hours, 2)
                shift_start = datetime.combine(datetime.today(), shift.start_time)
                shift_end = datetime.combine(datetime.today(), shift.end_time)
                cin_full = datetime.combine(datetime.today(), datetime.strptime(clock_in, '%H:%M').time())
                cout_full = datetime.combine(datetime.today(), datetime.strptime(clock_out, '%H:%M').time())
                grace = shift.grace_period or 0
                if cin_full > shift_start + timedelta(minutes=grace):
                    is_late = True
                if cout_full < shift_end - timedelta(minutes=grace):
                    is_early = True

        record.clock_in = clock_in if clock_in else None
        record.clock_out = clock_out if clock_out else None
        record.shift = shift
        record.status = status
        record.working_hours = working_hours
        record.overtime_hours = overtime_hours
        record.is_holiday = is_holiday
        record.notes = notes
        record.is_early_departure = is_early
        record.is_late_arrival = is_late
        record.save()
        return JsonResponse({'success': True})

    # GET — return data for edit
    data = {
        'id': record.id,
        'employee_id': record.employee_id,
        'employee_name': record.employee.full_name,
        'date': str(record.date),
        'clock_in': record.clock_in.strftime('%H:%M') if record.clock_in else '',
        'clock_out': record.clock_out.strftime('%H:%M') if record.clock_out else '',
        'shift_id': record.shift_id,
        'shift_name': record.shift.name if record.shift else '',
        'status': record.status,
        'working_hours': str(record.working_hours),
        'overtime_hours': str(record.overtime_hours),
        'is_holiday': record.is_holiday,
        'notes': record.notes,
        'is_early_departure': record.is_early_departure,
        'is_late_arrival': record.is_late_arrival,
    }
    return JsonResponse(data)


@login_required
def attendance_delete(request, pk):
    from .models import AttendanceRecord
    record = get_object_or_404(AttendanceRecord, pk=pk)
    if request.method == 'POST':
        record.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


# ==================== Shifts ====================

@login_required
def shift_list(request):
    from .models import Shift
    search = request.GET.get('search', '')
    per_page = request.GET.get('per_page', '9')
    shifts = Shift.objects.all()

    total_shifts = shifts.count()
    active_shifts = shifts.filter(is_active=True).count()
    night_shifts = shifts.filter(is_night_shift=True).count()
    day_shifts = shifts.filter(is_night_shift=False).count()

    if search:
        shifts = shifts.filter(
            Q(name__icontains=search) | Q(description__icontains=search)
        )

    paginator = Paginator(shifts, int(per_page))
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Shifts',
        'shifts': page_obj,
        'search': search,
        'per_page': per_page,
        'total_shifts': total_shifts,
        'active_shifts': active_shifts,
        'night_shifts': night_shifts,
        'day_shifts': day_shifts,
    }
    return render(request, 'hrm/shift_list.html', context)


@login_required
def shift_create(request):
    from .models import Shift
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        start_time = request.POST.get('start_time', '').strip()
        end_time = request.POST.get('end_time', '').strip()
        description = request.POST.get('description', '').strip()
        break_duration = request.POST.get('break_duration', '60').strip() or '60'
        break_start_time = request.POST.get('break_start_time', '').strip() or None
        break_end_time = request.POST.get('break_end_time', '').strip() or None
        grace_period = request.POST.get('grace_period', '15').strip() or '15'
        is_night_shift = request.POST.get('is_night_shift') == 'on'
        is_active = request.POST.get('status', 'active') == 'active'
        working_hours = request.POST.get('working_hours', '8.0').strip() or '8.0'
        if not name or not start_time or not end_time:
            return JsonResponse({'success': False, 'error': 'Name, start time and end time are required.'})
        shift = Shift.objects.create(
            name=name,
            start_time=start_time,
            end_time=end_time,
            description=description,
            break_duration=int(break_duration),
            break_start_time=break_start_time,
            break_end_time=break_end_time,
            grace_period=int(grace_period),
            is_night_shift=is_night_shift,
            is_active=is_active,
            working_hours=float(working_hours),
        )
        return JsonResponse({'success': True, 'id': shift.id, 'name': shift.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def shift_update(request, pk):
    from .models import Shift
    shift = get_object_or_404(Shift, pk=pk)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        start_time = request.POST.get('start_time', '').strip()
        end_time = request.POST.get('end_time', '').strip()
        description = request.POST.get('description', '').strip()
        if not name or not start_time or not end_time:
            return JsonResponse({'success': False, 'error': 'Name, start time and end time are required.'})
        shift.name = name
        shift.start_time = start_time
        shift.end_time = end_time
        shift.description = description
        shift.break_duration = int(request.POST.get('break_duration', '60') or '60')
        shift.break_start_time = request.POST.get('break_start_time', '').strip() or None
        shift.break_end_time = request.POST.get('break_end_time', '').strip() or None
        shift.grace_period = int(request.POST.get('grace_period', '15') or '15')
        shift.is_night_shift = request.POST.get('is_night_shift') == 'on'
        shift.is_active = request.POST.get('status', 'active') == 'active'
        shift.working_hours = float(request.POST.get('working_hours', '8.0') or '8.0')
        shift.save()
        return JsonResponse({'success': True})
    data = {
        'id': shift.id,
        'name': shift.name,
        'start_time': shift.start_time.strftime('%H:%M'),
        'end_time': shift.end_time.strftime('%H:%M'),
        'description': shift.description,
        'break_duration': shift.break_duration,
        'break_start_time': shift.break_start_time.strftime('%H:%M') if shift.break_start_time else '',
        'break_end_time': shift.break_end_time.strftime('%H:%M') if shift.break_end_time else '',
        'grace_period': shift.grace_period,
        'is_night_shift': shift.is_night_shift,
        'is_active': shift.is_active,
        'working_hours': str(shift.working_hours),
    }
    return JsonResponse(data)


@login_required
def shift_delete(request, pk):
    from .models import Shift
    shift = get_object_or_404(Shift, pk=pk)
    if request.method == 'POST':
        shift.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def shift_toggle_status(request, pk):
    from .models import Shift
    shift = get_object_or_404(Shift, pk=pk)
    shift.is_active = not shift.is_active
    shift.save()
    return JsonResponse({'success': True, 'is_active': shift.is_active})


# ==================== Attendance Policies ====================

@login_required
def attendance_policy_list(request):
    from .models import AttendancePolicy
    from django.core.paginator import Paginator
    from django.db.models import Avg
    search = request.GET.get('search', '')
    per_page = request.GET.get('per_page', 9)
    try:
        per_page = int(per_page)
    except (ValueError, TypeError):
        per_page = 9
    qs = AttendancePolicy.objects.all()
    if search:
        qs = qs.filter(name__icontains=search)
    total = qs.count()
    active = qs.filter(is_active=True).count()
    avg_late = qs.aggregate(avg=Avg('late_mark_after'))['avg'] or 0
    avg_overtime = qs.aggregate(avg=Avg('overtime_rate'))['avg'] or 0
    paginator = Paginator(qs, per_page)
    page_num = request.GET.get('page', 1)
    policies = paginator.get_page(page_num)
    context = {
        'page_title': 'Attendance Policies',
        'policies': policies,
        'search': search,
        'per_page': per_page,
        'total_policies': total,
        'active_policies': active,
        'avg_late_grace': round(avg_late),
        'avg_overtime_rate': round(float(avg_overtime), 2),
    }
    return render(request, 'hrm/attendance_policy_list.html', context)


@login_required
def attendance_policy_create(request):
    from .models import AttendancePolicy
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Policy name is required.'})
        policy = AttendancePolicy.objects.create(
            name=name,
            description=request.POST.get('description', '').strip(),
            work_hours_per_day=request.POST.get('work_hours_per_day', 8.0) or 8.0,
            late_mark_after=request.POST.get('late_mark_after', 15) or 15,
            early_departure_grace=request.POST.get('early_departure_grace', 15) or 15,
            overtime_rate=request.POST.get('overtime_rate', 0.0) or 0.0,
            half_day_hours=request.POST.get('half_day_hours', 4.0) or 4.0,
            is_active=(request.POST.get('is_active', 'true').lower() == 'true'),
        )
        return JsonResponse({'success': True, 'id': policy.id, 'name': policy.name})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def attendance_policy_update(request, pk):
    from .models import AttendancePolicy
    policy = get_object_or_404(AttendancePolicy, pk=pk)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Policy name is required.'})
        policy.name = name
        policy.description = request.POST.get('description', '').strip()
        policy.work_hours_per_day = request.POST.get('work_hours_per_day', 8.0) or 8.0
        policy.late_mark_after = request.POST.get('late_mark_after', 15) or 15
        policy.early_departure_grace = request.POST.get('early_departure_grace', 15) or 15
        policy.overtime_rate = request.POST.get('overtime_rate', 0.0) or 0.0
        policy.half_day_hours = request.POST.get('half_day_hours', 4.0) or 4.0
        policy.is_active = (request.POST.get('is_active', 'true').lower() == 'true')
        policy.save()
        return JsonResponse({'success': True})
    data = {
        'id': policy.id,
        'name': policy.name,
        'description': policy.description,
        'work_hours_per_day': str(policy.work_hours_per_day),
        'late_mark_after': policy.late_mark_after,
        'early_departure_grace': policy.early_departure_grace,
        'overtime_rate': str(policy.overtime_rate),
        'half_day_hours': str(policy.half_day_hours),
        'is_active': policy.is_active,
    }
    return JsonResponse(data)


@login_required
def attendance_policy_delete(request, pk):
    from .models import AttendancePolicy
    policy = get_object_or_404(AttendancePolicy, pk=pk)
    if request.method == 'POST':
        policy.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def attendance_policy_toggle_status(request, pk):
    from .models import AttendancePolicy
    policy = get_object_or_404(AttendancePolicy, pk=pk)
    policy.is_active = not policy.is_active
    policy.save()
    return JsonResponse({'success': True, 'is_active': policy.is_active})


# ==================== Attendance Regularization ====================

@login_required
def attendance_regularization_list(request):
    from .models import AttendanceRegularization, Employee, AttendanceRecord
    from django.core.paginator import Paginator

    search = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    per_page = request.GET.get('per_page', 9)
    try:
        per_page = int(per_page)
    except (ValueError, TypeError):
        per_page = 9

    qs = AttendanceRegularization.objects.select_related('employee', 'approved_by', 'attendance_record').all()
    if search:
        qs = qs.filter(
            Q(employee__full_name__icontains=search) |
            Q(reason__icontains=search)
        )
    if status_filter:
        qs = qs.filter(status=status_filter)

    all_regs = AttendanceRegularization.objects.all()
    total_requests = all_regs.count()
    pending_count = all_regs.filter(status='pending').count()
    approved_count = all_regs.filter(status='approved').count()
    rejected_count = all_regs.filter(status='rejected').count()
    approval_rate = round((approved_count / total_requests * 100), 1) if total_requests > 0 else 0

    paginator = Paginator(qs, per_page)
    page_num = request.GET.get('page', 1)
    regularizations = paginator.get_page(page_num)

    employees = Employee.objects.filter(employee_status='active').order_by('full_name')

    context = {
        'page_title': 'Attendance Regularizations',
        'regularizations': regularizations,
        'search': search,
        'status_filter': status_filter,
        'per_page': per_page,
        'status_choices': AttendanceRegularization.STATUS_CHOICES,
        'total_requests': total_requests,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'rejected_count': rejected_count,
        'approval_rate': approval_rate,
        'employees': employees,
    }
    return render(request, 'hrm/attendance_regularization_list.html', context)


@login_required
def attendance_regularization_create(request):
    from .models import AttendanceRegularization, Employee, AttendanceRecord

    if request.method == 'POST':
        employee_id = request.POST.get('employee')
        record_id = request.POST.get('attendance_record') or None
        clock_in = request.POST.get('clock_in') or None
        clock_out = request.POST.get('clock_out') or None
        reason = request.POST.get('reason', '').strip()
        is_draft = request.POST.get('is_draft') == 'true'

        if not employee_id:
            return JsonResponse({'success': False, 'error': 'Employee is required.'})
        if not reason:
            return JsonResponse({'success': False, 'error': 'Reason is required.'})

        employee = Employee.objects.filter(pk=employee_id).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found.'})

        record = AttendanceRecord.objects.filter(pk=record_id).first() if record_id else None
        date_val = record.date if record else request.POST.get('date') or None
        if not date_val:
            return JsonResponse({'success': False, 'error': 'Please select an attendance record or provide a date.'})

        reg = AttendanceRegularization.objects.create(
            employee=employee,
            attendance_record=record,
            date=date_val,
            clock_in=clock_in if clock_in else None,
            clock_out=clock_out if clock_out else None,
            reason=reason,
            status='pending',
            is_draft=is_draft,
        )
        return JsonResponse({'success': True, 'id': reg.id})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def attendance_regularization_update(request, pk):
    from .models import AttendanceRegularization, AttendanceRecord

    reg = get_object_or_404(AttendanceRegularization, pk=pk)

    if request.method == 'POST':
        clock_in = request.POST.get('clock_in') or None
        clock_out = request.POST.get('clock_out') or None
        reason = request.POST.get('reason', '').strip()
        is_draft = request.POST.get('is_draft') == 'true'
        record_id = request.POST.get('attendance_record') or None

        if not reason:
            return JsonResponse({'success': False, 'error': 'Reason is required.'})

        record = AttendanceRecord.objects.filter(pk=record_id).first() if record_id else reg.attendance_record

        reg.clock_in = clock_in if clock_in else None
        reg.clock_out = clock_out if clock_out else None
        reg.reason = reason
        reg.is_draft = is_draft
        reg.attendance_record = record
        if record:
            reg.date = record.date
        reg.save()
        return JsonResponse({'success': True})

    # GET — return JSON for edit modal
    data = {
        'id': reg.id,
        'employee_id': reg.employee_id,
        'employee_name': reg.employee.full_name,
        'attendance_record_id': reg.attendance_record_id,
        'date': str(reg.date),
        'clock_in': reg.clock_in.strftime('%I:%M %p') if reg.clock_in else '',
        'clock_out': reg.clock_out.strftime('%I:%M %p') if reg.clock_out else '',
        'original_clock_in': reg.attendance_record.clock_in.strftime('%I:%M %p') if reg.attendance_record and reg.attendance_record.clock_in else '',
        'original_clock_out': reg.attendance_record.clock_out.strftime('%I:%M %p') if reg.attendance_record and reg.attendance_record.clock_out else '',
        'reason': reg.reason,
        'status': reg.status,
        'is_draft': reg.is_draft,
        'created_at': reg.created_at.strftime('%Y-%m-%d'),
    }
    return JsonResponse(data)


@login_required
def attendance_regularization_delete(request, pk):
    from .models import AttendanceRegularization
    reg = get_object_or_404(AttendanceRegularization, pk=pk)
    if request.method == 'POST':
        reg.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def attendance_regularization_update_status(request, pk):
    from .models import AttendanceRegularization, Employee
    reg = get_object_or_404(AttendanceRegularization, pk=pk)
    if request.method == 'POST':
        new_status = request.POST.get('status', '')
        if new_status not in ['pending', 'approved', 'rejected']:
            return JsonResponse({'success': False, 'error': 'Invalid status.'})
        reg.status = new_status
        reg.save()
        return JsonResponse({'success': True, 'status': reg.status})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def employee_attendance_records_api(request):
    """Return attendance records for a specific employee (for dropdown in regularization modal)."""
    from .models import AttendanceRecord
    employee_id = request.GET.get('employee_id')
    if not employee_id:
        return JsonResponse({'records': []})
    records = AttendanceRecord.objects.filter(employee_id=employee_id).order_by('-date')[:50]
    data = []
    for r in records:
        data.append({
            'id': r.id,
            'date': str(r.date),
            'clock_in': r.clock_in.strftime('%I:%M %p') if r.clock_in else '--:--',
            'clock_out': r.clock_out.strftime('%I:%M %p') if r.clock_out else '--:--',
            'status': r.status,
        })
    return JsonResponse({'records': data})


# ==================== ZKTeco ADMS Push Endpoints (csrf_exempt, plain text) ====================

import logging
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, timedelta

adms_logger = logging.getLogger('hrm.adms')


@csrf_exempt
def iclock_cdata(request):
    """
    GET  /iclock/cdata?SN=XXXX  → return device options (initial handshake)
    POST /iclock/cdata?SN=XXXX&table=ATTLOG → parse raw attendance, save to DB
    """
    from .models import ZKDevice, BiometricAttendance

    # Some firmware sends lowercase 'sn' — be case-insensitive
    sn = (
        request.GET.get('SN') or
        request.GET.get('sn') or
        request.GET.get('Sn') or
        ''
    ).strip()
    if not sn:
        return HttpResponse('Unknown device', content_type='text/plain')

    # Register / update device
    ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '')).split(',')[0].strip()
    try:
        device, created = ZKDevice.objects.get_or_create(serial_number=sn)
        device.ip_address = ip
        device.last_seen = timezone.now()
        device.save(update_fields=['ip_address', 'last_seen'])
        adms_logger.info(f"[CDATA] SN={sn} IP={ip} {'REGISTERED' if created else 'SEEN'} method={request.method}")
    except Exception as e:
        adms_logger.error(f"[CDATA] Failed to register device SN={sn}: {e}")
        return HttpResponse('OK', content_type='text/plain')

    if request.method == 'GET':
        # ZKTeco ADMS handshake response.
        # Date= forces the device to sync its clock to Nepal Standard Time on every handshake.
        import pytz as _pytz
        _nst = _pytz.timezone('Asia/Kathmandu')
        _now_nst = timezone.now().astimezone(_nst)
        _date_str = _now_nst.strftime('%Y-%m-%d %H:%M:%S')
        options = (
            "GET OPTION FROM: {sn}\r\n"
            "ATTLOGStamp=9999\r\n"
            "OPERATIONStamp=9999\r\n"
            "ErrorDelay=60\r\n"
            "Delay=30\r\n"
            "TransTimes=00:00;14:05\r\n"
            "TransInterval=1\r\n"
            "TransFlag=TransData AttLog OpLog\r\n"
            "TimeZone=5.75\r\n"
            "Realtime=1\r\n"
            "Encrypt=0\r\n"
            "Date={date_str}\r\n"
        ).format(sn=sn, date_str=_date_str)
        adms_logger.info(f"[CDATA] Sending handshake to SN={sn} with Date={_date_str}")
        return HttpResponse(options, content_type='text/plain')

    if request.method == 'POST':
        table = request.GET.get('table', '').strip()
        try:
            body = request.body.decode('utf-8', errors='ignore').strip()
        except Exception:
            body = ''
        adms_logger.info(f"[CDATA POST] SN={sn} table={table} body_length={len(body)}")

        if table == 'ATTLOG' and body:
            saved = 0
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split('\t')
                if len(parts) < 2:
                    continue
                try:
                    pin = parts[0].strip()
                    ts_str = parts[1].strip()
                    status = int(parts[2].strip()) if len(parts) > 2 else 0
                    verify = int(parts[3].strip()) if len(parts) > 3 else 0

                    # Device sends local Nepal time (NST, UTC+5:45). Explicitly wrap as NST.
                    naive_dt = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
                    from django.utils.timezone import is_aware
                    import pytz
                    _nst = pytz.timezone('Asia/Kathmandu')
                    punch_dt = naive_dt if is_aware(naive_dt) else _nst.localize(naive_dt)

                    BiometricAttendance.objects.get_or_create(
                        pin=pin,
                        timestamp=punch_dt,
                        defaults={
                            'device': device,
                            'status': status,
                            'verify_mode': verify,
                            'raw_log': line,
                        }
                    )
                    saved += 1
                except (ValueError, IndexError) as e:
                    adms_logger.warning(f"[CDATA POST] Parse error: {e} line='{line}'")
                    continue
                except Exception as e:
                    adms_logger.error(f"[CDATA POST] Unexpected error: {e} line='{line}'")
                    continue

            adms_logger.info(f"[CDATA POST] SN={sn} saved={saved} attendance lines")

            # Update device transaction/user counts
            try:
                device.transaction_count = BiometricAttendance.objects.filter(device=device).count()
                device.user_count = BiometricAttendance.objects.filter(device=device).values('pin').distinct().count()
                device.save(update_fields=['transaction_count', 'user_count'])
            except Exception as e:
                adms_logger.error(f"[CDATA POST] Failed to update counts for SN={sn}: {e}")

        # Always return OK so the device doesn't mark the server as down
        return HttpResponse('OK', content_type='text/plain')

    return HttpResponse('OK', content_type='text/plain')


@csrf_exempt
def iclock_getrequest(request):
    """GET /iclock/getrequest?SN=XXXX → heartbeat, update last_seen, push Nepal time sync"""
    from .models import ZKDevice
    import pytz, time as _time

    sn = (
        request.GET.get('SN') or
        request.GET.get('sn') or
        request.GET.get('Sn') or
        ''
    ).strip()
    if sn:
        ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '')).split(',')[0].strip()
        try:
            device, created = ZKDevice.objects.get_or_create(serial_number=sn)
            device.ip_address = ip
            device.last_seen = timezone.now()
            device.save(update_fields=['ip_address', 'last_seen'])
            adms_logger.debug(f"[HEARTBEAT] SN={sn} IP={ip} {'REGISTERED' if created else 'SEEN'}")
        except Exception as e:
            adms_logger.error(f"[HEARTBEAT] Failed to update device SN={sn}: {e}")

    # Force-overwrite the device clock with Nepal Standard Time (UTC+5:45).
    # SET TIME is the direct write command — it overwrites the device RTC unconditionally.
    # Multiple formats sent for maximum firmware compatibility across K20 Pro firmware variants.
    nst = pytz.timezone('Asia/Kathmandu')
    now_nst = timezone.now().astimezone(nst)
    nst_str = now_nst.strftime('%Y-%m-%d %H:%M:%S')
    compact_str = now_nst.strftime('%Y%m%d%H%M%S')
    seq = int(_time.time())  # monotonically increasing command ID
    # C:ID:SET TIME  — direct RTC overwrite (most reliable for K20 Pro)
    # C:ID:SET OPTION Date=  — alternative SET OPTION form used by some firmware
    # C:ID:DATE TIME  — legacy compact format fallback
    sync_cmd = (
        f'C:{seq}:SET TIME {nst_str}\r\n'
        f'C:{seq+1}:SET OPTION Date={nst_str}\r\n'
        f'C:{seq+2}:DATE TIME {compact_str}\r\n'
        f'OK'
    )
    adms_logger.info(f"[HEARTBEAT] Force-writing time to SN={sn}: {nst_str}")
    return HttpResponse(sync_cmd, content_type='text/plain')


@csrf_exempt
def iclock_devicecmd(request):
    """POST /iclock/devicecmd?SN=XXXX → device command response, always OK"""
    return HttpResponse('OK', content_type='text/plain')


# ==================== Biometric Attendance List (Admin) ====================

@login_required
def biometric_attendance(request):
    """Aggregated attendance view: groups raw punches by (pin, date)."""
    from .models import BiometricAttendance, Employee
    from django.db.models import Min, Max, Count, Subquery, OuterRef
    from django.db.models.functions import TruncDate
    from django.utils import timezone as tz

    # Parse query params early
    search_q = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    # Build employee name lookup
    emp_name_map = {}
    for emp in Employee.objects.all():
        if emp.employee_code:
            emp_name_map[emp.employee_code] = emp.full_name

    # Base queryset with date filters pushed into ORM
    base_qs = BiometricAttendance.objects.all()

    if date_from:
        try:
            df = datetime.strptime(date_from, '%Y-%m-%d').date()
            base_qs = base_qs.filter(timestamp__date__gte=df)
        except ValueError:
            pass
    if date_to:
        try:
            dt_val = datetime.strptime(date_to, '%Y-%m-%d').date()
            base_qs = base_qs.filter(timestamp__date__lte=dt_val)
        except ValueError:
            pass

    # PIN search at ORM level
    if search_q:
        # Find employee codes that match by PIN or name
        matching_pins = set()
        sq_lower = search_q.lower()
        for code, name in emp_name_map.items():
            if sq_lower in code.lower() or sq_lower in name.lower():
                matching_pins.add(code)
        if matching_pins:
            base_qs = base_qs.filter(pin__in=matching_pins)
        else:
            # Fallback: search by PIN directly (for PINs not in employee table)
            base_qs = base_qs.filter(pin__icontains=search_q)

    # Subquery: get the device serial_number from the earliest punch per (pin, date) group
    # Uses timestamp__date= directly to avoid ORM ambiguity with the outer 'punch_date' annotation
    first_device_sq = BiometricAttendance.objects.filter(
        pin=OuterRef('pin'),
        device__isnull=False,
        timestamp__date=OuterRef('punch_date'),
    ).order_by('timestamp').values('device__serial_number')[:1]

    qs = base_qs.annotate(
        punch_date=TruncDate('timestamp')
    ).values('pin', 'punch_date').annotate(
        clock_in=Min('timestamp'),
        clock_out=Max('timestamp'),
        total_entries=Count('id'),
        device_sn=Subquery(first_device_sq),
    ).order_by('-punch_date', 'pin')

    # Convert to Nepal Standard Time (UTC+5:45) for display
    import pytz
    local_tz = pytz.timezone('Asia/Kathmandu')

    # Build records list
    records = []
    for row in qs:
        pin = row['pin']
        punch_date = row['punch_date']
        clock_in_dt = row['clock_in']
        clock_out_dt = row['clock_out']
        total = row['total_entries']

        # Convert UTC timestamps to Nepal time before extracting .time()
        clock_in_local = clock_in_dt.astimezone(local_tz).time() if clock_in_dt else None
        clock_out_local = clock_out_dt.astimezone(local_tz).time() if clock_out_dt and total > 1 else None

        records.append({
            'pin': pin,
            'employee_name': emp_name_map.get(pin, f'Employee {pin}'),
            'date': punch_date,
            'clock_in': clock_in_local,
            'clock_out': clock_out_local,
            'total_entries': total,
            'device_sn': row['device_sn'] or '—',
        })

    total_records = len(records)

    # Per-page
    per_page = request.GET.get('per_page', '10')
    try:
        per_page = int(per_page)
        if per_page not in [10, 20, 50, 100]:
            per_page = 10
    except (ValueError, TypeError):
        per_page = 10

    paginator = Paginator(records, per_page)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Biometric Attendance',
        'page_obj': page_obj,
        'total_records': total_records,
        'search_q': search_q,
        'date_from': date_from,
        'date_to': date_to,
        'per_page': per_page,
        'per_page_options': [10, 20, 50, 100],
    }
    return render(request, 'hrm/biometric_attendance.html', context)


@login_required
def biometric_attendance_view(request, pin, date_str):
    """Return all raw punches for a given employee PIN on a specific date."""
    from .models import BiometricAttendance, Employee
    from django.utils import timezone as tz

    try:
        punch_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid date format.'})

    punches = BiometricAttendance.objects.filter(
        pin=pin,
        timestamp__date=punch_date,
    ).select_related('device').order_by('timestamp')

    emp = Employee.objects.filter(employee_code=pin).first()
    emp_name = emp.full_name if emp else f'Employee {pin}'

    import pytz
    local_tz = pytz.timezone('Asia/Kathmandu')

    punch_list = []
    for p in punches:
        local_ts = p.timestamp.astimezone(local_tz)
        punch_list.append({
            'time': local_ts.strftime('%I:%M %p'),
            'status': p.get_status_display(),
            'verify_mode': p.verify_mode,
            'device': p.device.serial_number if p.device else '—',
            'raw_log': p.raw_log,
        })

    punch_count = len(punch_list)
    clock_in = punch_list[0]['time'] if punch_count > 0 else ''
    clock_out = punch_list[-1]['time'] if punch_count > 1 else ''

    return JsonResponse({
        'success': True,
        'record': {
            'pin': pin,
            'employee_name': emp_name,
            'date': date_str,
            'clock_in': clock_in,
            'clock_out': clock_out,
            'total_entries': punch_count,
        },
        'punches': punch_list,
    })


@login_required
def biometric_sync_all(request):
    """Re-aggregate from raw logs and sync to attendance records."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    from .models import BiometricAttendance
    count = BiometricAttendance.objects.count()

    # Sync biometric punches into AttendanceRecord
    _sync_biometric_to_attendance()

    return JsonResponse({
        'success': True,
        'message': f'Re-aggregated from {count} raw punch records and synced to attendance. Table refreshed.',
    })


@login_required
def biometric_sync_single(request, pin, date_str):
    """Re-aggregate a single employee's raw logs for a date — no API call needed."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    from .models import BiometricAttendance

    try:
        punch_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid date format.'})

    count = BiometricAttendance.objects.filter(pin=pin, timestamp__date=punch_date).count()
    return JsonResponse({
        'success': True,
        'message': f'Found {count} raw punches for PIN {pin} on {date_str}. Data is up to date.',
    })


@login_required
def biometric_attendance_delete(request, pin, date_str):
    """Delete all raw punches for a PIN on a specific date."""
    from .models import BiometricAttendance

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        punch_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid date format.'})

    deleted, _ = BiometricAttendance.objects.filter(pin=pin, timestamp__date=punch_date).delete()
    return JsonResponse({'success': True, 'message': f'Deleted {deleted} records for PIN {pin} on {date_str}.'})


# ==================== ZKTeco Device Settings Page ====================

@login_required
def zekto_settings(request):
    from .models import ZKDevice

    devices = ZKDevice.objects.all()
    now = timezone.now()

    device_list = []
    for d in devices:
        is_online = d.last_seen and (now - d.last_seen) < timedelta(minutes=5)
        device_list.append({
            'id': d.id,
            'serial_number': d.serial_number,
            'name': d.name or '',
            'model_name': d.model_name or '',
            'branch': d.branch or '',
            'ip_address': d.ip_address or '—',
            'last_seen': d.last_seen,
            'face_count': d.face_count,
            'fingerprint_count': d.fingerprint_count,
            'transaction_count': d.transaction_count,
            'user_count': d.user_count,
            'is_online': is_online,
        })

    context = {
        'page_title': 'Zekto Settings',
        'devices': device_list,
        'device_count': len(device_list),
    }
    return render(request, 'hrm/zekto_settings.html', context)


@login_required
def zekto_device_add(request):
    """Manually add a new ZKDevice via AJAX POST."""
    from .models import ZKDevice
    from django.core.validators import validate_ipv46_address
    from django.core.exceptions import ValidationError as DjangoValidationError
    import json

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON.'})

    serial_number = data.get('serial_number', '').strip()
    if not serial_number:
        return JsonResponse({'success': False, 'error': 'Serial number is required.'})

    if ZKDevice.objects.filter(serial_number=serial_number).exists():
        return JsonResponse({'success': False, 'error': f'A device with serial number "{serial_number}" already exists.'})

    ip_raw = data.get('ip_address', '').strip()
    ip_address = None
    if ip_raw:
        try:
            validate_ipv46_address(ip_raw)
            ip_address = ip_raw
        except DjangoValidationError:
            return JsonResponse({'success': False, 'error': 'Invalid IP address format.'})

    device = ZKDevice.objects.create(
        serial_number=serial_number,
        name=data.get('name', '').strip(),
        branch=data.get('branch', '').strip(),
        model_name=data.get('model_name', '').strip(),
        ip_address=ip_address,
    )

    return JsonResponse({
        'success': True,
        'message': f'Device "{device.name or device.serial_number}" added successfully.',
        'device_id': device.id,
    })


@login_required
def zekto_device_update(request, pk):
    """Update device name, branch, model_name via AJAX POST."""
    from .models import ZKDevice

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        device = ZKDevice.objects.get(pk=pk)
    except ZKDevice.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Device not found.'})

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON.'})

    name = data.get('name', '').strip()
    if not name:
        return JsonResponse({'success': False, 'error': 'Device name is required.'})

    device.name = name
    device.branch = data.get('branch', '').strip()
    device.model_name = data.get('model_name', '').strip()
    device.save(update_fields=['name', 'branch', 'model_name'])

    return JsonResponse({'success': True, 'message': f'Device "{device.name}" updated successfully.'})


@login_required
def zekto_device_delete(request, pk):
    """Delete a device and optionally its attendance records."""
    from .models import ZKDevice

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        device = ZKDevice.objects.get(pk=pk)
    except ZKDevice.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Device not found.'})

    sn = device.serial_number
    device.delete()
    return JsonResponse({'success': True, 'message': f'Device {sn} deleted successfully.'})


@login_required
def zekto_device_sync(request, pk):
    """Recalculate device counts (transactions, users, fingerprints, faces) from DB."""
    from .models import ZKDevice, BiometricAttendance

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        device = ZKDevice.objects.get(pk=pk)
    except ZKDevice.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Device not found.'})

    device.transaction_count = BiometricAttendance.objects.filter(device=device).count()
    device.user_count = BiometricAttendance.objects.filter(device=device).values('pin').distinct().count()
    device.save(update_fields=['transaction_count', 'user_count'])

    return JsonResponse({
        'success': True,
        'message': f'Device {device.serial_number} counts synced.',
        'data': {
            'transaction_count': device.transaction_count,
            'user_count': device.user_count,
            'face_count': device.face_count,
            'fingerprint_count': device.fingerprint_count,
        }
    })


@login_required
def zekto_device_detail(request, pk):
    """Return device detail as JSON for the update modal."""
    from .models import ZKDevice

    try:
        device = ZKDevice.objects.get(pk=pk)
    except ZKDevice.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Device not found.'})

    now = timezone.now()
    is_online = device.last_seen and (now - device.last_seen) < timedelta(minutes=5)

    return JsonResponse({
        'success': True,
        'device': {
            'id': device.id,
            'serial_number': device.serial_number,
            'name': device.name or '',
            'model_name': device.model_name or '',
            'branch': device.branch or '',
            'ip_address': device.ip_address or '',
            'last_seen': device.last_seen.strftime('%Y/%m/%d %I:%M:%S %p') if device.last_seen else 'Never',
            'face_count': device.face_count,
            'fingerprint_count': device.fingerprint_count,
            'transaction_count': device.transaction_count,
            'user_count': device.user_count,
            'is_online': is_online,
        }
    })


# ==================== Payroll Management ====================

@login_required
def payroll_management(request):
    context = {
        'page_title': 'Payroll Management',
    }
    return render(request, 'hrm/payroll_management.html', context)


@login_required
def salary_component_list(request):
    from .models import SalaryComponent

    # Handle POST for adding component directly on the page
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Component name is required.')
            return redirect('hrm:salary_component_list')
        SalaryComponent.objects.create(
            name=name,
            component_type=request.POST.get('component_type', 'earning'),
            description=request.POST.get('description', '').strip(),
            calculation_type=request.POST.get('calculation_type', 'fixed'),
            amount=request.POST.get('amount') or 0,
            is_taxable=request.POST.get('is_taxable') == 'on',
            is_active=request.POST.get('is_active', 'active') in ('on', 'active'),
        )
        messages.success(request, f'Salary component "{name}" created successfully!')
        return redirect('hrm:salary_component_list')

    search_query = request.GET.get('search', '').strip()
    type_filter = request.GET.get('type', '')
    calc_filter = request.GET.get('calc', '')
    status_filter = request.GET.get('status', '')
    components = SalaryComponent.objects.all()
    if search_query:
        components = components.filter(
            Q(name__icontains=search_query) | Q(description__icontains=search_query)
        )
    if type_filter:
        components = components.filter(component_type=type_filter)
    if calc_filter:
        components = components.filter(calculation_type=calc_filter)
    if status_filter == 'active':
        components = components.filter(is_active=True)
    elif status_filter == 'inactive':
        components = components.filter(is_active=False)

    total_components = SalaryComponent.objects.count()
    total_earnings = SalaryComponent.objects.filter(component_type='earning').count()
    total_deductions = SalaryComponent.objects.filter(component_type='deduction').count()

    context = {
        'page_title': 'Salary Components',
        'components': components,
        'search_query': search_query,
        'type_filter': type_filter,
        'calc_filter': calc_filter,
        'status_filter': status_filter,
        'total_components': total_components,
        'total_earnings': total_earnings,
        'total_deductions': total_deductions,
    }
    return render(request, 'hrm/salary_component_list.html', context)


@login_required
def salary_component_detail(request, pk):
    from .models import SalaryComponent
    comp = get_object_or_404(SalaryComponent, id=pk)
    return JsonResponse({
        'success': True,
        'component': {
            'id': comp.id,
            'name': comp.name,
            'component_type': comp.component_type,
            'description': comp.description,
            'calculation_type': comp.calculation_type,
            'amount': str(comp.amount),
            'is_taxable': comp.is_taxable,
            'is_active': comp.is_active,
        }
    })


@login_required
def salary_component_update(request, pk):
    from .models import SalaryComponent
    comp = get_object_or_404(SalaryComponent, id=pk)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Component name is required.'})

        comp.name = name
        comp.component_type = request.POST.get('component_type', comp.component_type)
        comp.description = request.POST.get('description', '').strip()
        comp.calculation_type = request.POST.get('calculation_type', comp.calculation_type)
        comp.amount = request.POST.get('amount') or 0
        comp.is_taxable = request.POST.get('is_taxable') == 'on'
        comp.is_active = request.POST.get('is_active', 'active') in ('on', 'active')
        comp.save()
        return JsonResponse({'success': True, 'message': f'Component "{comp.name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def salary_component_delete(request, pk):
    from .models import SalaryComponent
    comp = get_object_or_404(SalaryComponent, id=pk)

    if request.method == 'POST':
        comp_name = comp.name
        comp.delete()
        return JsonResponse({'success': True, 'message': f'Component "{comp_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def salary_component_toggle_status(request, pk):
    from .models import SalaryComponent
    comp = get_object_or_404(SalaryComponent, id=pk)

    if request.method == 'POST':
        comp.is_active = not comp.is_active
        comp.save()
        status = 'active' if comp.is_active else 'inactive'
        return JsonResponse({'success': True, 'message': f'Component "{comp.name}" is now {status}.'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def employee_salary_list(request):
    from .models import EmployeeSalary, SalaryComponent, Employee

    # Handle POST for creating a new employee salary
    if request.method == 'POST':
        employee_id = request.POST.get('employee', '')
        basic_salary = request.POST.get('basic_salary', '0')
        effective_date = request.POST.get('effective_date', '')
        notes = request.POST.get('notes', '').strip()
        component_ids = request.POST.getlist('components')

        if not employee_id or not effective_date:
            messages.error(request, 'Employee and effective date are required.')
            return redirect('hrm:employee_salary_list')

        try:
            employee = Employee.objects.get(id=int(employee_id))
        except (Employee.DoesNotExist, ValueError, TypeError):
            messages.error(request, 'Selected employee not found.')
            return redirect('hrm:employee_salary_list')

        try:
            basic_salary_val = round(float(basic_salary), 2) if basic_salary else 0
            if basic_salary_val < 0:
                raise ValueError('Salary cannot be negative.')
        except (ValueError, TypeError):
            messages.error(request, 'Invalid basic salary value.')
            return redirect('hrm:employee_salary_list')

        is_active_val = request.POST.get('is_active', '1') == '1'
        salary = EmployeeSalary.objects.create(
            employee=employee,
            basic_salary=basic_salary_val,
            effective_date=effective_date,
            notes=notes,
            is_active=is_active_val,
        )
        if component_ids:
            salary.components.set(component_ids)
        messages.success(request, f'Salary for "{employee.full_name}" assigned successfully!')
        return redirect('hrm:employee_salary_list')

    # GET request — list with search, filters, pagination
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')
    per_page = request.GET.get('per_page', '10')

    salaries = EmployeeSalary.objects.select_related('employee').prefetch_related('components').all()

    if search_query:
        salaries = salaries.filter(
            Q(employee__full_name__icontains=search_query) |
            Q(employee__employee_id__icontains=search_query)
        )
    if status_filter == 'active':
        salaries = salaries.filter(is_active=True)
    elif status_filter == 'inactive':
        salaries = salaries.filter(is_active=False)

    # Pagination
    try:
        per_page_int = int(per_page)
    except (ValueError, TypeError):
        per_page_int = 10
    paginator = Paginator(salaries, per_page_int)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    # Data for dropdowns
    employees = Employee.objects.all().order_by('full_name')
    components = SalaryComponent.objects.filter(is_active=True).order_by('component_type', 'name')
    earnings_components = components.filter(component_type='earning')
    deductions_components = components.filter(component_type='deduction')

    context = {
        'page_title': 'Employee Salaries',
        'salaries': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'per_page': per_page,
        'employees': employees,
        'components': components,
        'earnings_components': earnings_components,
        'deductions_components': deductions_components,
    }
    return render(request, 'hrm/employee_salary_list.html', context)


@login_required
def employee_salary_detail(request, pk):
    from .models import EmployeeSalary
    salary = get_object_or_404(EmployeeSalary.objects.select_related('employee').prefetch_related('components'), id=pk)
    return JsonResponse({
        'success': True,
        'salary': {
            'id': salary.id,
            'employee_id': salary.employee.id,
            'employee_name': salary.employee.full_name,
            'employee_code': salary.employee.employee_id,
            'basic_salary': str(salary.basic_salary),
            'effective_date': salary.effective_date.isoformat(),
            'notes': salary.notes,
            'is_active': salary.is_active,
            'created_at': salary.created_at.strftime('%b %d, %Y %I:%M %p'),
            'components': [
                {
                    'id': c.id,
                    'name': c.name,
                    'component_type': c.component_type,
                    'calculation_type': c.get_calculation_type_display(),
                    'amount': str(c.amount),
                }
                for c in salary.components.all()
            ],
        }
    })


@login_required
def employee_salary_update(request, pk):
    from .models import EmployeeSalary, Employee
    salary = get_object_or_404(EmployeeSalary, id=pk)

    if request.method == 'POST':
        employee_id = request.POST.get('employee', '')
        basic_salary = request.POST.get('basic_salary', '0')
        effective_date = request.POST.get('effective_date', '')
        notes = request.POST.get('notes', '').strip()
        component_ids = request.POST.getlist('components')

        if not employee_id or not effective_date:
            return JsonResponse({'success': False, 'error': 'Employee and effective date are required.'})

        try:
            employee = Employee.objects.get(id=int(employee_id))
        except (Employee.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Selected employee not found.'})

        try:
            basic_salary_val = round(float(basic_salary), 2) if basic_salary else 0
            if basic_salary_val < 0:
                raise ValueError('Salary cannot be negative.')
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Invalid basic salary value.'})

        is_active_val = request.POST.get('is_active', '1') == '1'
        salary.employee = employee
        salary.basic_salary = basic_salary_val
        salary.effective_date = effective_date
        salary.notes = notes
        salary.is_active = is_active_val
        salary.save()
        salary.components.set(component_ids)
        return JsonResponse({'success': True, 'message': f'Salary for "{employee.full_name}" updated successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def employee_salary_delete(request, pk):
    from .models import EmployeeSalary
    salary = get_object_or_404(EmployeeSalary, id=pk)

    if request.method == 'POST':
        emp_name = salary.employee.full_name
        salary.delete()
        return JsonResponse({'success': True, 'message': f'Salary record for "{emp_name}" deleted successfully!'})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def employee_salary_toggle_status(request, pk):
    from .models import EmployeeSalary
    salary = get_object_or_404(EmployeeSalary, id=pk)

    if request.method == 'POST':
        salary.is_active = not salary.is_active
        salary.save()
        status = 'active' if salary.is_active else 'inactive'
        return JsonResponse({'success': True, 'message': f'Salary for "{salary.employee.full_name}" is now {status}.', 'is_active': salary.is_active})

    return JsonResponse({'success': False, 'error': 'Invalid request method.'})


@login_required
def payroll_run_list(request):
    from .models import PayrollRun

    # Handle POST for creating a new payroll run
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        frequency = request.POST.get('frequency', '').strip()
        pay_period_start = request.POST.get('pay_period_start', '').strip()
        pay_period_end = request.POST.get('pay_period_end', '').strip()
        pay_date = request.POST.get('pay_date', '').strip()
        notes = request.POST.get('notes', '').strip()

        errors = []
        if not title:
            errors.append('Title is required.')
        if not frequency:
            errors.append('Payroll Frequency is required.')
        if not pay_period_start:
            errors.append('Pay Period Start is required.')
        if not pay_period_end:
            errors.append('Pay Period End is required.')
        if not pay_date:
            errors.append('Pay Date is required.')

        if pay_period_start and pay_period_end:
            from datetime import datetime as dt
            try:
                start_dt = dt.strptime(pay_period_start, '%Y-%m-%d').date()
                end_dt = dt.strptime(pay_period_end, '%Y-%m-%d').date()
                if end_dt < start_dt:
                    errors.append('Pay Period End must be after Pay Period Start.')
            except ValueError:
                errors.append('Invalid date format for Pay Period.')

        if errors:
            for err in errors:
                messages.error(request, err)
            return redirect('hrm:payroll_run_list')

        PayrollRun.objects.create(
            title=title,
            frequency=frequency,
            pay_period_start=pay_period_start,
            pay_period_end=pay_period_end,
            pay_date=pay_date,
            notes=notes,
            created_by=request.user,
        )
        messages.success(request, f'Payroll Run "{title}" created successfully!')
        return redirect('hrm:payroll_run_list')

    # GET — list with search, filters, pagination
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')
    frequency_filter = request.GET.get('frequency', '')
    per_page = request.GET.get('per_page', '10')

    runs = PayrollRun.objects.all()

    if search_query:
        runs = runs.filter(Q(title__icontains=search_query))
    if status_filter:
        runs = runs.filter(status=status_filter)
    if frequency_filter:
        runs = runs.filter(frequency=frequency_filter)

    # Pagination
    try:
        per_page_int = int(per_page)
    except (ValueError, TypeError):
        per_page_int = 10
    paginator = Paginator(runs, per_page_int)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Payroll Runs',
        'runs': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'frequency_filter': frequency_filter,
        'per_page': per_page,
    }
    return render(request, 'hrm/payroll_run_list.html', context)


@login_required
def payroll_run_detail(request, pk):
    from .models import PayrollRun
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    try:
        run = PayrollRun.objects.get(pk=pk)
    except PayrollRun.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Payroll Run not found.'}, status=404)
    data = {
        'success': True,
        'run': {
            'id': run.pk,
            'title': run.title,
            'frequency': run.get_frequency_display(),
            'pay_period_start': run.pay_period_start.strftime('%b %d, %Y') if run.pay_period_start else '-',
            'pay_period_end': run.pay_period_end.strftime('%b %d, %Y') if run.pay_period_end else '-',
            'pay_date': run.pay_date.strftime('%b %d, %Y') if run.pay_date else '-',
            'status': run.get_status_display(),
            'status_key': run.status,
            'employee_count': run.employee_count,
            'gross_pay': str(run.gross_pay),
            'net_pay': str(run.net_pay),
            'total_amount': str(run.total_amount),
            'notes': run.notes,
            'created_by': str(run.created_by) if run.created_by else '-',
            'created_at': run.created_at.strftime('%b %d, %Y %I:%M %p'),
        }
    }
    return JsonResponse(data)


@login_required
def payroll_run_delete(request, pk):
    from .models import PayrollRun
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})
    try:
        run = PayrollRun.objects.get(pk=pk)
    except PayrollRun.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Payroll Run not found.'}, status=404)
    title = run.title
    run.delete()
    return JsonResponse({'success': True, 'message': f'Payroll Run "{title}" deleted successfully.'})


@login_required
def generate_payslips(request, pk):
    from .models import PayrollRun, Payslip, Employee, EmployeeSalary
    import datetime

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)

    try:
        run = PayrollRun.objects.get(pk=pk)
    except PayrollRun.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Payroll Run not found.'}, status=404)

    if run.status == 'cancelled':
        return JsonResponse({'success': False, 'error': 'Cannot generate payslips for a cancelled payroll run.'}, status=400)

    employees = Employee.objects.filter(employee_status='active')
    if not employees.exists():
        return JsonResponse({'success': False, 'error': 'No active employees found.'}, status=400)

    created_count = 0
    skipped_count = 0
    today = datetime.date.today()

    for employee in employees:
        # Skip if payslip already exists for this run + employee
        if Payslip.objects.filter(payroll_run=run, employee=employee).exists():
            skipped_count += 1
            continue

        # Get latest active salary record for this employee
        salary_record = (
            EmployeeSalary.objects
            .filter(employee=employee, is_active=True)
            .prefetch_related('components')
            .order_by('-effective_date')
            .first()
        )

        basic = salary_record.basic_salary if salary_record else employee.base_salary

        earnings = Decimal('0')
        deductions = Decimal('0')

        if salary_record:
            components = salary_record.components.filter(is_active=True)

            # First pass: compute a provisional gross (basic + fixed earnings + %_of_basic earnings)
            # used later for %_of_gross calculations
            pre_gross = basic
            for comp in components:
                if comp.component_type == 'earning':
                    if comp.calculation_type == 'fixed':
                        pre_gross += comp.amount
                    elif comp.calculation_type == 'percentage_of_basic':
                        pre_gross += (basic * comp.amount / Decimal('100')).quantize(Decimal('0.01'))

            # Second pass: full component calculation
            for comp in components:
                if comp.component_type == 'earning':
                    if comp.calculation_type == 'fixed':
                        earnings += comp.amount
                    elif comp.calculation_type == 'percentage_of_basic':
                        earnings += (basic * comp.amount / Decimal('100')).quantize(Decimal('0.01'))
                    elif comp.calculation_type in ('percentage_of_gross', 'percentage_of_ctc'):
                        earnings += (pre_gross * comp.amount / Decimal('100')).quantize(Decimal('0.01'))
                elif comp.component_type == 'deduction':
                    if comp.calculation_type == 'fixed':
                        deductions += comp.amount
                    elif comp.calculation_type == 'percentage_of_basic':
                        deductions += (basic * comp.amount / Decimal('100')).quantize(Decimal('0.01'))
                    elif comp.calculation_type in ('percentage_of_gross', 'percentage_of_ctc'):
                        deductions += (pre_gross * comp.amount / Decimal('100')).quantize(Decimal('0.01'))

        gross_salary = (basic + earnings).quantize(Decimal('0.01'))
        total_deductions_val = deductions.quantize(Decimal('0.01'))
        net_salary = max(gross_salary - total_deductions_val, Decimal('0'))

        Payslip.objects.create(
            payroll_run=run,
            employee=employee,
            gross_salary=gross_salary,
            total_deductions=total_deductions_val,
            net_salary=net_salary,
            status='generated',
            generated_on=today,
        )
        created_count += 1

    # Refresh run totals from all payslips (including previously existing ones)
    from django.db.models import Sum as _Sum
    agg = Payslip.objects.filter(payroll_run=run).aggregate(
        cnt=Count('id'),
        g=_Sum('gross_salary'),
        n=_Sum('net_salary'),
    )
    run.employee_count = agg['cnt'] or 0
    run.gross_pay = agg['g'] or Decimal('0')
    run.net_pay = agg['n'] or Decimal('0')
    if run.status in ('draft', 'processing') and run.employee_count > 0:
        run.status = 'completed'
    run.save(update_fields=['employee_count', 'gross_pay', 'net_pay', 'status', 'updated_at'])

    msg = f'Generated {created_count} payslip(s) successfully.'
    if skipped_count:
        msg += f' {skipped_count} already existed and were skipped.'

    return JsonResponse({
        'success': True,
        'message': msg,
        'created': created_count,
        'skipped': skipped_count,
    })


@login_required
def payslip_list(request):
    from .models import Payslip
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')
    per_page = request.GET.get('per_page', '10')

    payslips = Payslip.objects.select_related('employee', 'payroll_run').all()

    if search_query:
        payslips = payslips.filter(
            Q(employee__full_name__icontains=search_query) |
            Q(payslip_number__icontains=search_query)
        )
    if status_filter:
        payslips = payslips.filter(status=status_filter)

    # Pagination — constrain per_page to allowed values only
    VALID_PER_PAGE = [10, 25, 50, 100]
    try:
        per_page_int = int(per_page)
    except (ValueError, TypeError):
        per_page_int = 10
    if per_page_int not in VALID_PER_PAGE:
        per_page_int = 10
    per_page = str(per_page_int)  # normalize back so template comparison always matches
    paginator = Paginator(payslips, per_page_int)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Payslips',
        'payslips': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'per_page': per_page,
    }
    return render(request, 'hrm/payslip_list.html', context)


@login_required
def payslip_detail(request, pk):
    from .models import Payslip
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    try:
        slip = Payslip.objects.select_related('employee', 'payroll_run').get(pk=pk)
    except Payslip.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Payslip not found.'}, status=404)
    data = {
        'success': True,
        'payslip': {
            'id': slip.pk,
            'payslip_number': slip.payslip_number,
            'employee': slip.employee.full_name,
            'employee_id': slip.employee.employee_id,
            'payroll_run': slip.payroll_run.title,
            'pay_period_start': slip.payroll_run.pay_period_start.strftime('%Y-%m-%d') if slip.payroll_run.pay_period_start else '-',
            'pay_period_end': slip.payroll_run.pay_period_end.strftime('%Y-%m-%d') if slip.payroll_run.pay_period_end else '-',
            'pay_date': slip.payroll_run.pay_date.strftime('%Y-%m-%d') if slip.payroll_run.pay_date else '-',
            'gross_salary': str(slip.gross_salary),
            'total_deductions': str(slip.total_deductions),
            'net_salary': str(slip.net_salary),
            'status': slip.get_status_display(),
            'status_key': slip.status,
            'paid_date': slip.paid_date.strftime('%b %d, %Y') if slip.paid_date else '-',
            'generated_on': slip.generated_on.strftime('%Y-%m-%d') if slip.generated_on else '-',
            'created_at': slip.created_at.strftime('%b %d, %Y %I:%M %p'),
        }
    }
    return JsonResponse(data)


@login_required
def payslip_download(request, pk):
    from .models import Payslip, EmployeeSalary, AttendanceRecord, AttendancePolicy
    import calendar
    from datetime import date as dt_date
    from decimal import Decimal, ROUND_HALF_UP

    try:
        slip = Payslip.objects.select_related(
            'employee', 'employee__department', 'employee__designation', 'payroll_run'
        ).get(pk=pk)
    except Payslip.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Payslip not found.'}, status=404)

    # Mark as downloaded
    if slip.status == 'generated':
        slip.status = 'downloaded'
        slip.save(update_fields=['status', 'updated_at'])

    employee = slip.employee
    run = slip.payroll_run

    # ── Determine month / year from pay period ──
    month = run.month or (run.pay_period_start.month if run.pay_period_start else timezone.now().month)
    year = run.year or (run.pay_period_start.year if run.pay_period_start else timezone.now().year)

    # ── Working days in month (Mon-Fri) ──
    first_day = dt_date(year, month, 1)
    last_day = dt_date(year, month, calendar.monthrange(year, month)[1])
    working_days = 0
    d = first_day
    while d <= last_day:
        if d.weekday() < 5:
            working_days += 1
        d += timedelta(days=1)

    # ── Attendance summary ──
    records = AttendanceRecord.objects.filter(employee=employee, date__year=year, date__month=month)
    present_days = Decimal('0')
    half_days = Decimal('0')
    absent_days = Decimal('0')
    on_leave_days = Decimal('0')
    total_overtime_hours = Decimal('0')

    for rec in records:
        if rec.status in ('present', 'late'):
            present_days += 1
        elif rec.status == 'absent':
            absent_days += 1
        elif rec.status == 'half_day':
            half_days += 1
        elif rec.status == 'on_leave':
            on_leave_days += 1
        total_overtime_hours += rec.overtime_hours

    paid_leave_days = on_leave_days

    # ── Salary component breakdown ──
    salary_record = (
        EmployeeSalary.objects.filter(employee=employee, is_active=True)
        .prefetch_related('components')
        .order_by('-effective_date')
        .first()
    )
    basic_salary = salary_record.basic_salary if salary_record else (employee.base_salary or Decimal('0'))
    components = salary_record.components.filter(is_active=True) if salary_record else []

    # Two-pass calculation (matches generate_payslips logic)
    pre_gross = basic_salary
    for comp in components:
        if comp.component_type == 'earning':
            if comp.calculation_type == 'fixed':
                pre_gross += comp.amount
            elif comp.calculation_type == 'percentage_of_basic':
                pre_gross += (basic_salary * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    earnings_list = []
    deductions_list = []
    total_earnings_comp = Decimal('0')
    total_deductions_comp = Decimal('0')

    for comp in components:
        if comp.calculation_type == 'fixed':
            calc_amount = comp.amount
        elif comp.calculation_type == 'percentage_of_basic':
            calc_amount = (basic_salary * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif comp.calculation_type in ('percentage_of_gross', 'percentage_of_ctc'):
            calc_amount = (pre_gross * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            calc_amount = comp.amount

        if comp.component_type == 'earning':
            earnings_list.append({'name': comp.name, 'amount': calc_amount})
            total_earnings_comp += calc_amount
        else:
            deductions_list.append({'name': comp.name, 'amount': calc_amount})
            total_deductions_comp += calc_amount

    total_earnings = basic_salary + total_earnings_comp

    # ── Attendance-based deductions ──
    per_day_salary = Decimal('0')
    if working_days > 0:
        per_day_salary = (total_earnings / Decimal(str(working_days))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    half_day_deduction = (per_day_salary * half_days * Decimal('0.5')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    absent_deduction = (per_day_salary * absent_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    attendance_deduction = half_day_deduction + absent_deduction

    # ── Overtime ──
    overtime_rate = Decimal('0')
    try:
        policy = AttendancePolicy.objects.filter(is_active=True).first()
        if policy:
            overtime_rate = policy.overtime_rate
    except Exception:
        pass
    overtime_amount = (total_overtime_hours * overtime_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    # ── Net salary ──
    total_deductions = total_deductions_comp + attendance_deduction
    net_salary = total_earnings + overtime_amount - total_deductions

    # ── Build side-by-side salary rows ──
    max_rows = max(len(earnings_list), len(deductions_list))
    salary_rows = []
    for i in range(max_rows):
        row = {}
        if i < len(earnings_list):
            row['earning_name'] = earnings_list[i]['name']
            row['earning_amount'] = earnings_list[i]['amount']
        else:
            row['earning_name'] = ''
            row['earning_amount'] = None
        if i < len(deductions_list):
            row['deduction_name'] = deductions_list[i]['name']
            row['deduction_amount'] = deductions_list[i]['amount']
        else:
            row['deduction_name'] = ''
            row['deduction_amount'] = None
        salary_rows.append(row)

    context = {
        'payslip': slip,
        'employee': employee,
        'company_name': 'HRM System',
        'pay_period_start': run.pay_period_start,
        'pay_period_end': run.pay_period_end,
        'pay_date': run.pay_date,
        'basic_salary': basic_salary,
        'working_days': working_days,
        'present_days': present_days,
        'absent_days': absent_days,
        'half_days': half_days,
        'paid_leave_days': paid_leave_days,
        'total_overtime_hours': total_overtime_hours,
        'earnings_list': earnings_list,
        'deductions_list': deductions_list,
        'salary_rows': salary_rows,
        'total_earnings': total_earnings + overtime_amount,
        'total_deductions': total_deductions,
        'overtime_amount': overtime_amount,
        'overtime_rate': overtime_rate,
        'attendance_deduction': attendance_deduction,
        'net_salary': net_salary,
    }
    return render(request, 'hrm/payslip_print.html', context)


@login_required
def payroll_calculation(request, pk):
    from .models import EmployeeSalary, AttendanceRecord, AttendancePolicy
    import calendar
    from datetime import date as dt_date
    from decimal import Decimal, ROUND_HALF_UP

    salary = get_object_or_404(
        EmployeeSalary.objects.select_related('employee').prefetch_related('components'),
        id=pk
    )
    employee = salary.employee

    # Determine the payroll month/year from query param or default to current
    now = timezone.now()
    sel_month = request.GET.get('month', '')
    sel_year = request.GET.get('year', '')
    try:
        month = int(sel_month) if sel_month else now.month
        year = int(sel_year) if sel_year else now.year
        if month < 1 or month > 12 or year < 2000 or year > 2100:
            raise ValueError
    except (ValueError, TypeError):
        month, year = now.month, now.year

    month_name = calendar.month_name[month]
    working_days_in_month = 0
    first_day = dt_date(year, month, 1)
    last_day = dt_date(year, month, calendar.monthrange(year, month)[1])

    # Count working days (Mon-Fri) in the month
    d = first_day
    while d <= last_day:
        if d.weekday() < 5:
            working_days_in_month += 1
        d += timedelta(days=1)

    # Fetch attendance records for this employee in the selected month
    attendance_records = AttendanceRecord.objects.filter(
        employee=employee,
        date__year=year,
        date__month=month
    ).order_by('date')

    # Attendance summary
    present_days = Decimal('0')
    half_days = Decimal('0')
    absent_days = Decimal('0')
    on_leave_days = Decimal('0')
    total_overtime_hours = Decimal('0')
    total_working_hours = Decimal('0')

    attendance_data = []
    for rec in attendance_records:
        overtime_display = str(rec.overtime_hours) + 'h' if rec.overtime_hours > 0 else '-'
        status_tags = []
        if rec.status == 'present':
            status_tags.append(('Present', 'present'))
            present_days += 1
        elif rec.status == 'absent':
            status_tags.append(('Absent', 'absent'))
            absent_days += 1
        elif rec.status == 'late':
            status_tags.append(('Present', 'present'))
            status_tags.append(('Late', 'late'))
            present_days += 1
        elif rec.status == 'half_day':
            status_tags.append(('Half Day', 'half_day'))
            half_days += 1
        elif rec.status == 'on_leave':
            status_tags.append(('On Leave', 'on_leave'))
            on_leave_days += 1

        if rec.is_early_departure and rec.status not in ('absent', 'on_leave', 'half_day'):
            status_tags.append(('Early', 'early'))
        if rec.is_late_arrival and rec.status not in ('late',):
            pass  # already shown as Late for 'late' status

        total_overtime_hours += rec.overtime_hours
        total_working_hours += rec.working_hours

        attendance_data.append({
            'date': rec.date,
            'clock_in': rec.clock_in,
            'clock_out': rec.clock_out,
            'total_hours': str(rec.working_hours) + 'h',
            'overtime': overtime_display,
            'status_tags': status_tags,
        })

    # Paid leave days = on_leave_days (treat on_leave as paid)
    paid_leave_days = on_leave_days
    total_unpaid_leave = Decimal('0')  # unpaid = absent + half_day * 0.5

    # Salary calculations
    basic_salary = salary.basic_salary
    components = salary.components.all()

    # Calculate component amounts
    earnings = []
    deductions = []
    total_earnings_components = Decimal('0')
    total_deductions_amount = Decimal('0')

    for comp in components:
        if comp.calculation_type == 'fixed':
            calc_amount = comp.amount
        elif comp.calculation_type == 'percentage_of_basic':
            calc_amount = (basic_salary * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif comp.calculation_type == 'percentage_of_gross':
            calc_amount = (basic_salary * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif comp.calculation_type == 'percentage_of_ctc':
            calc_amount = (basic_salary * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            calc_amount = comp.amount

        if comp.component_type == 'earning':
            earnings.append({'name': comp.name, 'amount': calc_amount})
            total_earnings_components += calc_amount
        else:
            deductions.append({'name': comp.name, 'amount': calc_amount})
            total_deductions_amount += calc_amount

    total_earnings = basic_salary + total_earnings_components

    # Per day salary
    per_day_salary = Decimal('0')
    if working_days_in_month > 0:
        per_day_salary = (total_earnings / Decimal(str(working_days_in_month))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    # Deductions based on attendance
    half_day_deduction_days = half_days * Decimal('0.5')
    total_unpaid_leave = absent_days + half_day_deduction_days
    unpaid_leave_deduction = (per_day_salary * absent_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    half_day_deduction = (per_day_salary * half_day_deduction_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    absent_day_deduction = unpaid_leave_deduction

    # Overtime calculation
    overtime_rate = Decimal('0')
    try:
        policy = AttendancePolicy.objects.filter(is_active=True).first()
        if policy:
            overtime_rate = policy.overtime_rate
    except Exception:
        pass
    overtime_amount = (total_overtime_hours * overtime_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    # Net salary
    total_attendance_deduction = unpaid_leave_deduction + half_day_deduction
    net_salary = total_earnings - total_attendance_deduction + overtime_amount - total_deductions_amount

    # Build available months for the dropdown (last 12 months)
    available_months = []
    for i in range(12):
        m = now.month - i
        y = now.year
        if m <= 0:
            m += 12
            y -= 1
        month_start = dt_date(y, m, 1)
        month_end = dt_date(y, m, calendar.monthrange(y, m)[1])
        label = f"{calendar.month_name[m]} {y} Payroll ({m}/1/{y} - {m}/{calendar.monthrange(y, m)[1]}/{y})"
        available_months.append({
            'month': m,
            'year': y,
            'label': label,
            'selected': (m == month and y == year),
        })

    context = {
        'page_title': f'Payroll Calculation - {employee.full_name}',
        'salary': salary,
        'employee': employee,
        'month': month,
        'year': year,
        'month_name': month_name,
        'working_days_in_month': working_days_in_month,
        'basic_salary': basic_salary,
        'net_salary': net_salary,
        'present_days': present_days,
        'half_days': half_days,
        'absent_days': absent_days,
        'paid_leave_days': paid_leave_days,
        'total_unpaid_leave': total_unpaid_leave,
        'total_overtime_hours': total_overtime_hours,
        'earnings': earnings,
        'deductions': deductions,
        'total_earnings': total_earnings,
        'total_deductions_amount': total_deductions_amount,
        'per_day_salary': per_day_salary,
        'unpaid_leave_deduction': unpaid_leave_deduction,
        'half_day_deduction': half_day_deduction,
        'half_day_deduction_days': half_day_deduction_days,
        'absent_day_deduction': absent_day_deduction,
        'overtime_amount': overtime_amount,
        'overtime_rate': overtime_rate,
        'total_attendance_deduction': total_attendance_deduction,
        'attendance_data': attendance_data,
        'available_months': available_months,
    }
    return render(request, 'hrm/payroll_calculation.html', context)


@login_required
def attendance_report(request):
    from .models import AttendanceRecord, Employee, Department
    from django.core.paginator import Paginator
    from django.db.models import Q, Count, Sum
    from django.http import HttpResponse
    import csv
    from datetime import datetime

    # ── Filters from GET ────────────────────────────────────────────────────
    search     = request.GET.get('search', '').strip()
    date_from  = request.GET.get('date_from', '')
    date_to    = request.GET.get('date_to', '')
    department = request.GET.get('department', '')
    status     = request.GET.get('status', '')
    per_page   = request.GET.get('per_page', 25)
    export     = request.GET.get('export', '')

    try:
        per_page = int(per_page)
        if per_page not in [10, 25, 50, 100]:
            per_page = 25
    except (ValueError, TypeError):
        per_page = 25

    qs = AttendanceRecord.objects.select_related(
        'employee', 'employee__department', 'employee__branch', 'shift'
    ).order_by('-date', 'employee__full_name')

    if search:
        qs = qs.filter(
            Q(employee__full_name__icontains=search) |
            Q(employee__employee_id__icontains=search)
        )
    if date_from:
        try:
            qs = qs.filter(date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_to:
        try:
            qs = qs.filter(date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass
    if department:
        qs = qs.filter(employee__department_id=department)
    if status:
        qs = qs.filter(status=status)

    # ── Aggregate stats over the filtered queryset ───────────────────────────
    stats = qs.aggregate(
        total=Count('id'),
        present=Count('id', filter=Q(status='present')),
        absent=Count('id', filter=Q(status='absent')),
        late=Count('id', filter=Q(status='late')),
        half_day=Count('id', filter=Q(status='half_day')),
        on_leave=Count('id', filter=Q(status='on_leave')),
        total_working_hours=Sum('working_hours'),
        total_overtime=Sum('overtime_hours'),
    )

    # ── Per-employee summary ─────────────────────────────────────────────────
    _emp_qs = (
        qs.values(
            'employee__id',
            'employee__employee_id',
            'employee__full_name',
            'employee__department__name',
        )
        .annotate(
            total=Count('id'),
            present=Count('id', filter=Q(status='present')),
            absent=Count('id', filter=Q(status='absent')),
            late=Count('id', filter=Q(status='late')),
            half_day=Count('id', filter=Q(status='half_day')),
            on_leave=Count('id', filter=Q(status='on_leave')),
            total_working_hours=Sum('working_hours'),
        )
        .order_by('employee__full_name')
    )
    emp_summary = []
    for e in _emp_qs:
        pct = round(e['present'] * 100 / e['total']) if e['total'] > 0 else 0
        e['pct'] = pct
        e['pct_color'] = '#16a34a' if pct >= 90 else ('#d97706' if pct >= 70 else '#e11d48')
        emp_summary.append(e)

    # ── CSV export ───────────────────────────────────────────────────────────
    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="attendance_report.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Employee ID', 'Employee Name', 'Department', 'Date',
            'Check In', 'Check Out', 'Working Hours', 'Overtime Hours', 'Status',
        ])
        for rec in qs.iterator():
            writer.writerow([
                rec.employee.employee_id,
                rec.employee.full_name,
                rec.employee.department.name if rec.employee.department else '',
                rec.date.strftime('%Y-%m-%d'),
                rec.clock_in.strftime('%H:%M') if rec.clock_in else '',
                rec.clock_out.strftime('%H:%M') if rec.clock_out else '',
                rec.working_hours,
                rec.overtime_hours,
                rec.get_status_display(),
            ])
        return response

    # ── Pagination ────────────────────────────────────────────────────────────
    paginator = Paginator(qs, per_page)
    page_num  = request.GET.get('page', 1)
    records   = paginator.get_page(page_num)

    departments = Department.objects.filter(status='active').order_by('name')
    employees = Employee.objects.filter(employee_status='active').select_related('department').order_by('full_name')

    context = {
        'records':     records,
        'stats':       stats,
        'emp_summary': emp_summary,
        'departments': departments,
        'employees':   employees,
        'search':      search,
        'date_from':   date_from,
        'date_to':     date_to,
        'department':  department,
        'sel_status':  status,
        'per_page':    per_page,
        'status_choices': AttendanceRecord.STATUS_CHOICES,
    }
    return render(request, 'hrm/attendance_report.html', context)


@login_required
def employee_period_attendance(request):
    from .models import AttendanceRecord, Employee
    from django.http import JsonResponse
    from datetime import date, timedelta
    import calendar

    emp_id = request.GET.get('employee_id', '').strip()
    period = request.GET.get('period', 'month')
    ref_date_str = request.GET.get('ref_date', '')

    if not emp_id:
        return JsonResponse({'error': 'Employee ID required'}, status=400)
    try:
        employee = Employee.objects.select_related('department', 'designation').get(id=emp_id)
    except Employee.DoesNotExist:
        return JsonResponse({'error': 'Employee not found'}, status=404)

    try:
        ref_date = date.fromisoformat(ref_date_str) if ref_date_str else date.today()
    except ValueError:
        ref_date = date.today()

    if period == 'day':
        date_from = date_to = ref_date
    elif period == 'week':
        weekday = ref_date.weekday()
        date_from = ref_date - timedelta(days=weekday)
        date_to = date_from + timedelta(days=6)
    else:
        date_from = ref_date.replace(day=1)
        _, last_day = calendar.monthrange(ref_date.year, ref_date.month)
        date_to = ref_date.replace(day=last_day)

    records_qs = AttendanceRecord.objects.filter(
        employee=employee, date__gte=date_from, date__lte=date_to
    ).select_related('shift').order_by('date')

    records_data = []
    for rec in records_qs:
        records_data.append({
            'date': rec.date.isoformat(),
            'date_display': rec.date.strftime('%d %b %Y'),
            'day_name': rec.date.strftime('%A'),
            'day_short': rec.date.strftime('%a'),
            'day_num': rec.date.day,
            'clock_in': rec.clock_in.strftime('%H:%M') if rec.clock_in else None,
            'clock_out': rec.clock_out.strftime('%H:%M') if rec.clock_out else None,
            'working_hours': float(rec.working_hours) if rec.working_hours else 0,
            'overtime_hours': float(rec.overtime_hours) if rec.overtime_hours else 0,
            'status': rec.status,
            'status_display': rec.get_status_display(),
            'shift': rec.shift.name if rec.shift else None,
            'is_late_arrival': rec.is_late_arrival,
            'is_early_departure': rec.is_early_departure,
            'is_holiday': rec.is_holiday,
            'notes': rec.notes,
        })

    total_working = sum(r['working_hours'] for r in records_data)
    total_ot = sum(r['overtime_hours'] for r in records_data)
    sc = {}
    for r in records_data:
        sc[r['status']] = sc.get(r['status'], 0) + 1

    _, month_days = calendar.monthrange(ref_date.year, ref_date.month) if period == 'month' else (None, None)

    return JsonResponse({
        'employee': {
            'id': employee.id,
            'employee_id': employee.employee_id,
            'full_name': employee.full_name,
            'department': employee.department.name if employee.department else '—',
            'designation': employee.designation.name if employee.designation else '—',
        },
        'period': period,
        'year': ref_date.year,
        'month': ref_date.month,
        'month_name': ref_date.strftime('%B'),
        'date_from': date_from.isoformat(),
        'date_to': date_to.isoformat(),
        'date_from_display': date_from.strftime('%d %b %Y'),
        'date_to_display': date_to.strftime('%d %b %Y'),
        'month_first_weekday': date_from.weekday() if period == 'month' else None,
        'month_days': month_days,
        'records': records_data,
        'records_by_date': {r['date']: r for r in records_data},
        'summary': {
            'total': len(records_data),
            'present': sc.get('present', 0),
            'absent': sc.get('absent', 0),
            'late': sc.get('late', 0),
            'half_day': sc.get('half_day', 0),
            'on_leave': sc.get('on_leave', 0),
            'total_working_hours': round(total_working, 2),
            'total_overtime_hours': round(total_ot, 2),
        },
    })


@login_required
def leave_report(request):
    from .models import LeaveRequest, LeaveType, Employee, Department
    from django.core.paginator import Paginator
    from django.db.models import Q, Count, Sum
    from django.http import HttpResponse
    import csv
    from datetime import datetime

    # ── Filters from GET ────────────────────────────────────────────────────
    search     = request.GET.get('search', '').strip()
    date_from  = request.GET.get('date_from', '')
    date_to    = request.GET.get('date_to', '')
    leave_type = request.GET.get('leave_type', '')
    department = request.GET.get('department', '')
    status     = request.GET.get('status', '')
    per_page   = request.GET.get('per_page', 25)
    export     = request.GET.get('export', '')

    try:
        per_page = int(per_page)
        if per_page not in [10, 25, 50, 100]:
            per_page = 25
    except (ValueError, TypeError):
        per_page = 25

    qs = LeaveRequest.objects.select_related(
        'employee', 'employee__department', 'leave_type', 'approved_by'
    ).order_by('-created_at')

    if search:
        qs = qs.filter(
            Q(employee__full_name__icontains=search) |
            Q(employee__employee_id__icontains=search)
        )
    if date_from:
        try:
            qs = qs.filter(start_date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_to:
        try:
            qs = qs.filter(end_date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass
    if leave_type:
        qs = qs.filter(leave_type_id=leave_type)
    if department:
        qs = qs.filter(employee__department_id=department)
    if status:
        qs = qs.filter(status=status)

    # ── Aggregate stats ──────────────────────────────────────────────────────
    stats = qs.aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(status='pending')),
        approved=Count('id', filter=Q(status='approved')),
        rejected=Count('id', filter=Q(status='rejected')),
        cancelled=Count('id', filter=Q(status='cancelled')),
        total_days=Sum('days'),
        approved_days=Sum('days', filter=Q(status='approved')),
    )

    # ── CSV export ───────────────────────────────────────────────────────────
    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="leave_report.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Employee ID', 'Employee Name', 'Department', 'Leave Type',
            'From', 'To', 'Days', 'Reason', 'Status', 'Applied On',
        ])
        for req in qs.iterator():
            writer.writerow([
                req.employee.employee_id,
                req.employee.full_name,
                req.employee.department.name if req.employee.department else '',
                req.leave_type.name if req.leave_type else '',
                req.start_date.strftime('%Y-%m-%d'),
                req.end_date.strftime('%Y-%m-%d'),
                req.days,
                req.reason,
                req.get_status_display(),
                req.created_at.strftime('%Y-%m-%d'),
            ])
        return response

    # ── Pagination ────────────────────────────────────────────────────────────
    paginator = Paginator(qs, per_page)
    page_num  = request.GET.get('page', 1)
    requests_page = paginator.get_page(page_num)

    leave_types = LeaveType.objects.filter(is_active=True).order_by('name')
    departments = Department.objects.filter(status='active').order_by('name')

    context = {
        'requests':       requests_page,
        'stats':          stats,
        'leave_types':    leave_types,
        'departments':    departments,
        'search':         search,
        'date_from':      date_from,
        'date_to':        date_to,
        'sel_leave_type': leave_type,
        'sel_department': department,
        'sel_status':     status,
        'per_page':       per_page,
    }
    return render(request, 'hrm/leave_report.html', context)

