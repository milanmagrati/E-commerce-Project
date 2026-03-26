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

@login_required
def attendance_list(request):
    from .models import AttendanceRecord, Employee, Shift
    from django.core.paginator import Paginator
    from django.db.models import Q, Count, Sum
    from datetime import date as dt_date

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



