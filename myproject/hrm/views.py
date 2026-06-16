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

from .models import (
    Branch, Department, Designation, DocumentType, Employee, EmployeeDocument, 
    AwardType, Award, Promotion, Resignation, Termination, Warning, Complaint, 
    AssetType, Asset, Payslip, PayslipAdjustment, PayslipAuditLog
)
from .forms import EmployeeForm, EmployeeDocumentForm


@login_required
def hrm_dashboard(request):
    from .models import (
        Employee, Branch, Department, AttendanceRecord, LeaveRequest,
        LeaveType, Promotion, Resignation, Warning, Complaint,
        PayrollRun, AdvancePayment,
    )
    from django.db.models.functions import TruncMonth, ExtractMonth
    from calendar import month_name

    now = timezone.now()
    today = now.date()
    current_year = today.year
    month_start = today.replace(day=1)

    # ── Stats cards ──
    total_employees = Employee.objects.count()
    active_employees = Employee.objects.filter(employee_status='active').count()
    employees_this_month = Employee.objects.filter(date_of_joining__gte=month_start).count()
    branches_count = Branch.objects.filter(status='active').count()
    departments_count = Department.objects.filter(status='active').count()

    today_attendance = AttendanceRecord.objects.filter(date=today)
    present_today = today_attendance.filter(status__in=['present', 'late']).count()
    on_leave_today = today_attendance.filter(status='on_leave').count()
    attendance_rate = round((present_today / active_employees * 100), 1) if active_employees else 0

    pending_leaves = LeaveRequest.objects.filter(status='pending').count()
    total_warnings = Warning.objects.filter(status__in=['issued', 'draft']).count()
    pending_promotions = Promotion.objects.filter(status='pending').count()

    stats = {
        'total_employees': total_employees,
        'employees_this_month': employees_this_month,
        'branches': branches_count,
        'departments': departments_count,
        'attendance_rate': attendance_rate,
        'present_today': present_today,
        'pending_leaves': pending_leaves,
        'on_leave_today': on_leave_today,
        'active_warnings': total_warnings,
        'pending_promotions': pending_promotions,
    }

    # ── Department Distribution Chart ──
    chart_colors = [
        '#6366f1', '#f97316', '#ec4899', '#ef4444', '#3b82f6', '#10b981',
        '#8b5cf6', '#06b6d4', '#f59e0b', '#14b8a6', '#d946ef', '#22c55e',
    ]
    dept_data = (
        Department.objects.filter(status='active')
        .annotate(emp_count=Count('employees'))
        .filter(emp_count__gt=0)
        .order_by('-emp_count')[:12]
    )
    department_distribution = {
        'labels': [d.name for d in dept_data],
        'data': [d.emp_count for d in dept_data],
        'colors': chart_colors[:len(dept_data)],
    }
    if not department_distribution['labels']:
        department_distribution = {'labels': ['No Data'], 'data': [0], 'colors': ['#d1d5db']}

    # ── Hiring Trend (last 6 months by date_of_joining) ──
    hiring_labels = []
    hiring_data = []
    for i in range(5, -1, -1):
        total_months = today.year * 12 + today.month - 1 - i
        y = total_months // 12
        m = total_months % 12 + 1
        hiring_labels.append(f"{month_name[m][:3]} {y}")
        count = Employee.objects.filter(
            date_of_joining__year=y, date_of_joining__month=m
        ).count()
        hiring_data.append(count)

    hiring_trend = {
        'labels': hiring_labels,
        'data': hiring_data,
    }

    # ── Leave Status Distribution Chart ──
    leave_status_qs = (
        LeaveRequest.objects.values('status')
        .annotate(cnt=Count('id'))
        .order_by('status')
    )
    leave_status_map = {
        'pending': ('#f59e0b', 'Pending'),
        'approved': ('#10b981', 'Approved'),
        'rejected': ('#ef4444', 'Rejected'),
        'cancelled': ('#6b7280', 'Cancelled'),
    }
    leave_status_labels = []
    leave_status_data = []
    leave_status_colors = []
    for item in leave_status_qs:
        info = leave_status_map.get(item['status'], ('#94a3b8', item['status'].title()))
        leave_status_labels.append(info[1])
        leave_status_data.append(item['cnt'])
        leave_status_colors.append(info[0])

    leave_status_distribution = {
        'labels': leave_status_labels or ['No Data'],
        'data': leave_status_data or [0],
        'colors': leave_status_colors or ['#d1d5db'],
    }

    # ── Leave Types Chart ──
    leave_type_colors = [
        '#10b981', '#6b7280', '#06b6d4', '#f97316', '#ef4444',
        '#ec4899', '#3b82f6', '#22c55e', '#8b5cf6', '#14b8a6',
    ]
    leave_type_qs = (
        LeaveType.objects.filter(is_active=True)
        .annotate(req_count=Count('requests'))
        .filter(req_count__gt=0)
        .order_by('-req_count')[:10]
    )
    leave_types = {
        'labels': [lt.name for lt in leave_type_qs],
        'data': [lt.req_count for lt in leave_type_qs],
        'colors': leave_type_colors[:len(leave_type_qs)],
    }
    if not leave_types['labels']:
        leave_types = {'labels': ['No Data'], 'data': [0], 'colors': ['#d1d5db']}

    # ── Employee Growth (monthly cumulative for current year) ──
    base_count = Employee.objects.filter(
        date_of_joining__year__lt=current_year
    ).count()
    monthly_hires = (
        Employee.objects.filter(date_of_joining__year=current_year)
        .values('date_of_joining__month')
        .annotate(cnt=Count('id'))
    )
    month_hire_map = {item['date_of_joining__month']: item['cnt'] for item in monthly_hires}
    growth_labels = []
    growth_data = []
    cumulative = base_count
    for m in range(1, 13):
        growth_labels.append(month_name[m])
        if m <= today.month:
            cumulative += month_hire_map.get(m, 0)
            growth_data.append(cumulative)
        else:
            growth_data.append(None)

    employee_growth = {
        'labels': growth_labels,
        'data': growth_data,
    }

    # ── Recent Leave Applications ──
    recent_leaves_qs = (
        LeaveRequest.objects.select_related('employee', 'leave_type')
        .order_by('-created_at')[:8]
    )
    recent_leaves = []
    for lr in recent_leaves_qs:
        recent_leaves.append({
            'employee': lr.employee.full_name,
            'status': lr.get_status_display(),
            'leave_type': lr.leave_type.name if lr.leave_type else 'N/A',
            'start_date': lr.start_date.strftime('%Y-%m-%d'),
            'end_date': lr.end_date.strftime('%Y-%m-%d'),
        })

    # ── Recent Promotions ──
    recent_promotions_qs = (
        Promotion.objects.select_related('employee', 'new_designation')
        .order_by('-created_at')[:5]
    )
    recent_promotions = []
    for p in recent_promotions_qs:
        recent_promotions.append({
            'employee': p.employee.full_name,
            'status': p.get_status_display(),
            'new_designation': p.new_designation.name if p.new_designation else 'N/A',
            'date': p.promotion_date.strftime('%Y-%m-%d') if p.promotion_date else '',
        })

    # ── Recent Warnings ──
    recent_warnings_qs = (
        Warning.objects.select_related('employee')
        .order_by('-created_at')[:5]
    )
    recent_warnings = []
    for w in recent_warnings_qs:
        recent_warnings.append({
            'employee': w.employee.full_name,
            'status': w.get_status_display(),
            'severity': w.get_severity_display(),
            'subject': w.subject,
            'date': w.warning_date.strftime('%Y-%m-%d') if w.warning_date else '',
        })

    # ── Recent Resignations ──
    recent_resignations_qs = (
        Resignation.objects.select_related('employee')
        .order_by('-created_at')[:5]
    )
    recent_resignations = []
    for r in recent_resignations_qs:
        recent_resignations.append({
            'employee': r.employee.full_name,
            'status': r.get_status_display(),
            'date': r.resignation_date.strftime('%Y-%m-%d') if r.resignation_date else '',
            'last_day': r.last_working_day.strftime('%Y-%m-%d') if r.last_working_day else 'TBD',
        })

    context = {
        'page_title': 'Dashboard',
        'stats': stats,
        'department_distribution': department_distribution,
        'hiring_trend': hiring_trend,
        'leave_status_distribution': leave_status_distribution,
        'leave_types': leave_types,
        'employee_growth': employee_growth,
        'recent_leaves': recent_leaves,
        'total_leaves': LeaveRequest.objects.count(),
        'recent_promotions': recent_promotions,
        'total_promotions': Promotion.objects.count(),
        'recent_warnings': recent_warnings,
        'total_warnings': Warning.objects.count(),
        'recent_resignations': recent_resignations,
        'total_resignations': Resignation.objects.count(),
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

    employees = Employee.objects.select_related('branch', 'department', 'designation', 'shift', 'attendance_policy').all()

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
        Employee.objects.select_related('branch', 'department', 'designation', 'shift', 'attendance_policy'),
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
            Q(employee__full_name__icontains=search_query) |
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
    elided_page_range = paginator.get_elided_page_range(warnings.number, on_each_side=2, on_ends=1)

    context = {
        'page_title': 'Warnings',
        'warnings': warnings,
        'total_warnings': paginator.count,
        'elided_page_range': elided_page_range,
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


def _sync_biometric_to_attendance(recent_days=None):
    """
    Aggregate raw BiometricAttendance punches into AttendanceRecord entries.
    For each unique (pin, date) group (grouped in Nepal Standard Time), finds the
    matching Employee by employee_code, computes clock_in (earliest punch) and
    clock_out (latest punch), calculates working hours, and creates or updates
    the AttendanceRecord.

    Uses pure-Python timezone grouping to avoid MySQL CONVERT_TZ which requires
    timezone tables that may not be installed on shared hosting servers.
    """
    from .models import BiometricAttendance, AttendanceRecord, Employee, Shift
    from collections import defaultdict
    import pytz

    NPT = pytz.timezone('Asia/Kathmandu')

    # Build PIN → Employee lookup (prefetch shift and attendance_policy)
    emp_map = {}
    for emp in Employee.objects.select_related('shift', 'attendance_policy').all():
        if emp.employee_code:
            code = str(emp.employee_code)
            emp_map[code] = emp
            emp_map[code.lstrip('0')] = emp

    if not emp_map:
        return

    # Fetch all raw punches and group them in Python using Nepal timezone.
    # This bypasses MySQL CONVERT_TZ entirely — no timezone tables needed.
    qs = BiometricAttendance.objects.all()
    if recent_days:
        from django.utils import timezone
        cutoff = timezone.now() - timezone.timedelta(days=recent_days)
        qs = qs.filter(timestamp__gte=cutoff)
    
    raw_punches = qs.values('pin', 'timestamp').order_by('pin', 'timestamp')

    # grouped[(pin, nepal_date)] = [utc_timestamp, ...]
    grouped = defaultdict(list)
    for rp in raw_punches:
        ts = rp['timestamp']
        if ts is None:
            continue
        # Convert UTC → Nepal Standard Time (UTC+5:45) to get the correct local date
        local_ts = ts.astimezone(NPT)
        punch_date = local_ts.date()
        grouped[(rp['pin'], punch_date)].append(ts)

    for (pin, punch_date), timestamps in grouped.items():
        first_punch = min(timestamps)
        last_punch = max(timestamps)
        punch_count = len(timestamps)

        pin_str = str(pin)
        employee = emp_map.get(pin_str) or emp_map.get(pin_str.lstrip('0'))
        if not employee:
            continue

        # Convert UTC-stored timestamps to Nepal time
        clock_in_time = first_punch.astimezone(NPT).time() if first_punch else None
        clock_out_time = last_punch.astimezone(NPT).time() if last_punch and punch_count > 1 else None

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
        status = 'present'

        # Try to find shift: existing record > employee's assigned shift
        existing_record = AttendanceRecord.objects.filter(
            employee=employee, date=punch_date
        ).first()

        if existing_record and existing_record.shift:
            shift = existing_record.shift
        elif employee.shift_id:
            shift = employee.shift

        # Get employee's attendance policy for grace periods
        policy = employee.attendance_policy if employee.attendance_policy_id else None

        if shift and clock_in_time and clock_out_time:
            from datetime import datetime, timedelta
            # Subtract break duration to get effective working hours
            break_hrs = (shift.break_duration or 0) / 60.0
            if working_hours > break_hrs:
                working_hours = round(working_hours - break_hrs, 2)
            shift_hours = float(shift.working_hours)
            if working_hours > shift_hours:
                overtime_hours = round(working_hours - shift_hours, 2)

            # Use AttendancePolicy grace values if available, else shift.grace_period
            late_grace = policy.late_mark_after if policy else (shift.grace_period or 0)
            early_grace = policy.early_departure_grace if policy else (shift.grace_period or 0)

            # punch_date is guaranteed non-None here (checked above), but guard defensively
            shift_start = datetime.combine(punch_date, shift.start_time)
            cin_full = datetime.combine(punch_date, clock_in_time)
            cout_full = datetime.combine(punch_date, clock_out_time)

            if shift.end_time:
                shift_end = datetime.combine(punch_date, shift.end_time)
                # Handle night shifts spanning midnight
                if shift_end <= shift_start:
                    shift_end += timedelta(days=1)
                    if cout_full < cin_full:
                        cout_full += timedelta(days=1)
                if cout_full < shift_end - timedelta(minutes=early_grace):
                    is_early = True

            if cin_full > shift_start + timedelta(minutes=late_grace):
                is_late = True

            # Auto-set status based on calculations
            if policy and working_hours > 0 and working_hours <= float(policy.half_day_hours):
                status = 'half_day'
            elif is_late:
                status = 'late'

        # Create or update the AttendanceRecord
        record, created = AttendanceRecord.objects.update_or_create(
            employee=employee,
            date=punch_date,
            defaults={
                'clock_in': clock_in_time,
                'clock_out': clock_out_time,
                'shift': shift,
                'status': status,
                'working_hours': working_hours,
                'overtime_hours': overtime_hours,
                'is_late_arrival': is_late,
                'is_early_departure': is_early,
            }
        )


@login_required
def attendance_list(request):
    from .models import AttendanceRecord, Employee, Shift
    from django.core.paginator import Paginator
    from django.db.models import Q, Count, Sum
    from datetime import date as dt_date

    # Auto-sync on page load. We sync all days as requested.
    _sync_biometric_to_attendance()

    search = request.GET.get('search', '')
    per_page = request.GET.get('per_page', 9)
    try:
        per_page = int(per_page)
    except (ValueError, TypeError):
        per_page = 9

    qs = AttendanceRecord.objects.select_related('employee', 'shift').order_by('-date', 'employee__full_name')
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

        employee = Employee.objects.select_related('shift', 'attendance_policy').filter(pk=employee_id).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found.'})

        if AttendanceRecord.objects.filter(employee=employee, date=date_val).exists():
            return JsonResponse({'success': False, 'error': 'Attendance already exists for this employee on this date.'})

        # Use shift from form, fallback to employee's assigned shift
        shift = Shift.objects.filter(pk=shift_id).first() if shift_id else employee.shift
        policy = employee.attendance_policy

        working_hours = 0
        overtime_hours = 0
        is_late = False
        is_early = False

        if clock_in and clock_out:
            cin = datetime.strptime(clock_in[:5], '%H:%M')
            cout = datetime.strptime(clock_out[:5], '%H:%M')
            diff = (cout - cin).total_seconds() / 3600
            if diff < 0:
                diff += 24
            working_hours = round(diff, 2)

            if shift:
                # Subtract break duration to get effective working hours
                break_hrs = (shift.break_duration or 0) / 60.0
                if working_hours > break_hrs:
                    working_hours = round(working_hours - break_hrs, 2)
                shift_hours = float(shift.working_hours)
                if working_hours > shift_hours:
                    overtime_hours = round(working_hours - shift_hours, 2)

                shift_start = datetime.combine(datetime.today(), shift.start_time)
                cin_full = datetime.combine(datetime.today(), datetime.strptime(clock_in[:5], '%H:%M').time())
                cout_full = datetime.combine(datetime.today(), datetime.strptime(clock_out[:5], '%H:%M').time())

                # Use AttendancePolicy grace values if available, else shift.grace_period
                late_grace = policy.late_mark_after if policy else (shift.grace_period or 0)
                early_grace = policy.early_departure_grace if policy else (shift.grace_period or 0)

                if shift.end_time:
                    shift_end = datetime.combine(datetime.today(), shift.end_time)
                    # Handle night shifts spanning midnight
                    if shift_end <= shift_start:
                        shift_end += timedelta(days=1)
                        if cout_full < cin_full:
                            cout_full += timedelta(days=1)
                    if cout_full < shift_end - timedelta(minutes=early_grace):
                        is_early = True

                if cin_full > shift_start + timedelta(minutes=late_grace):
                    is_late = True

                # Auto-set status only if user left it as default 'present'
                if status == 'present':
                    if policy and working_hours > 0 and working_hours <= float(policy.half_day_hours):
                        status = 'half_day'
                    elif is_late:
                        status = 'late'

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

    record = get_object_or_404(AttendanceRecord.objects.select_related('employee__shift', 'employee__attendance_policy'), pk=pk)

    if request.method == 'POST':
        clock_in = request.POST.get('clock_in') or None
        clock_out = request.POST.get('clock_out') or None
        shift_id = request.POST.get('shift') or None
        is_holiday = request.POST.get('is_holiday') == 'true'
        notes = request.POST.get('notes', '').strip()
        status = request.POST.get('status', 'present')

        # Use shift from form, fallback to existing record shift, then employee's assigned shift
        shift = Shift.objects.filter(pk=shift_id).first() if shift_id else (record.shift or record.employee.shift)
        policy = record.employee.attendance_policy

        working_hours = 0
        overtime_hours = 0
        is_late = False
        is_early = False

        if clock_in and clock_out:
            cin = datetime.strptime(clock_in[:5], '%H:%M')
            cout = datetime.strptime(clock_out[:5], '%H:%M')
            diff = (cout - cin).total_seconds() / 3600
            if diff < 0:
                diff += 24
            working_hours = round(diff, 2)

            if shift:
                # Subtract break duration to get effective working hours
                break_hrs = (shift.break_duration or 0) / 60.0
                if working_hours > break_hrs:
                    working_hours = round(working_hours - break_hrs, 2)
                shift_hours = float(shift.working_hours)
                if working_hours > shift_hours:
                    overtime_hours = round(working_hours - shift_hours, 2)

                shift_start = datetime.combine(datetime.today(), shift.start_time)
                cin_full = datetime.combine(datetime.today(), datetime.strptime(clock_in[:5], '%H:%M').time())
                cout_full = datetime.combine(datetime.today(), datetime.strptime(clock_out[:5], '%H:%M').time())

                # Use AttendancePolicy grace values if available, else shift.grace_period
                late_grace = policy.late_mark_after if policy else (shift.grace_period or 0)
                early_grace = policy.early_departure_grace if policy else (shift.grace_period or 0)

                if shift.end_time:
                    shift_end = datetime.combine(datetime.today(), shift.end_time)
                    # Handle night shifts spanning midnight
                    if shift_end <= shift_start:
                        shift_end += timedelta(days=1)
                        if cout_full < cin_full:
                            cout_full += timedelta(days=1)
                    if cout_full < shift_end - timedelta(minutes=early_grace):
                        is_early = True

                if cin_full > shift_start + timedelta(minutes=late_grace):
                    is_late = True

                # Auto-set status only if user left it as default 'present'
                if status == 'present':
                    if policy and working_hours > 0 and working_hours <= float(policy.half_day_hours):
                        status = 'half_day'
                    elif is_late:
                        status = 'late'

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
    from .models import Shift, Employee, EmployeeWeekend
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

    employees = Employee.objects.filter(employee_status='active').select_related('department').order_by('full_name')
    weekend_assignments = EmployeeWeekend.objects.select_related('employee', 'employee__department').order_by('-created_at')

    context = {
        'page_title': 'Shifts',
        'shifts': page_obj,
        'search': search,
        'per_page': per_page,
        'total_shifts': total_shifts,
        'active_shifts': active_shifts,
        'night_shifts': night_shifts,
        'day_shifts': day_shifts,
        'employees': employees,
        'weekend_assignments': weekend_assignments,
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
        if not name or not start_time:
            return JsonResponse({'success': False, 'error': 'Name and start time are required.'})
        shift = Shift.objects.create(
            name=name,
            start_time=start_time,
            end_time=end_time if end_time else None,
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
        if not name or not start_time:
            return JsonResponse({'success': False, 'error': 'Name and start time are required.'})
        shift.name = name
        shift.start_time = start_time
        shift.end_time = end_time if end_time else None
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
        'end_time': shift.end_time.strftime('%H:%M') if shift.end_time else '',
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


@login_required
def employee_weekend_save(request):
    from .models import EmployeeWeekend, Employee
    from datetime import datetime
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    emp_id = request.POST.get('employee_id', '').strip()
    weekend_days = request.POST.getlist('weekend_days')
    weekend_type = request.POST.get('weekend_type', 'weekend')
    effective_from = request.POST.get('effective_from', '').strip()
    effective_to = request.POST.get('effective_to', '').strip() or None
    notes = request.POST.get('notes', '').strip()
    record_id = request.POST.get('record_id', '').strip()
    if not emp_id:
        return JsonResponse({'error': 'Employee is required'}, status=400)
    if not weekend_days:
        return JsonResponse({'error': 'Select at least one day'}, status=400)
    if not effective_from:
        return JsonResponse({'error': 'Effective From date is required'}, status=400)
    try:
        employee = Employee.objects.get(id=int(emp_id))
    except (Employee.DoesNotExist, ValueError):
        return JsonResponse({'error': 'Employee not found'}, status=404)
    try:
        effective_from_date = datetime.strptime(effective_from, '%Y-%m-%d').date()
        effective_to_date = datetime.strptime(effective_to, '%Y-%m-%d').date() if effective_to else None
    except ValueError:
        return JsonResponse({'error': 'Invalid date format'}, status=400)
    if record_id:
        try:
            rec = EmployeeWeekend.objects.get(id=int(record_id))
        except EmployeeWeekend.DoesNotExist:
            rec = EmployeeWeekend(employee=employee)
    else:
        rec = EmployeeWeekend(employee=employee)
    rec.employee = employee
    rec.weekend_days = weekend_days
    rec.weekend_type = weekend_type
    rec.effective_from = effective_from_date
    rec.effective_to = effective_to_date
    rec.notes = notes
    rec.save()
    return JsonResponse({'success': True, 'id': rec.id, 'message': f'Weekend assignment saved for {employee.full_name}'})


@login_required
def employee_weekend_list(request):
    from .models import EmployeeWeekend
    records = EmployeeWeekend.objects.select_related('employee', 'employee__department').order_by('-created_at')
    data = []
    for r in records:
        data.append({
            'id': r.id,
            'employee_id': r.employee.employee_id,
            'employee_name': r.employee.full_name,
            'department': r.employee.department.name if r.employee.department else '—',
            'weekend_type': r.weekend_type,
            'weekend_type_display': r.get_weekend_type_display(),
            'weekend_days': r.weekend_days,
            'days_display': r.days_display,
            'effective_from': r.effective_from.strftime('%d %b %Y'),
            'effective_to': r.effective_to.strftime('%d %b %Y') if r.effective_to else None,
            'notes': r.notes,
        })
    return JsonResponse({'records': data})


@login_required
def employee_weekend_delete(request, pk):
    from .models import EmployeeWeekend
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        rec = EmployeeWeekend.objects.get(id=pk)
        rec.delete()
        return JsonResponse({'success': True})
    except EmployeeWeekend.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)


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

    # Stats always use the full (unfiltered) queryset
    all_qs = AttendancePolicy.objects.all()
    total = all_qs.count()
    active = all_qs.filter(is_active=True).count()
    avg_late = all_qs.aggregate(avg=Avg('late_mark_after'))['avg'] or 0
    avg_overtime = all_qs.aggregate(avg=Avg('overtime_rate'))['avg'] or 0

    qs = all_qs
    if search:
        qs = qs.filter(name__icontains=search)
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
        if AttendancePolicy.objects.filter(name__iexact=name).exists():
            return JsonResponse({'success': False, 'error': 'A policy with this name already exists.'})
        try:
            work_hours = float(request.POST.get('work_hours_per_day', 8) or 8)
            late_mark = int(request.POST.get('late_mark_after', 15) or 15)
            early_dep = int(request.POST.get('early_departure_grace', 15) or 15)
            overtime = float(request.POST.get('overtime_rate', 0) or 0)
            half_day = float(request.POST.get('half_day_hours', 4) or 4)
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Invalid numeric values provided.'})
        if late_mark < 0 or early_dep < 0 or overtime < 0 or work_hours <= 0 or half_day <= 0:
            return JsonResponse({'success': False, 'error': 'Values must be positive numbers.'})
        policy = AttendancePolicy.objects.create(
            name=name,
            description=request.POST.get('description', '').strip(),
            work_hours_per_day=work_hours,
            late_mark_after=late_mark,
            early_departure_grace=early_dep,
            overtime_rate=overtime,
            half_day_hours=half_day,
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
        if AttendancePolicy.objects.filter(name__iexact=name).exclude(pk=pk).exists():
            return JsonResponse({'success': False, 'error': 'A policy with this name already exists.'})
        try:
            work_hours = float(request.POST.get('work_hours_per_day', 8) or 8)
            late_mark = int(request.POST.get('late_mark_after', 15) or 15)
            early_dep = int(request.POST.get('early_departure_grace', 15) or 15)
            overtime = float(request.POST.get('overtime_rate', 0) or 0)
            half_day = float(request.POST.get('half_day_hours', 4) or 4)
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Invalid numeric values provided.'})
        if late_mark < 0 or early_dep < 0 or overtime < 0 or work_hours <= 0 or half_day <= 0:
            return JsonResponse({'success': False, 'error': 'Values must be positive numbers.'})
        policy.name = name
        policy.description = request.POST.get('description', '').strip()
        policy.work_hours_per_day = work_hours
        policy.late_mark_after = late_mark
        policy.early_departure_grace = early_dep
        policy.overtime_rate = overtime
        policy.half_day_hours = half_day
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
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request.'})
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
        import pytz
        _nst = pytz.timezone('Asia/Kathmandu')
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
            "TimeZone=345\r\n"   # 345 minutes = UTC+5:45 in minutes (correct Nepal offset)
            "Realtime=1\r\n"
            "Encrypt=0\r\n"
            "Date={date_str}\r\n"
        ).format(sn=sn, date_str=_date_str)
        adms_logger.info(f"[CDATA] Sending handshake to SN={sn} with Date={_date_str}")
        return HttpResponse(options, content_type='text/plain')

    if request.method == 'POST':
        import pytz
        _nst = pytz.timezone('Asia/Kathmandu')
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
    import pytz

    local_tz = pytz.timezone('Asia/Kathmandu')

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
    import pytz

    local_tz = pytz.timezone('Asia/Kathmandu')

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
    _sync_biometric_to_attendance()
    return JsonResponse({
        'success': True,
        'message': f'Found {count} raw punches for PIN {pin} on {date_str}. Data synced.',
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
        pay_ot_val = request.POST.get('pay_ot') == '1'
        sandwich_rule_val = request.POST.get('sandwich_rule') == '1'
        salary = EmployeeSalary.objects.create(
            employee=employee,
            basic_salary=basic_salary_val,
            effective_date=effective_date,
            notes=notes,
            is_active=is_active_val,
            pay_ot=pay_ot_val,
            sandwich_rule=sandwich_rule_val,
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
            'pay_ot': salary.pay_ot,
            'sandwich_rule': salary.sandwich_rule,
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
        pay_ot_val = request.POST.get('pay_ot') == '1'
        sandwich_rule_val = request.POST.get('sandwich_rule') == '1'
        salary.employee = employee
        salary.basic_salary = basic_salary_val
        salary.effective_date = effective_date
        salary.notes = notes
        salary.is_active = is_active_val
        salary.pay_ot = pay_ot_val
        salary.sandwich_rule = sandwich_rule_val
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
def active_employees_api(request):
    """Return active employees as JSON for the generate-payslips employee picker."""
    from .models import Employee
    employees = Employee.objects.filter(employee_status='active').select_related('department').order_by('full_name')
    data = [
        {
            'id': e.pk,
            'name': e.full_name,
            'employee_id': e.employee_id,
            'department': e.department.name if e.department else '',
        }
        for e in employees
    ]
    return JsonResponse({'success': True, 'employees': data})


def _count_sandwich_unpaid(first_day, last_day, weekend_day_nums, holiday_dates, attendance_records, count_up_to=None, paid_holiday_dates=None):
    """
    Count weekend/holiday days sandwiched between absences.
    A weekend/holiday day is "sandwiched" if the nearest working day before it
    AND the nearest working day after it are both absent.

    Returns dict with separate weekend vs holiday counts:
        {
            'wknd_earned': int,  'wknd_full': int,
            'hol_earned':  int,  'hol_full':  int,
            'total_full':  int,
        }
    """
    from datetime import timedelta
    if count_up_to is None:
        count_up_to = last_day

    # Build status map: date → attendance status
    status_map = {}
    for rec in attendance_records:
        status_map[rec.date] = rec.status

    # Identify all non-working dates (weekends + holidays)
    non_working = set()
    d = first_day
    while d <= last_day:
        if d.weekday() in weekend_day_nums or d in holiday_dates:
            # Exclude if they actually worked on this weekend/holiday
            if status_map.get(d) not in ('present', 'late', 'half_day'):
                non_working.add(d)
        d += timedelta(days=1)

    wknd_earned = 0; wknd_full = 0
    hol_earned = 0;  hol_full = 0

    for nw_date in sorted(non_working):
        # Look backward for nearest working day
        prev_d = nw_date - timedelta(days=1)
        while prev_d >= first_day and prev_d in non_working:
            prev_d -= timedelta(days=1)

        # Look forward for nearest working day
        next_d = nw_date + timedelta(days=1)
        while next_d <= last_day and next_d in non_working:
            next_d += timedelta(days=1)

        # Both neighbors must exist and be absent
        prev_absent = prev_d >= first_day and status_map.get(prev_d) == 'absent'
        next_absent = next_d <= last_day and status_map.get(next_d) == 'absent'

        if prev_absent and next_absent:
            is_holiday = nw_date in holiday_dates and nw_date.weekday() not in weekend_day_nums
            if is_holiday:
                if paid_holiday_dates is None or nw_date in paid_holiday_dates:
                    hol_full += 1
                    if nw_date <= count_up_to:
                        hol_earned += 1
            else:
                wknd_full += 1
                if nw_date <= count_up_to:
                    wknd_earned += 1

    return {
        'wknd_earned': wknd_earned, 'wknd_full': wknd_full,
        'hol_earned': hol_earned, 'hol_full': hol_full,
        'total_full': wknd_full + hol_full,
    }


@login_required
def generate_payslips(request, pk):
    from .models import PayrollRun, Payslip, Employee, EmployeeSalary, AttendanceRecord
    from .models import EmployeeWeekend as _EmpWeekend
    import datetime, calendar
    from datetime import date as dt_date
    from decimal import Decimal, ROUND_HALF_UP

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)

    try:
        run = PayrollRun.objects.get(pk=pk)
    except PayrollRun.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Payroll Run not found.'}, status=404)

    if run.status == 'cancelled':
        return JsonResponse({'success': False, 'error': 'Cannot generate payslips for a cancelled payroll run.'}, status=400)

    # ── Parse optional employee_ids from JSON body ──
    import json as _json
    employee_ids = None
    try:
        body = _json.loads(request.body) if request.body else {}
        employee_ids = body.get('employee_ids')  # list of int PKs or None
    except (ValueError, TypeError):
        pass

    employees = Employee.objects.select_related('attendance_policy').filter(employee_status='active')
    if employee_ids:
        employee_ids = [int(x) for x in employee_ids if str(x).isdigit()]
        employees = employees.filter(pk__in=employee_ids)
    if not employees.exists():
        return JsonResponse({'success': False, 'error': 'No active employees found.'}, status=400)

    created_count = 0
    skipped_count = 0
    today = datetime.date.today()

    # ── Determine month / year from pay period ──
    _month = run.month or (run.pay_period_start.month if run.pay_period_start else today.month)
    _year = run.year or (run.pay_period_start.year if run.pay_period_start else today.year)
    _first_day = dt_date(_year, _month, 1)
    _last_day = dt_date(_year, _month, calendar.monthrange(_year, _month)[1])

    _DAY_MAP = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2,
        'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6,
    }

    from .models import Holiday
    _paid_holiday_dates = set()
    for h in Holiday.objects.filter(is_paid=True, is_active=True):
        if h.end_date >= _first_day and h.start_date <= _last_day:
            d = max(h.start_date, _first_day)
            end = min(h.end_date, _last_day)
            while d <= end:
                _paid_holiday_dates.add(d)
                d += timedelta(days=1)

    for employee in employees:
        # Skip if payslip already exists AND is finalized for this run + employee
        _existing_slip = Payslip.objects.filter(payroll_run=run, employee=employee).first()
        if _existing_slip:
            if _existing_slip.is_finalized:
                # Finalized slips are protected — skip entirely
                skipped_count += 1
                continue
            else:
                # Non-finalized existing slip — skip (already generated)
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
        basic = basic or Decimal('0')

        # ── Attendance & weekend calculation (needed for pro-rating) ──
        # 1. Employee-specific weekend days
        _emp_weekend = _EmpWeekend.objects.filter(
            employee=employee,
            weekend_type='weekend',
            effective_from__lte=_last_day,
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=_first_day)
        ).order_by('-effective_from').first()
        _weekend_day_nums = {
            _DAY_MAP[d.lower()] for d in (_emp_weekend.weekend_days or [])
            if d.lower() in _DAY_MAP
        } if _emp_weekend else {5, 6}

        # 2. Count total_days, weekend_days, holiday_days → derive duty_days
        _total_days = (_last_day - _first_day).days + 1
        # Full-month weekend count (for duty_days display) + capped count (for payable_days)
        _count_up_to = min(_last_day, dt_date.today())
        _weekend_count_full = 0   # full month — for duty_days
        _weekend_count = 0        # capped at today — for payable_days
        _d = _first_day
        while _d <= _last_day:
            if _d.weekday() in _weekend_day_nums:
                _weekend_count_full += 1
                if _d <= _count_up_to:
                    _weekend_count += 1
            _d += timedelta(days=1)

        _records = AttendanceRecord.objects.filter(
            employee=employee, date__year=_year, date__month=_month
        )
        # Count holidays excluding those on weekends
        _holiday_count_full = sum(
            1 for d in _records.filter(is_holiday=True).values_list('date', flat=True).distinct()
            if d.weekday() not in _weekend_day_nums
        )  # full month — for duty_days
        _holiday_count = sum(
            1 for d in _records.filter(is_holiday=True).values_list('date', flat=True).distinct()
            if d.weekday() not in _weekend_day_nums and d <= _count_up_to and d in _paid_holiday_dates
        )  # capped at today AND paid — for payable_days
        _duty_days = max(_total_days - _weekend_count_full - _holiday_count_full, 0)

        # 3. Count present, paid-leave, half-day from attendance records
        #    Skip records on weekend dates (already paid) and holiday dates (already paid)
        _holiday_dates = set(
            _records.filter(is_holiday=True).values_list('date', flat=True).distinct()
        )
        _present_days = Decimal('0')
        _present_on_weekend = Decimal('0')
        _paid_leave_days = Decimal('0')
        _half_days_count = Decimal('0')
        for _rec in _records:
            if _rec.date.weekday() in _weekend_day_nums:
                if _rec.status in ('present', 'late'):
                    _present_on_weekend += 1
                elif _rec.status == 'half_day':
                    _present_on_weekend += Decimal('0.5')
                continue  # weekend — already counted as paid
            if _rec.date in _holiday_dates:
                if _rec.status in ('present', 'late'):
                    _present_on_weekend += 1
                elif _rec.status == 'half_day':
                    _present_on_weekend += Decimal('0.5')
                continue  # holiday — already counted as paid
            if _rec.status in ('present', 'late'):
                _present_days += 1
            elif _rec.status == 'half_day':
                _half_days_count += 1
            elif _rec.status == 'on_leave':
                _paid_leave_days += 1

        # 4a. Sandwich rule: reduce paid weekends/holidays if sandwiched between absences
        if salary_record and getattr(salary_record, 'sandwich_rule', False):
            _sw = _count_sandwich_unpaid(
                _first_day, _last_day, _weekend_day_nums, _holiday_dates,
                list(_records), count_up_to=_count_up_to, paid_holiday_dates=_paid_holiday_dates
            )
            _weekend_count = max(_weekend_count - _sw['wknd_earned'], 0)
            _weekend_count_full = max(_weekend_count_full - _sw['wknd_full'], 0)
            _holiday_count = max(_holiday_count - _sw['hol_earned'], 0)
            _holiday_count_full = max(_holiday_count_full - _sw['hol_full'], 0)

        # 4. Pro-rate salary based on payable days
        # Weekends + public holidays are paid; only absent days reduce salary
        _payable_days = _present_days + _present_on_weekend + _paid_leave_days + (_half_days_count * Decimal('0.5')) + Decimal(str(_weekend_count)) + Decimal(str(_holiday_count))
        _payable_days = min(_payable_days, Decimal(str(_total_days)))  # cap at total days in month
        if _total_days > 0:
            _earned_basic = (basic / Decimal(str(_total_days)) * _payable_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            _earned_basic = min(_earned_basic, basic)  # cap at full basic for rounding safety
        else:
            _earned_basic = Decimal('0')

        earnings = Decimal('0')
        deductions = Decimal('0')

        if salary_record:
            components = salary_record.components.filter(is_active=True)

            # First pass: compute a provisional gross (earned_basic + fixed/variable earnings + %_of_basic earnings)
            # used later for %_of_gross calculations
            pre_gross = _earned_basic
            for comp in components:
                if comp.component_type == 'earning':
                    if comp.calculation_type == 'fixed':
                        pre_gross += comp.amount
                    elif comp.calculation_type == 'variable':
                        # Variable: pro-rate the monthly amount based on attendance
                        # daily_rate = monthly_amount / total_days_in_month
                        # earned = daily_rate * payable_days (present + paid_leave + half_days*0.5)
                        if _total_days > 0:
                            _var_earned = (comp.amount / Decimal(str(_total_days)) * _payable_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                            _var_earned = min(_var_earned, comp.amount)
                            pre_gross += _var_earned
                    elif comp.calculation_type == 'percentage_of_basic':
                        pre_gross += (_earned_basic * comp.amount / Decimal('100')).quantize(Decimal('0.01'))

            # Second pass: full component calculation
            for comp in components:
                if comp.component_type == 'earning':
                    if comp.calculation_type == 'fixed':
                        earnings += comp.amount
                    elif comp.calculation_type == 'variable':
                        if _total_days > 0:
                            _var_earned = (comp.amount / Decimal(str(_total_days)) * _payable_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                            _var_earned = min(_var_earned, comp.amount)
                            earnings += _var_earned
                    elif comp.calculation_type == 'percentage_of_basic':
                        earnings += (_earned_basic * comp.amount / Decimal('100')).quantize(Decimal('0.01'))
                    elif comp.calculation_type in ('percentage_of_gross', 'percentage_of_ctc'):
                        earnings += (pre_gross * comp.amount / Decimal('100')).quantize(Decimal('0.01'))
                elif comp.component_type == 'deduction':
                    if comp.calculation_type == 'fixed':
                        deductions += comp.amount
                    elif comp.calculation_type == 'variable':
                        if _total_days > 0:
                            _var_earned = (comp.amount / Decimal(str(_total_days)) * _payable_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                            _var_earned = min(_var_earned, comp.amount)
                            deductions += _var_earned
                    elif comp.calculation_type == 'percentage_of_basic':
                        deductions += (_earned_basic * comp.amount / Decimal('100')).quantize(Decimal('0.01'))
                    elif comp.calculation_type in ('percentage_of_gross', 'percentage_of_ctc'):
                        deductions += (pre_gross * comp.amount / Decimal('100')).quantize(Decimal('0.01'))

        gross_salary = (_earned_basic + earnings).quantize(Decimal('0.01'))
        total_deductions_val = deductions.quantize(Decimal('0.01'))

        # ── Approved Bonuses for this employee/month/year ──
        from .models import Bonus as _Bonus
        _bonus_qs = _Bonus.objects.filter(
            employee=employee,
            month=_month,
            year=_year,
            status='approved',
        )
        _bonus_total = sum(b.amount for b in _bonus_qs) or Decimal('0')
        _bonus_total = Decimal(str(_bonus_total)).quantize(Decimal('0.01'))
        gross_salary = (gross_salary + _bonus_total).quantize(Decimal('0.01'))

        # Absent deduction is no longer needed — salary is already pro-rated based on present days
        _absent_deduction = Decimal('0')

        # ── Overtime calculation (only if pay_ot is enabled on salary record) ──
        _total_ot_hours = Decimal('0')
        _overtime_amount = Decimal('0')
        _ot_rate = Decimal('0')
        _pay_ot = salary_record.pay_ot if salary_record else False
        if _pay_ot:
            for _rec in _records:
                _total_ot_hours += _rec.overtime_hours
            _emp_policy = employee.attendance_policy
            if _emp_policy and _emp_policy.is_active:
                _ot_rate = _emp_policy.overtime_rate
            else:
                from .models import AttendancePolicy as _AttPolicy
                try:
                    _fallback = _AttPolicy.objects.filter(is_active=True).first()
                    if _fallback:
                        _ot_rate = _fallback.overtime_rate
                except Exception:
                    pass
            _overtime_amount = (_total_ot_hours * _ot_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        # Calculate advance payment deductions for salary-deduction mode advances
        from .models import AdvancePayment as _AdvPay
        active_advances = _AdvPay.objects.filter(
            employee=employee,
            status__in=['disbursed', 'repaying']
        )
        advance_deduction = Decimal('0')
        for adv in active_advances:
            remaining = max(adv.amount - adv.amount_repaid, Decimal('0'))
            inst = adv.installment_amount or Decimal('0')
            if adv.repayment_mode in ('salary_deduction', 'installments') or not adv.repayment_mode:
                _ded = min(inst if inst > 0 else remaining, remaining)
            elif adv.repayment_mode == 'lump_sum':
                _ded = remaining
            else:
                _ded = min(inst if inst > 0 else remaining, remaining)
            advance_deduction += _ded
        advance_deduction = advance_deduction.quantize(Decimal('0.01'))

        net_salary = max(gross_salary + _overtime_amount - total_deductions_val - advance_deduction - _absent_deduction, Decimal('0'))

        Payslip.objects.create(
            payroll_run=run,
            employee=employee,
            gross_salary=gross_salary,
            total_deductions=total_deductions_val,
            advance_deduction=advance_deduction,
            absent_deduction=_absent_deduction,
            net_salary=net_salary,
            status='generated',
            generated_on=today,
        )
        created_count += 1

        # ── Mark approved bonuses as paid ──
        _bonus_qs.update(status='paid')

        # ── Update advance records: apply this period's deductions ──
        from django.db.models import F as _F
        for adv in active_advances:
            _rem = max(adv.amount - adv.amount_repaid, Decimal('0'))
            _inst = adv.installment_amount or Decimal('0')
            if adv.repayment_mode in ('salary_deduction', 'installments') or not adv.repayment_mode:
                _ded = _inst if _inst > 0 else _rem
            elif adv.repayment_mode == 'lump_sum':
                _ded = _rem
            else:
                _ded = _inst if _inst > 0 else _rem
            _ded = min(_ded, _rem)
            if _ded > 0:
                _new_repaid = adv.amount_repaid + _ded
                _new_status = 'cleared' if _new_repaid >= adv.amount else 'repaying'
                _AdvPay.objects.filter(pk=adv.pk).update(
                    amount_repaid=_new_repaid,
                    paid_installments=_F('paid_installments') + 1,
                    status=_new_status,
                )

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
    from .models import Payslip, AdvancePayment
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
    per_page = str(per_page_int)
    paginator = Paginator(payslips, per_page_int)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    # Build live advance data for employees on this page
    page_slips = list(page_obj)
    employee_ids = list({slip.employee_id for slip in page_slips})

    # Fetch all relevant advances in one query
    all_advances = list(
        AdvancePayment.objects.filter(
            employee_id__in=employee_ids
        ).values(
            'employee_id', 'status', 'repayment_mode',
            'installment_amount', 'amount', 'amount_repaid', 'advance_number'
        )
    )

    # Build per-employee summary
    advance_info = {}
    for adv in all_advances:
        eid = adv['employee_id']
        if eid not in advance_info:
            advance_info[eid] = {'live_deduction': Decimal('0'), 'has_pending': False, 'has_cleared': False, 'has_rejected': False, 'advance_status': adv['status']}
        amt = adv['amount'] or Decimal('0')
        repaid = adv['amount_repaid'] or Decimal('0')
        remaining = max(amt - repaid, Decimal('0'))

        if adv['status'] in ('disbursed', 'repaying'):
            mode = adv['repayment_mode']
            inst = adv['installment_amount'] or Decimal('0')
            remaining = max(amt - repaid, Decimal('0'))
            if mode in ('salary_deduction', 'installments') or not mode:
                deduct_amt = min(inst if inst > 0 else remaining, remaining)
            elif mode == 'lump_sum':
                deduct_amt = remaining
            else:
                deduct_amt = min(inst if inst > 0 else remaining, remaining)
            advance_info[eid]['live_deduction'] += deduct_amt
            # Fully repaid but status not yet cleared
            if remaining == 0:
                advance_info[eid]['has_cleared'] = True
        elif adv['status'] in ('pending', 'approved'):
            advance_info[eid]['has_pending'] = True
            advance_info[eid]['advance_status'] = adv['status']
        elif adv['status'] == 'cleared':
            advance_info[eid]['has_cleared'] = True
        elif adv['status'] == 'rejected':
            advance_info[eid]['has_rejected'] = True
            # Do NOT overwrite advance_status — it's only used for pending/approved badge text

    # Attach live advance data to each payslip object
    for slip in page_slips:
        info = advance_info.get(slip.employee_id, {})
        slip.live_advance_deduction = info.get('live_deduction', Decimal('0'))
        slip.has_pending_advance = info.get('has_pending', False)
        slip.has_cleared_advance = info.get('has_cleared', False)
        slip.has_rejected_advance = info.get('has_rejected', False)
        slip.advance_status = info.get('advance_status', '')

    # Count payslips that need action: new advance not captured (live > stored), or rejected with stored deduction
    stale_count = sum(
        1 for s in page_slips
        if s.live_advance_deduction > s.advance_deduction
        or (s.has_rejected_advance and s.advance_deduction > 0)
    )

    context = {
        'page_title': 'Payslips',
        'payslips': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'per_page': per_page,
        'stale_count': stale_count,
    }
    return render(request, 'hrm/payslip_list.html', context)


@login_required
def payslip_sync_advances(request):
    """Recalculate and save advance_deduction + net_salary for all payslips."""
    from .models import Payslip, AdvancePayment
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    payslips = list(Payslip.objects.select_related('employee').all())
    employee_ids = list({s.employee_id for s in payslips})

    # Fetch active advances grouped by employee
    active_advances = list(
        AdvancePayment.objects.filter(
            employee_id__in=employee_ids,
            status__in=['disbursed', 'repaying']
        ).values('id', 'employee_id', 'repayment_mode', 'installment_amount', 'amount', 'amount_repaid')
    )
    advance_by_emp = {}
    to_clear_ids = []  # advances that are fully repaid but not marked cleared
    for adv in active_advances:
        eid = adv['employee_id']
        if eid not in advance_by_emp:
            advance_by_emp[eid] = Decimal('0')
        inst = adv['installment_amount'] or Decimal('0')
        amt = adv['amount'] or Decimal('0')
        repaid = adv['amount_repaid'] or Decimal('0')
        remaining = max(amt - repaid, Decimal('0'))
        mode = adv['repayment_mode']

        if remaining <= 0:
            # Fully repaid — mark for auto-clearing
            to_clear_ids.append(adv['id'])
        else:
            if mode in ('salary_deduction', 'installments') or not mode:
                # Cap at remaining balance so we don't over-deduct
                deduct_amt = min(inst if inst > 0 else remaining, remaining)
                advance_by_emp[eid] += deduct_amt
            elif mode == 'lump_sum':
                advance_by_emp[eid] += remaining
            else:
                advance_by_emp[eid] += min(inst if inst > 0 else remaining, remaining)

    # Auto-clear fully-repaid advances
    if to_clear_ids:
        AdvancePayment.objects.filter(id__in=to_clear_ids).update(status='cleared')

    updated = 0
    to_update = []
    for slip in payslips:
        new_adv = (advance_by_emp.get(slip.employee_id, Decimal('0'))).quantize(Decimal('0.01'))
        if slip.advance_deduction != new_adv:
            old_adv = slip.advance_deduction or Decimal('0')
            slip.advance_deduction = new_adv
            # Use delta approach: adjust net by the difference in advance deduction
            # This preserves all other components (overtime, etc.) that were in the stored net
            slip.net_salary = max(
                slip.net_salary + old_adv - new_adv,
                Decimal('0')
            ).quantize(Decimal('0.01'))
            to_update.append(slip)
            updated += 1

    if to_update:
        now = timezone.now()
        for slip in to_update:
            slip.updated_at = now
        Payslip.objects.bulk_update(to_update, ['advance_deduction', 'net_salary', 'updated_at'])

    return JsonResponse({
        'success': True,
        'updated': updated,
        'message': f'{updated} payslip(s) updated.' if updated else 'All payslips are already up to date.',
    })


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
            'advance_deduction': str(slip.advance_deduction),
            'absent_deduction': str(slip.absent_deduction),
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
            'employee', 'employee__department', 'employee__designation',
            'employee__attendance_policy', 'payroll_run'
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

    # ── Calendar bounds ──
    first_day = dt_date(year, month, 1)
    last_day = dt_date(year, month, calendar.monthrange(year, month)[1])
    total_days = (last_day - first_day).days + 1

    # ── Employee-specific weekend days ──
    from .models import EmployeeWeekend as _EmpWeekend
    from django.db.models import Q as _Q
    _DAY_MAP = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2,
        'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6,
    }

    from .models import Holiday
    paid_holiday_dates = set()
    for h in Holiday.objects.filter(is_paid=True, is_active=True):
        if h.end_date >= first_day and h.start_date <= last_day:
            d = max(h.start_date, first_day)
            end = min(h.end_date, last_day)
            while d <= end:
                paid_holiday_dates.add(d)
                d += timedelta(days=1)

    _emp_weekend = _EmpWeekend.objects.filter(
        employee=employee,
        weekend_type='weekend',
        effective_from__lte=last_day,
    ).filter(
        _Q(effective_to__isnull=True) | _Q(effective_to__gte=first_day)
    ).order_by('-effective_from').first()
    _weekend_day_nums = {
        _DAY_MAP[d.lower()] for d in (_emp_weekend.weekend_days or [])
        if d.lower() in _DAY_MAP
    } if _emp_weekend else {5, 6}  # default Sat+Sun if no assignment

    # ── Count weekend days for this employee ──
    # Full-month count for display & duty_days; capped count for payable_days
    _count_up_to = min(last_day, dt_date.today())
    weekend_days = 0          # full month — for display & duty_days
    _earned_weekends = 0      # capped at today — for payable_days
    d = first_day
    while d <= last_day:
        if d.weekday() in _weekend_day_nums:
            weekend_days += 1
            if d <= _count_up_to:
                _earned_weekends += 1
        d += timedelta(days=1)

    # ── Attendance records ──
    records = AttendanceRecord.objects.filter(employee=employee, date__year=year, date__month=month)
    present_days = Decimal('0')
    present_on_weekend = Decimal('0')
    half_days = Decimal('0')
    absent_days = Decimal('0')
    on_leave_days = Decimal('0')
    total_overtime_hours = Decimal('0')

    # Collect holiday dates for skip logic
    _holiday_dates = set(
        records.filter(is_holiday=True).values_list('date', flat=True).distinct()
    )

    for rec in records:
        # Skip weekend records (already paid) and holiday records (already paid)
        _is_weekend = rec.date.weekday() in _weekend_day_nums
        _is_holiday = rec.date in _holiday_dates
        total_overtime_hours += rec.overtime_hours
        if _is_weekend or _is_holiday:
            if rec.status in ('present', 'late'):
                present_on_weekend += 1
            elif rec.status == 'half_day':
                present_on_weekend += Decimal('0.5')
            continue
        if rec.status in ('present', 'late'):
            present_days += 1
        elif rec.status == 'absent':
            absent_days += 1
        elif rec.status == 'half_day':
            half_days += 1
        elif rec.status == 'on_leave':
            on_leave_days += 1

    # ── Holiday days from attendance records (exclude holidays on weekends) ──
    holiday_days = sum(
        1 for d in records.filter(is_holiday=True).values_list('date', flat=True).distinct()
        if d.weekday() not in _weekend_day_nums
    )  # full month — for display & duty_days
    _earned_holidays = sum(
        1 for d in records.filter(is_holiday=True).values_list('date', flat=True).distinct()
        if d.weekday() not in _weekend_day_nums and d <= _count_up_to and d in paid_holiday_dates
    )  # capped at today AND paid — for payable_days

    # ── Derived attendance values ──
    paid_leave_days = on_leave_days
    duty_days = max(total_days - weekend_days - holiday_days, 0)
    paid_days = present_days + paid_leave_days
    misc_days = half_days

    # ── Salary component breakdown ──
    salary_record = (
        EmployeeSalary.objects.filter(employee=employee, is_active=True)
        .prefetch_related('components')
        .order_by('-effective_date')
        .first()
    )
    basic_salary = salary_record.basic_salary if salary_record else (employee.base_salary or Decimal('0'))
    components = salary_record.components.filter(is_active=True) if salary_record else []

    # ── Sandwich rule: reduce paid weekends/holidays if sandwiched between absences ──
    sandwich_days = 0
    if salary_record and getattr(salary_record, 'sandwich_rule', False):
        _sw = _count_sandwich_unpaid(
            first_day, last_day, _weekend_day_nums, _holiday_dates,
            list(records), count_up_to=_count_up_to, paid_holiday_dates=paid_holiday_dates
        )
        sandwich_days = _sw['total_full']
        _earned_weekends = max(_earned_weekends - _sw['wknd_earned'], 0)
        weekend_days = max(weekend_days - _sw['wknd_full'], 0)
        _earned_holidays = max(_earned_holidays - _sw['hol_earned'], 0)
        holiday_days = max(holiday_days - _sw['hol_full'], 0)
        duty_days = max(total_days - weekend_days - holiday_days, 0)

    # ── Pro-rate salary based on payable days ──
    # Weekends + public holidays = paid; only absent days reduce salary
    per_day_salary = Decimal('0')
    if total_days > 0:
        per_day_salary = (basic_salary / Decimal(str(total_days))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    payable_days = present_days + present_on_weekend + paid_leave_days + (half_days * Decimal('0.5')) + Decimal(str(_earned_weekends)) + Decimal(str(_earned_holidays))
    payable_days = min(payable_days, Decimal(str(total_days)))  # cap at total days in month
    if total_days > 0:
        earned_basic = (basic_salary / Decimal(str(total_days)) * payable_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        earned_basic = min(earned_basic, basic_salary)  # cap at full basic for rounding safety
    else:
        earned_basic = Decimal('0')

    # Two-pass calculation (matches generate_payslips logic — uses earned_basic)
    pre_gross = earned_basic
    for comp in components:
        if comp.component_type == 'earning':
            if comp.calculation_type == 'fixed':
                pre_gross += comp.amount
            elif comp.calculation_type == 'variable':
                if total_days > 0:
                    _var_amt = (comp.amount / Decimal(str(total_days)) * payable_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    pre_gross += min(_var_amt, comp.amount)
            elif comp.calculation_type == 'percentage_of_basic':
                pre_gross += (earned_basic * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    earnings_list = []
    deductions_list = []
    total_earnings_comp = Decimal('0')
    total_deductions_comp = Decimal('0')

    for comp in components:
        if comp.calculation_type == 'fixed':
            calc_amount = comp.amount
        elif comp.calculation_type == 'variable':
            if total_days > 0:
                calc_amount = (comp.amount / Decimal(str(total_days)) * payable_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                calc_amount = min(calc_amount, comp.amount)
            else:
                calc_amount = Decimal('0')
        elif comp.calculation_type == 'percentage_of_basic':
            calc_amount = (earned_basic * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif comp.calculation_type in ('percentage_of_gross', 'percentage_of_ctc'):
            calc_amount = (pre_gross * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            calc_amount = comp.amount

        # Build display name — show days breakdown for variable components
        display_name = comp.name
        if comp.calculation_type == 'variable':
            display_name = f"{comp.name} ({payable_days}/{total_days} days)"

        if comp.component_type == 'earning':
            earnings_list.append({'name': display_name, 'amount': calc_amount})
            total_earnings_comp += calc_amount
        else:
            deductions_list.append({'name': display_name, 'amount': calc_amount})
            total_deductions_comp += calc_amount

    # -> ADD BONUS HERE <-
    from .models import Bonus as _Bonus
    _bonus_qs = _Bonus.objects.filter(
        employee=employee,
        month=month,
        year=year,
        status__in=['approved', 'paid']
    )
    for _b in _bonus_qs:
        earnings_list.append({'name': f"Bonus ({_b.get_bonus_type_display()})", 'amount': _b.amount})
        total_earnings_comp += _b.amount

    # -> ADD ADJUSTMENTS HERE <-
    for _adj in PayslipAdjustment.objects.filter(payslip=slip):
        if _adj.adjustment_type == 'earning':
            earnings_list.append({'name': f"Adjustment ({_adj.description})", 'amount': _adj.amount})
            total_earnings_comp += _adj.amount
        elif _adj.adjustment_type == 'deduction':
            deductions_list.append({'name': f"Adjustment ({_adj.description})", 'amount': _adj.amount})
            total_deductions_comp += _adj.amount

    total_earnings = earned_basic + total_earnings_comp

    # ── Overtime (only if pay_ot is enabled on salary record) ──
    overtime_rate = Decimal('0')
    overtime_amount = Decimal('0')
    _pay_ot = salary_record.pay_ot if salary_record else False
    if _pay_ot:
        _emp_policy = employee.attendance_policy
        if _emp_policy and _emp_policy.is_active:
            overtime_rate = _emp_policy.overtime_rate
        else:
            try:
                _fallback_policy = AttendancePolicy.objects.filter(is_active=True).first()
                if _fallback_policy:
                    overtime_rate = _fallback_policy.overtime_rate
            except Exception:
                pass
        ot_hours = total_overtime_hours
        overtime_amount = (ot_hours * overtime_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        ot_hours = total_overtime_hours

    # ── Advance Payment Deduction ──
    from .models import AdvancePayment as _AdvPay

    # Stored advance deduction (set by generate_payslips)
    _stored_adv = slip.advance_deduction or Decimal('0')

    # All advances for the employee (for summary table + deduction calc)
    all_employee_advances = list(
        _AdvPay.objects.filter(employee=employee).order_by('-created_at')
    )

    # ── Calculate per-advance deduction amounts ──
    # Helper: compute monthly installment from advance config
    def _adv_monthly_ded(adv, remaining=None):
        _amt = adv.amount or Decimal('0')
        _repaid = adv.amount_repaid or Decimal('0')
        _rem = remaining if remaining is not None else max(_amt - _repaid, Decimal('0'))
        _inst = adv.installment_amount or Decimal('0')
        _mode = adv.repayment_mode
        if _mode in ('salary_deduction', 'installments') or not _mode:
            _d = min(_inst if _inst > 0 else _rem, _rem)
        elif _mode == 'lump_sum':
            _d = _rem
        else:
            _d = min(_inst if _inst > 0 else _rem, _rem)
        return _d.quantize(Decimal('0.01')) if _d else Decimal('0')

    # Live advances (still active: disbursed / repaying)
    _live_advances = [a for a in all_employee_advances if a.status in ('disbursed', 'repaying')]
    _live_total = Decimal('0')
    for _adv in _live_advances:
        _adv.deducted_this_month = _adv_monthly_ded(_adv)
        _live_total += _adv.deducted_this_month

    if _stored_adv > 0 and _live_total == Decimal('0'):
        # ── Scenario A: generate_payslips already processed the advances ──
        # The advances are now 'cleared'/'repaying' — figure out per-advance share
        advance_deduction = _stored_adv
        _cleared = [a for a in all_employee_advances if a.status in ('cleared', 'repaying')]
        # Calculate what each advance's config says the monthly deduction should be
        _shares = {}
        _share_total = Decimal('0')
        for _adv in _cleared:
            _inst = _adv.installment_amount or Decimal('0')
            _mode = _adv.repayment_mode
            if _mode == 'lump_sum' or _inst <= 0:
                _share = _adv.amount or Decimal('0')
            else:
                _share = _inst
            _shares[_adv.pk] = _share
            _share_total += _share
        # Distribute stored total proportionally
        for _adv in all_employee_advances:
            if _adv.pk in _shares and _share_total > 0:
                _adv.deducted_this_month = (
                    _stored_adv * _shares[_adv.pk] / _share_total
                ).quantize(Decimal('0.01'))
            elif not hasattr(_adv, 'deducted_this_month') or _adv.status not in ('disbursed', 'repaying'):
                _adv.deducted_this_month = Decimal('0')

    elif _live_total > 0:
        # ── Scenario B: active advances found (created after generation, or not yet generated) ──
        advance_deduction = _live_total
        # Persist on payslip so future downloads are consistent
        # NOTE: we do NOT update the advance records here — that is exclusively done by
        # generate_payslips (official run) or payslip_sync_advances (explicit user action).
        if _stored_adv == Decimal('0'):
            _new_net = max(
                slip.gross_salary + overtime_amount - (total_deductions_comp + advance_deduction),
                Decimal('0'),
            )
            Payslip.objects.filter(pk=slip.pk).update(
                advance_deduction=advance_deduction,
                net_salary=_new_net,
            )
        # Set 0 for non-live advances
        for _adv in all_employee_advances:
            if _adv.status not in ('disbursed', 'repaying'):
                if not hasattr(_adv, 'deducted_this_month') or _adv.deducted_this_month is None:
                    _adv.deducted_this_month = Decimal('0')
    else:
        advance_deduction = Decimal('0')
        for _adv in all_employee_advances:
            _adv.deducted_this_month = Decimal('0')

    # Add advance deduction rows to salary table (deductions side)
    for _adv in all_employee_advances:
        if getattr(_adv, 'deducted_this_month', Decimal('0')) > 0:
            deductions_list.append({
                'name': f'Advance ({_adv.advance_number})',
                'amount': _adv.deducted_this_month,
            })
    total_deductions_comp += advance_deduction

    # ── Net salary ──
    # salary_comp_deductions = only salary component deductions (before advance)
    salary_comp_deductions = total_deductions_comp - advance_deduction  # remove advance that was added
    total_deductions = salary_comp_deductions + advance_deduction
    net_salary = max(total_earnings + overtime_amount - total_deductions, Decimal('0'))

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

    from dashboard.models import CompanySetup
    _co = CompanySetup.get_settings()
    context = {
        'payslip': slip,
        'employee': employee,
        'company_name': _co.company_name,
        'pay_period_start': run.pay_period_start,
        'pay_period_end': run.pay_period_end,
        'pay_date': run.pay_date,
        'basic_salary': basic_salary,
        'earned_basic': earned_basic,
        'payable_days': payable_days,
        # Attendance data from records
        'total_days': total_days,
        'weekend_days': weekend_days,
        'holiday_days': holiday_days,
        'duty_days': duty_days,
        'present_days': present_days,
        'present_on_weekend': present_on_weekend,
        'paid_leave_days': paid_leave_days,
        'absent_days': absent_days,
        'misc_days': misc_days,
        'half_days': half_days,
        'ot_hours': ot_hours,
        'total_overtime_hours': total_overtime_hours,
        # Salary calculation
        'per_day_salary': per_day_salary,
        'earnings_list': earnings_list,
        'deductions_list': deductions_list,
        'salary_rows': salary_rows,
        'total_earnings': total_earnings,
        'total_deductions': total_deductions,
        'salary_comp_deductions': salary_comp_deductions,
        'overtime_amount': overtime_amount,
        'overtime_rate': overtime_rate,
        'pay_ot': _pay_ot,
        'advance_deduction': advance_deduction,
        'all_advances': all_employee_advances,
        'net_salary': net_salary,
        'sandwich_days': sandwich_days,
    }
    return render(request, 'hrm/payslip_print.html', context)


@login_required
def payroll_calculation(request, pk):
    from .models import EmployeeSalary, AttendanceRecord, AttendancePolicy
    from .models import EmployeeWeekend as _EmpWeekend
    import calendar
    from datetime import date as dt_date
    from decimal import Decimal, ROUND_HALF_UP

    salary = get_object_or_404(
        EmployeeSalary.objects.select_related('employee', 'employee__attendance_policy').prefetch_related('components'),
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
    first_day = dt_date(year, month, 1)
    last_day = dt_date(year, month, calendar.monthrange(year, month)[1])
    total_days = (last_day - first_day).days + 1

    # ── Employee-specific weekend days ──
    _DAY_MAP = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2,
        'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6,
    }

    from .models import Holiday
    paid_holiday_dates = set()
    for h in Holiday.objects.filter(is_paid=True, is_active=True):
        if h.end_date >= first_day and h.start_date <= last_day:
            d = max(h.start_date, first_day)
            end = min(h.end_date, last_day)
            while d <= end:
                paid_holiday_dates.add(d)
                d += timedelta(days=1)

    _emp_weekend = _EmpWeekend.objects.filter(
        employee=employee,
        weekend_type='weekend',
        effective_from__lte=last_day,
    ).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=first_day)
    ).order_by('-effective_from').first()
    _weekend_day_nums = {
        _DAY_MAP[d.lower()] for d in (_emp_weekend.weekend_days or [])
        if d.lower() in _DAY_MAP
    } if _emp_weekend else {5, 6}

    # Count weekends for the full month (display & duty_days) and up-to-today (payable_days)
    _count_up_to = min(last_day, dt_date.today())
    weekend_days = 0          # full month — for display & duty_days
    _earned_weekends = 0      # capped at today — for payable_days
    d = first_day
    while d <= last_day:
        if d.weekday() in _weekend_day_nums:
            weekend_days += 1
            if d <= _count_up_to:
                _earned_weekends += 1
        d += timedelta(days=1)

    # Fetch attendance records for this employee in the selected month
    attendance_records = AttendanceRecord.objects.filter(
        employee=employee,
        date__year=year,
        date__month=month
    ).order_by('date')

    # Holiday days from attendance records (exclude holidays on weekends)
    _holiday_dates = set(
        attendance_records.filter(is_holiday=True).values_list('date', flat=True).distinct()
    )
    holiday_days = sum(1 for d in _holiday_dates if d.weekday() not in _weekend_day_nums)  # full month
    _earned_holidays = sum(1 for d in _holiday_dates if d.weekday() not in _weekend_day_nums and d <= _count_up_to and d in paid_holiday_dates)

    # duty_days = total_days - weekend_days - holiday_days (full month)
    duty_days = max(total_days - weekend_days - holiday_days, 0)

    # Attendance summary — skip weekend & holiday records (already paid)
    present_days = Decimal('0')
    present_on_weekend = Decimal('0')
    half_days = Decimal('0')
    absent_days = Decimal('0')
    on_leave_days = Decimal('0')
    total_overtime_hours = Decimal('0')

    attendance_data = []
    for rec in attendance_records:
        _is_weekend = rec.date.weekday() in _weekend_day_nums
        _is_holiday = rec.date in _holiday_dates
        overtime_display = str(rec.overtime_hours) + 'h' if rec.overtime_hours > 0 else '-'
        status_tags = []
        if rec.status == 'present':
            status_tags.append(('Present', 'present'))
            if not _is_weekend and not _is_holiday:
                present_days += 1
            else:
                present_on_weekend += 1
        elif rec.status == 'absent':
            status_tags.append(('Absent', 'absent'))
            if not _is_weekend and not _is_holiday:
                absent_days += 1
        elif rec.status == 'late':
            status_tags.append(('Present', 'present'))
            status_tags.append(('Late', 'late'))
            if not _is_weekend and not _is_holiday:
                present_days += 1
            else:
                present_on_weekend += 1
        elif rec.status == 'half_day':
            status_tags.append(('Half Day', 'half_day'))
            if not _is_weekend and not _is_holiday:
                half_days += 1
            else:
                present_on_weekend += Decimal('0.5')
        elif rec.status == 'on_leave':
            status_tags.append(('On Leave', 'on_leave'))
            if not _is_weekend and not _is_holiday:
                on_leave_days += 1

        if rec.is_early_departure and rec.status not in ('absent', 'on_leave', 'half_day'):
            status_tags.append(('Early', 'early'))

        total_overtime_hours += rec.overtime_hours

        attendance_data.append({
            'date': rec.date,
            'clock_in': rec.clock_in,
            'clock_out': rec.clock_out,
            'overtime': overtime_display,
            'status_tags': status_tags,
        })

    # Derived attendance values
    paid_leave_days = on_leave_days
    paid_days = present_days + paid_leave_days
    misc_days = half_days
    ot_hours = total_overtime_hours

    # Salary calculations
    basic_salary = salary.basic_salary
    components = salary.components.filter(is_active=True)

    # ── Sandwich rule: reduce paid weekends/holidays if sandwiched between absences ──
    sandwich_days = 0
    if getattr(salary, 'sandwich_rule', False):
        _sw = _count_sandwich_unpaid(
            first_day, last_day, _weekend_day_nums, _holiday_dates,
            list(attendance_records), count_up_to=_count_up_to, paid_holiday_dates=paid_holiday_dates
        )
        sandwich_days = _sw['total_full']
        _earned_weekends = max(_earned_weekends - _sw['wknd_earned'], 0)
        weekend_days = max(weekend_days - _sw['wknd_full'], 0)
        _earned_holidays = max(_earned_holidays - _sw['hol_earned'], 0)
        holiday_days = max(holiday_days - _sw['hol_full'], 0)
        duty_days = max(total_days - weekend_days - holiday_days, 0)

    # ── Pro-rate salary based on payable days ──
    # Weekends + public holidays = paid; only absent days reduce salary
    per_day_salary = Decimal('0')
    if total_days > 0:
        per_day_salary = (basic_salary / Decimal(str(total_days))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    payable_days = present_days + present_on_weekend + paid_leave_days + (half_days * Decimal('0.5')) + Decimal(str(_earned_weekends)) + Decimal(str(_earned_holidays))
    payable_days = min(payable_days, Decimal(str(total_days)))  # cap at total days in month
    if total_days > 0:
        earned_basic = (basic_salary / Decimal(str(total_days)) * payable_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        earned_basic = min(earned_basic, basic_salary)  # cap at full basic for rounding safety
    else:
        earned_basic = Decimal('0')

    # Two-pass calculation for %_of_gross (uses earned_basic)
    pre_gross = earned_basic
    for comp in components:
        if comp.component_type == 'earning':
            if comp.calculation_type == 'fixed':
                pre_gross += comp.amount
            elif comp.calculation_type == 'variable':
                if total_days > 0:
                    _var_amt = (comp.amount / Decimal(str(total_days)) * payable_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    pre_gross += min(_var_amt, comp.amount)
            elif comp.calculation_type == 'percentage_of_basic':
                pre_gross += (earned_basic * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    # Calculate component amounts
    earnings = []
    deductions = []
    total_earnings_components = Decimal('0')
    total_deductions_amount = Decimal('0')

    for comp in components:
        if comp.calculation_type == 'fixed':
            calc_amount = comp.amount
        elif comp.calculation_type == 'variable':
            if total_days > 0:
                calc_amount = (comp.amount / Decimal(str(total_days)) * payable_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                calc_amount = min(calc_amount, comp.amount)
            else:
                calc_amount = Decimal('0')
        elif comp.calculation_type == 'percentage_of_basic':
            calc_amount = (earned_basic * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif comp.calculation_type in ('percentage_of_gross', 'percentage_of_ctc'):
            calc_amount = (pre_gross * comp.amount / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            calc_amount = comp.amount

        # Build display name — show days breakdown for variable components
        display_name = comp.name
        if comp.calculation_type == 'variable':
            display_name = f"{comp.name} ({payable_days}/{total_days} days)"

        if comp.component_type == 'earning':
            earnings.append({'name': display_name, 'amount': calc_amount})
            total_earnings_components += calc_amount
        else:
            deductions.append({'name': display_name, 'amount': calc_amount})
            total_deductions_amount += calc_amount

    # -> ADD BONUS HERE <-
    from .models import Bonus as _Bonus
    _bonus_qs = _Bonus.objects.filter(
        employee=employee,
        month=month,
        year=year,
        status__in=['approved', 'paid']
    )
    for _b in _bonus_qs:
        earnings.append({'name': f"Bonus ({_b.get_bonus_type_display()})", 'amount': _b.amount})
        total_earnings_components += _b.amount

    # -> ADD ADJUSTMENTS HERE <-
    _existing_slip_for_adj = Payslip.objects.filter(
        employee=employee,
        payroll_run__pay_period_start__lte=last_day,
        payroll_run__pay_period_end__gte=first_day,
    ).order_by('-payroll_run__pay_date').first()
    
    if _existing_slip_for_adj:
        for _adj in PayslipAdjustment.objects.filter(payslip=_existing_slip_for_adj):
            if _adj.adjustment_type == 'earning':
                earnings.append({'name': f"Adjustment ({_adj.description})", 'amount': _adj.amount})
                total_earnings_components += _adj.amount
            elif _adj.adjustment_type == 'deduction':
                deductions.append({'name': f"Adjustment ({_adj.description})", 'amount': _adj.amount})
                total_deductions_amount += _adj.amount

    total_earnings = earned_basic + total_earnings_components

    # Overtime calculation (only if pay_ot is enabled on salary record)
    overtime_rate = Decimal('0')
    overtime_amount = Decimal('0')
    _pay_ot = salary.pay_ot if salary else False
    if _pay_ot:
        _emp_policy = employee.attendance_policy
        if _emp_policy and _emp_policy.is_active:
            overtime_rate = _emp_policy.overtime_rate
        else:
            try:
                _fallback_policy = AttendancePolicy.objects.filter(is_active=True).first()
                if _fallback_policy:
                    overtime_rate = _fallback_policy.overtime_rate
            except Exception:
                pass
        overtime_amount = (total_overtime_hours * overtime_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    # Advance payment deduction
    from .models import AdvancePayment as _AdvPay, Payslip as _Payslip

    advance_deduction = Decimal('0')
    advance_details = []

    # ── First: check if a payslip already exists for this employee + period ──
    # If generate_payslips already ran and processed (then cleared) the advance,
    # the stored advance_deduction on the payslip is the authoritative value.
    _existing_slip = _Payslip.objects.filter(
        employee=employee,
        payroll_run__pay_period_start__lte=last_day,
        payroll_run__pay_period_end__gte=first_day,
    ).order_by('-payroll_run__pay_date').first()

    if _existing_slip and (_existing_slip.advance_deduction or Decimal('0')) > 0:
        # Payslip exists with advance deduction already applied — use stored value
        advance_deduction = _existing_slip.advance_deduction
        # Show cleared advances that contributed to this deduction
        _cleared_advances = list(
            _AdvPay.objects.filter(employee=employee, status__in=['cleared', 'repaying'])
        )
        if _cleared_advances:
            for _adv in _cleared_advances:
                advance_details.append({'name': f'Advance ({_adv.advance_number})', 'amount': None})
            # If only one advance, assign the full amount; otherwise show total as one row
            if len(_cleared_advances) == 1:
                advance_details[0]['amount'] = advance_deduction
                deductions.append({'name': f'Advance ({_cleared_advances[0].advance_number})', 'amount': advance_deduction})
            else:
                advance_details = [{'name': 'Advance Deduction (Applied)', 'amount': advance_deduction}]
                deductions.append({'name': 'Advance Deduction (Applied)', 'amount': advance_deduction})
        else:
            deductions.append({'name': 'Advance Deduction (Applied)', 'amount': advance_deduction})
        total_deductions_amount += advance_deduction
    else:
        # ── No payslip yet — calculate from active (live) advances ──
        _active_advances = list(
            _AdvPay.objects.filter(employee=employee, status__in=['disbursed', 'repaying'])
        )
        for _adv in _active_advances:
            _remaining = max((_adv.amount or Decimal('0')) - (_adv.amount_repaid or Decimal('0')), Decimal('0'))
            _inst = _adv.installment_amount or Decimal('0')
            _mode = _adv.repayment_mode
            if _mode in ('salary_deduction', 'installments') or not _mode:
                _ded = min(_inst if _inst > 0 else _remaining, _remaining)
            elif _mode == 'lump_sum':
                _ded = _remaining
            else:
                _ded = min(_inst if _inst > 0 else _remaining, _remaining)
            _ded = _ded.quantize(Decimal('0.01'))
            if _ded > 0:
                advance_deduction += _ded
                advance_details.append({'name': f'Advance ({_adv.advance_number})', 'amount': _ded})
                deductions.append({'name': f'Advance ({_adv.advance_number})', 'amount': _ded})
                total_deductions_amount += _ded

    # salary_comp_deductions = component deductions only (without advance)
    salary_comp_deductions = total_deductions_amount - advance_deduction

    # Net salary = total_earnings - all_deductions + overtime
    net_salary = max(total_earnings - total_deductions_amount + overtime_amount, Decimal('0'))

    # Build available months for the dropdown (last 12 months)
    available_months = []
    for i in range(12):
        m = now.month - i
        y = now.year
        if m <= 0:
            m += 12
            y -= 1
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
        # Attendance data
        'total_days': total_days,
        'weekend_days': weekend_days,
        'holiday_days': holiday_days,
        'duty_days': duty_days,
        'present_days': present_days,
        'present_on_weekend': present_on_weekend,
        'paid_leave_days': paid_leave_days,
        'absent_days': absent_days,
        'misc_days': misc_days,
        'half_days': half_days,
        'ot_hours': ot_hours,
        'total_overtime_hours': total_overtime_hours,
        # Salary
        'basic_salary': basic_salary,
        'earned_basic': earned_basic,
        'payable_days': payable_days,
        'per_day_salary': per_day_salary,
        'net_salary': net_salary,
        'earnings': earnings,
        'deductions': deductions,
        'total_earnings': total_earnings,
        'total_deductions_amount': total_deductions_amount,
        'salary_comp_deductions': salary_comp_deductions,
        'overtime_amount': overtime_amount,
        'overtime_rate': overtime_rate,
        'pay_ot': _pay_ot,
        'advance_deduction': advance_deduction,
        'attendance_data': attendance_data,
        'available_months': available_months,
        'sandwich_days': sandwich_days,
    }
    return render(request, 'hrm/payroll_calculation.html', context)


@login_required
def attendance_report(request):
    from .models import (
        AttendanceRecord, Employee, Department, EmployeeWeekend,
        LeaveRequest, LeaveType,
    )
    from django.core.paginator import Paginator
    from django.db.models import Q, Count, Sum, F, DecimalField
    from django.db.models.functions import Coalesce
    from django.http import HttpResponse
    from collections import defaultdict
    from decimal import Decimal
    import csv
    from datetime import datetime, date as dt_date

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

    # ── Parse effective date range ──────────────────────────────────────────
    _eff_from = None
    _eff_to = None
    try:
        if date_from:
            _eff_from = datetime.strptime(date_from, '%Y-%m-%d').date()
    except ValueError:
        pass
    try:
        if date_to:
            _eff_to = datetime.strptime(date_to, '%Y-%m-%d').date()
    except ValueError:
        pass

    # ── Helper: count total calendar days ────────────────────────────────────
    total_days_in_range = (_eff_to - _eff_from).days + 1 if _eff_from and _eff_to else None

    # ── Helper: count weekdays (Mon-Fri) ─────────────────────────────────────
    def _count_weekdays(start, end):
        if not start or not end:
            return None
        count = 0
        current = start
        one_day = timedelta(days=1)
        while current <= end:
            if current.weekday() < 5:
                count += 1
            current += one_day
        return count

    working_days_in_range = _count_weekdays(_eff_from, _eff_to) if _eff_from and _eff_to else None

    # ── Helper: count employee weekend days in range ─────────────────────────
    DAY_NAME_TO_NUM = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6,
    }

    def _count_weekend_days_for_employee(emp_id, start, end):
        """Count how many days in [start..end] fall on the employee's weekend days."""
        if not start or not end:
            return 0
        wk_records = EmployeeWeekend.objects.filter(
            employee_id=emp_id,
            weekend_type='weekend',
            effective_from__lte=end,
        ).filter(Q(effective_to__gte=start) | Q(effective_to__isnull=True))
        weekend_nums = set()
        for wr in wk_records:
            for day_name in (wr.weekend_days or []):
                num = DAY_NAME_TO_NUM.get(day_name.lower())
                if num is not None:
                    weekend_nums.add(num)
        if not weekend_nums:
            weekend_nums = {5, 6}  # default Sat+Sun
        count = 0
        current = start
        one_day = timedelta(days=1)
        while current <= end:
            if current.weekday() in weekend_nums:
                count += 1
            current += one_day
        return count

    def _get_weekend_nums_for_employee(emp_id, start, end):
        """Get the set of weekday numbers that are weekends for this employee."""
        if not start or not end:
            return {5, 6}
        wk_records = EmployeeWeekend.objects.filter(
            employee_id=emp_id,
            weekend_type='weekend',
            effective_from__lte=end,
        ).filter(Q(effective_to__gte=start) | Q(effective_to__isnull=True))
        weekend_nums = set()
        for wr in wk_records:
            for day_name in (wr.weekend_days or []):
                num = DAY_NAME_TO_NUM.get(day_name.lower())
                if num is not None:
                    weekend_nums.add(num)
        if not weekend_nums:
            weekend_nums = {5, 6}
        return weekend_nums

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
    stats['working_days'] = working_days_in_range

    # ── Per-employee summary with full column set ────────────────────────────
    _emp_qs = (
        qs.values(
            'employee__id',
            'employee__employee_id',
            'employee__employee_code',
            'employee__full_name',
            'employee__department__name',
            'employee__department__id',
        )
        .annotate(
            total=Count('id'),
            present=Count('id', filter=Q(status='present')),
            absent=Count('id', filter=Q(status='absent')),
            late=Count('id', filter=Q(status='late')),
            half_day=Count('id', filter=Q(status='half_day')),
            on_leave=Count('id', filter=Q(status='on_leave')),
            total_working_hours=Coalesce(Sum('working_hours'), Decimal('0'), output_field=DecimalField()),
            total_overtime_hours=Coalesce(Sum('overtime_hours'), Decimal('0'), output_field=DecimalField()),
            holiday_present=Count('id', filter=Q(status__in=['present', 'late', 'half_day'], is_holiday=True)),
            late_in_count=Count('id', filter=Q(is_late_arrival=True)),
            early_out_count=Count('id', filter=Q(is_early_departure=True)),
            holiday_days_count=Count('id', filter=Q(is_holiday=True)),
        )
        .order_by('employee__department__name', 'employee__full_name')
    )

    # ── Build leave data per employee ────────────────────────────────────────
    # Collect the employee IDs from the filtered queryset to scope leave data
    _filtered_emp_ids = list(
        qs.values_list('employee__id', flat=True).distinct()
    )

    leave_data = {}  # emp_id -> {'paid': X, 'unpaid': Y}
    if _eff_from and _eff_to and _filtered_emp_ids:
        leave_qs = LeaveRequest.objects.filter(
            status='approved',
            employee_id__in=_filtered_emp_ids,
            start_date__lte=_eff_to,
            end_date__gte=_eff_from,
        ).select_related('leave_type')
        for lr in leave_qs:
            eid = lr.employee_id
            if eid not in leave_data:
                leave_data[eid] = {'paid': 0, 'unpaid': 0}
            # Calculate overlapping days within the report range
            overlap_start = max(lr.start_date, _eff_from)
            overlap_end = min(lr.end_date, _eff_to)
            days_in_range = (overlap_end - overlap_start).days + 1
            if days_in_range > 0:
                is_paid = lr.leave_type.is_paid if lr.leave_type else True
                if is_paid:
                    leave_data[eid]['paid'] += days_in_range
                else:
                    leave_data[eid]['unpaid'] += days_in_range

    # ── Build detailed per-employee records for present on weekend/dayoff ────
    emp_extra = {}  # emp_id -> {present_on_weekend: X, present_on_dayoff: X, ...}
    if _eff_from and _eff_to:
        # Get all records in range that are present/late/half_day
        present_records = qs.filter(
            status__in=['present', 'late', 'half_day'],
        ).values_list('employee__id', 'date', 'shift__is_night_shift', 'is_holiday')

        # Group by employee
        emp_present_dates = defaultdict(list)
        for emp_id, rec_date, is_night, is_hol in present_records:
            emp_present_dates[emp_id].append((rec_date, is_night, is_hol))

        for emp_id, dates_list in emp_present_dates.items():
            weekend_nums = _get_weekend_nums_for_employee(emp_id, _eff_from, _eff_to)
            present_on_off_day = 0
            for rec_date, is_night, is_hol in dates_list:
                if rec_date.weekday() in weekend_nums or is_hol:
                    present_on_off_day += 1
            emp_extra[emp_id] = {
                'present_on_off': present_on_off_day,
            }

    # ── Pre-fetch notes & office-visit counts (batch, avoid N+1) ───────────
    _emp_notes = {}      # emp_id -> list of notes
    _emp_ov_count = {}   # emp_id -> office visit count
    if _filtered_emp_ids:
        # Batch: collect non-empty notes per employee (up to 3)
        _notes_qs = (
            qs.filter(notes__gt='')
            .values_list('employee__id', 'notes')
            .order_by('employee__id')
        )
        _notes_by_emp = defaultdict(set)
        for eid, note in _notes_qs:
            if len(_notes_by_emp[eid]) < 3:
                _notes_by_emp[eid].add(note)
        _emp_notes = {eid: list(notes) for eid, notes in _notes_by_emp.items()}

        # Batch: office visit counts
        _ov_qs = (
            qs.filter(notes__icontains='office visit')
            .values('employee__id')
            .annotate(ov_count=Count('id'))
        )
        _emp_ov_count = {item['employee__id']: item['ov_count'] for item in _ov_qs}

    # ── Pre-compute elapsed holiday counts (for absent = past days only) ────
    today = dt_date.today()
    _yesterday = today - timedelta(days=1)
    _elapsed_to = min(_eff_to, _yesterday) if _eff_from and _eff_to else None
    _elapsed_holidays_per_emp = {}
    if _eff_from and _elapsed_to and _eff_from <= _yesterday and _elapsed_to < _eff_to:
        _eh_qs = (
            qs.filter(is_holiday=True, date__lte=_elapsed_to)
            .values('employee__id')
            .annotate(cnt=Count('id'))
        )
        _elapsed_holidays_per_emp = {item['employee__id']: item['cnt'] for item in _eh_qs}

    # ── Build full summary ───────────────────────────────────────────────────
    emp_summary = []
    dept_grouped = {}  # For grouping by department
    for e in _emp_qs:
        emp_id = e['employee__id']
        worked = e['present'] + e['late'] + e['half_day']

        # Weekend days for this employee in the range
        weekend_days = _count_weekend_days_for_employee(emp_id, _eff_from, _eff_to) if _eff_from and _eff_to else 0

        # Holiday days from attendance records marked as holiday
        holiday_days = e['holiday_days_count']

        # Total days: calendar days if range given, else total attendance records
        total_days = total_days_in_range if total_days_in_range else e['total']

        # Duty days = total days - weekend days - holiday days (only when range exists)
        if total_days_in_range:
            duty_days = max(total_days - weekend_days - holiday_days, 0)
        else:
            # No date range: duty = total records - holidays
            duty_days = max(e['total'] - holiday_days, 0)

        # Elapsed duty days (absent counts only fully-completed past days, today excluded)
        if _eff_from and _eff_to:
            if _eff_from > _yesterday:
                elapsed_duty_days = 0
            elif _eff_to <= _yesterday:
                elapsed_duty_days = duty_days
            else:
                elapsed_total = (_elapsed_to - _eff_from).days + 1
                elapsed_weekend = _count_weekend_days_for_employee(emp_id, _eff_from, _elapsed_to)
                elapsed_holiday = _elapsed_holidays_per_emp.get(emp_id, 0)
                elapsed_duty_days = max(elapsed_total - elapsed_weekend - elapsed_holiday, 0)
        else:
            elapsed_duty_days = duty_days

        # Present days (including late + half_day)
        present_days = worked

        # Present on holiday
        present_on_holiday = e['holiday_present']

        # Present on weekend/day off/holiday
        extra = emp_extra.get(emp_id, {})
        present_on_off = extra.get('present_on_off', 0)

        # Leave
        emp_leave = leave_data.get(emp_id, {'paid': 0, 'unpaid': 0})

        # Attendance percentage
        if duty_days and duty_days > 0:
            pct = round(worked * 100 / duty_days)
        elif e['total'] > 0:
            pct = round(worked * 100 / e['total'])
        else:
            pct = 0
        pct = min(pct, 100)

        # Notes/remarks — from pre-fetched batch
        emp_notes_list = _emp_notes.get(emp_id, [])
        remarks = '; '.join(emp_notes_list) if emp_notes_list else ''

        # Office visit: from pre-fetched batch
        office_visit = _emp_ov_count.get(emp_id, 0)

        row = {
            'employee__id': emp_id,
            'employee__employee_id': e['employee__employee_id'],
            'employee__employee_code': e['employee__employee_code'] or e['employee__employee_id'],
            'employee__full_name': e['employee__full_name'],
            'employee__department__name': e['employee__department__name'] or '—',
            'employee__department__id': e['employee__department__id'],
            'total_days': total_days,
            'duty_days': duty_days,
            'holiday_days': holiday_days,
            'weekend_days': weekend_days,
            'day_off': 0,
            'night_off': 0,
            'present_days': present_days,
            'present_on_holiday': present_on_holiday,
            'present_on_off': present_on_off,
            'absent': max(elapsed_duty_days - (present_days - present_on_off) - e['on_leave'], 0),
            'misc_days': e['half_day'],
            'leave_paid': emp_leave['paid'],
            'leave_unpaid': emp_leave['unpaid'],
            'worked_hours': e['total_working_hours'] or Decimal('0'),
            'ot_hours': e['total_overtime_hours'] or Decimal('0'),
            'late_in': e['late_in_count'],
            'late_out': 0,
            'early_in': 0,
            'early_out': e['early_out_count'],
            'office_visit': office_visit,
            'remarks': remarks,
            # Legacy fields
            'total': e['total'],
            'present': e['present'],
            'late': e['late'],
            'half_day': e['half_day'],
            'on_leave': e['on_leave'],
            'total_working_hours': e['total_working_hours'],
            'pct': pct,
            'pct_color': '#16a34a' if pct >= 90 else ('#d97706' if pct >= 70 else '#e11d48'),
            'working_days': working_days_in_range,
        }
        emp_summary.append(row)

        # Group by department
        dept_name = row['employee__department__name']
        dept_id = row['employee__department__id']
        dept_key = f"{dept_id}-{dept_name}" if dept_id else f"0-{dept_name}"
        if dept_key not in dept_grouped:
            dept_grouped[dept_key] = {
                'dept_label': f"{dept_id}-{dept_name}" if dept_id else dept_name,
                'employees': [],
            }
        dept_grouped[dept_key]['employees'].append(row)

    # Sort department groups by name
    dept_groups = sorted(dept_grouped.values(), key=lambda g: g['dept_label'])

    # ── CSV export ───────────────────────────────────────────────────────────
    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="employee_summary_report.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Code', 'Name', 'Department', 'Total Days', 'Duty Days',
            'Holiday Days', 'Weekend Days', 'Day Off', 'Night Off',
            'Present Days', 'Present On Holiday', 'Present On Day Off/Night Off/Weekend',
            'Absent Days', 'Misc Days', 'Leave Days - Paid', 'Leave Days - Unpaid',
            'Worked Hours', 'OT Hours', 'Late In', 'Late Out', 'Early In', 'Early Out',
            'Office Visit', 'Remarks',
        ])
        for row in emp_summary:
            writer.writerow([
                row['employee__employee_code'],
                row['employee__full_name'],
                row['employee__department__name'],
                row['total_days'],
                row['duty_days'],
                row['holiday_days'],
                row['weekend_days'],
                row['day_off'],
                row['night_off'],
                row['present_days'],
                row['present_on_holiday'],
                row['present_on_off'],
                row['absent'],
                row['misc_days'],
                row['leave_paid'],
                row['leave_unpaid'],
                row['worked_hours'],
                row['ot_hours'],
                row['late_in'],
                row['late_out'],
                row['early_in'],
                row['early_out'],
                row['office_visit'],
                row['remarks'],
            ])
        return response

    # ── Pagination (raw records table) ────────────────────────────────────────
    paginator = Paginator(qs, per_page)
    page_num  = request.GET.get('page', 1)
    records   = paginator.get_page(page_num)

    departments = Department.objects.filter(status='active').order_by('name')
    employees = Employee.objects.filter(employee_status='active').select_related('department').order_by('full_name')

    context = {
        'records':          records,
        'stats':            stats,
        'emp_summary':      emp_summary,
        'dept_groups':      dept_groups,
        'departments':      departments,
        'employees':        employees,
        'search':           search,
        'date_from':        date_from,
        'date_to':          date_to,
        'department':       department,
        'sel_status':       status,
        'per_page':         per_page,
        'status_choices':   AttendanceRecord.STATUS_CHOICES,
        'company_name':     'HRM System',
    }
    return render(request, 'hrm/attendance_report.html', context)


@login_required
def employee_period_attendance(request):
    from .models import AttendanceRecord, Employee, EmployeeWeekend, LeaveRequest
    from django.db.models import Q
    from django.http import JsonResponse
    from datetime import date, timedelta
    from collections import defaultdict
    import calendar

    DAY_NAME_TO_NUM = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6,
    }

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
            'is_night_shift': rec.shift.is_night_shift if rec.shift else False,
            'notes': rec.notes,
        })

    total_working = sum(r['working_hours'] for r in records_data)
    total_ot = sum(r['overtime_hours'] for r in records_data)
    sc = {}
    for r in records_data:
        sc[r['status']] = sc.get(r['status'], 0) + 1

    # ── Compute weekend days using EmployeeWeekend ──
    wk_records = EmployeeWeekend.objects.filter(
        employee=employee, weekend_type='weekend', effective_from__lte=date_to,
    ).filter(Q(effective_to__gte=date_from) | Q(effective_to__isnull=True))
    weekend_nums = set()
    for wr in wk_records:
        for day_name in (wr.weekend_days or []):
            num = DAY_NAME_TO_NUM.get(day_name.lower())
            if num is not None:
                weekend_nums.add(num)
    if not weekend_nums:
        weekend_nums = {5, 6}

    total_days_in_range = (date_to - date_from).days + 1
    weekend_day_count = 0
    _d = date_from
    while _d <= date_to:
        if _d.weekday() in weekend_nums:
            weekend_day_count += 1
        _d += timedelta(days=1)

    # Holiday days count
    holiday_days_count = sum(1 for r in records_data if r['is_holiday'])

    # Duty days = total - weekend - holiday (avoid double-count)
    duty_days = max(total_days_in_range - weekend_day_count - holiday_days_count, 0)

    # Elapsed duty days (absent counts only fully-completed past days, today excluded)
    today = date.today()
    yesterday = today - timedelta(days=1)
    if date_from > yesterday:
        elapsed_duty_days = 0
    elif date_to <= yesterday:
        elapsed_duty_days = duty_days
    else:
        elapsed_to = min(date_to, yesterday)
        elapsed_total = (elapsed_to - date_from).days + 1
        elapsed_weekend = 0
        _d = date_from
        while _d <= elapsed_to:
            if _d.weekday() in weekend_nums:
                elapsed_weekend += 1
            _d += timedelta(days=1)
        yesterday_iso = yesterday.isoformat()
        elapsed_holiday = sum(1 for r in records_data if r['is_holiday'] and r['date'] <= yesterday_iso)
        elapsed_duty_days = max(elapsed_total - elapsed_weekend - elapsed_holiday, 0)

    # Present count (present + late + half_day)
    present_cnt = sc.get('present', 0) + sc.get('late', 0) + sc.get('half_day', 0)

    # Present on holiday
    present_on_holiday = sum(1 for r in records_data if r['is_holiday'] and r['status'] in ('present', 'late', 'half_day'))

    # Present on day off / night off / weekend
    present_on_off = 0
    for r in records_data:
        rec_date = date.fromisoformat(r['date'])
        if r['status'] in ('present', 'late', 'half_day'):
            if rec_date.weekday() in weekend_nums or r['is_holiday']:
                present_on_off += 1

    # Late-in and early-out counts
    late_in_count = sum(1 for r in records_data if r['is_late_arrival'])
    early_out_count = sum(1 for r in records_data if r['is_early_departure'])

    # Office visit: records with 'office visit' in notes
    office_visit_count = sum(1 for r in records_data if r.get('notes') and 'office visit' in r['notes'].lower())

    # Leave data (paid / unpaid)
    leave_paid = 0
    leave_unpaid = 0
    leave_qs = LeaveRequest.objects.filter(
        status='approved', employee=employee,
        start_date__lte=date_to, end_date__gte=date_from,
    ).select_related('leave_type')
    for lr in leave_qs:
        overlap_start = max(lr.start_date, date_from)
        overlap_end = min(lr.end_date, date_to)
        days_in_range = (overlap_end - overlap_start).days + 1
        if days_in_range > 0:
            is_paid = lr.leave_type.is_paid if lr.leave_type else True
            if is_paid:
                leave_paid += days_in_range
            else:
                leave_unpaid += days_in_range

    # Remarks from notes
    notes_list = [r['notes'] for r in records_data if r.get('notes')]
    remarks = '; '.join(notes_list[:3]) if notes_list else ''

    # Count weekdays (Mon-Fri) for working_days
    _wd_count = 0
    _d = date_from
    while _d <= date_to:
        if _d.weekday() < 5:
            _wd_count += 1
        _d += timedelta(days=1)

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
        'weekend_nums': sorted(weekend_nums),
        'holiday_dates': [r['date'] for r in records_data if r['is_holiday']],
        'records': records_data,
        'records_by_date': {r['date']: r for r in records_data},
        'summary': {
            'total': len(records_data),
            'working_days': _wd_count,
            'present': sc.get('present', 0),
            'absent': max(elapsed_duty_days - (present_cnt - present_on_off) - sc.get('on_leave', 0), 0),
            'late': sc.get('late', 0),
            'half_day': sc.get('half_day', 0),
            'on_leave': sc.get('on_leave', 0),
            'total_working_hours': round(total_working, 2),
            'total_overtime_hours': round(total_ot, 2),
        },
        'detail_summary': {
            'code': employee.employee_id,
            'name': employee.full_name,
            'department': employee.department.name if employee.department else '—',
            'total_days': total_days_in_range,
            'duty_days': duty_days,
            'holiday_days': holiday_days_count,
            'weekend_days': weekend_day_count,
            'day_off': 0,
            'night_off': 0,
            'present_days': present_cnt,
            'present_on_holiday': present_on_holiday,
            'present_on_off': present_on_off,
            'absent_days': max(elapsed_duty_days - (present_cnt - present_on_off) - sc.get('on_leave', 0), 0),
            'misc_days': sc.get('half_day', 0),
            'leave_paid': leave_paid,
            'leave_unpaid': leave_unpaid,
            'worked_hours': round(total_working, 2),
            'ot_hours': round(total_ot, 2),
            'late_in': late_in_count,
            'late_out': 0,
            'early_in': 0,
            'early_out': early_out_count,
            'office_visit': office_visit_count,
            'remarks': remarks,
        },
    })


@login_required
def employee_summary_report_ajax(request):
    """AJAX endpoint: return filtered employee summary data as JSON."""
    from .models import (
        AttendanceRecord, Employee, Department, EmployeeWeekend,
        LeaveRequest, LeaveType,
    )
    from django.db.models import Q, Count, Sum, DecimalField
    from django.db.models.functions import Coalesce
    from django.http import JsonResponse
    from collections import defaultdict
    from decimal import Decimal
    from datetime import datetime, timedelta

    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    employee_ids = request.GET.getlist('employee')
    department_ids = request.GET.getlist('department')

    if not date_from or not date_to:
        return JsonResponse({'error': 'Date range is required.'}, status=400)

    try:
        _eff_from = datetime.strptime(date_from, '%Y-%m-%d').date()
        _eff_to = datetime.strptime(date_to, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Invalid date format.'}, status=400)

    # Start from Employee — so ALL matching employees appear even with 0 records
    emp_qs = Employee.objects.filter(
        employee_status='active',
    ).select_related('department').order_by('department__name', 'full_name')

    if employee_ids:
        emp_qs = emp_qs.filter(id__in=employee_ids)
    if department_ids:
        emp_qs = emp_qs.filter(department_id__in=department_ids)

    # Attendance records in date range — filtered once
    att_qs = AttendanceRecord.objects.select_related('shift').filter(
        date__gte=_eff_from, date__lte=_eff_to,
        employee__in=emp_qs,
    )

    total_days_in_range = (_eff_to - _eff_from).days + 1

    DAY_NAME_TO_NUM = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6,
    }

    def _count_weekend_days(emp_id, start, end):
        wk_records = EmployeeWeekend.objects.filter(
            employee_id=emp_id, weekend_type='weekend', effective_from__lte=end,
        ).filter(Q(effective_to__gte=start) | Q(effective_to__isnull=True))
        weekend_nums = set()
        for wr in wk_records:
            for day_name in (wr.weekend_days or []):
                num = DAY_NAME_TO_NUM.get(day_name.lower())
                if num is not None:
                    weekend_nums.add(num)
        if not weekend_nums:
            weekend_nums = {5, 6}
        count = 0
        current = start
        one_day = timedelta(days=1)
        while current <= end:
            if current.weekday() in weekend_nums:
                count += 1
            current += one_day
        return count, weekend_nums

    # Pre-aggregate attendance per employee via queryset annotation
    att_agg = (
        att_qs.values('employee_id').annotate(
            total=Count('id'),
            present=Count('id', filter=Q(status='present')),
            absent=Count('id', filter=Q(status='absent')),
            late=Count('id', filter=Q(status='late')),
            half_day=Count('id', filter=Q(status='half_day')),
            on_leave=Count('id', filter=Q(status='on_leave')),
            total_working_hours=Coalesce(Sum('working_hours'), Decimal('0'), output_field=DecimalField()),
            total_overtime_hours=Coalesce(Sum('overtime_hours'), Decimal('0'), output_field=DecimalField()),
            holiday_present=Count('id', filter=Q(status__in=['present', 'late', 'half_day'], is_holiday=True)),
            late_in_count=Count('id', filter=Q(is_late_arrival=True)),
            early_out_count=Count('id', filter=Q(is_early_departure=True)),
            holiday_days_count=Count('id', filter=Q(is_holiday=True)),
        )
    )
    att_map = {a['employee_id']: a for a in att_agg}

    # Leave data
    all_emp_ids = list(emp_qs.values_list('id', flat=True))
    leave_data = {}
    if all_emp_ids:
        leave_qs = LeaveRequest.objects.filter(
            status='approved', employee_id__in=all_emp_ids,
            start_date__lte=_eff_to, end_date__gte=_eff_from,
        ).select_related('leave_type')
        for lr in leave_qs:
            eid = lr.employee_id
            if eid not in leave_data:
                leave_data[eid] = {'paid': 0, 'unpaid': 0}
            overlap_start = max(lr.start_date, _eff_from)
            overlap_end = min(lr.end_date, _eff_to)
            days_in_range = (overlap_end - overlap_start).days + 1
            if days_in_range > 0:
                is_paid = lr.leave_type.is_paid if lr.leave_type else True
                if is_paid:
                    leave_data[eid]['paid'] += days_in_range
                else:
                    leave_data[eid]['unpaid'] += days_in_range

    # Present on off days
    present_records = att_qs.filter(
        status__in=['present', 'late', 'half_day'],
    ).values_list('employee_id', 'date', 'shift__is_night_shift', 'is_holiday')
    emp_present_dates = defaultdict(list)
    for emp_id, rec_date, is_night, is_hol in present_records:
        emp_present_dates[emp_id].append((rec_date, is_night, is_hol))

    # Notes & office visit batch
    _emp_notes = {}
    _emp_ov_count = {}
    if all_emp_ids:
        _notes_qs = att_qs.filter(notes__gt='').values_list('employee_id', 'notes').order_by('employee_id')
        _notes_by_emp = defaultdict(set)
        for eid, note in _notes_qs:
            if len(_notes_by_emp[eid]) < 3:
                _notes_by_emp[eid].add(note)
        _emp_notes = {eid: list(notes) for eid, notes in _notes_by_emp.items()}
        _ov_qs = att_qs.filter(notes__icontains='office visit').values('employee_id').annotate(ov_count=Count('id'))
        _emp_ov_count = {item['employee_id']: item['ov_count'] for item in _ov_qs}

    # Pre-compute today/yesterday and elapsed holidays to avoid N+1 queries
    _today = date.today()
    _yesterday = _today - timedelta(days=1)
    _elapsed_holiday_per_emp = {}
    if _eff_from <= _yesterday < _eff_to:  # range straddles today
        _eh_agg = (
            att_qs.filter(is_holiday=True, date__lte=_yesterday)
            .values('employee_id')
            .annotate(cnt=Count('id'))
        )
        _elapsed_holiday_per_emp = {item['employee_id']: item['cnt'] for item in _eh_agg}

    # Build rows — iterate over ALL employees
    dept_grouped = {}
    for emp in emp_qs:
        emp_id = emp.id
        a = att_map.get(emp_id, {})
        present_cnt = a.get('present', 0) + a.get('late', 0) + a.get('half_day', 0)
        weekend_days, weekend_nums = _count_weekend_days(emp_id, _eff_from, _eff_to)
        holiday_days = a.get('holiday_days_count', 0)
        total_days = total_days_in_range
        duty_days = max(total_days - weekend_days - holiday_days, 0)

        # Elapsed duty days (absent counts only fully-completed past days, today excluded)
        if _eff_from > _yesterday:
            elapsed_duty_days = 0
        elif _eff_to <= _yesterday:
            elapsed_duty_days = duty_days
        else:
            _el_total = (_yesterday - _eff_from).days + 1
            _el_weekend, _ = _count_weekend_days(emp_id, _eff_from, _yesterday)
            _el_holiday = _elapsed_holiday_per_emp.get(emp_id, 0)
            elapsed_duty_days = max(_el_total - _el_weekend - _el_holiday, 0)

        # Present on off
        present_on_off = 0
        if emp_id in emp_present_dates:
            for rec_date, is_night, is_hol in emp_present_dates[emp_id]:
                if rec_date.weekday() in weekend_nums or is_hol:
                    present_on_off += 1

        emp_leave = leave_data.get(emp_id, {'paid': 0, 'unpaid': 0})
        emp_notes_list = _emp_notes.get(emp_id, [])
        remarks = '; '.join(emp_notes_list) if emp_notes_list else ''
        office_visit = _emp_ov_count.get(emp_id, 0)

        row = {
            'code': emp.employee_id,
            'name': emp.full_name,
            'total_days': total_days,
            'duty_days': duty_days,
            'holiday_days': holiday_days,
            'weekend_days': weekend_days,
            'day_off': 0,
            'night_off': 0,
            'present_days': present_cnt,
            'present_on_holiday': a.get('holiday_present', 0),
            'present_on_off': present_on_off,
            'absent': max(elapsed_duty_days - (present_cnt - present_on_off) - a.get('on_leave', 0), 0),
            'misc_days': a.get('half_day', 0),
            'leave_paid': emp_leave['paid'],
            'leave_unpaid': emp_leave['unpaid'],
            'worked_hours': float(a.get('total_working_hours') or 0),
            'ot_hours': float(a.get('total_overtime_hours') or 0),
            'late_in': a.get('late_in_count', 0),
            'late_out': 0,
            'early_in': 0,
            'early_out': a.get('early_out_count', 0),
            'office_visit': office_visit,
            'remarks': remarks,
        }

        dept_name = emp.department.name if emp.department else '—'
        dept_id = emp.department_id
        dept_key = f"{dept_id}-{dept_name}" if dept_id else f"0-{dept_name}"
        if dept_key not in dept_grouped:
            dept_grouped[dept_key] = {'dept_label': dept_name, 'employees': []}
        dept_grouped[dept_key]['employees'].append(row)

    dept_groups = sorted(dept_grouped.values(), key=lambda g: g['dept_label'])

    return JsonResponse({
        'dept_groups': dept_groups,
        'date_from': date_from,
        'date_to': date_to,
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


@login_required
def leave_update_status(request, pk):
    from .models import LeaveRequest
    from django.utils import timezone
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        leave_req = LeaveRequest.objects.select_related('employee').get(pk=pk)
    except LeaveRequest.DoesNotExist:
        return JsonResponse({'error': 'Leave request not found'}, status=404)
    action = request.POST.get('action', '').strip()
    rejection_reason = request.POST.get('rejection_reason', '').strip()
    if action not in ['approve', 'reject', 'cancel']:
        return JsonResponse({'error': 'Invalid action'}, status=400)
    if action == 'reject' and not rejection_reason:
        return JsonResponse({'error': 'Rejection reason is required'}, status=400)
    approver = None
    try:
        approver = request.user.employee_profile
    except Exception:
        pass
    if action == 'approve':
        leave_req.status = 'approved'
        leave_req.approved_by = approver
        leave_req.approved_at = timezone.now()
        leave_req.rejection_reason = ''
    elif action == 'reject':
        leave_req.status = 'rejected'
        leave_req.approved_by = approver
        leave_req.approved_at = timezone.now()
        leave_req.rejection_reason = rejection_reason
    elif action == 'cancel':
        leave_req.status = 'cancelled'
        leave_req.rejection_reason = ''
    leave_req.save()
    return JsonResponse({
        'success': True,
        'status': leave_req.status,
        'status_display': leave_req.get_status_display(),
        'message': f'Leave request {leave_req.get_status_display().lower()} successfully.',
    })


# ==================== ADVANCE PAYMENTS ====================

@login_required
def advance_payment_list(request):
    from .models import AdvancePayment
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')
    per_page = request.GET.get('per_page', '10')

    advances = AdvancePayment.objects.select_related('employee', 'approved_by').all()

    if search_query:
        advances = advances.filter(
            Q(employee__full_name__icontains=search_query) |
            Q(advance_number__icontains=search_query) |
            Q(employee__employee_id__icontains=search_query)
        )
    if status_filter:
        advances = advances.filter(status=status_filter)

    VALID_PER_PAGE = [10, 25, 50, 100]
    try:
        per_page_int = int(per_page)
    except (ValueError, TypeError):
        per_page_int = 10
    if per_page_int not in VALID_PER_PAGE:
        per_page_int = 10
    per_page = str(per_page_int)
    paginator = Paginator(advances, per_page_int)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    total_amount = advances.aggregate(t=Sum('amount'))['t'] or 0
    total_disbursed = advances.filter(status__in=['disbursed', 'repaying', 'cleared']).aggregate(t=Sum('amount'))['t'] or 0
    total_pending = advances.filter(status='pending').count()
    total_cleared = advances.filter(status='cleared').count()

    employees = Employee.objects.filter(employee_status='active').order_by('full_name')

    context = {
        'page_title': 'Advance Payments',
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'per_page': per_page,
        'total_amount': total_amount,
        'total_disbursed': total_disbursed,
        'total_pending': total_pending,
        'total_cleared': total_cleared,
        'status_choices': AdvancePayment.STATUS_CHOICES,
        'employees': employees,
    }
    return render(request, 'hrm/advance_payment_list.html', context)


@login_required
def advance_payment_create(request):
    from .models import AdvancePayment
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    try:
        employee_id = request.POST.get('employee')
        amount = request.POST.get('amount', '').strip()
        payment_date = request.POST.get('payment_date') or None
        reason = request.POST.get('reason', '').strip()
        repayment_mode = request.POST.get('repayment_mode', 'salary_deduction')
        repayment_start_date = request.POST.get('repayment_start_date') or None
        installment_amount = request.POST.get('installment_amount') or None
        total_installments = request.POST.get('total_installments') or None
        notes = request.POST.get('notes', '').strip()

        if not employee_id or not amount:
            return JsonResponse({'success': False, 'error': 'Employee and amount are required.'}, status=400)

        employee = get_object_or_404(Employee, pk=employee_id)
        advance = AdvancePayment(
            employee=employee,
            amount=Decimal(amount),
            payment_date=payment_date,
            reason=reason,
            repayment_mode=repayment_mode,
            repayment_start_date=repayment_start_date,
            installment_amount=Decimal(installment_amount) if installment_amount else None,
            total_installments=int(total_installments) if total_installments else None,
            notes=notes,
            created_by=request.user,
        )
        advance.save()
        return JsonResponse({
            'success': True,
            'message': f'Advance payment {advance.advance_number} created successfully.',
            'advance_id': advance.pk,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def advance_payment_detail(request, pk):
    from .models import AdvancePayment
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    try:
        adv = AdvancePayment.objects.select_related('employee', 'approved_by', 'created_by').get(pk=pk)
    except AdvancePayment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    data = {
        'success': True,
        'advance': {
            'id': adv.pk,
            'advance_number': adv.advance_number,
            'employee_id': adv.employee.pk,
            'employee_name': adv.employee.full_name,
            'employee_code': adv.employee.employee_id,
            'amount': str(adv.amount),
            'amount_repaid': str(adv.amount_repaid),
            'remaining_amount': str(adv.remaining_amount),
            'payment_date': adv.payment_date.strftime('%Y-%m-%d') if adv.payment_date else '',
            'reason': adv.reason,
            'repayment_mode': adv.repayment_mode,
            'repayment_start_date': adv.repayment_start_date.strftime('%Y-%m-%d') if adv.repayment_start_date else '',
            'installment_amount': str(adv.installment_amount) if adv.installment_amount else '',
            'total_installments': adv.total_installments or '',
            'paid_installments': adv.paid_installments,
            'status': adv.status,
            'status_display': adv.get_status_display(),
            'rejection_reason': adv.rejection_reason,
            'notes': adv.notes,
            'approved_by': adv.approved_by.get_full_name() if adv.approved_by else '',
            'approved_at': adv.approved_at.strftime('%Y-%m-%d %H:%M') if adv.approved_at else '',
        }
    }
    return JsonResponse(data)


@login_required
def advance_payment_update(request, pk):
    from .models import AdvancePayment
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    try:
        adv = AdvancePayment.objects.get(pk=pk)
    except AdvancePayment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)

    try:
        employee_id = request.POST.get('employee')
        amount = request.POST.get('amount', '').strip()
        reason = request.POST.get('reason', '').strip()
        if not employee_id or not amount:
            return JsonResponse({'success': False, 'error': 'Employee and amount are required.'}, status=400)

        adv.employee = get_object_or_404(Employee, pk=employee_id)
        adv.amount = Decimal(amount)
        adv.payment_date = request.POST.get('payment_date') or None
        adv.reason = reason
        adv.repayment_mode = request.POST.get('repayment_mode', adv.repayment_mode)
        adv.repayment_start_date = request.POST.get('repayment_start_date') or None
        inst_amt = request.POST.get('installment_amount') or None
        adv.installment_amount = Decimal(inst_amt) if inst_amt else None
        total_inst = request.POST.get('total_installments') or None
        adv.total_installments = int(total_inst) if total_inst else None
        paid_inst = request.POST.get('paid_installments') or None
        if paid_inst:
            adv.paid_installments = int(paid_inst)
        amt_repaid = request.POST.get('amount_repaid') or None
        if amt_repaid:
            adv.amount_repaid = Decimal(amt_repaid)
        adv.notes = request.POST.get('notes', adv.notes)
        adv.save()
        return JsonResponse({'success': True, 'message': 'Advance payment updated successfully.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def advance_payment_delete(request, pk):
    from .models import AdvancePayment
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    try:
        adv = AdvancePayment.objects.get(pk=pk)
    except AdvancePayment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    adv_number = adv.advance_number
    adv.delete()
    return JsonResponse({'success': True, 'message': f'Advance payment {adv_number} deleted successfully.'})


@login_required
def advance_payment_update_status(request, pk):
    from .models import AdvancePayment
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    try:
        adv = AdvancePayment.objects.get(pk=pk)
    except AdvancePayment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)

    action = request.POST.get('action', '').strip()
    rejection_reason = request.POST.get('rejection_reason', '').strip()

    if action == 'approve':
        adv.status = 'approved'
        adv.approved_by = request.user
        adv.approved_at = timezone.now()
        adv.rejection_reason = ''
    elif action == 'reject':
        adv.status = 'rejected'
        adv.approved_by = request.user
        adv.approved_at = timezone.now()
        adv.rejection_reason = rejection_reason
    elif action == 'disburse':
        adv.status = 'disbursed'
        if not adv.payment_date:
            adv.payment_date = date.today()
    elif action == 'mark_repaying':
        adv.status = 'repaying'
    elif action == 'clear':
        adv.status = 'cleared'
        adv.amount_repaid = adv.amount
    elif action == 'set_pending':
        adv.status = 'pending'
        adv.approved_by = None
        adv.rejection_reason = ''
    else:
        return JsonResponse({'success': False, 'error': 'Invalid action.'}, status=400)

    adv.save()
    return JsonResponse({
        'success': True,
        'status': adv.status,
        'status_display': adv.get_status_display(),
        'message': f'Advance payment marked as {adv.get_status_display()}.',
    })


# ==================== LEAVE MANAGEMENT ====================

LEAVE_TYPE_COLORS = [
    '#22c55e', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6',
    '#06b6d4', '#ec4899', '#14b8a6', '#f97316', '#6366f1',
]


@login_required
def leave_application_list(request):
    from .models import LeaveRequest, LeaveType, Employee, Department
    from django.core.paginator import Paginator
    from django.db.models import Q
    import csv

    search = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')
    leave_type_filter = request.GET.get('leave_type', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    per_page = request.GET.get('per_page', '10')
    export = request.GET.get('export', '')

    try:
        per_page = int(per_page)
        if per_page not in [10, 25, 50, 100]:
            per_page = 10
    except (ValueError, TypeError):
        per_page = 10

    qs = LeaveRequest.objects.select_related(
        'employee', 'leave_type', 'approved_by'
    ).order_by('-created_at')

    if search:
        qs = qs.filter(
            Q(employee__full_name__icontains=search) |
            Q(employee__employee_id__icontains=search) |
            Q(employee__email__icontains=search)
        )
    if status_filter:
        qs = qs.filter(status=status_filter)
    if leave_type_filter:
        qs = qs.filter(leave_type_id=leave_type_filter)
    if date_from:
        try:
            from datetime import datetime
            qs = qs.filter(start_date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import datetime
            qs = qs.filter(end_date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass

    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="leave_applications.csv"'
        writer = csv.writer(response)
        writer.writerow(['#', 'Employee ID', 'Employee', 'Email', 'Leave Type',
                         'Start Date', 'End Date', 'Days', 'Status', 'Applied On'])
        for i, la in enumerate(qs.iterator(), 1):
            writer.writerow([
                i, la.employee.employee_id, la.employee.full_name,
                la.employee.email,
                la.leave_type.name if la.leave_type else '',
                la.start_date.strftime('%Y-%m-%d'),
                la.end_date.strftime('%Y-%m-%d'),
                la.days,
                la.get_status_display(),
                la.created_at.strftime('%Y-%m-%d'),
            ])
        return response

    total = qs.count()
    paginator = Paginator(qs, per_page)
    page_num = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_num)

    leave_types = LeaveType.objects.filter(is_active=True).order_by('name')
    employees = Employee.objects.filter(employee_status='active').order_by('full_name')

    # Assign colors to leave types
    lt_colors = {}
    for idx, lt in enumerate(LeaveType.objects.all()):
        lt_colors[lt.pk] = LEAVE_TYPE_COLORS[idx % len(LEAVE_TYPE_COLORS)]

    context = {
        'page_title': 'Leave Applications',
        'page_obj': page_obj,
        'total': total,
        'leave_types': leave_types,
        'employees': employees,
        'lt_colors': lt_colors,
        'search': search,
        'status_filter': status_filter,
        'leave_type_filter': leave_type_filter,
        'date_from': date_from,
        'date_to': date_to,
        'per_page': per_page,
        'per_page_options': [10, 25, 50, 100],
        'status_choices': LeaveRequest.STATUS_CHOICES,
    }
    return render(request, 'hrm/leave_applications.html', context)


@login_required
def leave_application_create(request):
    from .models import LeaveRequest, LeaveType, Employee
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        employee_id = request.POST.get('employee')
        leave_type_id = request.POST.get('leave_type')
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        reason = request.POST.get('reason', '').strip()
        status = request.POST.get('status', 'approved').strip()

        if not all([employee_id, leave_type_id, start_date, end_date, reason]):
            return JsonResponse({'success': False, 'error': 'All required fields must be filled.'}, status=400)

        from datetime import datetime as dt
        employee = get_object_or_404(Employee, pk=employee_id)
        leave_type = get_object_or_404(LeaveType, pk=leave_type_id)

        la = LeaveRequest(
            employee=employee,
            leave_type=leave_type,
            start_date=dt.strptime(start_date, '%Y-%m-%d').date(),
            end_date=dt.strptime(end_date, '%Y-%m-%d').date(),
            reason=reason,
            status=status if status in ('pending', 'approved', 'rejected', 'cancelled') else 'approved',
        )
        if 'attachment' in request.FILES:
            la.attachment = request.FILES['attachment']
        la.save()

        return JsonResponse({
            'success': True,
            'message': 'Leave application created successfully.',
            'id': la.pk,
            'application': {
                'id': la.pk,
                'employee_name': la.employee.full_name,
                'employee_email': la.employee.email or '',
                'employee_initial': (la.employee.full_name or 'U')[0].upper(),
                'employee_avatar': la.employee.profile_image.url if la.employee.profile_image else '',
                'leave_type': la.leave_type.name if la.leave_type else '',
                'leave_type_color': la.leave_type.color if la.leave_type else '#22c55e',
                'start_date': la.start_date.strftime('%Y-%m-%d'),
                'end_date': la.end_date.strftime('%Y-%m-%d'),
                'days': la.days,
                'status': la.status,
                'status_display': la.get_status_display(),
                'created_at': la.created_at.strftime('%Y-%m-%d'),
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def leave_application_detail(request, pk):
    from .models import LeaveRequest
    try:
        la = LeaveRequest.objects.select_related('employee', 'leave_type', 'approved_by').get(pk=pk)
    except LeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    data = {
        'success': True,
        'application': {
            'id': la.pk,
            'employee_id': la.employee.pk,
            'employee_name': la.employee.full_name,
            'employee_code': la.employee.employee_id,
            'employee_email': la.employee.email,
            'leave_type_id': la.leave_type.pk if la.leave_type else None,
            'leave_type_name': la.leave_type.name if la.leave_type else '',
            'start_date': la.start_date.strftime('%Y-%m-%d'),
            'end_date': la.end_date.strftime('%Y-%m-%d'),
            'days': la.days,
            'reason': la.reason,
            'status': la.status,
            'status_display': la.get_status_display(),
            'rejection_reason': la.rejection_reason,
            'approved_by': la.approved_by.full_name if la.approved_by else '',
            'approved_at': la.approved_at.strftime('%Y-%m-%d %H:%M') if la.approved_at else '',
            'applied_on': la.created_at.strftime('%Y-%m-%d'),
            'attachment_url': la.attachment.url if la.attachment else '',
        }
    }
    return JsonResponse(data)


@login_required
def leave_application_update(request, pk):
    from .models import LeaveRequest, LeaveType, Employee
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        la = LeaveRequest.objects.get(pk=pk)
    except LeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    try:
        employee_id = request.POST.get('employee')
        leave_type_id = request.POST.get('leave_type')
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        reason = request.POST.get('reason', '').strip()

        if not all([employee_id, leave_type_id, start_date, end_date, reason]):
            return JsonResponse({'success': False, 'error': 'All required fields must be filled.'}, status=400)

        from datetime import datetime as dt
        la.employee = get_object_or_404(Employee, pk=employee_id)
        la.leave_type = get_object_or_404(LeaveType, pk=leave_type_id)
        la.start_date = dt.strptime(start_date, '%Y-%m-%d').date()
        la.end_date = dt.strptime(end_date, '%Y-%m-%d').date()
        la.reason = reason
        status = request.POST.get('status')
        if status in ('pending', 'approved', 'rejected', 'cancelled'):
            la.status = status
        if 'attachment' in request.FILES:
            la.attachment = request.FILES['attachment']
        la.save()

        return JsonResponse({
            'success': True,
            'message': 'Leave application updated successfully.',
            'application': {
                'id': la.pk,
                'employee_name': la.employee.full_name,
                'employee_email': la.employee.email or '',
                'employee_initial': (la.employee.full_name or 'U')[0].upper(),
                'employee_avatar': la.employee.profile_image.url if la.employee.profile_image else '',
                'leave_type': la.leave_type.name if la.leave_type else '',
                'leave_type_color': la.leave_type.color if la.leave_type else '#22c55e',
                'start_date': la.start_date.strftime('%Y-%m-%d'),
                'end_date': la.end_date.strftime('%Y-%m-%d'),
                'days': la.days,
                'status': la.status,
                'status_display': la.get_status_display(),
                'created_at': la.created_at.strftime('%Y-%m-%d'),
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def leave_application_delete(request, pk):
    from .models import LeaveRequest
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        la = LeaveRequest.objects.get(pk=pk)
    except LeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    la.delete()
    return JsonResponse({'success': True, 'message': 'Leave application deleted successfully.'})


@login_required
def leave_application_update_status(request, pk):
    from .models import LeaveRequest
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        la = LeaveRequest.objects.select_related('employee').get(pk=pk)
    except LeaveRequest.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    action = request.POST.get('action', '').strip()
    rejection_reason = request.POST.get('rejection_reason', '').strip()
    if action not in ['approve', 'reject', 'cancel', 'pending']:
        return JsonResponse({'success': False, 'error': 'Invalid action.'}, status=400)
    if action == 'reject' and not rejection_reason:
        return JsonResponse({'success': False, 'error': 'Rejection reason is required.'}, status=400)
    approver = None
    try:
        approver = request.user.employee_profile
    except Exception:
        pass
    if action == 'approve':
        la.status = 'approved'
        la.approved_by = approver
        la.approved_at = timezone.now()
        la.rejection_reason = ''
    elif action == 'reject':
        la.status = 'rejected'
        la.approved_by = approver
        la.approved_at = timezone.now()
        la.rejection_reason = rejection_reason
    elif action == 'cancel':
        la.status = 'cancelled'
        la.rejection_reason = ''
    elif action == 'pending':
        la.status = 'pending'
        la.rejection_reason = ''
    la.save()
    return JsonResponse({
        'success': True,
        'status': la.status,
        'status_display': la.get_status_display(),
        'message': f'Leave application {la.get_status_display().lower()} successfully.',
    })


@login_required
def leave_balance_list(request):
    from .models import LeaveBalance, LeaveType, Employee
    from django.core.paginator import Paginator
    from django.db.models import Q

    search = request.GET.get('search', '').strip()
    year_filter = request.GET.get('year', str(timezone.now().year))
    per_page = request.GET.get('per_page', '9')

    try:
        per_page = int(per_page)
        if per_page not in [9, 18, 27, 54]:
            per_page = 9
    except (ValueError, TypeError):
        per_page = 9

    try:
        year_int = int(year_filter)
    except (ValueError, TypeError):
        year_int = timezone.now().year

    # Get all active employees who have balances for the given year
    emp_qs = Employee.objects.filter(employee_status='active').order_by('full_name')
    if search:
        emp_qs = emp_qs.filter(
            Q(full_name__icontains=search) | Q(employee_id__icontains=search)
        )

    # Prefetch leave balances for the year
    from django.db.models import Prefetch
    balances_prefetch = Prefetch(
        'leave_balances',
        queryset=LeaveBalance.objects.filter(year=year_int).select_related('leave_type').order_by('leave_type__name'),
        to_attr='year_balances'
    )
    emp_qs = emp_qs.prefetch_related(balances_prefetch)

    total = emp_qs.count()
    paginator = Paginator(emp_qs, per_page)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    leave_types = LeaveType.objects.filter(is_active=True).order_by('name')
    employees = Employee.objects.filter(employee_status='active').order_by('full_name')
    current_year = timezone.now().year
    years = list(range(current_year - 3, current_year + 2))

    # Last sync time: use the most recent updated_at from LeaveBalance for the year
    last_synced = LeaveBalance.objects.filter(year=year_int).order_by('-updated_at').values_list('updated_at', flat=True).first()

    context = {
        'page_title': 'Leave Balances',
        'page_obj': page_obj,
        'total': total,
        'leave_types': leave_types,
        'employees': employees,
        'years': years,
        'search': search,
        'year_filter': str(year_int),
        'per_page': per_page,
        'current_year': current_year,
        'last_synced': last_synced,
    }
    return render(request, 'hrm/leave_balances.html', context)


@login_required
def leave_balance_create(request):
    from .models import LeaveBalance, LeaveType, Employee
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        employee_id = request.POST.get('employee')
        leave_type_id = request.POST.get('leave_type')
        year = request.POST.get('year', timezone.now().year)
        allocated_days = request.POST.get('allocated_days', 0)
        carry_forward_days = request.POST.get('carry_forward_days', 0)

        if not all([employee_id, leave_type_id]):
            return JsonResponse({'success': False, 'error': 'Employee and leave type are required.'}, status=400)

        employee = get_object_or_404(Employee, pk=employee_id)
        leave_type = get_object_or_404(LeaveType, pk=leave_type_id)

        lb, created = LeaveBalance.objects.get_or_create(
            employee=employee, leave_type=leave_type, year=int(year),
            defaults={'allocated_days': allocated_days, 'carry_forward_days': carry_forward_days}
        )
        if not created:
            lb.allocated_days = allocated_days
            lb.carry_forward_days = carry_forward_days
            
        from django.db.models import Sum
        from .models import LeaveRequest
        used = LeaveRequest.objects.filter(
            employee=employee,
            leave_type=leave_type,
            start_date__year=int(year),
            start_date__lte=timezone.now().date(),
            status__in=['approved', 'pending']
        ).aggregate(total=Sum('days'))['total'] or 0
        
        lb.used_days = used
        lb.save()

        remaining = max(float(lb.allocated_days) + float(lb.carry_forward_days) - float(lb.used_days), 0)
        return JsonResponse({
            'success': True,
            'message': 'Leave balance saved successfully.',
            'created': created,
            'balance': {
                'pk': lb.pk,
                'emp_pk': employee.pk,
                'emp_name': employee.full_name,
                'emp_employee_id': employee.employee_id,
                'leave_type_pk': leave_type.pk,
                'leave_type_name': leave_type.name,
                'allocated_days': float(lb.allocated_days),
                'used_days': float(lb.used_days),
                'remaining_days': remaining,
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def leave_balance_delete(request, pk):
    from .models import LeaveBalance
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        lb = LeaveBalance.objects.get(pk=pk)
    except LeaveBalance.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    lb.delete()
    return JsonResponse({'success': True, 'message': 'Leave balance deleted.'})


@login_required
def leave_balance_resync(request):
    """Auto-create/initialize leave balances for all active employees x all active leave types for the given year."""
    from .models import LeaveBalance, LeaveType, Employee, LeaveRequest
    from django.db.models import Sum
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        year = int(request.POST.get('year', timezone.now().year))
        employees = Employee.objects.filter(employee_status='active')
        leave_types = LeaveType.objects.filter(is_active=True)
        
        created_count = 0
        updated_count = 0
        
        for emp in employees:
            for lt in leave_types:
                # Calculate used days for this employee and leave type
                used = LeaveRequest.objects.filter(
                    employee=emp,
                    leave_type=lt,
                    start_date__year=year,
                    start_date__lte=timezone.now().date(),
                    status__in=['approved', 'pending']
                ).aggregate(total=Sum('days'))['total'] or 0
                
                lb = LeaveBalance.objects.filter(employee=emp, leave_type=lt, year=year).first()
                
                if not lb:
                    # Only auto-create if they have some leave applications
                    has_requests = LeaveRequest.objects.filter(employee=emp, leave_type=lt, start_date__year=year).exists()
                    if has_requests:
                        allocated = lt.max_days_per_year if lt.max_days_per_year and lt.max_days_per_year > 0 else 0
                        LeaveBalance.objects.create(
                            employee=emp, leave_type=lt, year=year,
                            allocated_days=allocated, used_days=used, carry_forward_days=0
                        )
                        created_count += 1
                else:
                    if lb.used_days != used:
                        lb.used_days = used
                        lb.save()
                        updated_count += 1
                        
        return JsonResponse({'success': True, 'message': f'Re-sync complete. {created_count} new balances created, {updated_count} balances updated for {year}.', 'created': created_count})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def leave_balance_sync_history(request):
    """Return recent sync history (leave balance update activity by day)."""
    from .models import LeaveBalance
    from django.db.models.functions import TruncDate
    from django.db.models import Count, Max
    year = request.GET.get('year', str(timezone.now().year))
    try:
        year = int(year)
    except (ValueError, TypeError):
        year = timezone.now().year
    history = (
        LeaveBalance.objects
        .filter(year=year)
        .annotate(date=TruncDate('updated_at'))
        .values('date')
        .annotate(count=Count('id'), last_updated=Max('updated_at'))
        .order_by('-date')[:20]
    )
    data = [
        {'date': str(h['date']), 'count': h['count'],
         'last_updated': h['last_updated'].strftime('%Y-%m-%d %H:%M') if h['last_updated'] else ''}
        for h in history
    ]
    return JsonResponse({'success': True, 'history': data, 'year': year})


@login_required
def leave_type_management(request):
    from .models import LeaveType
    from django.core.paginator import Paginator

    search = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()
    per_page = request.GET.get('per_page', '10')

    try:
        per_page = int(per_page)
        if per_page not in [10, 25, 50, 100]:
            per_page = 10
    except (ValueError, TypeError):
        per_page = 10

    qs = LeaveType.objects.all().order_by('name')
    if search:
        qs = qs.filter(name__icontains=search)
    if status_filter == 'active':
        qs = qs.filter(is_active=True)
    elif status_filter == 'inactive':
        qs = qs.filter(is_active=False)

    total = qs.count()
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    context = {
        'page_title': 'Leave Types',
        'page_obj': page_obj,
        'total': total,
        'search': search,
        'status_filter': status_filter,
        'per_page': per_page,
    }
    return render(request, 'hrm/leave_types.html', context)


@login_required
def leave_type_create(request):
    from .models import LeaveType
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        max_days = request.POST.get('max_days_per_year', 0)
        is_paid = request.POST.get('is_paid') == 'true'
        is_active = request.POST.get('is_active', 'true') == 'true'

        if not name:
            return JsonResponse({'success': False, 'error': 'Name is required.'}, status=400)
        if LeaveType.objects.filter(name__iexact=name).exists():
            return JsonResponse({'success': False, 'error': 'Leave type with this name already exists.'}, status=400)

        color = request.POST.get('color', '#22c55e').strip()
        lt = LeaveType.objects.create(
            name=name, description=description,
            max_days_per_year=int(max_days), color=color, is_paid=is_paid, is_active=is_active
        )
        return JsonResponse({'success': True, 'message': f'Leave type "{lt.name}" created.', 'id': lt.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def leave_type_detail(request, pk):
    from .models import LeaveType
    try:
        lt = LeaveType.objects.get(pk=pk)
    except LeaveType.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    return JsonResponse({
        'success': True,
        'leave_type': {
            'id': lt.pk,
            'name': lt.name,
            'description': lt.description,
            'max_days_per_year': lt.max_days_per_year,
            'color': lt.color,
            'is_paid': lt.is_paid,
            'is_active': lt.is_active,
        }
    })


@login_required
def leave_type_update(request, pk):
    from .models import LeaveType
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        lt = LeaveType.objects.get(pk=pk)
    except LeaveType.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    try:
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Name is required.'}, status=400)
        if LeaveType.objects.filter(name__iexact=name).exclude(pk=pk).exists():
            return JsonResponse({'success': False, 'error': 'Name already used.'}, status=400)
        lt.name = name
        lt.description = request.POST.get('description', lt.description)
        max_days = request.POST.get('max_days_per_year')
        if max_days is not None:
            lt.max_days_per_year = int(max_days)
        color = request.POST.get('color')
        if color:
            lt.color = color
        is_paid = request.POST.get('is_paid')
        if is_paid is not None:
            lt.is_paid = is_paid in ('true', 'on', '1')
        is_active = request.POST.get('is_active')
        if is_active is not None:
            lt.is_active = is_active == 'true'
        lt.save()
        return JsonResponse({'success': True, 'message': f'Leave type "{lt.name}" updated.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def leave_type_delete(request, pk):
    from .models import LeaveType
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        lt = LeaveType.objects.get(pk=pk)
    except LeaveType.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    if lt.requests.exists():
        return JsonResponse({'success': False, 'error': 'Cannot delete: leave type is used in leave applications.'}, status=400)
    name = lt.name
    lt.delete()
    return JsonResponse({'success': True, 'message': f'Leave type "{name}" deleted.'})


@login_required
def leave_type_toggle_status(request, pk):
    from .models import LeaveType
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        lt = LeaveType.objects.get(pk=pk)
    except LeaveType.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    lt.is_active = not lt.is_active
    lt.save()
    return JsonResponse({'success': True, 'is_active': lt.is_active, 'message': f'Status updated to {"Active" if lt.is_active else "Inactive"}.'})


@login_required
def leave_policy_list(request):
    from .models import LeavePolicy, LeaveType
    from django.core.paginator import Paginator
    import json

    search = request.GET.get('search', '').strip()
    per_page = request.GET.get('per_page', '10')

    try:
        per_page = int(per_page)
        if per_page not in [10, 25, 50, 100]:
            per_page = 10
    except (ValueError, TypeError):
        per_page = 10

    qs = LeavePolicy.objects.prefetch_related('leave_types').order_by('-created_at')
    if search:
        qs = qs.filter(name__icontains=search)

    total = qs.count()
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    leave_types = LeaveType.objects.filter(is_active=True).order_by('name')

    # Assign colors to leave types
    lt_colors = {}
    for idx, lt in enumerate(LeaveType.objects.all()):
        lt_colors[lt.pk] = LEAVE_TYPE_COLORS[idx % len(LEAVE_TYPE_COLORS)]

    context = {
        'page_title': 'Leave Policies',
        'page_obj': page_obj,
        'total': total,
        'leave_types': leave_types,
        'lt_colors': lt_colors,
        'lt_colors_json': json.dumps({str(k): v for k, v in lt_colors.items()}),
        'search': search,
        'per_page': per_page,
        'per_page_options': [10, 25, 50, 100],
    }
    return render(request, 'hrm/leave_policies.html', context)


@login_required
def leave_policy_create(request):
    from .models import LeavePolicy, LeaveType
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        carry_forward_days = request.POST.get('max_carry_forward_days', 0)
        min_days = request.POST.get('min_days_per_application', 1)
        max_days = request.POST.get('max_days_per_application', 14)
        requires_approval = request.POST.get('requires_approval') in ('true', 'on', '1')
        is_active = request.POST.get('is_active', 'true') == 'true'
        leave_type_id = request.POST.get('leave_type', '')
        lt_ids = request.POST.getlist('leave_types')

        if not name:
            return JsonResponse({'success': False, 'error': 'Name is required.'}, status=400)

        try:
            carry_forward_int = int(carry_forward_days)
        except (ValueError, TypeError):
            carry_forward_int = 0

        policy = LeavePolicy.objects.create(
            name=name, description=description,
            carry_forward=carry_forward_int > 0,
            max_carry_forward_days=carry_forward_int,
            min_days_per_application=int(min_days) if min_days else 1,
            max_days_per_application=int(max_days) if max_days else 14,
            requires_approval=requires_approval,
            encashment_allowed=False,
            is_active=is_active,
        )
        # Handle single leave_type or multiple leave_types
        if leave_type_id:
            policy.leave_types.set(LeaveType.objects.filter(pk=leave_type_id))
        elif lt_ids:
            policy.leave_types.set(LeaveType.objects.filter(pk__in=lt_ids))
        return JsonResponse({'success': True, 'message': f'Policy "{policy.name}" created.', 'id': policy.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def leave_policy_detail(request, pk):
    from .models import LeavePolicy
    try:
        policy = LeavePolicy.objects.prefetch_related('leave_types').get(pk=pk)
    except LeavePolicy.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    return JsonResponse({
        'success': True,
        'policy': {
            'id': policy.pk,
            'name': policy.name,
            'description': policy.description,
            'carry_forward': policy.carry_forward,
            'max_carry_forward_days': policy.max_carry_forward_days,
            'min_days_per_application': policy.min_days_per_application,
            'max_days_per_application': policy.max_days_per_application,
            'requires_approval': policy.requires_approval,
            'encashment_allowed': policy.encashment_allowed,
            'is_active': policy.is_active,
            'leave_type_ids': list(policy.leave_types.values_list('id', flat=True)),
        }
    })


@login_required
def leave_policy_update(request, pk):
    from .models import LeavePolicy, LeaveType
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        policy = LeavePolicy.objects.get(pk=pk)
    except LeavePolicy.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    try:
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Name is required.'}, status=400)
        policy.name = name
        policy.description = request.POST.get('description', policy.description)
        carry_forward_days = request.POST.get('max_carry_forward_days')
        if carry_forward_days is not None:
            try:
                carry_forward_int = int(carry_forward_days)
            except (ValueError, TypeError):
                carry_forward_int = 0
            policy.max_carry_forward_days = carry_forward_int
            policy.carry_forward = carry_forward_int > 0
        min_days = request.POST.get('min_days_per_application')
        if min_days is not None:
            policy.min_days_per_application = int(min_days) if min_days else 1
        max_days = request.POST.get('max_days_per_application')
        if max_days is not None:
            policy.max_days_per_application = int(max_days) if max_days else 14
        req_approval = request.POST.get('requires_approval')
        policy.requires_approval = req_approval in ('true', 'on', '1')
        ia = request.POST.get('is_active')
        if ia is not None:
            policy.is_active = ia == 'true'
        leave_type_id = request.POST.get('leave_type', '')
        lt_ids = request.POST.getlist('leave_types')
        if leave_type_id:
            policy.leave_types.set(LeaveType.objects.filter(pk=leave_type_id))
        elif lt_ids is not None:
            policy.leave_types.set(LeaveType.objects.filter(pk__in=lt_ids))
        policy.save()
        return JsonResponse({'success': True, 'message': f'Policy "{policy.name}" updated.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def leave_policy_delete(request, pk):
    from .models import LeavePolicy
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        policy = LeavePolicy.objects.get(pk=pk)
    except LeavePolicy.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    name = policy.name
    policy.delete()
    return JsonResponse({'success': True, 'message': f'Policy "{name}" deleted.'})


@login_required
def leave_policy_toggle_status(request, pk):
    from .models import LeavePolicy
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        policy = LeavePolicy.objects.get(pk=pk)
    except LeavePolicy.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Not found.'}, status=404)
    policy.is_active = not policy.is_active
    policy.save()
    return JsonResponse({'success': True, 'is_active': policy.is_active,
                         'message': f'Status updated to {"Active" if policy.is_active else "Inactive"}.'})


# Import LeaveBalance and LeavePolicy for use in views above
from .models import LeaveBalance, LeavePolicy


# ==================== Holiday Management Views ====================

@login_required
def holiday_list(request):
    from .models import Holiday
    search_query = request.GET.get('search', '')
    type_filter = request.GET.get('holiday_type', '')
    status_filter = request.GET.get('status', '')
    per_page = request.GET.get('per_page', '10')

    holidays = Holiday.objects.all()

    if search_query:
        holidays = holidays.filter(
            Q(name__icontains=search_query) |
            Q(description__icontains=search_query)
        )
    if type_filter:
        holidays = holidays.filter(holiday_type=type_filter)
    if status_filter == 'active':
        holidays = holidays.filter(is_active=True)
    elif status_filter == 'inactive':
        holidays = holidays.filter(is_active=False)

    paginator = Paginator(holidays, int(per_page))
    page_obj = paginator.get_page(request.GET.get('page', 1))

    context = {
        'page_title': 'Holiday Setup',
        'holidays': page_obj,
        'search_query': search_query,
        'type_filter': type_filter,
        'status_filter': status_filter,
        'per_page': per_page,
        'total_holidays': paginator.count,
        'holiday_type_choices': Holiday.HOLIDAY_TYPE_CHOICES,
    }
    return render(request, 'hrm/holiday_list.html', context)


@login_required
def holiday_create(request):
    from .models import Holiday
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    name = request.POST.get('name', '').strip()
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()

    if not name:
        return JsonResponse({'success': False, 'error': 'Holiday name is required.'})
    if not start_date or not end_date:
        return JsonResponse({'success': False, 'error': 'Start and end dates are required.'})

    try:
        from datetime import date as _date
        import datetime
        sd = datetime.date.fromisoformat(start_date)
        ed = datetime.date.fromisoformat(end_date)
        if ed < sd:
            return JsonResponse({'success': False, 'error': 'End date cannot be before start date.'})
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid date format.'})

    holiday = Holiday.objects.create(
        name=name,
        holiday_type=request.POST.get('holiday_type', 'public'),
        start_date=sd,
        end_date=ed,
        description=request.POST.get('description', '').strip(),
        apply_for_all=request.POST.get('apply_for_all') == 'on',
        is_paid=request.POST.get('is_paid') == 'on',
        is_active=True,
        created_by=request.user,
    )
    return JsonResponse({'success': True, 'message': f'Holiday "{holiday.name}" created successfully!'})


@login_required
def holiday_detail(request, pk):
    from .models import Holiday
    holiday = get_object_or_404(Holiday, pk=pk)
    return JsonResponse({
        'success': True,
        'holiday': {
            'id': holiday.id,
            'name': holiday.name,
            'holiday_type': holiday.holiday_type,
            'holiday_type_display': holiday.get_holiday_type_display(),
            'start_date': holiday.start_date.strftime('%Y-%m-%d'),
            'end_date': holiday.end_date.strftime('%Y-%m-%d'),
            'description': holiday.description,
            'apply_for_all': holiday.apply_for_all,
            'is_paid': holiday.is_paid,
            'is_active': holiday.is_active,
            'total_days': holiday.total_days,
        }
    })


@login_required
def holiday_update(request, pk):
    from .models import Holiday
    holiday = get_object_or_404(Holiday, pk=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    name = request.POST.get('name', '').strip()
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()

    if not name:
        return JsonResponse({'success': False, 'error': 'Holiday name is required.'})
    try:
        import datetime
        sd = datetime.date.fromisoformat(start_date)
        ed = datetime.date.fromisoformat(end_date)
        if ed < sd:
            return JsonResponse({'success': False, 'error': 'End date cannot be before start date.'})
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid date format.'})

    holiday.name = name
    holiday.holiday_type = request.POST.get('holiday_type', holiday.holiday_type)
    holiday.start_date = sd
    holiday.end_date = ed
    holiday.description = request.POST.get('description', '').strip()
    holiday.apply_for_all = request.POST.get('apply_for_all') == 'on'
    holiday.is_paid = request.POST.get('is_paid') == 'on'
    holiday.save()
    return JsonResponse({'success': True, 'message': f'Holiday "{holiday.name}" updated successfully!'})


@login_required
def holiday_delete(request, pk):
    from .models import Holiday, AttendanceRecord
    holiday = get_object_or_404(Holiday, pk=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    name = holiday.name
    
    # Revert is_holiday flag if this holiday was applied
    if holiday.apply_for_all:
        import datetime
        current = holiday.start_date
        while current <= holiday.end_date:
            records = AttendanceRecord.objects.filter(date=current, is_holiday=True)
            for r in records:
                r.is_holiday = False
                if r.notes and f'Holiday: {name}' in r.notes:
                    r.notes = r.notes.replace(f'Holiday: {name}', '').strip()
                r.save(update_fields=['is_holiday', 'notes', 'updated_at'])
            current += datetime.timedelta(days=1)

    holiday.delete()
    return JsonResponse({'success': True, 'message': f'Holiday "{name}" deleted and reverted from attendance successfully!'})


@login_required
def holiday_toggle_status(request, pk):
    from .models import Holiday
    holiday = get_object_or_404(Holiday, pk=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    holiday.is_active = not holiday.is_active
    holiday.save()
    status_text = 'Active' if holiday.is_active else 'Inactive'
    return JsonResponse({'success': True, 'message': f'"{holiday.name}" is now {status_text}.'})


@login_required
def holiday_apply(request, pk):
    """Apply a holiday to all active employees by creating/updating AttendanceRecord rows."""
    from .models import Holiday, AttendanceRecord, Employee
    import datetime

    holiday = get_object_or_404(Holiday, pk=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    if not holiday.apply_for_all:
        return JsonResponse({'success': False, 'error': 'This holiday does not have apply-for-all enabled.'})

    employees = Employee.objects.filter(employee_status='active')
    if not employees.exists():
        return JsonResponse({'success': False, 'error': 'No active employees found.'})

    created = 0
    updated = 0
    current = holiday.start_date
    while current <= holiday.end_date:
        for emp in employees:
            record, was_created = AttendanceRecord.objects.get_or_create(
                employee=emp,
                date=current,
                defaults={
                    'status': 'on_leave',
                    'is_holiday': True,
                    'notes': f'Holiday: {holiday.name}',
                }
            )
            if was_created:
                created += 1
            elif not record.is_holiday:
                record.is_holiday = True
                record.notes = (record.notes + f'\nHoliday: {holiday.name}').strip()
                record.save(update_fields=['is_holiday', 'notes', 'updated_at'])
                updated += 1
        current += datetime.timedelta(days=1)

    return JsonResponse({
        'success': True,
        'message': f'Holiday applied! {created} records created, {updated} updated.',
        'created': created,
        'updated': updated,
    })


# ==================== Bonus Management Views ====================

@login_required
def bonus_list(request):
    from .models import Bonus
    import calendar as _cal

    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('bonus_type', '')
    month_filter = request.GET.get('month', '')
    year_filter = request.GET.get('year', '')
    per_page = request.GET.get('per_page', '10')

    bonuses = Bonus.objects.select_related('employee', 'approved_by', 'created_by')

    if search_query:
        bonuses = bonuses.filter(
            Q(employee__full_name__icontains=search_query) |
            Q(employee__employee_id__icontains=search_query) |
            Q(remarks__icontains=search_query)
        )
    if status_filter:
        bonuses = bonuses.filter(status=status_filter)
    if type_filter:
        bonuses = bonuses.filter(bonus_type=type_filter)
    if month_filter:
        bonuses = bonuses.filter(month=int(month_filter))
    if year_filter:
        bonuses = bonuses.filter(year=int(year_filter))

    paginator = Paginator(bonuses, int(per_page))
    page_obj = paginator.get_page(request.GET.get('page', 1))

    now = timezone.now()
    years = list(range(now.year - 2, now.year + 2))

    context = {
        'page_title': 'Bonus Management',
        'bonuses': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'month_filter': month_filter,
        'year_filter': year_filter,
        'per_page': per_page,
        'total_bonuses': paginator.count,
        'bonus_type_choices': Bonus.BONUS_TYPE_CHOICES,
        'status_choices': Bonus.STATUS_CHOICES,
        'months': [(i, _cal.month_name[i]) for i in range(1, 13)],
        'years': years,
        'current_year': now.year,
        'current_month': now.month,
        'employees': Employee.objects.filter(employee_status='active').order_by('full_name'),
    }
    return render(request, 'hrm/bonus_list.html', context)


@login_required
def bonus_create(request):
    from .models import Bonus
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    apply_for_all = request.POST.get('apply_for_all') == 'on'
    employee_id = request.POST.get('employee', '').strip()
    amount = request.POST.get('amount', '').strip()
    month = request.POST.get('month', '').strip()
    year = request.POST.get('year', '').strip()

    if not apply_for_all and not employee_id:
        return JsonResponse({'success': False, 'error': 'Employee is required.'})
    if not amount:
        return JsonResponse({'success': False, 'error': 'Amount is required.'})
    if not month or not year:
        return JsonResponse({'success': False, 'error': 'Month and year are required.'})

    emp = None
    if not apply_for_all:
        emp = get_object_or_404(Employee, pk=employee_id)

    try:
        from decimal import Decimal
        amt = Decimal(amount)
        if amt <= 0:
            return JsonResponse({'success': False, 'error': 'Amount must be positive.'})
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid amount.'})

    bonus_type = request.POST.get('bonus_type', 'other')
    remarks = request.POST.get('remarks', '').strip()

    if apply_for_all:
        # Create bonus for all active employees
        active_employees = Employee.objects.filter(employee_status='active')
        count = 0
        for e in active_employees:
            Bonus.objects.create(
                employee=e,
                bonus_type=bonus_type,
                amount=amt,
                month=int(month),
                year=int(year),
                remarks=remarks,
                apply_for_all=True,
                created_by=request.user,
            )
            count += 1
        return JsonResponse({'success': True, 'message': f'Bonus created for {count} active employees!'})
    else:
        bonus = Bonus.objects.create(
            employee=emp,
            bonus_type=bonus_type,
            amount=amt,
            month=int(month),
            year=int(year),
            remarks=remarks,
            apply_for_all=False,
            created_by=request.user,
        )
        return JsonResponse({'success': True, 'message': f'Bonus created for {emp.full_name}!'})


@login_required
def bonus_detail(request, pk):
    from .models import Bonus
    import calendar as _cal
    bonus = get_object_or_404(Bonus.objects.select_related('employee', 'approved_by', 'created_by'), pk=pk)
    month_name = _cal.month_name[bonus.month] if 1 <= bonus.month <= 12 else str(bonus.month)
    return JsonResponse({
        'success': True,
        'bonus': {
            'id': bonus.id,
            'employee_id': bonus.employee.id,
            'employee_name': bonus.employee.full_name,
            'employee_emp_id': bonus.employee.employee_id,
            'bonus_type': bonus.bonus_type,
            'bonus_type_display': bonus.get_bonus_type_display(),
            'amount': str(bonus.amount),
            'month': bonus.month,
            'month_name': month_name,
            'year': bonus.year,
            'remarks': bonus.remarks,
            'status': bonus.status,
            'status_display': bonus.get_status_display(),
            'apply_for_all': bonus.apply_for_all,
            'approved_by': bonus.approved_by.get_full_name() if bonus.approved_by else '',
            'approved_at': bonus.approved_at.strftime('%Y-%m-%d %H:%M') if bonus.approved_at else '',
            'created_at': bonus.created_at.strftime('%Y-%m-%d'),
        }
    })


@login_required
def bonus_update(request, pk):
    from .models import Bonus
    bonus = get_object_or_404(Bonus, pk=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    if bonus.status in ('approved', 'paid'):
        return JsonResponse({'success': False, 'error': 'Cannot edit an approved or paid bonus.'})

    amount = request.POST.get('amount', '').strip()
    month = request.POST.get('month', '').strip()
    year = request.POST.get('year', '').strip()

    try:
        from decimal import Decimal
        amt = Decimal(amount)
        if amt <= 0:
            return JsonResponse({'success': False, 'error': 'Amount must be positive.'})
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid amount.'})

    bonus.bonus_type = request.POST.get('bonus_type', bonus.bonus_type)
    bonus.amount = amt
    bonus.month = int(month)
    bonus.year = int(year)
    bonus.remarks = request.POST.get('remarks', '').strip()
    bonus.save()
    return JsonResponse({'success': True, 'message': f'Bonus updated successfully!'})


@login_required
def bonus_delete(request, pk):
    from .models import Bonus
    bonus = get_object_or_404(Bonus, pk=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    if bonus.status in ('approved', 'paid'):
        return JsonResponse({'success': False, 'error': 'Cannot delete an approved or paid bonus.'})
    bonus.delete()
    return JsonResponse({'success': True, 'message': 'Bonus deleted successfully!'})


@login_required
def bonus_update_status(request, pk):
    from .models import Bonus
    bonus = get_object_or_404(Bonus, pk=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    new_status = request.POST.get('status', '').strip()
    valid_statuses = [s[0] for s in Bonus.STATUS_CHOICES]
    if new_status not in valid_statuses:
        return JsonResponse({'success': False, 'error': 'Invalid status.'})

    if bonus.status == 'paid' and new_status != 'paid':
        return JsonResponse({'success': False, 'error': 'Cannot change status of a paid bonus.'})

    bonus.status = new_status
    if new_status == 'approved':
        bonus.approved_by = request.user
        bonus.approved_at = timezone.now()
    bonus.save()
    return JsonResponse({'success': True, 'message': f'Bonus status updated to {bonus.get_status_display()}.'})


# ==================== Payslip Adjustment & Finalization Views ====================

@login_required
def payslip_adjust(request, pk):

    from decimal import Decimal

    slip = get_object_or_404(
        Payslip.objects.select_related('employee', 'payroll_run', 'finalized_by')
                       .prefetch_related('adjustments', 'audit_logs__performed_by'),
        pk=pk
    )

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if slip.is_finalized:
            return JsonResponse({'success': False, 'error': 'This payslip is finalized and cannot be modified.'})

        if action == 'add_adjustment':
            from django.db import transaction
            
            adj_type = request.POST.get('adjustment_type', '').strip()
            category = request.POST.get('category', '').strip()
            description = request.POST.get('description', '').strip()
            amount_str = request.POST.get('amount', '').strip()
            reason = request.POST.get('reason', '').strip()

            if not description:
                return JsonResponse({'success': False, 'error': 'Description is required.'})
            if not amount_str:
                return JsonResponse({'success': False, 'error': 'Amount is required.'})

            try:
                amount = Decimal(amount_str)
                if amount <= 0:
                    return JsonResponse({'success': False, 'error': 'Amount must be positive.'})
            except Exception:
                return JsonResponse({'success': False, 'error': 'Invalid amount.'})

            with transaction.atomic():
                adj = PayslipAdjustment.objects.create(
                    payslip=slip,
                    adjustment_type=adj_type,
                    category=category,
                    description=description,
                    amount=amount,
                    reason=reason,
                    created_by=request.user,
                )

                # Recalculate payslip totals
                _recalculate_payslip(slip)

                PayslipAuditLog.objects.create(
                    payslip=slip,
                    action=f'Added {adj.get_adjustment_type_display()}: {description}',
                    detail=f'Category: {adj.get_category_display()}, Amount: Rs.{amount}, Reason: {reason}',
                    performed_by=request.user,
                )
            return JsonResponse({'success': True, 'message': f'Adjustment added successfully!'})

        elif action == 'remove_adjustment':
            from django.db import transaction
            
            adj_id = request.POST.get('adjustment_id', '').strip()
            try:
                adj = PayslipAdjustment.objects.get(pk=adj_id, payslip=slip)
            except PayslipAdjustment.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Adjustment not found.'})

            with transaction.atomic():
                detail = f'{adj.get_adjustment_type_display()}: {adj.description} (Rs.{adj.amount})'
                adj.delete()
                _recalculate_payslip(slip)

                PayslipAuditLog.objects.create(
                    payslip=slip,
                    action='Removed adjustment',
                    detail=detail,
                    performed_by=request.user,
                )
            return JsonResponse({'success': True, 'message': 'Adjustment removed.'})

        return JsonResponse({'success': False, 'error': 'Unknown action.'})

    # GET — render adjustment page
    import calendar as _cal
    month_name = _cal.month_name[slip.payroll_run.month] if slip.payroll_run.month else ''
    
    total_earnings = sum(a.amount for a in slip.adjustments.all() if a.adjustment_type == 'earning')
    total_deductions = sum(a.amount for a in slip.adjustments.all() if a.adjustment_type == 'deduction')

    context = {
        'total_earnings': total_earnings,
        'total_deductions': total_deductions,
        'page_title': f'Adjust Payslip — {slip.employee.full_name}',
        'slip': slip,
        'month_name': month_name,
        'adjustments': slip.adjustments.all(),
        'audit_logs': slip.audit_logs.all()[:30],
        'adjustment_type_choices': PayslipAdjustment.ADJUSTMENT_TYPE_CHOICES,
        'category_choices': PayslipAdjustment.CATEGORY_CHOICES,
    }
    return render(request, 'hrm/payslip_adjust.html', context)


def _recalculate_payslip(slip):
    """Recalculate gross_salary, total_deductions, net_salary based on manual adjustments."""

    from decimal import Decimal

    adjustments = PayslipAdjustment.objects.filter(payslip=slip)
    extra_earnings = sum(a.amount for a in adjustments if a.adjustment_type == 'earning') or Decimal('0')
    extra_deductions = sum(a.amount for a in adjustments if a.adjustment_type == 'deduction') or Decimal('0')

    # Base gross is stored; we rebuild from base (stored before adjustments)
    # To avoid double-counting, store base values if not yet done
    if not hasattr(slip, '_base_gross'):
        # Use current values minus previous adjustments total as base
        prev_earnings = sum(
            a.amount for a in adjustments if a.adjustment_type == 'earning'
        ) or Decimal('0')
        prev_deductions = sum(
            a.amount for a in adjustments if a.adjustment_type == 'deduction'
        ) or Decimal('0')

    # The strategy: keep original auto-computed values in notes as base
    # We store base in payslip notes field on first adjustment
    import json
    base_data = {}
    try:
        if slip.notes and slip.notes.startswith('__base__'):
            base_data = json.loads(slip.notes[8:])
    except Exception:
        pass

    if not base_data:
        # First time: save current values as base
        base_data = {
            'gross': str(slip.gross_salary),
            'deductions': str(slip.total_deductions),
            'net': str(slip.net_salary),
        }
        slip.notes = '__base__' + json.dumps(base_data)

    base_gross = Decimal(base_data['gross'])
    base_deductions = Decimal(base_data['deductions'])
    base_net = Decimal(base_data['net'])

    new_gross = (base_gross + Decimal(str(extra_earnings))).quantize(Decimal('0.01'))
    new_deductions = (base_deductions + Decimal(str(extra_deductions))).quantize(Decimal('0.01'))
    new_net = max(base_net + Decimal(str(extra_earnings)) - Decimal(str(extra_deductions)), Decimal('0')).quantize(Decimal('0.01'))

    slip.gross_salary = new_gross
    slip.total_deductions = new_deductions
    slip.net_salary = new_net
    slip.save(update_fields=['gross_salary', 'total_deductions', 'net_salary', 'notes', 'updated_at'])


@login_required
def payslip_finalize(request, pk):
    from .models import Payslip, PayslipAuditLog
    slip = get_object_or_404(Payslip, pk=pk)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    if slip.is_finalized:
        return JsonResponse({'success': False, 'error': 'Payslip is already finalized.'})

    slip.is_finalized = True
    slip.finalized_by = request.user
    slip.finalized_at = timezone.now()
    slip.status = 'generated'
    slip.save(update_fields=['is_finalized', 'finalized_by', 'finalized_at', 'status', 'updated_at'])

    PayslipAuditLog.objects.create(
        payslip=slip,
        action='Payslip Finalized',
        detail=f'Finalized by {request.user.get_full_name() or request.user.username}. '
               f'Net Salary: Rs.{slip.net_salary}. Protected from auto-regeneration.',
        performed_by=request.user,
    )

    return JsonResponse({'success': True, 'message': f'Payslip for {slip.employee.full_name} has been finalized and locked.'})


@login_required
def payslip_unfinalize(request, pk):
    """Allow HR to unlock a finalized payslip (with caution)."""
    slip = get_object_or_404(Payslip, pk=pk)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    if not slip.is_finalized:
        return JsonResponse({'success': False, 'error': 'Payslip is not finalized.'})

    slip.is_finalized = False
    slip.finalized_by = None
    slip.finalized_at = None
    slip.save(update_fields=['is_finalized', 'finalized_by', 'finalized_at', 'updated_at'])

    PayslipAuditLog.objects.create(
        payslip=slip,
        action='Payslip Un-finalized',
        detail=f'Lock removed by {request.user.get_full_name() or request.user.username}.',
        performed_by=request.user,
    )
    return JsonResponse({'success': True, 'message': 'Payslip unlocked and returned to draft.'})




@login_required
def payslip_delete(request, pk):
    slip = get_object_or_404(Payslip, pk=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    
    if slip.is_finalized:
        return JsonResponse({'success': False, 'error': 'Cannot delete a finalized payslip. Unlock it first.'})
        
    slip.delete()
    return JsonResponse({'success': True, 'message': 'Payslip deleted successfully!'})
