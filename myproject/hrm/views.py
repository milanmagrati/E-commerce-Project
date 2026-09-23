import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import JsonResponse, HttpResponse, Http404
from django.db import IntegrityError
from django.db.models import Q, Sum, Count
from django.core.paginator import Paginator
from django.contrib import messages

hrm_logger = logging.getLogger('hrm')

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

    today_attendance = AttendanceRecord.objects.filter(date=today, is_deleted=False)
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
        # Which policy/shift the employee was on before this save decides how
        # their past days were measured and labelled — compare after saving so
        # a change re-derives them (see _refresh_attendance_records).
        prev_policy_id = employee.attendance_policy_id
        prev_shift_id = employee.shift_id
        form = EmployeeForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            employee = form.save()
            if (employee.attendance_policy_id != prev_policy_id
                    or employee.shift_id != prev_shift_id):
                from .models import AttendanceRecord
                _safe_refresh_attendance(
                    _refresh_attendance_records,
                    AttendanceRecord.objects.filter(employee_id=employee.id),
                    'employee_edit',
                )
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


def _normalize_pin(raw):
    """Canonical form for a device PIN / employee code.

    ZKTeco devices don't consistently pad the PIN the same way between the
    realtime push (each punch sent immediately, e.g. '5') and the buffered
    ATTLOG table push (e.g. '05') — sometimes even between different
    firmware code paths on the *same* device. If punches for one employee's
    day land under two differently-padded pin strings, each ends up in its
    own raw-punch group with just one punch, and the day is wrongly reported
    as missing a clock-in or clock-out even though both punches exist.
    Stripping leading zeros collapses all variants back to one PIN.
    """
    s = str(raw).strip()
    return s.lstrip('0') or '0'


# A second scan this soon after the first one is an accidental double-tap at the
# reader, not the employee leaving for the day. Without this guard a staff member
# who taps twice at 09:00 gets clock_out=09:00 and a 0.00 hr "full" day, which
# then auto-classifies as Absent and quietly poisons payroll.
DUPLICATE_PUNCH_WINDOW_SECONDS = 300


def _derive_clock_times(timestamps, tzinfo):
    """Reduce one employee-day's raw punches to (clock_in, clock_out) local times.

    Returns ``clock_out=None`` when every punch of the day falls inside
    DUPLICATE_PUNCH_WINDOW_SECONDS of the first one — the day is genuinely
    incomplete and still waiting for the real evening punch, so it is better
    flagged for correction than reported as a zero-hour shift.

    Shared by the aggregator and both biometric read views so the raw-punch
    table, the punch-detail modal and AttendanceRecord can never disagree
    about when someone clocked out.
    """
    if not timestamps:
        return None, None

    first = min(timestamps)
    last = max(timestamps)
    clock_in = first.astimezone(tzinfo).time()

    # Covers the single-punch case too (gap of 0 seconds).
    if (last - first).total_seconds() <= DUPLICATE_PUNCH_WINDOW_SECONDS:
        return clock_in, None

    return clock_in, last.astimezone(tzinfo).time()


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
            emp_map[_normalize_pin(emp.employee_code)] = emp

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

    # grouped[(normalized_pin, nepal_date)] = [utc_timestamp, ...]
    # Grouping by the normalized PIN (not the raw stored string) means two
    # inconsistently-padded punches for the same employee/day (see
    # _normalize_pin) still land in the same group and pair up correctly,
    # even for punches saved before ingestion started normalizing.
    grouped = defaultdict(list)
    for rp in raw_punches:
        ts = rp['timestamp']
        if ts is None:
            continue
        # Convert UTC → Nepal Standard Time (UTC+5:45) to get the correct local date
        local_ts = ts.astimezone(NPT)
        punch_date = local_ts.date()
        grouped[(_normalize_pin(rp['pin']), punch_date)].append(ts)

    regularized_pks = []

    for (pin, punch_date), timestamps in grouped.items():
        pin_str = str(pin)
        employee = emp_map.get(pin_str) or emp_map.get(pin_str.lstrip('0'))
        if not employee:
            continue

        # Convert UTC-stored timestamps to Nepal time (double-tap aware)
        clock_in_time, clock_out_time = _derive_clock_times(timestamps, NPT)

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

        # A manually regularized/corrected record (approved regularization or
        # "Fix Attendance") is an explicit human decision — never let the raw
        # biometric punches silently overwrite it back on the next page load.
        # Everything *derived* from those times (hours, overtime, the late /
        # early flags, the Half Day / Absent label) still has to track the
        # current shift and policy though: this sync is the only pass that
        # ever revisits these rows, so skipping them outright froze them on
        # whatever arithmetic was in force at fix time. Collected here and
        # re-derived in one pass below.
        if existing_record and existing_record.is_regularized:
            regularized_pks.append(existing_record.pk)
            continue

        if existing_record and existing_record.shift:
            shift = existing_record.shift
        elif employee.shift_id:
            shift = employee.shift

        # Get employee's attendance policy for grace periods (falls back to the
        # active company-wide policy when the employee has none assigned)
        policy = employee.effective_attendance_policy

        if shift and clock_in_time:
            from datetime import datetime, timedelta
            late_grace = policy.late_mark_after if policy else (shift.grace_period or 0)
            shift_start = datetime.combine(punch_date, shift.start_time)
            cin_full = datetime.combine(punch_date, clock_in_time)

            if cin_full > shift_start + timedelta(minutes=late_grace):
                is_late = True
                status = 'late'

            if clock_out_time:
                # Subtract break duration to get effective working hours
                break_hrs = (shift.break_duration or 0) / 60.0
                if working_hours > break_hrs:
                    working_hours = round(working_hours - break_hrs, 2)
                shift_hours = float(shift.working_hours)
                if working_hours > shift_hours:
                    overtime_hours = round(working_hours - shift_hours, 2)

                early_grace = policy.early_departure_grace if policy else (shift.grace_period or 0)
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

        # Auto-classify the day using the effective Attendance Policy's
        # Absent/Half Day thresholds (falls back to the shift's own
        # half_day_hours, then hardcoded defaults, when no policy is
        # configured) — overrides 'late'/'present' since hours actually
        # worked take priority over a late mark.
        if clock_in_time and clock_out_time:
            status = _classify_attendance_status(working_hours, is_late, policy, shift)

        # Flag incomplete punches (only one of clock-in/clock-out recorded)
        # instead of silently marking the day 'Present' — needs manual
        # correction. Takes priority over any status computed above since a
        # day we can't fully verify shouldn't be reported as complete.
        if not (clock_in_time and clock_out_time):
            status = 'incomplete'

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
                # Fresh biometric data is authoritative — revive a row an
                # admin previously trashed rather than leaving today's real
                # punch invisible in the trash.
                'is_deleted': False,
                'deleted_at': None,
                'deleted_by': None,
            }
        )

    # One pass for every hand-fixed row seen above, so a threshold or shift
    # change reaches their hours, overtime and label on the next page load
    # without touching their manually corrected clock times.
    if regularized_pks:
        _refresh_attendance_records(
            AttendanceRecord.objects.filter(pk__in=regularized_pks)
        )


def _maybe_auto_sync_attendance():
    """Runs the biometric-to-attendance sync automatically once the interval
    configured in AttendanceSyncSettings has elapsed, independent of anyone
    opening the Attendance Records page (which already triggers a full sync
    on every visit — see attendance_list below). There's no Celery Beat in
    this project, so this piggybacks on the Incomplete Attendance widget's
    existing 60s background poll (present on every page via
    partials/incomplete_attendance_alert.html), turning that poll into the
    heartbeat for a real periodic data pull."""
    from .models import AttendanceSyncSettings
    from django.core.cache import cache

    settings_obj = AttendanceSyncSettings.get_settings()
    now = timezone.now()
    if settings_obj.last_synced_at and (now - settings_obj.last_synced_at).total_seconds() < settings_obj.interval_minutes * 60:
        return

    # Debounce concurrent requests that race past the interval check at the
    # same moment (e.g. two staff with a page open when the interval elapses).
    if not cache.add('attendance_auto_sync_lock', 1, timeout=120):
        return
    try:
        _sync_biometric_to_attendance()
        settings_obj.last_synced_at = now
        settings_obj.save()
    finally:
        cache.delete('attendance_auto_sync_lock')


@login_required
def attendance_list(request):
    from .models import AttendanceRecord, Employee, Shift
    from dashboard.timezone_utils import get_nepali_now
    from django.core.paginator import Paginator
    from django.db.models import Q, Count, Sum
    from datetime import date as dt_date

    # Auto-sync on page load. We sync all days as requested.
    # Never let a sync failure (bad punch data, DB hiccup, etc.) take down
    # the whole page — log it and fall back to showing existing records.
    try:
        _sync_biometric_to_attendance()
    except Exception:
        adms_logger.exception('attendance_list: biometric auto-sync failed, showing existing records')

    search = request.GET.get('search', '')
    filter_date = request.GET.get('filter_date', '')
    sort_by = request.GET.get('sort_by', '-date')
    per_page = request.GET.get('per_page', 9)
    try:
        per_page = int(per_page)
    except (ValueError, TypeError):
        per_page = 9

    # Gather everything the page needs behind a guard. If any query fails — a
    # missing column because a migration wasn't applied on the server, a corrupt
    # row, a DB hiccup — log the full traceback and render a degraded-but-working
    # page (empty list + warning banner) instead of a bare 500. The exception is
    # always written to the hrm.adms log so the real cause is captured on the
    # first hit, not left to a second round of debugging in production.
    load_error = False
    try:
        qs = AttendanceRecord.objects.select_related('employee', 'shift').filter(is_deleted=False)

        if filter_date:
            qs = qs.filter(date=filter_date)

        if search:
            qs = qs.filter(
                Q(employee__full_name__icontains=search) |
                Q(notes__icontains=search)
            )

        if sort_by == 'date':
            qs = qs.order_by('date', 'employee__full_name')
        elif sort_by == 'employee__full_name':
            qs = qs.order_by('employee__full_name', '-date')
        elif sort_by == '-employee__full_name':
            qs = qs.order_by('-employee__full_name', '-date')
        else:
            qs = qs.order_by('-date', 'employee__full_name')

        today = dt_date.today()
        all_records = AttendanceRecord.objects.filter(is_deleted=False)
        total_records = all_records.count()
        # Count anyone who has actually clocked in today as "present", even if
        # their punch is still incomplete (no clock-out yet, e.g. mid-shift) —
        # a missing clock-out shouldn't hide someone who showed up.
        present_today = all_records.filter(date=today).filter(
            Q(status__in=['present', 'late', 'half_day']) |
            Q(status='incomplete', clock_in__isnull=False)
        ).count()
        on_leave_today = all_records.filter(date=today, status='on_leave').count()
        late_today = all_records.filter(date=today, is_late_arrival=True).count()
        overtime_today = all_records.filter(date=today, overtime_hours__gt=0).count()

        paginator = Paginator(qs, per_page)
        page_num = request.GET.get('page', 1)
        records = paginator.get_page(page_num)

        employees = Employee.objects.filter(employee_status='active').order_by('full_name')
        shifts = Shift.objects.filter(is_active=True).order_by('name')
    except Exception:
        adms_logger.exception('attendance_list: failed to load records')
        load_error = True
        records = []
        total_records = present_today = on_leave_today = late_today = overtime_today = 0
        employees = Employee.objects.none()
        shifts = Shift.objects.none()

    context = {
        'page_title': 'Attendance Records',
        'records': records,
        'search': search,
        'filter_date': filter_date,
        'sort_by': sort_by,
        'per_page': per_page,
        'total_records': total_records,
        'present_today': present_today,
        'on_leave_today': on_leave_today,
        'late_today': late_today,
        'overtime_today': overtime_today,
        'employees': employees,
        'shifts': shifts,
        'load_error': load_error,
        # Caps the Add/Edit date picker at today in Nepal time. The server
        # rejects future dates regardless (_parse_attendance_date); this just
        # stops the user picking one in the first place, and uses the Nepal
        # date rather than whatever the browser's clock says.
        'today_str': get_nepali_now().date().isoformat(),
    }
    return render(request, 'hrm/attendance_list.html', context)


# ==================== Manual attendance entry guards (shared) ====================
#
# Raw biometric punches are de-duplicated by the (pin, timestamp) unique
# constraint and _ingest_attlog_lines. The helpers below are the equivalent
# guard for the *manual* paths — Attendance Records and Attendance
# Regularizations — where a person (or a double-clicked Save button) is the
# source of the duplicate rather than a device.


def _parse_attendance_date(value):
    """Coerce a posted date to a real ``date``. Returns (date, error_message).

    Posted dates arrive as 'YYYY-MM-DD' strings but reach the ORM untyped, so
    a malformed value used to surface as a ValidationError 500 inside an AJAX
    call. A date in the future is rejected outright — attendance cannot be
    recorded for a day that has not happened, and back-dating typos ("2026"
    keyed as "2062" in a Nepali-calendar habit) are a common way duplicate or
    orphaned rows get created.
    """
    from datetime import date as _date, datetime as _datetime

    from dashboard.timezone_utils import get_nepali_now

    if not value:
        return None, 'Date is required.'

    if isinstance(value, _date):
        parsed = value
    else:
        try:
            parsed = _datetime.strptime(str(value).strip()[:10], '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return None, 'Invalid date. Use the date picker (YYYY-MM-DD).'

    today = get_nepali_now().date()
    if parsed > today:
        return None, f'Cannot record attendance for a future date ({parsed}). Today is {today}.'

    return parsed, None


def _validate_clock_pair(clock_in, clock_out):
    """Reject clock in/out combinations that can only be data-entry mistakes.

    Returns an error message, or None when the pair is usable.

    Identical times are the manual-entry twin of the biometric double-tap
    (see DUPLICATE_PUNCH_WINDOW_SECONDS): they compute to 0.00 worked hours,
    which _compute_attendance_metrics then auto-classifies as Absent — so a
    mistyped duplicate time silently books the employee absent for a day they
    actually worked.
    """
    if not clock_in and not clock_out:
        return 'Either Clock In or Clock Out time is required.'

    if clock_in and clock_out:
        from datetime import datetime as _datetime
        try:
            cin = _datetime.strptime(str(clock_in)[:5], '%H:%M')
            cout = _datetime.strptime(str(clock_out)[:5], '%H:%M')
        except (ValueError, TypeError):
            return 'Invalid time value. Use the time picker (HH:MM).'
        if cin == cout:
            return (
                'Clock In and Clock Out cannot be the same time — that records a '
                '0-hour day and would mark the employee Absent. Leave Clock Out '
                'empty if the second punch is still missing.'
            )

    return None


def _find_duplicate_attendance(employee, date_val, exclude_pk=None):
    """The AttendanceRecord already occupying this employee+date slot, if any.

    Deliberately ignores ``is_deleted``: a trashed row still holds the
    (employee, date) unique slot, so creating "a new one" would hit an
    IntegrityError rather than succeed. Callers use the returned row to tell
    the user *where* the conflicting record is instead of a dead-end
    "already exists".
    """
    from .models import AttendanceRecord

    qs = AttendanceRecord.objects.filter(employee=employee, date=date_val)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def _duplicate_attendance_error(record):
    """Actionable message for an employee+date slot that is already taken."""
    who = record.employee.full_name
    when = record.date
    if record.is_deleted:
        return (
            f'{who} already has a deleted attendance record for {when}, sitting in '
            f'Attendance Adjustments (Trash). Restore or permanently delete it there '
            f'before adding a new one.'
        )
    times = []
    if record.clock_in:
        times.append(f'in {record.clock_in.strftime("%I:%M %p")}')
    if record.clock_out:
        times.append(f'out {record.clock_out.strftime("%I:%M %p")}')
    detail = f' ({", ".join(times)})' if times else ''
    return (
        f'{who} already has an attendance record for {when}{detail}. '
        f'Edit that record instead of adding a duplicate.'
    )


def _find_conflicting_regularization(employee, date_val, exclude_pk=None):
    """An existing regularization that a new request for this day would clash with.

    Returns (regularization, error_message) or (None, None).

    Two rules, both about corrupting the record rather than mere tidiness:

    * Only one *open* (pending or draft) request may exist per employee+date.
      Two open requests are both approvable, and the second approval silently
      overwrites what the first one applied.
    * A day that already has an *approved* request cannot take a new one,
      because approval snapshots the record into pre_regularization_state to
      support restore-on-reject. Approving a second request would capture the
      already-regularized values, so a later reject would "restore" the day to
      the wrong times instead of the original ones.

    A *rejected* request is not a conflict — re-requesting after a rejection is
    a normal thing to do.
    """
    from .models import AttendanceRegularization

    qs = AttendanceRegularization.objects.filter(employee=employee, date=date_val)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    existing = qs.filter(status='approved').first()
    if existing:
        return existing, (
            f'{employee.full_name} already has an approved regularization for {date_val}. '
            f'Edit that approved request instead — filing a second one would make a later '
            f'rejection restore the wrong clock times.'
        )

    existing = qs.filter(status='pending').first()
    if existing:
        state = 'draft' if existing.is_draft else 'pending'
        return existing, (
            f'{employee.full_name} already has a {state} regularization for {date_val}. '
            f'Update that request instead of filing a duplicate.'
        )

    return None, None


# Statuses that are *derived* from the day's clock times + the effective
# Attendance Policy thresholds. Anything outside this set ('on_leave') is a
# deliberate human marker and must never be overwritten by a recompute.
AUTO_ATTENDANCE_STATUSES = {'present', 'late', 'half_day', 'absent', 'incomplete'}


def _attendance_thresholds(policy, shift):
    """The effective (absent, half-day) worked-hour thresholds for a day.

    Single source of truth for the fallback chain: the employee's effective
    Attendance Policy first, then the shift's own half_day_hours, then the
    hardcoded defaults. Every place that classifies a day (biometric sync,
    manual edit, regularization approval, the recompute pass below) goes
    through this so a threshold can never mean two different things.
    """
    absent_th = 2.0
    if policy is not None and policy.absent_threshold_hours is not None:
        absent_th = float(policy.absent_threshold_hours)

    if policy is not None and policy.half_day_threshold_hours is not None:
        half_th = float(policy.half_day_threshold_hours)
    elif shift is not None and shift.half_day_hours is not None:
        half_th = float(shift.half_day_hours)
    else:
        half_th = 4.0

    return absent_th, half_th


def _classify_attendance_status(working_hours, is_late, policy, shift):
    """Absent / Half Day / Late / Present from worked hours + thresholds."""
    absent_th, half_th = _attendance_thresholds(policy, shift)
    try:
        hours = float(working_hours or 0)
    except (TypeError, ValueError):
        hours = 0.0

    if hours <= absent_th:
        return 'absent'
    if hours <= half_th:
        return 'half_day'
    return 'late' if is_late else 'present'


def _refresh_attendance_records(records=None, dry_run=False):
    """Re-derive existing AttendanceRecords from their stored clock times
    against the *current* shift + policy, and return how many rows changed.

    Everything on an attendance row except the clock times is derived:
    working_hours (clock-out minus clock-in, less the shift's break),
    overtime_hours (anything past the shift's working hours), the late /
    early-departure flags, and the Present / Half Day / Absent / Late label.
    All of it is computed once, when the row is written, and then stored --
    so it silently goes stale whenever the inputs behind it change:

      * raising or lowering a Half Day / Absent threshold on the Attendance
        Policies page only affected rows saved afterwards;
      * editing a shift's break duration, working hours or start/end times
        left every day already recorded against it on the old arithmetic --
        e.g. a 9:19 AM to 9:18 PM day still reporting 8.00h and no overtime;
      * and a hand-fixed row (is_regularized=True) is deliberately skipped by
        the biometric auto-sync, so it could never pick any of that up at all.

    The stored clock times are the one thing this never touches -- a manual
    correction stays exactly as it was entered; only the numbers derived
    from it are rebuilt. 'on_leave' is likewise preserved, since it
    is a deliberate marker rather than something punches imply.

    With dry_run=True nothing is written and a list of
    (record, {field: (old, new)}) tuples is returned instead of a count
    (used by the recompute_attendance_status management command).
    """
    from .models import AttendanceRecord

    qs = AttendanceRecord.objects.all() if records is None else records
    qs = qs.select_related('employee__shift', 'employee__attendance_policy', 'shift')

    policy_cache = {}
    changed = []
    for rec in qs.iterator():
        employee = rec.employee
        if employee.id not in policy_cache:
            policy_cache[employee.id] = employee.effective_attendance_policy

        metrics = _compute_attendance_metrics_from_times(
            rec.clock_in,
            rec.clock_out,
            rec.shift or employee.shift,
            policy_cache[employee.id],
            rec.status,
        )

        diff = {}
        if metrics['status'] != rec.status:
            diff['status'] = (rec.status, metrics['status'])
        if round(float(rec.working_hours or 0), 2) != round(float(metrics['working_hours']), 2):
            diff['working_hours'] = (float(rec.working_hours or 0), float(metrics['working_hours']))
        if round(float(rec.overtime_hours or 0), 2) != round(float(metrics['overtime_hours']), 2):
            diff['overtime_hours'] = (float(rec.overtime_hours or 0), float(metrics['overtime_hours']))
        if bool(rec.is_late_arrival) != bool(metrics['is_late']):
            diff['is_late_arrival'] = (rec.is_late_arrival, metrics['is_late'])
        if bool(rec.is_early_departure) != bool(metrics['is_early']):
            diff['is_early_departure'] = (rec.is_early_departure, metrics['is_early'])

        if not diff:
            continue

        rec.status = metrics['status']
        rec.working_hours = metrics['working_hours']
        rec.overtime_hours = metrics['overtime_hours']
        rec.is_late_arrival = metrics['is_late']
        rec.is_early_departure = metrics['is_early']
        changed.append((rec, diff))

    if changed and not dry_run:
        AttendanceRecord.objects.bulk_update(
            [rec for rec, _diff in changed],
            ['status', 'working_hours', 'overtime_hours',
             'is_late_arrival', 'is_early_departure'],
            batch_size=500,
        )
    return changed if dry_run else len(changed)


def _refresh_records_for_policy(policy):
    """Re-derive every attendance row whose *effective* policy is `policy`.

    That is employees explicitly assigned to it plus -- when this policy is
    the active company-wide fallback -- everyone with no policy of their own
    (see Employee.effective_attendance_policy).
    """
    from .models import AttendancePolicy, AttendanceRecord, Employee

    emp_q = Q(attendance_policy=policy)
    fallback = AttendancePolicy.objects.filter(is_active=True).order_by('id').first()
    if fallback is not None and fallback.pk == policy.pk:
        emp_q |= Q(attendance_policy__isnull=True)

    employee_ids = list(Employee.objects.filter(emp_q).values_list('id', flat=True))
    if not employee_ids:
        return 0
    return _refresh_attendance_records(
        AttendanceRecord.objects.filter(employee_id__in=employee_ids)
    )


def _refresh_records_for_shift(shift):
    """Re-derive rows recorded against `shift` — its start/end times, break
    duration and working hours drive their hours, overtime and late/early
    flags, and its half_day_hours is the fallback Half Day threshold for
    employees with no effective Attendance Policy."""
    from .models import AttendanceRecord

    return _refresh_attendance_records(
        AttendanceRecord.objects.filter(
            Q(shift=shift) | Q(shift__isnull=True, employee__shift=shift)
        )
    )


def _safe_refresh_attendance(refresh_fn, arg, context):
    """Run a refresh without ever letting it break the save it follows.
    Returns the number of rows updated (0 on failure, which is logged)."""
    try:
        return refresh_fn(arg)
    except Exception:
        hrm_logger.exception('%s: attendance status refresh failed', context)
        return 0


def _compute_attendance_metrics(clock_in, clock_out, shift, policy, status):
    """Derive status/working_hours/overtime_hours/is_late/is_early from clock
    in/out strings ('HH:MM'-prefixed) against a shift+policy.

    Status is fully recomputed from worked hours (Absent / Half Day / Late /
    Present, using the effective policy's Absent/Half Day thresholds —
    falling back to the shift's own half_day_hours, then hardcoded defaults,
    when no policy is configured) for every "clock-derived" status. Only
    'on_leave' is left untouched, since it's a deliberate day-off marker
    unrelated to punch times, not something worked hours should overwrite.

    Shared by attendance create/update, the Attendance Adjustments quick
    edit, and regularization approval so a requested clock time actually
    produces the same derived state everywhere instead of drifting out of
    sync (see: regularization approval used to only flip the request's own
    status and never touched the linked AttendanceRecord at all; and the
    Attendance Adjustments quick edit could leave a record stuck on
    'incomplete' or a stale 'half_day'/'present' even after both times were
    filled in, because the old version only re-derived status starting from
    a bare 'present'/'late').

    This is the string front door for the forms; the derivation itself lives
    in _compute_attendance_metrics_from_times()."""
    from datetime import datetime

    def _parse(value):
        return datetime.strptime(value[:5], '%H:%M').time() if value else None

    return _compute_attendance_metrics_from_times(
        _parse(clock_in), _parse(clock_out), shift, policy, status
    )


def _compute_attendance_metrics_from_times(clock_in, clock_out, shift, policy, status):
    """The derivation behind _compute_attendance_metrics, taking datetime.time
    objects (what AttendanceRecord stores) instead of 'HH:MM' strings.

    Working with the stored times directly keeps full precision: biometric
    punches carry seconds, and re-deriving a saved row through the string
    path would truncate them to the minute and nudge its hours on every
    pass. Same numbers as the biometric sync computes, so re-deriving a row
    can never fight with the next sync.
    """
    from datetime import datetime, timedelta

    day = datetime.today().date()
    working_hours = 0
    overtime_hours = 0
    is_late = False
    is_early = False

    if clock_in and shift:
        late_grace = policy.late_mark_after if policy else (shift.grace_period or 0)
        shift_start = datetime.combine(day, shift.start_time)
        if datetime.combine(day, clock_in) > shift_start + timedelta(minutes=late_grace):
            is_late = True

    if clock_out and shift and shift.end_time:
        shift_start = datetime.combine(day, shift.start_time)
        shift_end = datetime.combine(day, shift.end_time)
        early_grace = policy.early_departure_grace if policy else (shift.grace_period or 0)
        cout_full = datetime.combine(day, clock_out)

        # Handle night shifts spanning midnight
        if shift_end <= shift_start:
            shift_end += timedelta(days=1)
            if clock_in:
                if cout_full < datetime.combine(day, clock_in):
                    cout_full += timedelta(days=1)
            elif cout_full < shift_start:
                cout_full += timedelta(days=1)

        if cout_full < shift_end - timedelta(minutes=early_grace):
            is_early = True

    if clock_in and clock_out:
        diff = (
            datetime.combine(day, clock_out) - datetime.combine(day, clock_in)
        ).total_seconds() / 3600
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

        # Recompute the day's status from actual worked hours whenever it's
        # one of the clock-derived statuses (covers the default 'present',
        # an auto-detected 'late', and re-editing an already 'half_day' /
        # 'absent' / 'incomplete' record's times on the Attendance
        # Adjustments page — all of those should reflect the new times, not
        # keep whatever status happened to be stored before the edit).
        if status in AUTO_ATTENDANCE_STATUSES:
            status = _classify_attendance_status(working_hours, is_late, policy, shift)

    # Flag incomplete punches (only one of clock-in/clock-out given)
    # instead of leaving the day reported as Present/Late/Half Day/Absent.
    if not (clock_in and clock_out) and status in AUTO_ATTENDANCE_STATUSES:
        status = 'incomplete'

    return {
        'status': status,
        'working_hours': working_hours,
        'overtime_hours': overtime_hours,
        'is_late': is_late,
        'is_early': is_early,
    }


@login_required
def attendance_create(request):
    from .models import AttendanceRecord, Employee, Shift, AttendancePolicy
    from django.db import transaction

    if request.method == 'POST':
        employee_id = request.POST.get('employee')
        date_val = request.POST.get('date')
        clock_in = request.POST.get('clock_in') or None
        clock_out = request.POST.get('clock_out') or None
        shift_id = request.POST.get('shift') or None
        is_holiday = request.POST.get('is_holiday') == 'true'
        notes = request.POST.get('notes', '').strip()
        status = request.POST.get('status', 'present')

        if not employee_id:
            return JsonResponse({'success': False, 'error': 'Employee is required.'})

        date_val, date_error = _parse_attendance_date(date_val)
        if date_error:
            return JsonResponse({'success': False, 'error': date_error})

        clock_error = _validate_clock_pair(clock_in, clock_out)
        if clock_error:
            return JsonResponse({'success': False, 'error': clock_error})

        employee = Employee.objects.select_related('shift', 'attendance_policy').filter(pk=employee_id).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found.'})

        # Includes soft-deleted rows: a trashed record still holds this
        # (employee, date) slot, so "already exists" has to say where it is
        # rather than leave the user hunting a record they cannot see.
        clash = _find_duplicate_attendance(employee, date_val)
        if clash:
            return JsonResponse({'success': False, 'error': _duplicate_attendance_error(clash)})

        # Use shift from form, fallback to employee's assigned shift
        shift = Shift.objects.filter(pk=shift_id).first() if shift_id else employee.shift
        policy = employee.effective_attendance_policy

        metrics = _compute_attendance_metrics(clock_in, clock_out, shift, policy, status)

        # The .exists() check above cannot stop a double-clicked Save or two
        # staff submitting the same day at once — both pass it, then the
        # second INSERT trips the (employee, date) unique constraint. Catch
        # that here so the race reports the same friendly message instead of
        # an HTML 500 inside the AJAX handler.
        try:
            # atomic() wraps the INSERT in a savepoint so a constraint failure
            # is contained — required for the except below to be safe if
            # ATOMIC_REQUESTS is ever turned on.
            with transaction.atomic():
                record = AttendanceRecord.objects.create(
                    employee=employee,
                    date=date_val,
                    clock_in=clock_in if clock_in else None,
                    clock_out=clock_out if clock_out else None,
                    shift=shift,
                    status=metrics['status'],
                    working_hours=metrics['working_hours'],
                    overtime_hours=metrics['overtime_hours'],
                    is_holiday=is_holiday,
                    notes=notes,
                    is_early_departure=metrics['is_early'],
                    is_late_arrival=metrics['is_late'],
                )
        except IntegrityError:
            clash = _find_duplicate_attendance(employee, date_val)
            error = (
                _duplicate_attendance_error(clash) if clash
                else 'Attendance already exists for this employee on this date.'
            )
            return JsonResponse({'success': False, 'error': error})

        return JsonResponse({'success': True, 'id': record.id})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def attendance_update(request, pk):
    from .models import AttendanceRecord, Shift, AttendanceFixLog
    from datetime import datetime as dt

    record = get_object_or_404(
        AttendanceRecord.objects.select_related('employee__shift', 'employee__attendance_policy'),
        pk=pk, is_deleted=False
    )

    if request.method == 'POST':
        # The Incomplete Attendance modal's "Fix" action posts through this
        # same endpoint but is tagged so it can be gated by its own
        # permission — the general Attendance Records edit flow (which
        # doesn't send this marker) is left untouched.
        source = request.POST.get('source', '').strip()
        if source in ('incomplete_fix', 'adjustment_edit'):
            user = request.user
            # Require both flags — the Role Permissions page can save
            # "Fix" without "View" checked (it has no cascade guard like
            # the user create/edit forms do), so the fix capability itself
            # must not trust "can_fix" alone as the single source of truth.
            can_fix = user.is_superuser or user.role == 'administrator' or (
                user.can_fix_hrm_incomplete_attendance and user.can_view_hrm_incomplete_attendance
            )
            if not can_fix:
                return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

        clock_in = request.POST.get('clock_in') or None
        clock_out = request.POST.get('clock_out') or None
        shift_id = request.POST.get('shift') or None
        is_holiday = request.POST.get('is_holiday') == 'true'
        notes = request.POST.get('notes', '').strip()
        remarks = request.POST.get('remarks', '').strip()
        status = request.POST.get('status', 'present')

        # Same guard as attendance_create — an edit can introduce the identical
        # clock in/out (0-hour day silently classified Absent) just as easily
        # as a fresh entry can.
        clock_error = _validate_clock_pair(clock_in, clock_out)
        if clock_error:
            return JsonResponse({'success': False, 'error': clock_error})

        # Use shift from form, fallback to existing record shift, then employee's assigned shift
        shift = Shift.objects.filter(pk=shift_id).first() if shift_id else (record.shift or record.employee.shift)
        policy = record.employee.effective_attendance_policy

        metrics = _compute_attendance_metrics(clock_in, clock_out, shift, policy, status)

        # Capture the pre-edit times (real time objects, as loaded from the DB)
        # before overwriting, so the fix log can record an accurate old → new diff.
        old_clock_in = record.clock_in
        old_clock_out = record.clock_out
        new_clock_in = dt.strptime(clock_in[:5], '%H:%M').time() if clock_in else None
        new_clock_out = dt.strptime(clock_out[:5], '%H:%M').time() if clock_out else None

        record.clock_in = new_clock_in
        record.clock_out = new_clock_out
        record.shift = shift
        record.status = metrics['status']
        record.working_hours = metrics['working_hours']
        record.overtime_hours = metrics['overtime_hours']
        record.is_holiday = is_holiday
        record.notes = notes
        record.is_early_departure = metrics['is_early']
        record.is_late_arrival = metrics['is_late']
        # A manual "Fix Attendance" edit is an explicit human correction —
        # the biometric auto-sync must not overwrite it on the next page load.
        record.is_regularized = True

        times_changed = old_clock_in != new_clock_in or old_clock_out != new_clock_out
        if times_changed or remarks:
            record.last_fix_remarks = remarks
            record.last_fixed_by = request.user if request.user.is_authenticated else None
            record.last_fixed_at = timezone.now()

        record.save()

        # Log every meaningful edit (a time actually changed, or a remark was
        # left) so the "Logs" view has an accurate, append-only history —
        # not just the latest snapshot stored on the record itself.
        if times_changed or remarks:
            AttendanceFixLog.objects.create(
                attendance_record=record,
                old_clock_in=old_clock_in,
                old_clock_out=old_clock_out,
                new_clock_in=new_clock_in,
                new_clock_out=new_clock_out,
                remarks=remarks,
                fixed_by=request.user if request.user.is_authenticated else None,
            )

        return JsonResponse({'success': True})

    # GET — return data for edit
    data = {
        'id': record.id,
        'employee_id': record.employee_id,
        'employee_name': record.employee.full_name,
        'date': str(record.date),
        # Raw 24-hour value for the <input type="time"> edit field;
        # *_display is the 12-hour Nepal-style AM/PM string for the read-only view.
        'clock_in': record.clock_in.strftime('%H:%M') if record.clock_in else '',
        'clock_out': record.clock_out.strftime('%H:%M') if record.clock_out else '',
        'clock_in_display': record.clock_in.strftime('%I:%M %p') if record.clock_in else '',
        'clock_out_display': record.clock_out.strftime('%I:%M %p') if record.clock_out else '',
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
def attendance_fix_logs(request, pk):
    """Full 'who fixed this and why' history for one attendance record —
    powers the Logs icon in the Incomplete Attendance table. Gated by the
    same permission as the Incomplete Attendance widgets (HRM + Dashboard),
    since that's the only surface this is reachable from."""
    from .models import AttendanceRecord
    from dashboard.timezone_utils import format_nepali_datetime

    user = request.user
    if not (
        user.is_superuser or user.role == 'administrator'
        or user.can_view_hrm_incomplete_attendance or user.can_view_dashboard_incomplete_attendance
    ):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    record = get_object_or_404(AttendanceRecord.objects.select_related('employee', 'employee__department'), pk=pk)
    logs = record.fix_logs.select_related('fixed_by').all()

    def fmt_time(t):
        return t.strftime('%I:%M %p') if t else None

    def fmt_user(u):
        if not u:
            return 'System'
        full_name = u.get_full_name() if hasattr(u, 'get_full_name') else ''
        return full_name or u.username

    return JsonResponse({
        'success': True,
        'employee_name': record.employee.full_name,
        'employee_code': record.employee.employee_id,
        'department': record.employee.department.name if record.employee.department else '—',
        'date_display': record.date.strftime('%d %b %Y'),
        'logs': [
            {
                'old_clock_in': fmt_time(log.old_clock_in),
                'old_clock_out': fmt_time(log.old_clock_out),
                'new_clock_in': fmt_time(log.new_clock_in),
                'new_clock_out': fmt_time(log.new_clock_out),
                'remarks': log.remarks,
                'fixed_by': fmt_user(log.fixed_by),
                'fixed_at': format_nepali_datetime(log.fixed_at),
            }
            for log in logs
        ],
    })


@login_required
def attendance_delete(request, pk):
    """Soft-delete — moves the record to the Attendance Adjustments trash
    instead of destroying it outright, so a wrongly-removed record (or one
    trashed while double-checking a duplicate/erroneous punch) can still be
    recovered. Permanent removal only happens via attendance_hard_delete,
    reachable from the trash. Shared by the plain Attendance Records page
    delete button and the Attendance Adjustments page."""
    from .models import AttendanceRecord
    record = get_object_or_404(AttendanceRecord, pk=pk, is_deleted=False)
    if request.method == 'POST':
        record.soft_delete(deleted_by_user=request.user if request.user.is_authenticated else None)
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def attendance_adjustment_restore(request, pk):
    """Restore a trashed AttendanceRecord back to the active views."""
    from .models import AttendanceRecord

    if not _can_fix_attendance_adjustments(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    record = get_object_or_404(AttendanceRecord, pk=pk, is_deleted=True)
    if request.method == 'POST':
        record.restore()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def attendance_adjustment_hard_delete(request, pk):
    """Permanently delete a trashed AttendanceRecord — irreversible, so it's
    restricted to admins and only reachable from the trash (a record must be
    soft-deleted first). Also purges the raw biometric punches for that
    employee/day so a permanently-removed bad record doesn't immediately
    reappear on the next biometric sync."""
    from .models import AttendanceRecord, BiometricAttendance

    user = request.user
    if not (user.is_superuser or user.role == 'administrator'):
        return JsonResponse({'success': False, 'error': 'Only administrators can permanently delete attendance records.'}, status=403)

    record = get_object_or_404(AttendanceRecord, pk=pk, is_deleted=True)
    if request.method == 'POST':
        if record.employee and record.employee.employee_code:
            import pytz
            from datetime import datetime
            local_tz = pytz.timezone('Asia/Kathmandu')
            start_of_day = local_tz.localize(datetime.combine(record.date, datetime.min.time()))
            end_of_day = local_tz.localize(datetime.combine(record.date, datetime.max.time()))
            # Match by normalized PIN (see _normalize_pin) so a differently-
            # padded raw punch for this employee/day doesn't survive the purge
            # and silently resurrect the record on the next biometric sync.
            target_pin = _normalize_pin(record.employee.employee_code)
            purge_ids = [
                p.id for p in BiometricAttendance.objects.filter(timestamp__range=(start_of_day, end_of_day)).only('id', 'pin')
                if _normalize_pin(p.pin) == target_pin
            ]
            BiometricAttendance.objects.filter(id__in=purge_ids).delete()
        record.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def incomplete_attendance_list_ajax(request):
    """Lists AttendanceRecord rows missing a clock-in or clock-out, expressed
    as a queryset filter equivalent to AttendanceRecord.is_incomplete_punch
    (Absent/On Leave excluded, since blank times are normal there) so it can
    be searched/paginated at the DB level instead of filtered in Python."""
    from .models import AttendanceRecord
    from datetime import datetime
    from dashboard.timezone_utils import format_nepali_datetime

    user = request.user
    if not (
        user.is_superuser or user.role == 'administrator'
        or user.can_view_hrm_incomplete_attendance or user.can_view_dashboard_incomplete_attendance
    ):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    # Runs only for callers who already passed the permission check above —
    # this endpoint is polled every 60s from every page (see the global
    # partial), so it doubles as the heartbeat for the periodic data pull,
    # but an unauthorized request should never have this side effect.
    try:
        _maybe_auto_sync_attendance()
    except Exception:
        adms_logger.exception('incomplete_attendance_list_ajax: auto-sync failed')

    qs = AttendanceRecord.objects.exclude(status__in=['absent', 'on_leave']).filter(
        Q(clock_in__isnull=True) | Q(clock_out__isnull=True),
        is_deleted=False,
    )

    search = request.GET.get('search', '').strip()
    if search:
        qs = qs.filter(
            Q(employee__full_name__icontains=search) |
            Q(employee__employee_id__icontains=search)
        )
    date_from = request.GET.get('date_from', '').strip()
    if date_from:
        try:
            qs = qs.filter(date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    date_to = request.GET.get('date_to', '').strip()
    if date_to:
        try:
            qs = qs.filter(date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass

    if request.GET.get('count_only') == '1':
        return JsonResponse({'success': True, 'count': qs.count()})

    qs = qs.select_related('employee', 'employee__department', 'shift', 'last_fixed_by').annotate(
        fix_logs_count=Count('fix_logs', distinct=True)
    ).order_by('-date', 'employee__full_name')

    try:
        per_page = int(request.GET.get('per_page', 15))
    except (ValueError, TypeError):
        per_page = 15
    per_page = max(5, min(per_page, 100))

    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    results = []
    for rec in page_obj:
        if not rec.clock_in and not rec.clock_out:
            missing = 'both'
        elif not rec.clock_in:
            missing = 'clock_in'
        else:
            missing = 'clock_out'
        results.append({
            'id': rec.id,
            'employee_name': rec.employee.full_name,
            'employee_code': rec.employee.employee_id,
            'department': rec.employee.department.name if rec.employee.department else '—',
            'date': str(rec.date),
            'date_display': rec.date.strftime('%d %b %Y'),
            'clock_in': rec.clock_in.strftime('%H:%M') if rec.clock_in else '',
            'clock_out': rec.clock_out.strftime('%H:%M') if rec.clock_out else '',
            'clock_in_display': rec.clock_in.strftime('%I:%M %p') if rec.clock_in else '',
            'clock_out_display': rec.clock_out.strftime('%I:%M %p') if rec.clock_out else '',
            'missing': missing,
            'status': rec.status,
            'status_display': rec.get_status_display(),
            'shift_id': rec.shift_id,
            'shift_name': rec.shift.name if rec.shift else '—',
            'is_holiday': rec.is_holiday,
            'notes': rec.notes,
            'remarks': rec.last_fix_remarks or '',
            'fixed_by': (rec.last_fixed_by.get_full_name() or rec.last_fixed_by.username) if rec.last_fixed_by else '',
            'fixed_at': format_nepali_datetime(rec.last_fixed_at) if rec.last_fixed_at else '',
            'fix_logs_count': rec.fix_logs_count,
        })

    return JsonResponse({
        'success': True,
        'count': paginator.count,
        'page': page_obj.number,
        'total_pages': paginator.num_pages,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
        'results': results,
    })


# ==================== Attendance Adjustments ====================
# A permanent, full-page home for everything that comes out of the
# Incomplete Attendance "Fix" modal: every record that is currently
# incomplete OR has ever been fixed, with full detail (remarks, who/when),
# in-place editing, and a soft-delete/trash/hard-delete lifecycle — the
# modal itself only ever shows the current incomplete backlog and has no
# memory of records once they're fixed.

def _can_view_attendance_adjustments(user):
    # Matches the permission set already used by every other endpoint behind
    # the Incomplete Attendance modal (incomplete_attendance_list_ajax,
    # attendance_fix_logs) plus can_view_hrm_attendance, since this page also
    # surfaces plain Attendance Records-style data (previously-fixed,
    # now-complete rows) that those don't.
    return bool(
        user.is_superuser or user.role == 'administrator'
        or user.can_view_hrm_incomplete_attendance or user.can_view_dashboard_incomplete_attendance
        or user.can_view_hrm_attendance
    )


def _can_fix_attendance_adjustments(user):
    return bool(
        user.is_superuser or user.role == 'administrator'
        or (user.can_fix_hrm_incomplete_attendance and user.can_view_hrm_incomplete_attendance)
    )


@login_required
def attendance_adjustments(request):
    """Page shell for Attendance Adjustments. All data loads via
    attendance_adjustments_list_ajax — see that view for the query logic."""
    from .models import AttendanceRecord, Department
    from dashboard.timezone_utils import get_nepali_now

    user = request.user
    if not _can_view_attendance_adjustments(user):
        messages.error(request, '❌ You do not have permission to access this page.', extra_tags='permission_denied')
        return redirect('dashboard')

    # Pull in the latest punches first, same as Attendance Records — otherwise
    # a record landed on directly here (not via the Incomplete Attendance
    # modal) could show a stale backlog until the next 60s background poll.
    try:
        _sync_biometric_to_attendance()
    except Exception:
        adms_logger.exception('attendance_adjustments: biometric auto-sync failed, showing existing records')

    base_qs = AttendanceRecord.objects.filter(is_deleted=False)
    incomplete_q = Q(clock_in__isnull=True) | Q(clock_out__isnull=True)
    incomplete_q &= ~Q(status__in=['absent', 'on_leave'])
    fixed_q = Q(last_fixed_at__isnull=False)

    context = {
        'page_title': 'Attendance Adjustments',
        'can_fix_adjustments': _can_fix_attendance_adjustments(user),
        'is_admin_user': bool(user.is_superuser or user.role == 'administrator'),
        'departments': Department.objects.filter(status='active').order_by('name'),
        'total_adjustments': base_qs.filter(incomplete_q | fixed_q).count(),
        'incomplete_count': base_qs.filter(incomplete_q).count(),
        'fixed_count': base_qs.filter(fixed_q).count(),
        'trashed_count': AttendanceRecord.objects.filter(is_deleted=True).count(),
        'today_nepal': str(get_nepali_now().date()),
    }
    return render(request, 'hrm/attendance_adjustments.html', context)


@login_required
def attendance_adjustments_list_ajax(request):
    """Data source for the Attendance Adjustments page: active records
    (incomplete and/or previously fixed, per status_filter) or the trash
    (view=trash), searchable/filterable and paginated at the DB level."""
    from .models import AttendanceRecord
    from datetime import datetime
    from dashboard.timezone_utils import format_nepali_datetime

    user = request.user
    if not _can_view_attendance_adjustments(user):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    view = request.GET.get('view', 'active')
    qs = AttendanceRecord.objects.filter(is_deleted=(view == 'trash'))

    if view != 'trash':
        status_filter = request.GET.get('status_filter', 'all')
        incomplete_q = Q(clock_in__isnull=True) | Q(clock_out__isnull=True)
        incomplete_q &= ~Q(status__in=['absent', 'on_leave'])
        fixed_q = Q(last_fixed_at__isnull=False)
        if status_filter == 'incomplete':
            qs = qs.filter(incomplete_q)
        elif status_filter == 'fixed':
            qs = qs.filter(fixed_q)
        else:
            qs = qs.filter(incomplete_q | fixed_q)

    search = request.GET.get('search', '').strip()
    if search:
        qs = qs.filter(
            Q(employee__full_name__icontains=search) |
            Q(employee__employee_id__icontains=search)
        )
    department = request.GET.get('department', '').strip()
    if department.isdigit():
        qs = qs.filter(employee__department_id=department)
    date_from = request.GET.get('date_from', '').strip()
    if date_from:
        try:
            qs = qs.filter(date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    date_to = request.GET.get('date_to', '').strip()
    if date_to:
        try:
            qs = qs.filter(date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass

    qs = qs.select_related(
        'employee', 'employee__department', 'shift', 'last_fixed_by', 'deleted_by'
    ).annotate(fix_logs_count=Count('fix_logs', distinct=True))
    qs = qs.order_by('-deleted_at', '-date') if view == 'trash' else qs.order_by('-date', 'employee__full_name')

    try:
        per_page = int(request.GET.get('per_page', 15))
    except (ValueError, TypeError):
        per_page = 15
    per_page = max(5, min(per_page, 100))

    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    results = []
    for rec in page_obj:
        if not rec.clock_in and not rec.clock_out:
            missing = 'both'
        elif not rec.clock_in:
            missing = 'clock_in'
        elif not rec.clock_out:
            missing = 'clock_out'
        else:
            missing = 'none'
        results.append({
            'id': rec.id,
            'employee_name': rec.employee.full_name,
            'employee_code': rec.employee.employee_id,
            'department': rec.employee.department.name if rec.employee.department else '—',
            'date': str(rec.date),
            'date_display': rec.date.strftime('%d %b %Y'),
            # Raw 24-hour value for the <input type="time"> edit field;
            # *_display is the 12-hour Nepal-style AM/PM string shown in the table.
            'clock_in': rec.clock_in.strftime('%H:%M') if rec.clock_in else '',
            'clock_out': rec.clock_out.strftime('%H:%M') if rec.clock_out else '',
            'clock_in_display': rec.clock_in.strftime('%I:%M %p') if rec.clock_in else '',
            'clock_out_display': rec.clock_out.strftime('%I:%M %p') if rec.clock_out else '',
            'missing': missing,
            'status': rec.status,
            'status_display': rec.get_status_display(),
            'shift_id': rec.shift_id,
            'shift_name': rec.shift.name if rec.shift else '—',
            'is_holiday': rec.is_holiday,
            'notes': rec.notes,
            'remarks': rec.last_fix_remarks or '',
            'fixed_by': (rec.last_fixed_by.get_full_name() or rec.last_fixed_by.username) if rec.last_fixed_by else '',
            'fixed_at': format_nepali_datetime(rec.last_fixed_at) if rec.last_fixed_at else '',
            'fix_logs_count': rec.fix_logs_count,
            'deleted_at': format_nepali_datetime(rec.deleted_at) if rec.deleted_at else '',
            'deleted_by': (rec.deleted_by.get_full_name() or rec.deleted_by.username) if rec.deleted_by else '',
        })

    return JsonResponse({
        'success': True,
        'count': paginator.count,
        'page': page_obj.number,
        'total_pages': paginator.num_pages,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
        'results': results,
    })


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


def _validate_shift_hours(start_time, end_time, break_duration, working_hours, half_day_hours):
    """Keep a shift's numbers consistent with its own clock.

    Working Time drives overtime (anything worked past it) and the report's
    hours are the punch span minus Break Duration, so a Working Time larger
    than the shift actually runs — or a break longer than the shift — makes
    every day recorded against it wrong in a way nothing downstream can
    detect. The form auto-fills Working Time from the times, but it lets the
    value be typed over and the server accepted whatever arrived; this is the
    guard that holds on the server, where it also covers the API.

    Returns an error message, or None when the shift is consistent.
    """
    from datetime import datetime, timedelta

    if working_hours <= 0:
        return 'Working Time must be a positive number of hours.'
    if half_day_hours <= 0:
        return 'Half day threshold must be a positive number.'
    if half_day_hours > working_hours:
        return "The Half Day threshold cannot exceed the shift's Working Time."
    if break_duration < 0:
        return 'Break duration cannot be negative.'

    if not (start_time and end_time):
        # Open-ended shift: nothing to measure Working Time against.
        return None

    def _as_time(value):
        if isinstance(value, str):
            return datetime.strptime(value[:5], '%H:%M').time()
        return value

    try:
        start = _as_time(start_time)
        end = _as_time(end_time)
    except (ValueError, TypeError):
        return 'Invalid shift start or end time.'

    today = datetime.today().date()
    span = datetime.combine(today, end) - datetime.combine(today, start)
    if span <= timedelta(0):
        span += timedelta(days=1)  # night shift crossing midnight
    span_hours = span.total_seconds() / 3600
    break_hours = break_duration / 60.0

    if break_hours >= span_hours:
        return (
            f'Break duration ({break_duration} min) cannot be as long as the '
            f'shift itself ({round(span_hours, 2)}h).'
        )

    net_hours = round(span_hours - break_hours, 2)
    if round(working_hours, 2) > net_hours + 0.01:
        return (
            f'Working Time ({round(working_hours, 2)}h) cannot exceed the '
            f'{round(span_hours, 2)}h the shift runs less its '
            f'{break_duration} min break — that is {net_hours}h.'
        )

    return None


@login_required
def shift_create(request):
    from .models import Shift
    if request.method == 'POST':
        try:
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
            half_day_hours = request.POST.get('half_day_hours', '4.0').strip() or '4.0'
            if not name or not start_time:
                return JsonResponse({'success': False, 'error': 'Name and start time are required.'})
            try:
                half_day_hours = float(half_day_hours)
            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'error': 'Invalid half day threshold provided.'})
            try:
                working_hours = float(working_hours)
                break_duration = int(break_duration)
            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'error': 'Invalid working hours or break duration provided.'})
            shift_error = _validate_shift_hours(
                start_time, end_time or None, break_duration, working_hours, half_day_hours
            )
            if shift_error:
                return JsonResponse({'success': False, 'error': shift_error})
            shift = Shift.objects.create(
                name=name,
                start_time=start_time,
                end_time=end_time if end_time else None,
                description=description,
                break_duration=break_duration,
                break_start_time=break_start_time,
                break_end_time=break_end_time,
                grace_period=int(grace_period),
                is_night_shift=is_night_shift,
                is_active=is_active,
                working_hours=working_hours,
                half_day_hours=half_day_hours,
            )
            return JsonResponse({'success': True, 'id': shift.id, 'name': shift.name})
        except Exception as e:
            hrm_logger.exception('shift_create failed')
            return JsonResponse({'success': False, 'error': 'Could not save shift: ' + str(e)}, status=500)
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def shift_update(request, pk):
    from .models import Shift
    try:
        shift = Shift.objects.get(pk=pk)
    except Shift.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'This shift no longer exists. It may have been deleted.'}, status=404)

    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            start_time = request.POST.get('start_time', '').strip()
            end_time = request.POST.get('end_time', '').strip()
            description = request.POST.get('description', '').strip()
            if not name or not start_time:
                return JsonResponse({'success': False, 'error': 'Name and start time are required.'})
            try:
                half_day_hours = float(request.POST.get('half_day_hours', '4.0') or '4.0')
            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'error': 'Invalid half day threshold provided.'})
            try:
                new_working_hours = float(request.POST.get('working_hours', '8.0') or '8.0')
                new_break_duration = int(request.POST.get('break_duration', '60') or '60')
            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'error': 'Invalid working hours or break duration provided.'})
            shift_error = _validate_shift_hours(
                start_time, end_time or None, new_break_duration, new_working_hours, half_day_hours
            )
            if shift_error:
                return JsonResponse({'success': False, 'error': shift_error})
            shift.name = name
            shift.start_time = start_time
            shift.end_time = end_time if end_time else None
            shift.description = description
            shift.break_duration = new_break_duration
            shift.break_start_time = request.POST.get('break_start_time', '').strip() or None
            shift.break_end_time = request.POST.get('break_end_time', '').strip() or None
            shift.grace_period = int(request.POST.get('grace_period', '15') or '15')
            shift.is_night_shift = request.POST.get('is_night_shift') == 'on'
            shift.is_active = request.POST.get('status', 'active') == 'active'
            shift.working_hours = new_working_hours
            shift.half_day_hours = half_day_hours
            shift.save()
            # A shift's times, break and working hours are the arithmetic
            # behind every day recorded against it (hours, overtime, the
            # late/early flags), and half_day_hours is the fallback Half Day
            # threshold — re-derive those days instead of leaving them on
            # the old numbers.
            updated = _safe_refresh_attendance(_refresh_records_for_shift, shift, 'shift_update')
            return JsonResponse({'success': True, 'records_updated': updated})
        except Exception as e:
            hrm_logger.exception('shift_update POST failed for shift %s', pk)
            return JsonResponse({'success': False, 'error': 'Could not save shift: ' + str(e)}, status=500)

    try:
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
            'half_day_hours': str(shift.half_day_hours),
        }
        return JsonResponse(data)
    except Exception as e:
        hrm_logger.exception('shift_update GET failed for shift %s', pk)
        return JsonResponse({'success': False, 'error': 'Could not load shift: ' + str(e)}, status=500)


@login_required
def shift_delete(request, pk):
    from .models import Shift
    try:
        shift = Shift.objects.get(pk=pk)
    except Shift.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'This shift no longer exists.'}, status=404)
    if request.method == 'POST':
        try:
            shift.delete()
            return JsonResponse({'success': True})
        except Exception as e:
            hrm_logger.exception('shift_delete failed for shift %s', pk)
            return JsonResponse({'success': False, 'error': 'Could not delete shift: ' + str(e)}, status=500)
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def shift_toggle_status(request, pk):
    from .models import Shift
    try:
        shift = Shift.objects.get(pk=pk)
    except Shift.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'This shift no longer exists.'}, status=404)
    try:
        shift.is_active = not shift.is_active
        shift.save()
        return JsonResponse({'success': True, 'is_active': shift.is_active})
    except Exception as e:
        hrm_logger.exception('shift_toggle_status failed for shift %s', pk)
        return JsonResponse({'success': False, 'error': 'Could not update shift: ' + str(e)}, status=500)


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
    # can_view_incomplete_attendance_alert / can_fix_incomplete_attendance /
    # incomplete_attendance_today are supplied globally by the
    # incomplete_attendance_alert context processor (dashboard/context_processors.py)
    # so the alert works the same way on every page, not just this one.
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
def incomplete_attendance_alert_settings(request):
    """Admin-only: configure how often the site-wide Incomplete Attendance
    alert toast re-appears (once per session / every refresh / custom interval)."""
    from .models import AttendanceAlertSettings

    user = request.user
    if not (user.is_superuser or user.role == 'administrator'):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    settings_obj = AttendanceAlertSettings.get_settings()

    if request.method != 'POST':
        return JsonResponse({
            'success': True,
            'mode': settings_obj.mode,
            'interval_minutes': settings_obj.interval_minutes,
        })

    mode = request.POST.get('mode', '').strip()
    valid_modes = [c[0] for c in AttendanceAlertSettings.MODE_CHOICES]
    if mode not in valid_modes:
        return JsonResponse({'success': False, 'error': 'Invalid alert mode.'}, status=400)

    interval_minutes = settings_obj.interval_minutes
    if mode == 'interval':
        try:
            interval_minutes = int(request.POST.get('interval_minutes', interval_minutes))
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Interval must be a whole number of minutes.'}, status=400)
        interval_minutes = max(1, min(interval_minutes, 1440))

    settings_obj.mode = mode
    settings_obj.interval_minutes = interval_minutes
    settings_obj.save()
    return JsonResponse({
        'success': True,
        'mode': settings_obj.mode,
        'interval_minutes': settings_obj.interval_minutes,
    })


@login_required
def attendance_sync_settings(request):
    """Admin-only: configure how often raw biometric punches are
    automatically re-aggregated into AttendanceRecord rows — a real data
    pull, distinct from incomplete_attendance_alert_settings above (which
    only controls how often the incomplete-attendance toast re-announces an
    already-known backlog, not when data is refreshed)."""
    from .models import AttendanceSyncSettings
    from dashboard.timezone_utils import format_nepali_datetime

    user = request.user
    if not (user.is_superuser or user.role == 'administrator'):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    settings_obj = AttendanceSyncSettings.get_settings()

    if request.method != 'POST':
        return JsonResponse({
            'success': True,
            'interval_minutes': settings_obj.interval_minutes,
            'last_synced_at': format_nepali_datetime(settings_obj.last_synced_at) if settings_obj.last_synced_at else None,
        })

    try:
        interval_minutes = int(request.POST.get('interval_minutes', settings_obj.interval_minutes))
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Interval must be a whole number of minutes.'}, status=400)
    interval_minutes = max(60, min(interval_minutes, 1440))

    settings_obj.interval_minutes = interval_minutes
    settings_obj.save()
    return JsonResponse({
        'success': True,
        'interval_minutes': settings_obj.interval_minutes,
        'last_synced_at': format_nepali_datetime(settings_obj.last_synced_at) if settings_obj.last_synced_at else None,
    })


def _parse_attendance_policy_form(request):
    """Shared numeric parsing/validation for the policy create/update forms.
    Returns (fields_dict, error_message) — error_message is None on success."""
    try:
        work_hours = float(request.POST.get('work_hours_per_day', 8) or 8)
        late_mark = int(request.POST.get('late_mark_after', 15) or 15)
        early_dep = int(request.POST.get('early_departure_grace', 15) or 15)
        overtime = float(request.POST.get('overtime_rate', 0) or 0)
        absent_th = float(request.POST.get('absent_threshold_hours', 2) or 2)
        half_day_th = float(request.POST.get('half_day_threshold_hours', 4) or 4)
    except (ValueError, TypeError):
        return None, 'Invalid numeric values provided.'
    if late_mark < 0 or early_dep < 0 or overtime < 0 or work_hours <= 0:
        return None, 'Values must be positive numbers.'
    if absent_th <= 0 or half_day_th <= 0:
        return None, 'Absent and Half Day thresholds must be positive numbers.'
    if absent_th >= half_day_th:
        return None, 'The Absent threshold must be lower than the Half Day threshold.'
    if half_day_th > work_hours:
        return None, 'The Half Day threshold cannot exceed Work Hours / Day.'
    return {
        'work_hours_per_day': work_hours,
        'late_mark_after': late_mark,
        'early_departure_grace': early_dep,
        'overtime_rate': overtime,
        'absent_threshold_hours': absent_th,
        'half_day_threshold_hours': half_day_th,
    }, None


@login_required
def attendance_policy_create(request):
    from .models import AttendancePolicy
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Policy name is required.'})
        if AttendancePolicy.objects.filter(name__iexact=name).exists():
            return JsonResponse({'success': False, 'error': 'A policy with this name already exists.'})
        fields, error = _parse_attendance_policy_form(request)
        if error:
            return JsonResponse({'success': False, 'error': error})
        policy = AttendancePolicy.objects.create(
            name=name,
            description=request.POST.get('description', '').strip(),
            is_active=(request.POST.get('is_active', 'true').lower() == 'true'),
            **fields,
        )
        # A new active policy can become the company-wide fallback, which
        # changes how already-saved days classify — re-label them now instead
        # of leaving the report on the old thresholds.
        updated = _safe_refresh_attendance(_refresh_records_for_policy, policy, 'attendance_policy_create')
        return JsonResponse({
            'success': True, 'id': policy.id, 'name': policy.name,
            'records_updated': updated,
        })
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
        fields, error = _parse_attendance_policy_form(request)
        if error:
            return JsonResponse({'success': False, 'error': error})
        policy.name = name
        policy.description = request.POST.get('description', '').strip()
        for field_name, value in fields.items():
            setattr(policy, field_name, value)
        policy.is_active = (request.POST.get('is_active', 'true').lower() == 'true')
        policy.save()
        # Thresholds are stored, not applied at render time: re-label every
        # day already saved under this policy (including hand-fixed rows the
        # biometric sync deliberately skips) so the Attendance Report and the
        # payroll counts agree with the thresholds just saved.
        updated = _safe_refresh_attendance(_refresh_records_for_policy, policy, 'attendance_policy_update')
        return JsonResponse({'success': True, 'records_updated': updated})
    data = {
        'id': policy.id,
        'name': policy.name,
        'description': policy.description,
        'work_hours_per_day': str(policy.work_hours_per_day),
        'late_mark_after': policy.late_mark_after,
        'early_departure_grace': policy.early_departure_grace,
        'overtime_rate': str(policy.overtime_rate),
        'absent_threshold_hours': str(policy.absent_threshold_hours),
        'half_day_threshold_hours': str(policy.half_day_threshold_hours),
        'is_active': policy.is_active,
    }
    return JsonResponse(data)


@login_required
def attendance_policy_delete(request, pk):
    from .models import AttendancePolicy, AttendanceRecord, Employee
    policy = get_object_or_404(AttendancePolicy, pk=pk)
    if request.method == 'POST':
        # Employees pointing at this policy fall back to the active
        # company-wide one once it is gone — recompute their days against
        # that, otherwise the report keeps the deleted policy's thresholds.
        affected_ids = list(
            Employee.objects
            .filter(Q(attendance_policy=policy) | Q(attendance_policy__isnull=True))
            .values_list('id', flat=True)
        )
        policy.delete()
        updated = 0
        if affected_ids:
            updated = _safe_refresh_attendance(
                _refresh_attendance_records,
                AttendanceRecord.objects.filter(employee_id__in=affected_ids),
                'attendance_policy_delete',
            )
        return JsonResponse({'success': True, 'records_updated': updated})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@login_required
def attendance_policy_toggle_status(request, pk):
    from .models import AttendancePolicy
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request.'})
    policy = get_object_or_404(AttendancePolicy, pk=pk)
    policy.is_active = not policy.is_active
    policy.save()
    # Activating/deactivating changes which policy is the fallback for
    # employees with none assigned, so their days can classify differently.
    updated = _safe_refresh_attendance(_refresh_records_for_policy, policy, 'attendance_policy_toggle_status')
    return JsonResponse({
        'success': True, 'is_active': policy.is_active, 'records_updated': updated,
    })


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

    from django.db.models import Count, Q as _Q
    counts = AttendanceRegularization.objects.aggregate(
        total=Count('id'),
        pending=Count('id', filter=_Q(status='pending')),
        approved=Count('id', filter=_Q(status='approved')),
        rejected=Count('id', filter=_Q(status='rejected')),
    )
    total_requests = counts['total']
    pending_count = counts['pending']
    approved_count = counts['approved']
    rejected_count = counts['rejected']
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
        raw_date = record.date if record else (request.POST.get('date') or None)
        if not raw_date:
            return JsonResponse({'success': False, 'error': 'Please select an attendance record or provide a date.'})

        date_val, date_error = _parse_attendance_date(raw_date)
        if date_error:
            return JsonResponse({'success': False, 'error': date_error})

        # A submitted request with neither time applies nothing on approval —
        # it would sit in the queue, get approved, and change nothing at all.
        # Drafts are exempt: a draft is explicitly work-in-progress.
        if not is_draft and not clock_in and not clock_out:
            return JsonResponse({
                'success': False,
                'error': 'Enter the requested Clock In and/or Clock Out time — '
                         'a regularization with no times has nothing to apply.',
            })

        if clock_in or clock_out:
            clock_error = _validate_clock_pair(clock_in, clock_out)
            if clock_error:
                return JsonResponse({'success': False, 'error': clock_error})

        # This page had no duplicate guard at all: two open requests for the
        # same employee+day are both approvable, and the second approval
        # overwrites the first while snapshotting already-regularized values
        # as the "pre-regularization" state to restore on reject.
        conflict, conflict_error = _find_conflicting_regularization(employee, date_val)
        if conflict:
            return JsonResponse({'success': False, 'error': conflict_error})

        # Keep the linked record consistent with the resolved date — a request
        # attached to a record for a different day would apply its times to
        # that other day on approval.
        if record and record.employee_id != employee.pk:
            return JsonResponse({
                'success': False,
                'error': 'The selected attendance record belongs to a different employee.',
            })

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
    from django.db import transaction

    reg = get_object_or_404(AttendanceRegularization, pk=pk)

    if request.method == 'POST':
        clock_in = request.POST.get('clock_in') or None
        clock_out = request.POST.get('clock_out') or None
        reason = request.POST.get('reason', '').strip()
        is_draft = request.POST.get('is_draft') == 'true'
        record_id = request.POST.get('attendance_record') or None

        if not reason:
            return JsonResponse({'success': False, 'error': 'Reason is required.'})

        if not is_draft and not clock_in and not clock_out:
            return JsonResponse({
                'success': False,
                'error': 'Enter the requested Clock In and/or Clock Out time — '
                         'a regularization with no times has nothing to apply.',
            })

        if clock_in or clock_out:
            clock_error = _validate_clock_pair(clock_in, clock_out)
            if clock_error:
                return JsonResponse({'success': False, 'error': clock_error})

        record = AttendanceRecord.objects.filter(pk=record_id).first() if record_id else reg.attendance_record

        if record and record.employee_id != reg.employee_id:
            return JsonResponse({
                'success': False,
                'error': 'The selected attendance record belongs to a different employee.',
            })

        # Re-pointing a request at a different day must not land it on a day
        # that already has an open or approved request — same rule the create
        # path enforces, excluding this request itself.
        target_date = record.date if record else reg.date
        conflict, conflict_error = _find_conflicting_regularization(
            reg.employee, target_date, exclude_pk=reg.pk
        )
        if conflict:
            return JsonResponse({'success': False, 'error': conflict_error})

        with transaction.atomic():
            reg.clock_in = clock_in if clock_in else None
            reg.clock_out = clock_out if clock_out else None
            reg.reason = reason
            reg.is_draft = is_draft
            reg.attendance_record = record
            if record:
                reg.date = record.date
            reg.save()

            # This request was already approved — its requested times just
            # changed, so the linked AttendanceRecord (and whatever the
            # attendance report shows) has to be re-synced to match, not left
            # holding whatever was saved at the original approval time.
            if reg.status == 'approved':
                # clock_in/clock_out were just assigned from raw POST strings
                # above — a TimeField only coerces those to real time objects
                # on load from the DB, so re-fetch before treating them as
                # such (_apply_regularization_to_record calls .strftime() on
                # them).
                reg.refresh_from_db()
                _apply_regularization_to_record(reg)

        return JsonResponse({'success': True})

    # GET — return JSON for edit modal
    data = {
        'id': reg.id,
        'employee_id': reg.employee_id,
        'employee_name': reg.employee.full_name,
        'attendance_record_id': reg.attendance_record_id,
        'date': str(reg.date),
        # 12-hour AM/PM strings for the read-only view-details panel;
        # *_raw is the 24-hour value the <input type="time"> edit fields need
        # (an HTML5 time input silently rejects a "09:01 AM"-style value).
        'clock_in': reg.clock_in.strftime('%I:%M %p') if reg.clock_in else '',
        'clock_out': reg.clock_out.strftime('%I:%M %p') if reg.clock_out else '',
        'clock_in_raw': reg.clock_in.strftime('%H:%M') if reg.clock_in else '',
        'clock_out_raw': reg.clock_out.strftime('%H:%M') if reg.clock_out else '',
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
        # An approved request has already been written onto the attendance
        # record; deleting the request must not leave that edit stranded.
        if reg.status == 'approved':
            _revert_regularization_from_record(reg)
        reg.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


def _apply_regularization_to_record(reg):
    """Push an (approved) regularization's requested clock_in/out onto its
    linked AttendanceRecord, recomputing status/hours the same way manual
    attendance edits do.

    Shared by the approve action and the edit action — an edit made to an
    already-approved request has to re-land on the record too, otherwise the
    attendance report keeps showing whatever was saved at the original
    approval time no matter what the request is edited to afterwards."""
    from .models import AttendanceRecord

    record = reg.attendance_record
    if record is None:
        # No linked record yet (e.g. a wholly missing day) — attach to an
        # existing record for that employee/date if one exists, otherwise
        # create one, so approval always lands somewhere the attendance
        # report will actually read from.
        record, _created_now = AttendanceRecord.objects.select_related(
            'employee__shift', 'employee__attendance_policy'
        ).get_or_create(employee=reg.employee, date=reg.date)
        _reg_record_existed = not _created_now
        reg.attendance_record = record
        reg.save(update_fields=['attendance_record'])
    else:
        _reg_record_existed = True
        record = AttendanceRecord.objects.select_related(
            'employee__shift', 'employee__attendance_policy'
        ).get(pk=record.pk)

    # Remember what the record looked like before we overwrite it, so
    # rejecting or deleting this request later can put it back. Only captured
    # on the first application -- re-approving an already-applied request must
    # not snapshot the regularized values over the genuine original.
    if not (reg.pre_regularization_state or {}):
        reg.pre_regularization_state = {
            'existed': _reg_record_existed,
            'clock_in': record.clock_in.strftime('%H:%M:%S') if record.clock_in else None,
            'clock_out': record.clock_out.strftime('%H:%M:%S') if record.clock_out else None,
            'status': record.status,
            'working_hours': str(record.working_hours or 0),
            'overtime_hours': str(record.overtime_hours or 0),
            'is_early_departure': record.is_early_departure,
            'is_late_arrival': record.is_late_arrival,
            'is_regularized': record.is_regularized,
        }
        reg.save(update_fields=['pre_regularization_state'])

    # Requested clock_in/out override the existing record's value;
    # a blank request field means "keep what's already there".
    new_clock_in = reg.clock_in or record.clock_in
    new_clock_out = reg.clock_out or record.clock_out
    clock_in_str = new_clock_in.strftime('%H:%M') if new_clock_in else None
    clock_out_str = new_clock_out.strftime('%H:%M') if new_clock_out else None

    status = record.status
    shift = record.shift or record.employee.shift
    policy = record.employee.effective_attendance_policy
    metrics = _compute_attendance_metrics(clock_in_str, clock_out_str, shift, policy, status)

    record.clock_in = new_clock_in
    record.clock_out = new_clock_out
    record.status = metrics['status']
    record.working_hours = metrics['working_hours']
    record.overtime_hours = metrics['overtime_hours']
    record.is_early_departure = metrics['is_early']
    record.is_late_arrival = metrics['is_late']
    # Mark as manually regularized so the biometric auto-sync
    # (attendance_list/report page load) never silently overwrites
    # this approved correction back to raw punch data.
    record.is_regularized = True
    record.save()


def _revert_regularization_from_record(reg):
    """Undo what _apply_regularization_to_record() wrote.

    Approving a regularization overwrites the attendance record and sets
    is_regularized=True, which also tells the biometric auto-sync never to
    touch that row again. Un-approving or deleting the request used to leave
    all of that in place, so a rejected correction stayed on the record
    permanently and the raw punch data could never restore it.

    Restores the snapshot taken at apply time; if the record did not exist
    before the regularization created it, removes it again. Returns True if
    anything was reverted.
    """
    from datetime import datetime as _dt
    from decimal import Decimal as _Dec

    state = reg.pre_regularization_state or {}
    record = reg.attendance_record
    if not state or record is None:
        return False

    if not state.get('existed', True):
        # The approval conjured this row into being -- take it away again.
        reg.attendance_record = None
        reg.pre_regularization_state = {}
        reg.save(update_fields=['attendance_record', 'pre_regularization_state'])
        record.delete()
        return True

    def _as_time(v):
        return _dt.strptime(v, '%H:%M:%S').time() if v else None

    record.clock_in = _as_time(state.get('clock_in'))
    record.clock_out = _as_time(state.get('clock_out'))
    record.status = state.get('status') or record.status
    record.working_hours = _Dec(str(state.get('working_hours') or 0))
    record.overtime_hours = _Dec(str(state.get('overtime_hours') or 0))
    record.is_early_departure = bool(state.get('is_early_departure'))
    record.is_late_arrival = bool(state.get('is_late_arrival'))
    # Hand the row back to the biometric sync unless it was already pinned
    # before this regularization touched it.
    record.is_regularized = bool(state.get('is_regularized'))
    record.save()

    reg.pre_regularization_state = {}
    reg.save(update_fields=['pre_regularization_state'])
    return True


@login_required
def attendance_regularization_update_status(request, pk):
    from .models import AttendanceRegularization
    from django.db import transaction

    reg = get_object_or_404(AttendanceRegularization, pk=pk)
    if request.method == 'POST':
        new_status = request.POST.get('status', '')
        if new_status not in ['pending', 'approved', 'rejected']:
            return JsonResponse({'success': False, 'error': 'Invalid status.'})

        approver = None
        try:
            approver = request.user.employee_profile
        except Exception:
            pass

        with transaction.atomic():
            if new_status == 'approved':
                _apply_regularization_to_record(reg)
            elif reg.status == 'approved':
                # Moving away from approved: take the correction back off the
                # attendance record instead of leaving it applied forever.
                _revert_regularization_from_record(reg)

            reg.status = new_status
            reg.approved_by = approver
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
    records = AttendanceRecord.objects.filter(employee_id=employee_id, is_deleted=False).order_by('-date')[:50]
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


# ==================== ATTLOG parsing / ingestion (shared) ====================
#
# The same rows reach us two ways: pushed live by the device over ADMS
# (iclock_cdata) and hand-carried on a USB stick as attlog.dat
# (biometric_upload_dat). Both funnel through the helpers below so PIN
# normalization, timezone handling and de-duplication can never drift apart
# between the two paths.

# Matches the timestamp anywhere in a row, tolerating the '/' separator and
# the missing-seconds form some firmware writes to attlog.dat.
_ATTLOG_TS_RE = re.compile(
    r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})[\sT]+(\d{1,2}):(\d{2})(?::(\d{2}))?'
)

# PIN / trailing flag columns are tab-separated over ADMS, but USB exports show
# up comma-, semicolon- or space-separated depending on the firmware build.
_ATTLOG_SEP_RE = re.compile(r'[,;\s]+')

# Limits for the manual attlog.dat upload.
_MAX_ATTLOG_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB — ~200k punch rows
_ATTLOG_UPLOAD_SUFFIXES = ('.dat', '.txt', '.csv', '.log')

# How far back a "Sync Device" click asks the device to replay its stored
# ATTLOG. 60 days comfortably covers a long outage while staying inside the
# flash buffer most K20-class devices actually retain.
FORCE_RESYNC_LOOKBACK_DAYS = 60


def _parse_attlog_line(line):
    """Parse one raw ATTLOG row into ``(pin, naive_datetime, status, verify)``.

    Returns None for anything without a usable PIN + timestamp (blank lines,
    header rows, trailing junk), so callers can count those as skipped rather
    than aborting the whole batch.
    """
    line = line.strip().lstrip('﻿')
    if not line:
        return None

    match = _ATTLOG_TS_RE.search(line)
    if not match:
        return None

    year, month, day, hour, minute, second = match.groups()
    try:
        punch_dt = datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second or 0)
        )
    except ValueError:
        # Real calendar-invalid values (month 13, Feb 30, hour 25).
        return None

    # The PIN is the last token before the timestamp — row numbers or export
    # labels prefixed by some firmware fall away naturally.
    head_tokens = [t for t in _ATTLOG_SEP_RE.split(line[:match.start()]) if t]
    if not head_tokens:
        return None
    pin = _normalize_pin(head_tokens[-1])[:50]
    if not pin:
        return None

    tail_tokens = [t for t in _ATTLOG_SEP_RE.split(line[match.end():]) if t]

    def _flag(index):
        if index < len(tail_tokens):
            try:
                return int(tail_tokens[index])
            except ValueError:
                return 0
        return 0

    # Column order matches what the device POSTs over ADMS: PIN, datetime,
    # status, verify mode, [workcode, reserved...].
    status = _flag(0)
    # Clamp to the declared STATUS_CHOICES range; an out-of-range value makes
    # get_status_display() return None and renders as a blank cell.
    if status not in (0, 1, 2, 3, 4, 5):
        status = 0

    return pin, punch_dt, status, _flag(1)


def _ingest_attlog_lines(lines, device=None, source='device'):
    """Persist raw ATTLOG rows as BiometricAttendance punches.

    Device rows carry Nepal local time (UTC+5:45) with no offset, so naive
    values are localized as NST before storage.

    Returns a stats dict: created / duplicates / skipped / pins / first / last.
    De-duplication is explicit (rather than per-row get_or_create) because
    ATTLOGStamp=0 makes the device re-push its entire buffer on every
    reconnect, and a USB attlog.dat is almost always mostly-already-imported.
    """
    from .models import BiometricAttendance
    from django.utils.timezone import is_aware
    import pytz

    nst = pytz.timezone('Asia/Kathmandu')

    pending = []
    seen = set()
    skipped = 0
    duplicates = 0

    for line in lines:
        parsed = _parse_attlog_line(line)
        if parsed is None:
            if line.strip():
                skipped += 1
            continue

        pin, naive_dt, status, verify = parsed
        punch_dt = naive_dt if is_aware(naive_dt) else nst.localize(naive_dt)

        key = (pin, punch_dt)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)

        pending.append(BiometricAttendance(
            device=device,
            pin=pin,
            timestamp=punch_dt,
            status=status,
            verify_mode=verify,
            raw_log=line.strip()[:2000],
        ))

    stats = {
        'created': 0,
        'duplicates': duplicates,
        'skipped': skipped,
        'pins': sorted({o.pin for o in pending}),
        'first': None,
        'last': None,
    }
    if not pending:
        return stats

    timestamps = [o.timestamp for o in pending]
    stats['first'] = min(timestamps)
    stats['last'] = max(timestamps)

    # One bounded query resolves what is already stored, instead of a SELECT
    # per row. Bounded by the batch's own PINs and time span so it stays cheap
    # even against a large punch table.
    already = set(
        BiometricAttendance.objects.filter(
            pin__in=stats['pins'],
            timestamp__gte=stats['first'],
            timestamp__lte=stats['last'],
        ).values_list('pin', 'timestamp')
    )

    to_create = [o for o in pending if (o.pin, o.timestamp) not in already]
    stats['duplicates'] += len(pending) - len(to_create)

    if to_create:
        # ignore_conflicts guards the race with a concurrent push of the same
        # rows; the unique (pin, timestamp) constraint is the real backstop.
        BiometricAttendance.objects.bulk_create(
            to_create, batch_size=1000, ignore_conflicts=True
        )
        stats['created'] = len(to_create)

    adms_logger.info(
        f"[ATTLOG:{source}] created={stats['created']} duplicates={stats['duplicates']} "
        f"skipped={stats['skipped']} pins={len(stats['pins'])}"
    )
    return stats


def _unmatched_pins(pins):
    """PINs from a batch that no Employee.employee_code maps to.

    Compared through _normalize_pin on both sides, so a device sending "05"
    still matches an employee coded "5" and is not reported as unmatched.
    """
    from .models import Employee

    if not pins:
        return []
    known = {
        _normalize_pin(code)
        for code in Employee.objects.exclude(employee_code='').exclude(
            employee_code__isnull=True
        ).values_list('employee_code', flat=True)
    }
    return sorted(p for p in pins if p not in known)


def _refresh_device_counts(device):
    """Recompute a device's cached transaction/user totals from stored punches."""
    from .models import BiometricAttendance

    if not device:
        return
    try:
        punches = BiometricAttendance.objects.filter(device=device)
        device.transaction_count = punches.count()
        device.user_count = punches.values('pin').distinct().count()
        device.save(update_fields=['transaction_count', 'user_count'])
    except Exception as e:
        adms_logger.error(f"[COUNTS] Failed to update counts for {device.serial_number}: {e}")


@csrf_exempt
def iclock_cdata(request):
    """
    GET  /iclock/cdata?SN=XXXX  → return device options (initial handshake)
    POST /iclock/cdata?SN=XXXX&table=ATTLOG → parse raw attendance, save to DB
    """
    from .models import ZKDevice

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
        # ATTLOGStamp/OPERATIONStamp=0 make the device treat its whole flash
        # buffer as un-uploaded and re-send it on (re)connect, so punches taken
        # while the internet was down are recovered instead of lost. Re-sending
        # already-stored rows is harmless: (pin, timestamp) is unique and
        # _ingest_attlog_lines drops the repeats.
        options = (
            "GET OPTION FROM: {sn}\r\n"
            "ATTLOGStamp=0\r\n"
            "OPERATIONStamp=0\r\n"
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
        table = request.GET.get('table', '').strip()
        try:
            body = request.body.decode('utf-8', errors='ignore').strip()
        except Exception:
            body = ''
        adms_logger.info(f"[CDATA POST] SN={sn} table={table} body_length={len(body)}")

        if table == 'ATTLOG' and body:
            try:
                stats = _ingest_attlog_lines(
                    body.splitlines(), device=device, source='push:' + sn
                )
                adms_logger.info(
                    f"[CDATA POST] SN={sn} saved={stats['created']} "
                    f"duplicate={stats['duplicates']} skipped={stats['skipped']}"
                )
                _refresh_device_counts(device)
            except Exception as e:
                # Never surface an error to the device: it would retry the
                # same batch forever and treat the server as down.
                adms_logger.exception(f"[CDATA POST] Ingest failed for SN={sn}: {e}")

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
    force_resync = False
    if sn:
        ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '')).split(',')[0].strip()
        try:
            device, created = ZKDevice.objects.get_or_create(serial_number=sn)
            device.ip_address = ip
            device.last_seen = timezone.now()
            update_fields = ['ip_address', 'last_seen']
            # Claim the pending resync request here (clearing it in the same
            # save) so only one heartbeat acts on an admin's Sync Device
            # click, even with several workers serving the device.
            if device.force_resync_requested_at:
                force_resync = True
                device.force_resync_requested_at = None
                update_fields.append('force_resync_requested_at')
            device.save(update_fields=update_fields)
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
    if force_resync:
        # An admin asked for a full pull: ask the device to replay its stored
        # ATTLOG for the window below. CHECK then makes it re-run its upload
        # cycle immediately instead of waiting for the next TransInterval.
        window_start = (now_nst - timedelta(days=FORCE_RESYNC_LOOKBACK_DAYS)).strftime('%Y-%m-%d 00:00:00')
        sync_cmd = (
            f'C:{seq}:SET TIME {nst_str}\r\n'
            f'C:{seq+1}:SET OPTION Date={nst_str}\r\n'
            f'C:{seq+2}:DATA QUERY ATTLOG StartTime={window_start}\tEndTime={nst_str}\r\n'
            f'C:{seq+3}:CHECK\r\n'
            f'OK'
        )
        adms_logger.info(
            f'[HEARTBEAT] Pushing DATA QUERY ATTLOG (since {window_start}) to SN={sn}'
        )
    else:
        sync_cmd = (
            f'C:{seq}:SET TIME {nst_str}\r\n'
            f'C:{seq+1}:SET OPTION Date={nst_str}\r\n'
            f'C:{seq+2}:DATE TIME {compact_str}\r\n'
            f'C:{seq+3}:CHECK\r\n'
            f'OK'
        )
        adms_logger.debug(f'[HEARTBEAT] Force-writing time to SN={sn}: {nst_str}')

    return HttpResponse(sync_cmd, content_type='text/plain')


@csrf_exempt
def iclock_devicecmd(request):
    """POST /iclock/devicecmd?SN=XXXX → device command response, always OK"""
    return HttpResponse('OK', content_type='text/plain')


# ==================== Biometric Attendance List (Admin) ====================

@login_required
def biometric_attendance(request):
    """Per-date attendance view: groups raw punches by (pin, date), defaults to today."""
    from .models import BiometricAttendance, Employee, ZKDevice
    from django.utils import timezone as tz
    import pytz

    local_tz = pytz.timezone('Asia/Kathmandu')
    today_local = tz.now().astimezone(local_tz).date()

    # Parse query params early
    search_q = request.GET.get('q', '').strip()

    # Default to today if no date filter is provided
    # Use a sentinel: if user explicitly set date_from/date_to (even blank), respect that.
    # We detect "no filter applied" by checking if both params are absent from GET.
    date_from_raw = request.GET.get('date_from', None)
    date_to_raw   = request.GET.get('date_to', None)

    # If this is a fresh page load with no date params at all, default to today
    if date_from_raw is None and date_to_raw is None and not search_q:
        date_from = today_local.strftime('%Y-%m-%d')
        date_to   = today_local.strftime('%Y-%m-%d')
        default_today = True
    else:
        date_from = (date_from_raw or '').strip()
        date_to   = (date_to_raw or '').strip()
        default_today = False

    # Build employee name lookup, keyed by normalized PIN (see _normalize_pin)
    emp_name_map = {}
    for emp in Employee.objects.all():
        if emp.employee_code:
            emp_name_map[_normalize_pin(emp.employee_code)] = emp.full_name

    # If the user entered an inverted range (From later than To), swap them
    # instead of silently returning zero results.
    if date_from and date_to:
        try:
            df_check = datetime.strptime(date_from, '%Y-%m-%d').date()
            dt_check = datetime.strptime(date_to, '%Y-%m-%d').date()
            if df_check > dt_check:
                date_from, date_to = date_to, date_from
        except ValueError:
            pass

    # Base queryset with date filters pushed into ORM.
    #
    # NOTE: Do NOT use `timestamp__date__gte`/`__lte` here. Those lookups compile to
    # DATE(CONVERT_TZ(timestamp, 'UTC', 'Asia/Kathmandu')) on MySQL, and CONVERT_TZ()
    # silently returns NULL unless the server's mysql.time_zone_name tables are loaded
    # (they are not, on this server) — which makes the WHERE clause evaluate to NULL
    # and the filter match ZERO rows every time, regardless of what data exists.
    # Instead, compute the Nepal-local day boundaries ourselves and filter on the
    # plain (timezone-aware) `timestamp` field, which Django converts to UTC in
    # Python before sending to the DB — no CONVERT_TZ involved.
    base_qs = BiometricAttendance.objects.all()

    if date_from:
        try:
            df = datetime.strptime(date_from, '%Y-%m-%d').date()
            start_dt = local_tz.localize(datetime.combine(df, datetime.min.time()))
            base_qs = base_qs.filter(timestamp__gte=start_dt)
        except ValueError:
            pass
    if date_to:
        try:
            dt_val = datetime.strptime(date_to, '%Y-%m-%d').date()
            end_dt = local_tz.localize(datetime.combine(dt_val, datetime.min.time())) + timedelta(days=1)
            base_qs = base_qs.filter(timestamp__lt=end_dt)
        except ValueError:
            pass

    # NOTE: PIN search is applied after grouping below (against the
    # normalized pin), not pushed into this queryset — the raw `pin` column
    # can still hold un-normalized (zero-padded) values from before punches
    # started being normalized at ingestion, so filtering on the raw column
    # here would silently miss rows that the grouping below would otherwise
    # correctly fold into the right employee/day.

    # BUG FIX: TruncDate() uses the DATABASE timezone (UTC), not Nepal time.
    # For punches near midnight NPT, this gives the wrong date.
    # We fetch raw timestamps and group by Nepal-timezone date in Python,
    # exactly like _sync_biometric_to_attendance() does — keeping them in sync.
    raw_qs = base_qs.select_related('device').values(
        'id', 'pin', 'timestamp', 'device__serial_number'
    ).order_by('pin', 'timestamp')

    from collections import defaultdict
    # grouped[(normalized_pin, nepal_date)] = {'punches': [ts,...], 'device_sn': str}
    # Grouping by the normalized PIN means two inconsistently-padded punches
    # for the same employee/day still land in the same group — see
    # _normalize_pin for why that matters.
    grouped = defaultdict(lambda: {'punches': [], 'device_sn': None})
    for row in raw_qs:
        ts = row['timestamp']
        if ts is None:
            continue
        nepal_date = ts.astimezone(local_tz).date()
        key = (_normalize_pin(row['pin']), nepal_date)
        grouped[key]['punches'].append(ts)
        if grouped[key]['device_sn'] is None and row['device__serial_number']:
            grouped[key]['device_sn'] = row['device__serial_number']

    # Build records list sorted by date desc, pin asc
    sq_lower = search_q.lower() if search_q else ''
    records = []
    for (pin, punch_date), info in grouped.items():
        employee_name = emp_name_map.get(pin, f'Employee {pin}')

        if sq_lower and sq_lower not in pin.lower() and sq_lower not in employee_name.lower():
            continue

        punches = info['punches']
        total = len(punches)
        # Same double-tap-aware derivation the aggregator uses, so this table
        # and the AttendanceRecord it produces always show the same times.
        clock_in_local, clock_out_local = _derive_clock_times(punches, local_tz)

        records.append({
            'pin': pin,
            'employee_name': employee_name,
            'date': punch_date,
            'clock_in': clock_in_local,
            'clock_out': clock_out_local,
            'total_entries': total,
            'device_sn': info['device_sn'] or '—',
        })

    # Sorting
    sort_by = request.GET.get('sort_by', '-date')
    if sort_by == 'date':
        records.sort(key=lambda r: (r['date'].toordinal(), r['employee_name'].lower()))
    elif sort_by == 'employee_name':
        records.sort(key=lambda r: (r['employee_name'].lower(), -r['date'].toordinal()))
    elif sort_by == '-employee_name':
        records.sort(key=lambda r: (r['employee_name'].lower(), r['date'].toordinal()), reverse=True)
    else:
        records.sort(key=lambda r: (-r['date'].toordinal(), r['employee_name'].lower()))

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

    # A "real" user-applied filter — excludes the automatic today-default,
    # so the UI can tell "no punches yet today" apart from "your filter matched nothing".
    has_filters = bool(search_q) or (bool(date_from or date_to) and not default_today)

    context = {
        'page_title': 'Biometric Attendance',
        'page_obj': page_obj,
        'total_records': total_records,
        'search_q': search_q,
        'date_from': date_from,
        'date_to': date_to,
        'sort_by': sort_by,
        'default_today': default_today,
        'has_filters': has_filters,
        'today_str': today_local.strftime('%Y-%m-%d'),
        'per_page': per_page,
        'per_page_options': [10, 20, 50, 100],
        # Offered in the attlog.dat upload modal so an imported batch can be
        # attributed to the device it came off (optional — punches import fine
        # without one).
        'devices': ZKDevice.objects.order_by('name', 'serial_number'),
    }
    return render(request, 'hrm/biometric_attendance.html', context)


@login_required
def biometric_attendance_view(request, pin, date_str):
    """Return all raw punches for a given employee PIN on a specific date (JSON)."""
    from .models import BiometricAttendance, Employee
    from django.utils import timezone as tz
    import pytz
    import traceback

    # Always return JSON — never let an exception propagate to an HTML 500 page
    try:
        local_tz = pytz.timezone('Asia/Kathmandu')

        try:
            punch_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid date format.'})

        # NOTE: Do NOT use `timestamp__date=punch_date` — see the comment on
        # biometric_attendance() above for why that silently matches zero rows
        # on this server. Use an explicit Nepal-local day range instead, same
        # as biometric_sync_single()/biometric_attendance_delete() below.
        start_of_day = local_tz.localize(datetime.combine(punch_date, datetime.min.time()))
        end_of_day = local_tz.localize(datetime.combine(punch_date, datetime.max.time()))

        # Match by normalized PIN, not an exact string, so this picks up both
        # punches even if one was saved with different zero-padding than the
        # other before ingestion started normalizing — see _normalize_pin.
        target_pin = _normalize_pin(pin)
        punches = [
            p for p in BiometricAttendance.objects.filter(
                timestamp__range=(start_of_day, end_of_day),
            ).select_related('device').order_by('timestamp')
            if _normalize_pin(p.pin) == target_pin
        ]

        emp = next(
            (e for e in Employee.objects.all() if e.employee_code and _normalize_pin(e.employee_code) == target_pin),
            None,
        )
        emp_name = emp.full_name if emp else f'Employee {pin}'

        punch_list = []
        punch_times = []
        for p in punches:
            try:
                local_ts = p.timestamp.astimezone(local_tz)
                punch_times.append(p.timestamp)
                punch_list.append({
                    'time': local_ts.strftime('%I:%M %p'),
                    'status': p.get_status_display() if hasattr(p, 'get_status_display') else str(p.status),
                    'verify_mode': p.verify_mode,
                    'device': p.device.serial_number if p.device else '—',
                    'raw_log': p.raw_log or '',
                })
            except Exception:
                continue

        punch_count = len(punch_list)
        # Derive from the raw timestamps (not the formatted strings) so the
        # double-tap guard in _derive_clock_times applies here too — otherwise
        # this modal would claim a clock-out the attendance record doesn't have.
        cin_t, cout_t = _derive_clock_times(punch_times, local_tz)
        clock_in  = cin_t.strftime('%I:%M %p')  if cin_t  else ''
        clock_out = cout_t.strftime('%I:%M %p') if cout_t else ''

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
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'})


@login_required
def biometric_sync_all(request):
    """Re-aggregate from raw logs and sync to attendance records."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        from .models import AttendanceRecord, BiometricAttendance
        count = BiometricAttendance.objects.count()
        _sync_biometric_to_attendance()
        days_count = AttendanceRecord.objects.filter(is_deleted=False).count()
        # Deliberately spells out "raw punches" vs "attendance days" — these
        # are different units (each day is usually 2+ punches), and a bare
        # "Re-aggregated from {count}..." next to the Attendance Records
        # page's own "Total Records" stat (which counts days) reads as a
        # mismatch/bug even when the sync worked correctly.
        return JsonResponse({
            'success': True,
            'message': (
                f'Re-aggregated {count} raw punches into {days_count} attendance-day records. Table refreshed.'
            ),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Sync failed: {str(e)}'})




@login_required
def biometric_sync_single(request, pin, date_str):
    """Re-aggregate a single employee's raw logs for a date — no API call needed."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        from .models import BiometricAttendance

        try:
            punch_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid date format.'})

        # Fix timezone issue here as well
        import pytz
        from django.utils import timezone
        local_tz = pytz.timezone('Asia/Kathmandu')
        start_of_day = local_tz.localize(datetime.combine(punch_date, datetime.min.time()))
        end_of_day = local_tz.localize(datetime.combine(punch_date, datetime.max.time()))

        target_pin = _normalize_pin(pin)
        count = sum(
            1 for p in BiometricAttendance.objects.filter(timestamp__range=(start_of_day, end_of_day)).only('pin')
            if _normalize_pin(p.pin) == target_pin
        )
        # Only sync back far enough to cover the record being refreshed — a
        # hardcoded 7-day window silently did nothing (while still reporting
        # "Synced successfully") when refreshing an older incomplete record.
        days_ago = (timezone.now().astimezone(local_tz).date() - punch_date).days
        _sync_biometric_to_attendance(recent_days=max(7, days_ago + 1))
        return JsonResponse({
            'success': True,
            'message': f'Found {count} raw punches for PIN {pin} on {date_str}. Synced successfully.',
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Sync failed: {str(e)}'})


@login_required
def biometric_upload_dat(request):
    """POST an attlog.dat exported from a ZKTeco device over USB and ingest it.

    This is the offline recovery path for when the device never reached the
    server at all (no internet at the branch, ADMS misconfigured, a device
    that was replaced). The file is parsed with the same helpers the live ADMS
    push uses, so an uploaded punch is indistinguishable from a pushed one and
    re-uploading the same file is a no-op.
    """
    from .models import ZKDevice

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    upload = request.FILES.get('attlog_file')
    if not upload:
        return JsonResponse({'success': False, 'error': 'No file selected. Choose an attlog.dat file to upload.'})

    name = (upload.name or '').strip()
    if not name.lower().endswith(_ATTLOG_UPLOAD_SUFFIXES):
        return JsonResponse({
            'success': False,
            'error': 'Unsupported file type. Upload the device export (.dat, .txt, .csv or .log).',
        })

    if upload.size == 0:
        return JsonResponse({'success': False, 'error': f'"{name}" is empty.'})

    if upload.size > _MAX_ATTLOG_UPLOAD_BYTES:
        limit_mb = _MAX_ATTLOG_UPLOAD_BYTES // (1024 * 1024)
        return JsonResponse({
            'success': False,
            'error': f'File is too large ({upload.size / (1024 * 1024):.1f} MB). Limit is {limit_mb} MB.',
        })

    # Devices write these files in whatever the firmware locale uses; latin-1
    # never raises, so it is the last-resort fallback that keeps a stray byte
    # in a name column from failing the whole import.
    raw = upload.read()
    text = None
    for encoding in ('utf-8-sig', 'utf-16', 'latin-1'):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None:
        return JsonResponse({'success': False, 'error': 'Could not read the file — it does not look like a text export.'})

    # A binary file (a .dat that is not an ATTLOG text dump) decodes under
    # latin-1 but is full of NULs; reject it rather than reporting "0 rows".
    if '\x00' in text[:4096]:
        return JsonResponse({
            'success': False,
            'error': 'That file is binary, not a text attendance log. Export ATTLOG (attlog.dat) from the device.',
        })

    device = None
    device_id = (request.POST.get('device_id') or '').strip()
    if device_id:
        device = ZKDevice.objects.filter(pk=device_id).first()
        if device is None:
            return JsonResponse({'success': False, 'error': 'Selected device no longer exists.'})

    try:
        stats = _ingest_attlog_lines(text.splitlines(), device=device, source=f'upload:{name}')
    except Exception as e:
        adms_logger.exception(f'[UPLOAD] Failed to ingest {name}: {e}')
        return JsonResponse({'success': False, 'error': f'Import failed while saving punches: {e}'})

    if stats['created'] == 0 and stats['duplicates'] == 0:
        return JsonResponse({
            'success': False,
            'error': (
                f'No attendance rows found in "{name}". '
                f'{stats["skipped"]} line(s) had no recognisable PIN and timestamp — '
                f'check you exported the attendance log rather than user or fingerprint data.'
            ),
        })

    _refresh_device_counts(device)

    # Fold the new punches into AttendanceRecord straight away, otherwise the
    # import looks like it did nothing until the next auto-sync interval.
    aggregation_error = None
    try:
        _sync_biometric_to_attendance()
    except Exception as e:
        aggregation_error = str(e)
        adms_logger.exception(f'[UPLOAD] Aggregation failed after importing {name}: {e}')

    unmatched = _unmatched_pins(stats['pins'])

    import pytz
    nst = pytz.timezone('Asia/Kathmandu')
    date_range = ''
    if stats['first'] and stats['last']:
        first_d = stats['first'].astimezone(nst).date()
        last_d = stats['last'].astimezone(nst).date()
        date_range = str(first_d) if first_d == last_d else f'{first_d} to {last_d}'

    parts = [f'Imported {stats["created"]} new punch(es) from "{name}"']
    if stats['duplicates']:
        parts.append(f'{stats["duplicates"]} already on file')
    if stats['skipped']:
        parts.append(f'{stats["skipped"]} unreadable line(s) skipped')
    if date_range:
        parts.append(f'covering {date_range}')
    message = ', '.join(parts) + '.'

    if unmatched:
        shown = ', '.join(unmatched[:10])
        more = f' and {len(unmatched) - 10} more' if len(unmatched) > 10 else ''
        # Punches for a PIN with no matching Employee.employee_code are stored
        # but produce no AttendanceRecord — silently dropping them is exactly
        # the kind of thing that shows up as "missing days" weeks later.
        message += f' No employee matches PIN {shown}{more} — those punches will not appear in Attendance Records until an employee has that employee code.'

    if aggregation_error:
        message += f' Punches were saved, but re-aggregating attendance failed: {aggregation_error}'

    adms_logger.info(
        f'[UPLOAD] {request.user} imported {name}: created={stats["created"]} '
        f'duplicates={stats["duplicates"]} skipped={stats["skipped"]} unmatched_pins={len(unmatched)}'
    )

    return JsonResponse({
        'success': True,
        'message': message,
        'stats': {
            'created': stats['created'],
            'duplicates': stats['duplicates'],
            'skipped': stats['skipped'],
            'employees': len(stats['pins']) - len(unmatched),
            'unmatched_pins': unmatched,
            'date_range': date_range,
        },
    })


@login_required
def biometric_attendance_delete(request, pin, date_str):
    """Delete all raw punches for a PIN on a specific date."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        from .models import BiometricAttendance

        try:
            punch_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid date format.'})

        import pytz
        from django.utils import timezone
        local_tz = pytz.timezone('Asia/Kathmandu')
        start_of_day = local_tz.localize(datetime.combine(punch_date, datetime.min.time()))
        end_of_day = local_tz.localize(datetime.combine(punch_date, datetime.max.time()))

        # Match by normalized PIN so this clears every punch shown in the
        # grouped row, even ones saved under a differently-padded pin string
        # (see _normalize_pin) — otherwise a "delete" from the UI could leave
        # some of that day's raw punches behind, ready to resurrect the record
        # on the next sync.
        target_pin = _normalize_pin(pin)
        ids_to_delete = [
            p.id for p in BiometricAttendance.objects.filter(timestamp__range=(start_of_day, end_of_day)).only('id', 'pin')
            if _normalize_pin(p.pin) == target_pin
        ]
        deleted, _ = BiometricAttendance.objects.filter(id__in=ids_to_delete).delete()
        return JsonResponse({'success': True, 'message': f'Deleted {deleted} records for PIN {pin} on {date_str}.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Delete failed: {str(e)}'})


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
    """Refresh cached device counts and request a full ATTLOG replay from the device."""
    from .models import ZKDevice

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})

    try:
        device = ZKDevice.objects.get(pk=pk)
    except ZKDevice.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Device not found.'})

    # Ask the device to replay its buffered ATTLOG on its next heartbeat. The
    # device answers asynchronously (within ~30s), so the rows land after this
    # response returns — the message below says so rather than implying the
    # pull already finished.
    device.force_resync_requested_at = timezone.now()
    device.save(update_fields=['force_resync_requested_at'])

    _refresh_device_counts(device)

    # Re-aggregate whatever raw punches are already stored so the attendance
    # table is current immediately, without waiting for the device round-trip.
    try:
        _sync_biometric_to_attendance()
    except Exception:
        # A stale/corrupt punch row must not turn Sync Device into a 500 — the
        # resync request above is already queued and is the point of the click.
        adms_logger.exception(
            f'zekto_device_sync: aggregation failed for SN={device.serial_number}'
        )

    return JsonResponse({
        'success': True,
        'message': (
            f'Device {device.serial_number} synced. Requested a {FORCE_RESYNC_LOOKBACK_DAYS}-day history pull — '
            f'buffered punches will arrive on the next device heartbeat (~30s).'
        ),
        'data': {
            'transaction_count': device.transaction_count,
            'user_count': device.user_count,
            'face_count': device.face_count,
            'fingerprint_count': device.fingerprint_count,
        }
    })



@login_required
def zekto_device_test(request, pk):
    """Test live connectivity and status of a specific ZKTeco device."""
    from .models import ZKDevice, BiometricAttendance
    from django.utils import timezone
    from datetime import datetime, timedelta
    import pytz

    try:
        device = ZKDevice.objects.get(pk=pk)
    except ZKDevice.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Device not found.'})

    now = timezone.now()
    local_tz = pytz.timezone('Asia/Kathmandu')
    now_local = now.astimezone(local_tz)

    last_seen_formatted = 'Never connected'
    last_seen_relative = 'Never'
    diff_seconds = None

    if device.last_seen:
        ls_local = device.last_seen.astimezone(local_tz)
        last_seen_formatted = ls_local.strftime('%Y-%m-%d %I:%M:%S %p')
        diff = now - device.last_seen
        diff_seconds = int(diff.total_seconds())

        if diff_seconds < 60:
            last_seen_relative = f'{diff_seconds}s ago'
        elif diff_seconds < 3600:
            last_seen_relative = f'{diff_seconds // 60}m ago'
        elif diff_seconds < 86400:
            last_seen_relative = f'{diff_seconds // 3600}h ago'
        else:
            last_seen_relative = f'{diff_seconds // 86400}d ago'

    if diff_seconds is not None and diff_seconds <= 180:
        status = 'connected'
        status_label = 'Connected / Online'
        status_class = 'success'
        status_detail = 'Device is actively communicating with the server (heartbeat healthy).'
    elif diff_seconds is not None and diff_seconds <= 900:
        status = 'idle'
        status_label = 'Idle / Warning'
        status_class = 'warning'
        status_detail = f'Device was seen {last_seen_relative}, but heartbeat is delayed.'
    else:
        status = 'disconnected'
        status_label = 'Disconnected / Failed'
        status_class = 'danger'
        status_detail = f'No recent heartbeat. Last connected: {last_seen_formatted} ({last_seen_relative}). Check power, network cable/Wi-Fi, and ADMS settings.'

    today_start = local_tz.localize(datetime.combine(now_local.date(), datetime.min.time()))
    today_punches = BiometricAttendance.objects.filter(device=device, timestamp__gte=today_start).count()

    return JsonResponse({
        'success': True,
        'device_id': device.id,
        'device_name': device.name or device.serial_number,
        'serial_number': device.serial_number,
        'model_name': device.model_name or 'ZKTeco ADMS',
        'ip_address': device.ip_address or '—',
        'status': status,
        'status_label': status_label,
        'status_class': status_class,
        'status_detail': status_detail,
        'last_seen': last_seen_formatted,
        'last_seen_relative': last_seen_relative,
        'today_punches': today_punches,
        'transaction_count': device.transaction_count,
        'user_count': device.user_count,
        'tested_at': now_local.strftime('%I:%M:%S %p NST'),
    })


@login_required
def zekto_device_test_all(request):
    """Test live connectivity and status of all ZKTeco devices."""
    from .models import ZKDevice, BiometricAttendance
    from django.utils import timezone
    from datetime import datetime, timedelta
    import pytz

    devices = ZKDevice.objects.all().order_by('name', 'serial_number')
    now = timezone.now()
    local_tz = pytz.timezone('Asia/Kathmandu')
    now_local = now.astimezone(local_tz)
    today_start = local_tz.localize(datetime.combine(now_local.date(), datetime.min.time()))

    results = []
    for dev in devices:
        diff_seconds = None
        last_seen_formatted = 'Never connected'
        last_seen_relative = 'Never'

        if dev.last_seen:
            ls_local = dev.last_seen.astimezone(local_tz)
            last_seen_formatted = ls_local.strftime('%Y-%m-%d %I:%M:%S %p')
            diff = now - dev.last_seen
            diff_seconds = int(diff.total_seconds())

            if diff_seconds < 60:
                last_seen_relative = f'{diff_seconds}s ago'
            elif diff_seconds < 3600:
                last_seen_relative = f'{diff_seconds // 60}m ago'
            elif diff_seconds < 86400:
                last_seen_relative = f'{diff_seconds // 3600}h ago'
            else:
                last_seen_relative = f'{diff_seconds // 86400}d ago'

        if diff_seconds is not None and diff_seconds <= 180:
            status = 'connected'
            status_label = 'Connected / Online'
            status_class = 'success'
            status_detail = 'Device actively communicating with server.'
        elif diff_seconds is not None and diff_seconds <= 900:
            status = 'idle'
            status_label = 'Idle / Warning'
            status_class = 'warning'
            status_detail = f'Seen {last_seen_relative}. Heartbeat delayed.'
        else:
            status = 'disconnected'
            status_label = 'Disconnected / Failed'
            status_class = 'danger'
            status_detail = f'No recent heartbeat. Last seen: {last_seen_formatted}.'

        today_punches = BiometricAttendance.objects.filter(device=dev, timestamp__gte=today_start).count()

        results.append({
            'device_id': dev.id,
            'device_name': dev.name or dev.serial_number,
            'serial_number': dev.serial_number,
            'model_name': dev.model_name or 'ZKTeco ADMS',
            'ip_address': dev.ip_address or '—',
            'status': status,
            'status_label': status_label,
            'status_class': status_class,
            'status_detail': status_detail,
            'last_seen': last_seen_formatted,
            'last_seen_relative': last_seen_relative,
            'today_punches': today_punches,
            'transaction_count': dev.transaction_count,
            'user_count': dev.user_count,
        })

    return JsonResponse({
        'success': True,
        'devices': results,
        'tested_at': now_local.strftime('%I:%M:%S %p NST'),
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

    page_runs = list(page_obj)
    bonus_stale_runs, totals_stale_runs = _attach_run_reflection(page_runs)

    context = {
        'page_title': 'Payroll Runs',
        'runs': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'frequency_filter': frequency_filter,
        'per_page': per_page,
        'bonus_stale_runs': bonus_stale_runs,
        'totals_stale_runs': totals_stale_runs,
        'bonus_stale_slips': sum(getattr(r, 'bonus_stale_count', 0) for r in page_runs),
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

    _attach_run_reflection([run])
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
            'bonus_total': str(run.bonus_total),
            'bonus_pending': str(run.bonus_pending),
            'bonus_stale_count': run.bonus_stale_count,
            'bonus_locked_count': run.bonus_locked_count,
            'advance_total': str(run.advance_total),
            'live_gross': str(run.live_gross),
            'live_net': str(run.live_net),
            'live_count': run.live_count,
            'totals_stale': run.totals_stale,
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
    # Deleting a run cascades to its payslips, which would silently destroy
    # finalized ones that payslip_delete refuses to touch.
    from .models import Payslip as _Payslip
    _finalized = _Payslip.objects.filter(payroll_run=run, is_finalized=True).count()
    if _finalized:
        return JsonResponse({
            'success': False,
            'error': f'This run has {_finalized} finalized payslip(s). Unlock them before deleting the run.',
        })

    # Same reasoning as payslip_delete: hand back the advance repayments these
    # payslips charged before the cascade removes the record of them. Skip
    # already-trashed slips -- soft-delete already reversed those once, and
    # doing it again would credit the employee's advance balance twice.
    for _slip in _Payslip.objects.filter(payroll_run=run, is_deleted=False):
        _reverse_advance_deductions(_slip)

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


def _calculate_payroll_breakdown(employee, cycle_start, cycle_end, salary_record, payroll_settings=None):
    """
    Core payroll calculation engine (two-layer architecture).

    LAYER 1: Attendance from actual calendar dates.
        CalendarDays  = (cycle_end - cycle_start).days + 1
        WeekendDays   = configured weekend days within cycle
        WorkingDays   = CalendarDays - WeekendDays - HolidayDays

    LAYER 2: Salary divisor — fully decoupled from attendance.
        SalaryDivisor = 30 (FIXED_30) or CalendarDays (ACTUAL_CYCLE_DAYS)
        DailyRate     = MonthlySalary / SalaryDivisor
        HourlyRate    = DailyRate / ShiftHoursPerDay

    Formulas:
        AbsentDays      = WorkingDays - PresentWorkingDays - PaidLeaveDays  (half-day=0.5)
        AbsentDeduction = AbsentDays * DailyRate
        WeekendPay      = WeekendWorkedDays * DailyRate * WeekendMultiplier
        HolidayPay      = HolidayWorkedDays * DailyRate * HolidayMultiplier
        OTPay           = HourlyRate * OTHours * OTMultiplier
        NetBasic        = MonthlySalary - AbsentDeduction
        TotalEarnings   = NetBasic + WeekendPay + HolidayPay + OTPay + Allowances
    """
    from decimal import Decimal, ROUND_HALF_UP
    from datetime import timedelta as _td
    from .models import (
        PayrollSetting as _PS, AttendanceRecord, EmployeeWeekend as _EmpWeekend,
        Holiday, LeaveRequest,
    )

    if payroll_settings is None:
        payroll_settings = _PS.get_settings()
    ps = payroll_settings

    _DAY_MAP = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2,
        'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6,
    }

    # ── LAYER 1: Attendance from actual calendar dates ──────────────────────
    calendar_days = (cycle_end - cycle_start).days + 1

    # Employee weekend config
    _emp_weekend = _EmpWeekend.objects.filter(
        employee=employee,
        weekend_type='weekend',
        effective_from__lte=cycle_end,
    ).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=cycle_start)
    ).order_by('-effective_from').first()
    weekend_day_nums = {
        _DAY_MAP[d.lower()] for d in (_emp_weekend.weekend_days or [])
        if d.lower() in _DAY_MAP
    } if _emp_weekend else {5, 6}

    # Count weekend days in cycle
    weekend_days_in_cycle = 0
    _d = cycle_start
    while _d <= cycle_end:
        if _d.weekday() in weekend_day_nums:
            weekend_days_in_cycle += 1
        _d += _td(days=1)

    # Build holiday date sets within cycle
    paid_holiday_dates = set()
    all_holiday_dates = set()
    for _h in Holiday.objects.filter(is_active=True):
        if _h.end_date >= cycle_start and _h.start_date <= cycle_end:
            _hd = max(_h.start_date, cycle_start)
            _he = min(_h.end_date, cycle_end)
            while _hd <= _he:
                all_holiday_dates.add(_hd)
                if _h.is_paid:
                    paid_holiday_dates.add(_hd)
                _hd += _td(days=1)

    # Attendance records for cycle
    att_records = AttendanceRecord.objects.filter(
        employee=employee, date__gte=cycle_start, date__lte=cycle_end, is_deleted=False
    ).order_by('date')

    # Merge attendance-flagged holidays
    att_holiday_dates = set(
        att_records.filter(is_holiday=True).values_list('date', flat=True).distinct()
    )
    all_holiday_dates = all_holiday_dates | att_holiday_dates

    # Holiday days = non-weekend holiday dates in cycle
    holiday_dates_non_weekend = {d for d in all_holiday_dates if d.weekday() not in weekend_day_nums}
    holiday_days_in_cycle = len(holiday_dates_non_weekend)
    working_days = max(calendar_days - weekend_days_in_cycle - holiday_days_in_cycle, 0)

    # Classify attendance records
    present_working_days = Decimal('0')
    paid_leave_days = Decimal('0')
    half_days_count = Decimal('0')
    weekend_worked_days = Decimal('0')
    holiday_worked_days = Decimal('0')
    total_ot_hours = Decimal('0')

    for _rec in att_records:
        _is_weekend = _rec.date.weekday() in weekend_day_nums
        _is_holiday = _rec.date in holiday_dates_non_weekend
        total_ot_hours += (_rec.overtime_hours or Decimal('0'))

        if _is_weekend:
            if _rec.status in ('present', 'late'):
                weekend_worked_days += 1
            elif _rec.status == 'half_day':
                weekend_worked_days += Decimal('0.5')
        elif _is_holiday:
            if _rec.status in ('present', 'late'):
                holiday_worked_days += 1
            elif _rec.status == 'half_day':
                holiday_worked_days += Decimal('0.5')
        else:
            if _rec.status in ('present', 'late'):
                present_working_days += 1
            elif _rec.status == 'on_leave':
                paid_leave_days += 1
            elif _rec.status == 'half_day':
                half_days_count += 1

    # Half-days: 0.5 present + 0.5 absent
    present_working_days += half_days_count * Decimal('0.5')

    # ── Approved leave with no attendance row of its own ────────────────────
    # paid_leave_days above only counts AttendanceRecord.status == 'on_leave',
    # and the only thing that ever writes that status is holiday_apply().
    # Approving a leave request creates no attendance row at all, so an
    # approved *paid* leave day fell straight through into absent_days and was
    # deducted from the employee's salary. Reconcile against LeaveRequest --
    # the same source the attendance report already reads -- so payroll and
    # the report agree. Unpaid leave types are deliberately left to fall
    # through as absent, which is what "unpaid" means.
    _att_by_date = {_r.date: _r for _r in att_records}
    _leave_qs = LeaveRequest.objects.filter(
        employee=employee, status='approved',
        start_date__lte=cycle_end, end_date__gte=cycle_start,
    ).select_related('leave_type')
    for _lr in _leave_qs:
        _is_paid_leave = _lr.leave_type.is_paid if _lr.leave_type else True
        if not _is_paid_leave:
            continue
        _ld = max(_lr.start_date, cycle_start)
        _lend = min(_lr.end_date, cycle_end)
        while _ld <= _lend:
            # Weekends and holidays are already outside working_days.
            if _ld.weekday() in weekend_day_nums or _ld in holiday_dates_non_weekend:
                _ld += _td(days=1)
                continue
            _lrec = _att_by_date.get(_ld)
            # Already accounted for: an explicit on_leave row, or they worked.
            if _lrec is not None and _lrec.status in ('present', 'late', 'half_day', 'on_leave'):
                _ld += _td(days=1)
                continue
            paid_leave_days += 1
            _ld += _td(days=1)

    # Sandwich rule -- must run BEFORE absent_days is derived.
    # It converts weekend/holiday days sandwiched between absences into working
    # days, which is the whole point: those days stop being paid. Deriving
    # absent_days first meant the rule moved working_days and nothing else, so
    # it never actually deducted anything, and the payslip printed an
    # attendance summary that contradicted itself -- e.g. Working Days 20,
    # Present 16, Absent 2, where 20 - 16 should leave 4.
    sandwich_days = 0
    if salary_record and getattr(salary_record, 'sandwich_rule', False):
        _sw = _count_sandwich_unpaid(
            cycle_start, cycle_end, weekend_day_nums,
            holiday_dates_non_weekend, list(att_records),
            count_up_to=cycle_end, paid_holiday_dates=paid_holiday_dates
        )
        sandwich_days = _sw['total_full']
        weekend_days_in_cycle = max(weekend_days_in_cycle - _sw['wknd_full'], 0)
        holiday_days_in_cycle = max(holiday_days_in_cycle - _sw['hol_full'], 0)
        working_days = max(calendar_days - weekend_days_in_cycle - holiday_days_in_cycle, 0)

    # AbsentDays = WorkingDays - PresentWorkingDays - PaidLeaveDays
    absent_days = max(
        Decimal(str(working_days)) - present_working_days - paid_leave_days,
        Decimal('0')
    )

    # ── LAYER 2: Salary divisor — decoupled from attendance ─────────────────
    basic_salary = Decimal('0')
    if salary_record:
        basic_salary = salary_record.basic_salary or Decimal('0')
    elif hasattr(employee, 'base_salary') and employee.base_salary:
        basic_salary = Decimal(str(employee.base_salary))

    if ps.salary_divisor_type == 'ACTUAL_CYCLE_DAYS':
        salary_divisor = Decimal(str(max(calendar_days, 1)))
        divisor_label = f'Actual {calendar_days} days'
    else:
        salary_divisor = Decimal('30')
        divisor_label = 'Fixed 30'

    daily_rate = (basic_salary / salary_divisor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    shift_hours = Decimal(str(ps.shift_hours_per_day)) if ps.shift_hours_per_day else Decimal('8')
    hourly_rate = (daily_rate / shift_hours).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP) if shift_hours > 0 else Decimal('0')

    absent_deduction = (absent_days * daily_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    weekend_multiplier = Decimal(str(ps.weekend_multiplier)) if ps.weekend_multiplier else Decimal('1.0')
    holiday_multiplier = Decimal(str(ps.holiday_multiplier)) if ps.holiday_multiplier else Decimal('1.0')
    
    weekend_pay = (Decimal(str(weekend_worked_days)) * daily_rate * weekend_multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    holiday_pay = (Decimal(str(holiday_worked_days)) * daily_rate * holiday_multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    pay_ot = salary_record.pay_ot if salary_record else False
    ot_pay = Decimal('0')
    if pay_ot and total_ot_hours > 0:
        ot_multiplier = Decimal(str(ps.ot_multiplier)) if ps.ot_multiplier else Decimal('1.5')
        ot_pay = (Decimal(str(total_ot_hours)) * hourly_rate * ot_multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    net_basic = max(basic_salary - absent_deduction, Decimal('0'))

    # Salary components (allowances + deductions)
    allowances_total = Decimal('0')
    other_deductions_total = Decimal('0')
    earnings_list = []
    deductions_list = []

    if salary_record:
        _comps = salary_record.components.filter(is_active=True)
        _pre_gross = net_basic + weekend_pay + holiday_pay + ot_pay
        _paid_wd = present_working_days + paid_leave_days
        for _comp in _comps:
            if _comp.component_type == 'earning':
                if _comp.calculation_type == 'fixed':
                    _pre_gross += _comp.amount
                elif _comp.calculation_type == 'variable' and salary_divisor > 0:
                    _pre_gross += min((_comp.amount / salary_divisor * _paid_wd).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), _comp.amount)
                elif _comp.calculation_type == 'percentage_of_basic':
                    _pre_gross += (net_basic * _comp.amount / Decimal('100')).quantize(Decimal('0.01'))

        for _comp in _comps:
            if _comp.calculation_type == 'fixed':
                _ca = _comp.amount
                _dname = _comp.name
            elif _comp.calculation_type == 'variable':
                if salary_divisor > 0:
                    _ca = (_comp.amount / salary_divisor * _paid_wd).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    _ca = min(_ca, _comp.amount)
                else:
                    _ca = Decimal('0')
                _dname = f"{_comp.name} ({_paid_wd}/{salary_divisor} days)"
            elif _comp.calculation_type == 'percentage_of_basic':
                _ca = (net_basic * _comp.amount / Decimal('100')).quantize(Decimal('0.01'))
                _dname = _comp.name
            elif _comp.calculation_type in ('percentage_of_gross', 'percentage_of_ctc'):
                _ca = (_pre_gross * _comp.amount / Decimal('100')).quantize(Decimal('0.01'))
                _dname = _comp.name
            else:
                _ca = _comp.amount
                _dname = _comp.name

            if _comp.component_type == 'earning':
                earnings_list.append({'name': _dname, 'amount': _ca})
                allowances_total += _ca
            else:
                deductions_list.append({'name': _dname, 'amount': _ca})
                other_deductions_total += _ca

    total_earnings = (net_basic + weekend_pay + holiday_pay + ot_pay + allowances_total).quantize(Decimal('0.01'))

    return {
        # Cycle
        'cycle_start': cycle_start,
        'cycle_end': cycle_end,
        'calendar_days': calendar_days,
        'salary_divisor': salary_divisor,
        'divisor_label': divisor_label,
        # Attendance (Layer 1)
        'weekend_day_nums': weekend_day_nums,
        'holiday_dates_non_weekend': holiday_dates_non_weekend,
        'paid_holiday_dates': paid_holiday_dates,
        'weekend_days': weekend_days_in_cycle,
        'holiday_days': holiday_days_in_cycle,
        'working_days': working_days,
        'present_working_days': present_working_days,
        'paid_leave_days': paid_leave_days,
        'half_days': half_days_count,
        'absent_days': absent_days,
        'weekend_worked_days': weekend_worked_days,
        'holiday_worked_days': holiday_worked_days,
        'total_ot_hours': total_ot_hours,
        'sandwich_days': sandwich_days,
        'att_records': att_records,
        # Salary (Layer 2)
        'basic_salary': basic_salary,
        'daily_rate': daily_rate,
        'hourly_rate': hourly_rate,
        'absent_deduction': absent_deduction,
        'weekend_pay': weekend_pay,
        'holiday_pay': holiday_pay,
        'ot_pay': ot_pay,
        'pay_ot': pay_ot,
        'net_basic': net_basic,
        'allowances_total': allowances_total,
        'other_deductions_total': other_deductions_total,
        'earnings_list': earnings_list,
        'deductions_list': deductions_list,
        'total_earnings': total_earnings,
        'weekend_multiplier': weekend_multiplier,
        'holiday_multiplier': holiday_multiplier,
        'ot_multiplier': ot_multiplier if 'ot_multiplier' in locals() else Decimal('1.5'),
    }


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


from django.db import transaction


def _refresh_payroll_run_totals(run):
    """Re-derive a PayrollRun's employee_count / gross_pay / net_pay from its
    payslips.

    These are denormalised columns rendered straight onto the Payroll Runs
    page. They used to be written only by generate_payslips(), so every later
    change to a payslip -- a bonus approval, a manual adjustment, an advance
    sync, a deletion -- left the run showing stale money. Call this after any
    of those.
    """
    from .models import Payslip
    from django.db.models import Sum as _Sum

    if run is None:
        return
    agg = Payslip.objects.filter(payroll_run=run, is_deleted=False).aggregate(
        cnt=Count('id'), g=_Sum('gross_salary'), n=_Sum('net_salary'),
    )
    run.employee_count = agg['cnt'] or 0
    run.gross_pay = agg['g'] or Decimal('0')
    run.net_pay = agg['n'] or Decimal('0')
    _fields = ['employee_count', 'gross_pay', 'net_pay', 'updated_at']
    if run.status in ('draft', 'processing') and run.employee_count > 0:
        run.status = 'completed'
        _fields.append('status')
    run.save(update_fields=_fields)


def _reverse_advance_deductions(slip):
    """Hand back the advance repayment that this payslip recorded.

    generate_payslips() advances each active AdvancePayment's amount_repaid /
    paid_installments when it builds a payslip. Nothing used to undo that, so
    deleting a payslip and regenerating it -- an ordinary correction workflow
    -- charged the employee the same installment twice and cleared the advance
    early. The per-advance amounts are stored on the payslip snapshot at
    generation so they can be reversed exactly rather than guessed at.

    Returns the number of advances credited back.
    """
    from .models import AdvancePayment

    breakdown = (slip.salary_structure or {}).get('advance_breakdown') or {}
    if not breakdown:
        return 0

    reversed_count = 0
    for _adv_id, _amt in breakdown.items():
        try:
            amt = Decimal(str(_amt))
            adv = AdvancePayment.objects.filter(pk=int(_adv_id)).first()
        except (TypeError, ValueError, ArithmeticError):
            continue
        if adv is None or amt <= 0:
            continue
        new_repaid = max((adv.amount_repaid or Decimal('0')) - amt, Decimal('0'))
        new_status = adv.status
        if adv.status == 'cleared' and new_repaid < (adv.amount or Decimal('0')):
            new_status = 'repaying'
        AdvancePayment.objects.filter(pk=adv.pk).update(
            amount_repaid=new_repaid,
            paid_installments=max((adv.paid_installments or 0) - 1, 0),
            status=new_status,
        )
        reversed_count += 1
    return reversed_count


@login_required
@transaction.atomic
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

    # ── Determine cycle dates from actual pay_period_start/end ──
    # Never hardcode monthrange — CalendarDays = (end - start).days + 1
    if run.pay_period_start and run.pay_period_end:
        _cycle_start = run.pay_period_start
        _cycle_end = run.pay_period_end
    else:
        import calendar as _cal
        _month = run.month or today.month
        _year = run.year or today.year
        _cycle_start = datetime.date(_year, _month, 1)
        _cycle_end = datetime.date(_year, _month, _cal.monthrange(_year, _month)[1])

    _month = _cycle_start.month
    _year = _cycle_start.year

    # Load PayrollSetting once for all employees
    from .models import PayrollSetting as _PayrollSetting
    _ps = _PayrollSetting.get_settings()

    _DAY_MAP = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2,
        'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6,
    }

    from .models import Holiday
    _paid_holiday_dates = set()
    for h in Holiday.objects.filter(is_paid=True, is_active=True):
        if h.end_date >= _cycle_start and h.start_date <= _cycle_end:
            d = max(h.start_date, _cycle_start)
            end = min(h.end_date, _cycle_end)
            while d <= end:
                _paid_holiday_dates.add(d)
                d += timedelta(days=1)

    for employee in employees:
        # Skip if payslip already exists AND is finalized for this run + employee
        _existing_slip = Payslip.objects.filter(payroll_run=run, employee=employee).first()
        if _existing_slip:
            skipped_count += 1
            continue
            
        # Prevent duplicate runs for same cycle
        _dup_slip = Payslip.objects.filter(employee=employee, payroll_run__month=_month, payroll_run__year=_year).exclude(payroll_run=run).exists()
        if _dup_slip:
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

        # ── Run the shared two-layer payroll calculation engine ────────────────────────
        _bd = _calculate_payroll_breakdown(
            employee=employee,
            cycle_start=_cycle_start,
            cycle_end=_cycle_end,
            salary_record=salary_record,
            payroll_settings=_ps,
        )

        # ── Approved Bonuses for this employee/month/year ──
        from .models import Bonus as _Bonus
        # Must match the filter used by _sync_bonus_to_payslip() and the
        # print view below -- both count 'approved' AND 'paid'. Counting only
        # 'approved' here meant a bonus marked paid before the run was left out
        # of gross at generation, then re-added as a "new" delta on the first
        # download, inflating the slip.
        _bonus_qs = _Bonus.objects.filter(
            employee=employee,
            month=_month,
            year=_year,
            status__in=['approved', 'paid'],
        )
        _bonus_total = sum(b.amount for b in _bonus_qs) or Decimal('0')
        _bonus_total = Decimal(str(_bonus_total)).quantize(Decimal('0.01'))

        # gross = net_basic + weekend_pay + holiday_pay + ot_pay + allowances + bonus
        gross_salary = (_bd['total_earnings'] + _bonus_total).quantize(Decimal('0.01'))
        total_deductions_val = _bd['other_deductions_total'].quantize(Decimal('0.01'))

        # ── Advance payment deductions ──
        from .models import AdvancePayment as _AdvPay
        active_advances = _AdvPay.objects.filter(
            employee=employee,
            status__in=['disbursed', 'repaying']
        )
        advance_deduction = Decimal('0')
        # Keep each advance's share so the write-back below doesn't recompute
        # it, and so deleting this payslip can credit back exactly what it took.
        _adv_breakdown = {}
        for adv in active_advances:
            remaining = max(adv.amount - adv.amount_repaid, Decimal('0'))
            inst = adv.installment_amount or Decimal('0')
            if adv.repayment_mode in ('salary_deduction', 'installments') or not adv.repayment_mode:
                _ded = min(inst if inst > 0 else remaining, remaining)
            elif adv.repayment_mode == 'lump_sum':
                _ded = remaining
            else:
                _ded = min(inst if inst > 0 else remaining, remaining)
            _ded = _ded.quantize(Decimal('0.01'))
            if _ded > 0:
                _adv_breakdown[str(adv.pk)] = str(_ded)
            advance_deduction += _ded
        advance_deduction = advance_deduction.quantize(Decimal('0.01'))

        net_salary = max(
            gross_salary - total_deductions_val - advance_deduction,
            Decimal('0')
        )

        def _serialize_list(lst):
            return [{'name': item['name'], 'amount': float(item['amount'])} for item in lst]

        Payslip.objects.create(
            payroll_run=run,
            employee=employee,
            gross_salary=gross_salary,
            total_deductions=total_deductions_val,
            advance_deduction=advance_deduction,
            absent_deduction=_bd['absent_deduction'],
            net_salary=net_salary,
            basic_salary=_bd.get('basic_salary', Decimal('0')),
            salary_structure={
                'earnings_list': _serialize_list(_bd.get('earnings_list', [])),
                'deductions_list': _serialize_list(_bd.get('deductions_list', [])),
                'bonus_total_included': str(_bonus_total),
                'gross_before_bonus': str(_bd['total_earnings'].quantize(Decimal('0.01'))),
                'advance_breakdown': _adv_breakdown,
            },
            status='generated',
            generated_on=today,
        )
        created_count += 1

        # ── Mark approved bonuses as paid ──
        _bonus_qs.update(status='paid')

        # ── Update advance records: apply this period's deductions ──
        # Reuse the shares computed above rather than deriving them a second
        # time -- two copies of this arithmetic can drift apart, and the
        # snapshot written onto the payslip has to match what is charged here.
        from django.db.models import F as _F
        for adv in active_advances:
            _ded = Decimal(_adv_breakdown.get(str(adv.pk), '0'))
            if _ded > 0:
                _new_repaid = adv.amount_repaid + _ded
                _new_status = 'cleared' if _new_repaid >= adv.amount else 'repaying'
                _AdvPay.objects.filter(pk=adv.pk).update(
                    amount_repaid=_new_repaid,
                    paid_installments=_F('paid_installments') + 1,
                    status=_new_status,
                )

    # Refresh run totals from all payslips (including previously existing ones)
    _refresh_payroll_run_totals(run)

    msg = f'Generated {created_count} payslip(s) successfully.'
    if skipped_count:
        msg += f' {skipped_count} already existed and were skipped.'

    return JsonResponse({
        'success': True,
        'message': msg,
        'created': created_count,
        'skipped': skipped_count,
    })


BONUS_COUNTED_STATUSES = ('approved', 'paid')


def _payslip_period_key(slip):
    """The (month, year) a payslip's bonuses are filed under.

    Bonuses are recorded per employee-month, and generation files a payslip
    under its *cycle start* -- `pay_period_start` when the run carries one,
    otherwise the run's own month/year. A Shrawan run spanning 17 Jul - 16 Aug
    is a July payslip here, which is exactly what `_sync_bonus_to_payslip`
    matches on, so both sides agree on which bonuses belong to which slip.
    """
    run = slip.payroll_run
    if run is None:
        return None, None
    if run.pay_period_start:
        return run.pay_period_start.month, run.pay_period_start.year
    if run.month and run.year:
        return run.month, run.year
    return None, None


def _attach_bonus_state(slips):
    """Annotate payslips with their stored vs. live bonus totals.

    ``salary_structure['bonus_total_included']`` is the payroll engine's record
    of how much bonus is already inside `gross_salary`. Comparing it with the
    live approved/paid bonus total for the same employee-month is what makes a
    bonus approved *after* the run visible on this page instead of silently
    absent from the payslip.

    A slip with no marker at all predates that bookkeeping: it cannot prove it
    included anything, so it counts as stale whenever a bonus exists. Syncing
    such a slip runs the healer, which either stamps it (no money moves) or
    repairs it. Read-only and query-cheap -- no payroll recomputation here.

    Returns the number of stale slips.
    """
    from .models import Bonus

    keys = {}
    for slip in slips:
        month, year = _payslip_period_key(slip)
        keys[slip.pk] = (slip.employee_id, month, year)

    employee_ids = {eid for eid, m, _y in keys.values() if m}
    live = {}
    if employee_ids:
        rows = (
            Bonus.objects
            .filter(employee_id__in=employee_ids, status__in=BONUS_COUNTED_STATUSES)
            .values('employee_id', 'month', 'year')
            .annotate(total=Sum('amount'))
        )
        for row in rows:
            live[(row['employee_id'], row['month'], row['year'])] = (
                row['total'] or Decimal('0')
            ).quantize(Decimal('0.01'))

    stale_count = 0
    for slip in slips:
        key = keys[slip.pk]
        slip.bonus_live = live.get(key, Decimal('0')) if key[1] else Decimal('0')

        struct = slip.salary_structure if isinstance(slip.salary_structure, dict) else {}
        marker = struct.get('bonus_total_included')
        if marker is None:
            slip.bonus_included = None
        else:
            try:
                slip.bonus_included = Decimal(str(marker)).quantize(Decimal('0.01'))
            except (InvalidOperation, ValueError):
                slip.bonus_included = None

        if slip.bonus_included is None:
            slip.bonus_stale = slip.bonus_live > 0
        else:
            slip.bonus_stale = slip.bonus_included != slip.bonus_live
        # What the payslip is actually paying out right now, which is the
        # marker when there is one -- never the live figure, or the column
        # would show money the slip has not been credited with yet.
        slip.bonus_amount = slip.bonus_included if slip.bonus_included is not None else Decimal('0')
        if slip.bonus_stale and not slip.is_finalized:
            stale_count += 1
    return stale_count


@login_required
def payslip_list(request):
    from .models import Payslip, AdvancePayment
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')
    per_page = request.GET.get('per_page', '10')
    view = request.GET.get('view', 'active')
    is_trash = (view == 'trash')

    payslips = Payslip.objects.select_related('employee', 'payroll_run', 'deleted_by').filter(is_deleted=is_trash)

    if search_query:
        payslips = payslips.filter(
            Q(employee__full_name__icontains=search_query) |
            Q(payslip_number__icontains=search_query)
        )
    if status_filter and not is_trash:
        payslips = payslips.filter(status=status_filter)

    payslips = payslips.order_by('-deleted_at', '-created_at') if is_trash else payslips.order_by('-created_at')

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

    # Bonuses approved after the run was generated live in the Bonus table but
    # not yet in the payslip's gross -- same shape of drift the advance sync
    # fixes, and shown the same way.
    bonus_stale_count = _attach_bonus_state(page_slips)

    user = request.user
    is_admin_user = bool(user.is_superuser or user.role == 'administrator')

    context = {
        'page_title': 'Payslips',
        'payslips': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'per_page': per_page,
        'stale_count': 0 if is_trash else stale_count,
        'bonus_stale_count': 0 if is_trash else bonus_stale_count,
        'view': view,
        'is_trash': is_trash,
        'trashed_count': Payslip.objects.filter(is_deleted=True).count(),
        'is_admin_user': is_admin_user,
    }
    return render(request, 'hrm/payslip_list.html', context)


@login_required
def payslip_sync_advances(request):
    """Recalculate and save advance_deduction + net_salary for all payslips."""
    from .models import Payslip, AdvancePayment
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    # Finalized payslips are locked from edits everywhere else (adjustments,
    # bonus sync, delete) -- don't let a bulk advance sync quietly rewrite
    # their net salary.
    payslips = list(Payslip.objects.select_related('employee').filter(is_finalized=False, is_deleted=False))
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
        # Net salary moved, so the Payroll Runs page totals have to follow.
        from .models import PayrollRun as _PayrollRun
        for _run in _PayrollRun.objects.filter(
            pk__in={s.payroll_run_id for s in to_update}
        ):
            _refresh_payroll_run_totals(_run)

    return JsonResponse({
        'success': True,
        'updated': updated,
        'message': f'{updated} payslip(s) updated.' if updated else 'All payslips are already up to date.',
    })


def _attach_run_reflection(runs):
    """Annotate payroll runs with what their own payslips actually add up to.

    A run's `employee_count` / `gross_pay` / `net_pay` are denormalised columns
    written when payslips are generated and refreshed by
    `_refresh_payroll_run_totals()`. Anything that edits a payslip without
    calling that leaves the run showing money its payslips no longer hold, so
    the live aggregate is computed here and the difference is surfaced rather
    than papered over.

    Also carries the bonus picture down to the run: what its payslips already
    include, and how much approved bonus is still waiting for a sync.

    One query for the payslips plus one for the bonus totals, whatever the page
    size. Returns ``(bonus_stale_runs, totals_stale_runs)``.
    """
    from .models import Payslip

    run_ids = [run.pk for run in runs]
    slips = []
    if run_ids:
        slips = list(
            Payslip.objects
            .filter(payroll_run_id__in=run_ids, is_deleted=False)
            .select_related('payroll_run')
            .only(
                'id', 'employee_id', 'payroll_run_id', 'salary_structure',
                'gross_salary', 'net_salary', 'advance_deduction', 'is_finalized',
                'payroll_run__pay_period_start', 'payroll_run__month',
                'payroll_run__year',
            )
        )
    _attach_bonus_state(slips)

    by_run = {}
    for slip in slips:
        by_run.setdefault(slip.payroll_run_id, []).append(slip)

    bonus_stale_runs = 0
    totals_stale_runs = 0
    for run in runs:
        rows = by_run.get(run.pk, [])
        run.live_count = len(rows)
        cents = Decimal('0.01')
        run.live_gross = sum((r.gross_salary for r in rows), Decimal('0')).quantize(cents)
        run.live_net = sum((r.net_salary for r in rows), Decimal('0')).quantize(cents)
        run.advance_total = sum((r.advance_deduction for r in rows), Decimal('0')).quantize(cents)
        run.bonus_total = sum((r.bonus_amount for r in rows), Decimal('0')).quantize(cents)
        run.bonus_stale_count = sum(
            1 for r in rows if r.bonus_stale and not r.is_finalized)
        run.bonus_locked_count = sum(
            1 for r in rows if r.bonus_stale and r.is_finalized)
        # What syncing would add (or take back, if a bonus was withdrawn).
        run.bonus_pending = sum(
            ((r.bonus_live - r.bonus_amount) for r in rows
             if r.bonus_stale and not r.is_finalized),
            Decimal('0'),
        ).quantize(cents)
        run.totals_stale = (
            run.employee_count != run.live_count
            or run.gross_pay != run.live_gross
            or run.net_pay != run.live_net
        )
        if run.bonus_stale_count:
            bonus_stale_runs += 1
        if run.totals_stale:
            totals_stale_runs += 1

    return bonus_stale_runs, totals_stale_runs


def _refresh_stale_run_totals(run_ids=None):
    """Re-derive the stored totals of every payroll run that no longer matches
    its payslips. Returns how many were rewritten.

    Deliberately aggregate-only: the comparison needs counts and sums, not the
    payslip rows themselves, so a whole-database reconciliation stays one query
    plus one save per genuinely stale run.
    """
    from .models import Payslip, PayrollRun

    runs = PayrollRun.objects.all()
    if run_ids is not None:
        runs = runs.filter(pk__in=run_ids)
    runs = list(runs)
    if not runs:
        return 0

    live = {
        row['payroll_run_id']: row
        for row in Payslip.objects
        .filter(is_deleted=False, payroll_run_id__in=[r.pk for r in runs])
        .values('payroll_run_id')
        .annotate(cnt=Count('id'), g=Sum('gross_salary'), n=Sum('net_salary'))
    }

    refreshed = 0
    for run in runs:
        row = live.get(run.pk) or {}
        if (run.employee_count != (row.get('cnt') or 0)
                or run.gross_pay != (row.get('g') or Decimal('0'))
                or run.net_pay != (row.get('n') or Decimal('0'))):
            _refresh_payroll_run_totals(run)
            refreshed += 1
    return refreshed


@login_required
def payslip_sync_bonuses(request):
    """Fold approved/paid bonuses into every payslip whose stored bonus has
    drifted -- the bonus counterpart of ``payslip_sync_advances``.

    The per-employee-month work is delegated to ``_sync_bonus_to_payslip()``,
    the same routine a bonus approval and a payslip download already run, so a
    bulk sync can never reach a different answer than those paths do. Each
    employee-month is synced once no matter how many payslips it covers.

    The outcome is reported in three buckets, because "0 updated" on a page
    that showed ten stale badges reads like a failure when it is really the
    healer confirming the money was already right:

      updated   gross/net actually moved
      verified  the slip was stale-looking but already carried the bonus; it
                is now stamped, so it stops showing as stale
      locked    a finalized payslip the bonus could not be written into
    """
    from .models import Payslip
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    # The Payroll Runs page syncs one run at a time; the Payslips page syncs
    # everything. Same routine either way, only the scope differs.
    run_id = request.POST.get('run') or request.GET.get('run')
    scope = Payslip.objects.filter(is_deleted=False)
    if run_id:
        try:
            run_id = int(run_id)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid payroll run.'}, status=400)
        scope = scope.filter(payroll_run_id=run_id)

    slips = list(
        scope.select_related('employee', 'payroll_run')
        .order_by('is_finalized', '-created_at')
    )
    _attach_bonus_state(slips)

    updated = 0
    verified = 0
    locked = 0
    seen = set()

    for slip in slips:
        # Only the drifting employee-months are worth walking: where the stored
        # marker already equals the live bonus total the sync is a guaranteed
        # no-op, and skipping those keeps a bulk run off the healer's payroll
        # recomputation for every healthy payslip in the database.
        if not slip.bonus_stale:
            continue
        month, year = _payslip_period_key(slip)
        if not month:
            continue
        key = (slip.employee_id, month, year)
        if key in seen:
            continue
        seen.add(key)

        before = (slip.gross_salary, slip.net_salary)

        result = _sync_bonus_to_payslip(slip.employee, month, year)

        if result['finalized_skipped']:
            locked += 1
            continue
        if result['applied']:
            updated += 1
            continue
        # The healer may have moved the money itself (a slip inflated by a
        # double-added bonus), which `applied` does not report.
        slip.refresh_from_db(fields=['gross_salary', 'net_salary'])
        if (slip.gross_salary, slip.net_salary) != before:
            updated += 1
        else:
            verified += 1

    # A payslip's gross moving pulls its run's denormalised totals out of date.
    # `_sync_bonus_to_payslip` refreshes the runs it touches, but a run can also
    # be stale for reasons this sync never looked at, so every run in scope is
    # reconciled here -- and only saved when it actually differs.
    runs_refreshed = _refresh_stale_run_totals([run_id] if run_id else None)

    parts = []
    if updated:
        parts.append(f'{updated} payslip(s) updated')
    if verified:
        parts.append(f'{verified} already correct and now confirmed')
    if locked:
        parts.append(f'{locked} finalized payslip(s) skipped')
    if runs_refreshed:
        parts.append(f'{runs_refreshed} payroll run total(s) refreshed')

    return JsonResponse({
        'success': True,
        'updated': updated,
        'verified': verified,
        'locked': locked,
        'runs_refreshed': runs_refreshed,
        'message': ', '.join(parts) + '.' if parts else 'All payslips already match their bonuses.',
    })


@login_required
def payslip_detail(request, pk):
    """One payslip, as a page.

    A browser landing here (from the Salary Report drawer, an employee's
    payslip history, or a pasted URL) gets the rendered record; only callers
    that ask for JSON -- ``?format=json`` or an XHR -- get the raw payload,
    which is what this endpoint used to return to everybody.
    """
    from .models import Payslip
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)

    wants_json = (
        request.GET.get('format') == 'json'
        or request.headers.get('x-requested-with') == 'XMLHttpRequest'
    )

    try:
        slip = Payslip.objects.select_related('employee', 'payroll_run').get(pk=pk, is_deleted=False)
    except Payslip.DoesNotExist:
        if wants_json:
            return JsonResponse({'success': False, 'error': 'Payslip not found.'}, status=404)
        raise Http404('Payslip not found.')

    if not wants_json:
        return _render_payslip_detail_page(request, slip)

    # Bonus is part of gross but has no column of its own on the model, so the
    # JSON callers (the Payslips list modal) get both the amount folded in and
    # whether a newer approved bonus is still waiting for a sync.
    _attach_bonus_state([slip])
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
            'bonus': str(slip.bonus_amount),
            'bonus_live': str(slip.bonus_live),
            'bonus_stale': slip.bonus_stale,
            'net_salary': str(slip.net_salary),
            'status': slip.get_status_display(),
            'status_key': slip.status,
            'paid_date': slip.paid_date.strftime('%b %d, %Y') if slip.paid_date else '-',
            'generated_on': slip.generated_on.strftime('%Y-%m-%d') if slip.generated_on else '-',
            'created_at': slip.created_at.strftime('%b %d, %Y %I:%M %p'),
        }
    }
    return JsonResponse(data)


def _render_payslip_detail_page(request, slip):
    """Render the full payslip record page.

    Amounts, warnings and history come from the Salary Report's payload builder
    rather than a second calculation here, so this page, the report table and
    the drawer can never disagree about the same payslip.

    The earnings/deductions columns are assembled from the payslip's own
    ``salary_structure`` snapshot and are built to reconcile: the earnings rows
    always sum to gross and the deduction rows to what was actually withheld,
    so a slip whose snapshot only stored salary components (basic, overtime,
    weekend and holiday pay are not components) still shows a column that adds
    up instead of one that silently undershoots the total above it.
    """
    from .salary_report import _payslip_detail_payload, _as_dict, _component_list, money, _fmt_money

    payload = _payslip_detail_payload(slip.pk)
    if payload is None:
        raise Http404('Payslip not found.')

    amounts = payload.get('amounts') or {}
    struct = _as_dict(slip.salary_structure)

    basic = money(slip.basic_salary)
    absent = money(slip.absent_deduction)
    gross = money(slip.gross_salary)
    deduction_total = money(slip.total_deductions)
    advance = money(slip.advance_deduction)
    bonus = money(struct.get('bonus_total_included'))
    # `net_basic` in the payroll engine: the absent deduction comes off basic
    # before gross is built, which is why it is not part of `total_deductions`.
    base_pay = max(basic - absent, Decimal('0'))

    components = _component_list(struct, 'earnings_list')
    component_total = sum((c['amount'] for c in components), Decimal('0'))

    # Older rows never stored `basic_salary`; a 0.00 basic line on those is
    # noise, and the pay it stands for surfaces in the remainder row below.
    earning_rows = []
    if base_pay > 0 or absent > 0:
        earning_rows.append({
            'name': 'Basic salary',
            'sub': (f'Rs. {_fmt_money(basic)} basic less Rs. {_fmt_money(absent)} absent deduction'
                    if absent > 0 else 'Monthly basic for this pay period'),
            'amount': _fmt_money(base_pay),
        })
    earning_rows += [
        {'name': c['name'], 'sub': None, 'amount': _fmt_money(c['amount'])}
        for c in components
    ]
    if bonus > 0:
        earning_rows.append({
            'name': 'Bonus', 'sub': 'Approved bonus folded into this payslip',
            'amount': _fmt_money(bonus), 'tone': 'good',
        })

    # Overtime, weekend and holiday pay are computed at generation but never
    # stored as components, so they only exist as the gap between gross and the
    # rows above. Show that gap rather than leaving the column short of gross.
    remainder = (gross - base_pay - component_total - bonus).quantize(Decimal('0.01'))
    if remainder != 0:
        if not earning_rows and not components:
            # Nothing itemised at all: the remainder *is* the pay, so don't
            # label it as overtime it may well not be.
            name, sub = ('Recorded earnings',
                         'This payslip stored no itemised earnings — shown as the gross total')
        elif remainder > 0:
            name, sub = ('Overtime, weekend & holiday pay',
                         'Earned outside the fixed salary components')
        else:
            name, sub = ('Other pay adjustment',
                         'Applied outside the fixed salary components')
        earning_rows.append({'name': name, 'sub': sub, 'amount': _fmt_money(remainder)})

    deduction_components = _component_list(struct, 'deductions_list')
    if deduction_components:
        deduction_rows = [
            {'name': c['name'], 'sub': None, 'amount': _fmt_money(c['amount'])}
            for c in deduction_components
        ]
        component_deductions = sum((c['amount'] for c in deduction_components), Decimal('0'))
        unlisted = (deduction_total - component_deductions).quantize(Decimal('0.01'))
        if unlisted != 0:
            deduction_rows.append({
                'name': 'Other deductions', 'sub': 'Recorded on the payslip total, not itemised',
                'amount': _fmt_money(unlisted),
            })
    elif deduction_total > 0:
        deduction_rows = [{
            'name': 'Recorded deductions',
            'sub': 'This payslip predates component snapshots, so no itemised list was stored',
            'amount': _fmt_money(deduction_total),
        }]
    else:
        deduction_rows = []

    withheld = (deduction_total + advance).quantize(Decimal('0.01'))

    # Deductions and advance are shown as what they take away, so the strip
    # reads Gross - Deductions - Advance = Net the way the report's KPI row does.
    equation = [
        {'label': 'Gross salary', 'value': amounts.get('gross', '0.00'), 'tone': 'good', 'op': None},
        {'label': 'Deductions', 'value': amounts.get('deductions', '0.00'), 'tone': 'bad', 'op': '−'},
        {'label': 'Advance recovered', 'value': amounts.get('advance', '0.00'), 'tone': 'warn', 'op': '−'},
        {'label': 'Net salary', 'value': amounts.get('net', '0.00'), 'tone': 'net', 'op': '='},
    ]

    context = {
        'page_title': f"Payslip {payload.get('payslip_number') or slip.pk}",
        'slip': slip,
        'p': payload,
        'amounts': amounts,
        'equation': equation,
        'earning_rows': earning_rows,
        'deduction_rows': deduction_rows,
        'withheld': _fmt_money(withheld),
        'employee': slip.employee,
        'can_adjust': not slip.is_finalized,
    }
    return render(request, 'hrm/payslip_detail.html', context)


def _build_payslip_print_context(slip):
    """Build the full print/PDF template context for a single payslip --
    salary breakdown, attendance summary, advance recovery rows, etc.

    Extracted from payslip_download() so payslip_bulk_print() (combined /
    bulk-download PDF) can reuse the exact same calculation for each slip in
    the batch instead of duplicating it."""
    from .models import EmployeeSalary, PayrollSetting
    from decimal import Decimal

    employee = slip.employee
    run = slip.payroll_run

    # ── Determine cycle dates from actual pay period ──────────────────────────────
    import calendar as _cal
    from datetime import date as dt_date

    if run.pay_period_start and run.pay_period_end:
        cycle_start = run.pay_period_start
        cycle_end = run.pay_period_end
    else:
        _month = run.month or (run.pay_period_start.month if run.pay_period_start else timezone.now().month)
        _year = run.year or (run.pay_period_start.year if run.pay_period_start else timezone.now().year)
        cycle_start = dt_date(_year, _month, 1)
        cycle_end = dt_date(_year, _month, _cal.monthrange(_year, _month)[1])

    # Fold in any bonuses approved/paid after this payslip was generated,
    # so the printed totals aren't stale relative to the bonus rows shown below.
    _sync_bonus_to_payslip(employee, cycle_start.month, cycle_start.year)
    slip.refresh_from_db(fields=['salary_structure', 'gross_salary', 'net_salary'])

    salary_record = (
        EmployeeSalary.objects.filter(employee=employee, is_active=True)
        .prefetch_related('components')
        .order_by('-effective_date')
        .first()
    )

    _ps = PayrollSetting.get_settings()
    
    # Load snapshot from payslip (or fallback to dynamic if old data)
    _struct = slip.salary_structure or {}
    if _struct.get('earnings_list') or _struct.get('deductions_list'):
        earnings_list = _struct.get('earnings_list', [])
        deductions_list = _struct.get('deductions_list', [])
        # We still need attendance data for the calendar and context, so run calculation
        # but override earnings and deductions!
        bd = _calculate_payroll_breakdown(
            employee=employee, cycle_start=cycle_start, cycle_end=cycle_end,
            salary_record=salary_record, payroll_settings=_ps,
        )
        from decimal import Decimal
        bd['basic_salary'] = slip.basic_salary or bd['basic_salary']
        bd['absent_deduction'] = slip.absent_deduction
        bd['net_basic'] = max(bd['basic_salary'] - slip.absent_deduction, Decimal('0'))
        bd['earnings_list'] = earnings_list
        bd['deductions_list'] = deductions_list
    else:
        # Fallback for old slips
        bd = _calculate_payroll_breakdown(
            employee=employee, cycle_start=cycle_start, cycle_end=cycle_end,
            salary_record=salary_record, payroll_settings=_ps,
        )
        earnings_list = list(bd['earnings_list'])
        deductions_list = list(bd['deductions_list'])

    _month = cycle_start.month
    _year = cycle_start.year
    from .models import Bonus as _Bonus
    _bonus_qs = _Bonus.objects.filter(
        employee=employee, month=_month, year=_year, status__in=['approved', 'paid']
    )

    for _b in _bonus_qs:
        earnings_list.append({'name': f"Bonus ({_b.get_bonus_type_display()})", 'amount': _b.amount})
    _bonus_total = sum(_b.amount for _b in _bonus_qs) or Decimal('0')
    _bonus_total = Decimal(str(_bonus_total)).quantize(Decimal('0.01'))

    _adj_earnings_total = Decimal('0')
    _adj_deductions_total = Decimal('0')
    for _adj in PayslipAdjustment.objects.filter(payslip=slip):
        if _adj.adjustment_type == 'earning':
            earnings_list.append({'name': f"Adjustment ({_adj.description})", 'amount': _adj.amount})
            _adj_earnings_total += _adj.amount
        elif _adj.adjustment_type == 'deduction':
            deductions_list.append({'name': f"Adjustment ({_adj.description})", 'amount': _adj.amount})
            _adj_deductions_total += _adj.amount

    from .models import AdvancePayment as _AdvPay
    _stored_adv = slip.advance_deduction or Decimal('0')
    all_employee_advances = list(
        _AdvPay.objects.filter(employee=employee).order_by('-created_at')
    )

    def _adv_monthly_ded(adv):
        _amt = adv.amount or Decimal('0')
        _repaid = adv.amount_repaid or Decimal('0')
        _rem = max(_amt - _repaid, Decimal('0'))
        _inst = adv.installment_amount or Decimal('0')
        _mode = adv.repayment_mode
        if _mode in ('salary_deduction', 'installments') or not _mode:
            _d = min(_inst if _inst > 0 else _rem, _rem)
        elif _mode == 'lump_sum':
            _d = _rem
        else:
            _d = min(_inst if _inst > 0 else _rem, _rem)
        return _d.quantize(Decimal('0.01')) if _d else Decimal('0')

    _live_advances = [a for a in all_employee_advances if a.status in ('disbursed', 'repaying')]
    _live_total = Decimal('0')
    for _adv in _live_advances:
        _adv.deducted_this_month = _adv_monthly_ded(_adv)
        _live_total += _adv.deducted_this_month

    if _stored_adv > 0 and _live_total == Decimal('0'):
        advance_deduction = _stored_adv
        _cleared = [a for a in all_employee_advances if a.status in ('cleared', 'repaying')]
        _shares = {}
        _share_total = Decimal('0')
        for _adv in _cleared:
            _inst = _adv.installment_amount or Decimal('0')
            if _adv.repayment_mode == 'lump_sum' or _inst <= 0:
                _share = _adv.amount or Decimal('0')
            else:
                _share = _inst
            _shares[_adv.pk] = _share
            _share_total += _share
        for _adv in all_employee_advances:
            if _adv.pk in _shares and _share_total > 0:
                _adv.deducted_this_month = (_stored_adv * _shares[_adv.pk] / _share_total).quantize(Decimal('0.01'))
            elif not hasattr(_adv, 'deducted_this_month') or _adv.status not in ('disbursed', 'repaying'):
                _adv.deducted_this_month = Decimal('0')
    elif _live_total > 0:
        advance_deduction = _live_total
        if _stored_adv != advance_deduction:
            # Base this on the payslip's own persisted gross/deductions (which
            # already include any approved bonuses and manual adjustments),
            # not the bare recalculated bd[] baseline -- otherwise this silently
            # wipes out those amounts the moment an employee has a live advance
            # that hasn't been recorded on the slip yet. Re-sync whenever the
            # live total has drifted from what's stored (not just from zero) --
            # e.g. a second advance disbursed, an installment edited, or a
            # repayment posted elsewhere -- so the footer total below (which
            # reads slip.advance_deduction) never falls behind the per-advance
            # rows above it (which read the freshly recomputed amounts).
            _new_net = max(slip.gross_salary - (slip.total_deductions + advance_deduction), Decimal('0'))
            Payslip.objects.filter(pk=slip.pk).update(advance_deduction=advance_deduction, net_salary=_new_net)
            # Re-read what we just wrote. The printed totals below are taken
            # from `slip`, and a bare .update() leaves this instance holding
            # the pre-advance values -- so the printed net salary would
            # disagree with both the payslip list and the database.
            slip.refresh_from_db(fields=['advance_deduction', 'net_salary'])
            _refresh_payroll_run_totals(run)
        for _adv in all_employee_advances:
            if _adv.status not in ('disbursed', 'repaying'):
                if not hasattr(_adv, 'deducted_this_month') or _adv.deducted_this_month is None:
                    _adv.deducted_this_month = Decimal('0')
    else:
        advance_deduction = Decimal('0')
        for _adv in all_employee_advances:
            _adv.deducted_this_month = Decimal('0')

    for _adv in all_employee_advances:
        if getattr(_adv, 'deducted_this_month', Decimal('0')) > 0:
            deductions_list.append({'name': f'Advance ({_adv.advance_number})', 'amount': _adv.deducted_this_month})

    if _struct.get('earnings_list') or _struct.get('deductions_list'):
        total_deductions_comp = slip.total_deductions + slip.advance_deduction
        total_earnings = slip.gross_salary
        net_salary = slip.net_salary
    else:
        # Fallback slips have no snapshot totals to trust, so unlike the
        # struct branch above (where slip.gross_salary/total_deductions are
        # already kept current by _recalculate_payslip), manual adjustments
        # must be folded in here explicitly or they'd show as line items
        # above while silently dropping out of the totals below.
        total_deductions_comp = bd['other_deductions_total'] + advance_deduction + _adj_deductions_total
        total_earnings = bd['total_earnings'] + _bonus_total + _adj_earnings_total
        net_salary = max(total_earnings - total_deductions_comp, Decimal('0'))

    max_rows = max(len(earnings_list), len(deductions_list), 1)
    salary_rows = []
    for i in range(max_rows):
        salary_rows.append({
            'earning_name': earnings_list[i]['name'] if i < len(earnings_list) else '',
            'earning_amount': earnings_list[i]['amount'] if i < len(earnings_list) else None,
            'deduction_name': deductions_list[i]['name'] if i < len(deductions_list) else '',
            'deduction_amount': deductions_list[i]['amount'] if i < len(deductions_list) else None,
        })

    from dashboard.models import CompanySetup
    _co = CompanySetup.get_settings()
    context = {
        'payslip': slip,
        'employee': employee,
        'company_name': _co.company_name,
        'pay_period_start': run.pay_period_start,
        'pay_period_end': run.pay_period_end,
        'pay_date': run.pay_date,
        'basic_salary': bd['basic_salary'],
        'daily_rate': bd['daily_rate'],
        'salary_divisor': bd['salary_divisor'],
        'divisor_label': bd['divisor_label'],
        'calendar_days': bd['calendar_days'],
        'weekend_days': bd['weekend_days'],
        'holiday_days': bd['holiday_days'],
        'working_days': bd['working_days'],
        'present_days': bd['present_working_days'],
        'paid_leave_days': bd['paid_leave_days'],
        'absent_days': bd['absent_days'],
        'half_days': bd['half_days'],
        'weekend_worked_days': bd['weekend_worked_days'],
        'holiday_worked_days': bd['holiday_worked_days'],
        'total_overtime_hours': bd['total_ot_hours'],
        'ot_hours': bd['total_ot_hours'],
        'sandwich_days': bd['sandwich_days'],
        'absent_deduction': bd['absent_deduction'],
        'weekend_pay': bd['weekend_pay'],
        'holiday_pay': bd['holiday_pay'],
        'ot_pay': bd['ot_pay'],
        'pay_ot': bd['pay_ot'],
        'net_basic': bd['net_basic'],
        'weekend_multiplier': bd['weekend_multiplier'],
        'holiday_multiplier': bd['holiday_multiplier'],
        'ot_multiplier': bd['ot_multiplier'],
        'earnings_list': earnings_list,
        'deductions_list': deductions_list,
        'salary_rows': salary_rows,
        'total_earnings': total_earnings,
        'total_deductions': total_deductions_comp,
        'salary_comp_deductions': slip.total_deductions if _struct.get('deductions_list') else bd['other_deductions_total'],
        'advance_deduction': advance_deduction,
        'all_advances': all_employee_advances,
        'net_salary': net_salary,
    }
    return context


@login_required
def payslip_download(request, pk):
    from .models import Payslip
    from django.http import HttpResponseForbidden

    try:
        slip = Payslip.objects.select_related(
            'employee', 'employee__department', 'employee__designation',
            'employee__attendance_policy', 'payroll_run'
        ).get(pk=pk, is_deleted=False)
    except Payslip.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Payslip not found.'}, status=404)

    if not request.user.is_staff and getattr(request.user, 'employee_profile', None) != slip.employee:
        return HttpResponseForbidden("You are not authorized to view this payslip.")

    if slip.status == 'generated':
        slip.status = 'downloaded'
        slip.save(update_fields=['status', 'updated_at'])

    context = _build_payslip_print_context(slip)
    return render(request, 'hrm/payslip_print.html', context)


@login_required
def payslip_bulk_print(request):
    """Combined multi-payslip print/PDF view. Renders every requested
    payslip as its own page inside one document so the browser's
    Print / Save-as-PDF produces a single multi-page file -- used by both
    the "Bulk Download" and "Generate Combined PDF" bulk actions on the
    Payslips page (the only difference is whether mark_downloaded=1 is
    sent, and whether printing auto-starts).

    Accepts POST (from a hidden-form submit with target=_blank, so the
    combined view opens in a new tab) with:
      - mode: 'ids' | 'all' | 'custom'
      - ids: comma-separated Payslip PKs (mode=ids)
      - search / status: same filters as payslip_list, applied across ALL
        matching pages, not just the current one (mode=all)
      - custom_text: newline/comma separated payslip numbers, employee
        names or employee IDs, resolved to payslips (mode=custom)
      - mark_downloaded: '1' to flip eligible slips to 'downloaded', like
        the single-slip download link does
      - auto_print: '1' to auto-trigger window.print() on load
    """
    from .models import Payslip
    from django.http import HttpResponseForbidden

    if request.method != 'POST':
        return HttpResponseForbidden('POST required.')

    mode = request.POST.get('mode', 'ids')
    base_qs = Payslip.objects.select_related(
        'employee', 'employee__department', 'employee__designation',
        'employee__attendance_policy', 'payroll_run'
    ).filter(is_deleted=False)

    if mode == 'all':
        search_query = request.POST.get('search', '').strip()
        status_filter = request.POST.get('status', '').strip()
        qs = base_qs
        if search_query:
            qs = qs.filter(
                Q(employee__full_name__icontains=search_query) |
                Q(payslip_number__icontains=search_query)
            )
        if status_filter:
            qs = qs.filter(status=status_filter)
        slips = list(qs.order_by('-created_at'))
    elif mode == 'custom':
        raw = request.POST.get('custom_text', '')
        tokens = [t.strip() for t in re.split(r'[,\n]', raw) if t.strip()]
        if not tokens:
            return HttpResponse('No payslip numbers or employee names entered.', status=400)
        q = Q()
        for tok in tokens:
            q |= Q(payslip_number__iexact=tok) | Q(payslip_number__icontains=tok) \
                | Q(employee__full_name__icontains=tok) | Q(employee__employee_id__iexact=tok)
        slips = list(base_qs.filter(q).order_by('-created_at').distinct())
    else:
        ids_raw = request.POST.get('ids', '')
        ids = [int(x) for x in ids_raw.split(',') if x.strip().isdigit()]
        if not ids:
            return HttpResponse('No payslips selected.', status=400)
        slips = list(base_qs.filter(pk__in=ids))
        # Preserve the order the caller selected them in.
        order = {pk_: i for i, pk_ in enumerate(ids)}
        slips.sort(key=lambda s: order.get(s.pk, 0))

    if not slips:
        return HttpResponse('No matching payslips found.', status=404)

    MAX_COMBINED = 300
    truncated = len(slips) > MAX_COMBINED
    slips = slips[:MAX_COMBINED]

    if not request.user.is_staff:
        emp = getattr(request.user, 'employee_profile', None)
        slips = [s for s in slips if s.employee_id == getattr(emp, 'pk', None)]
        if not slips:
            return HttpResponseForbidden("You are not authorized to view these payslips.")

    mark_downloaded = request.POST.get('mark_downloaded') == '1'
    if mark_downloaded:
        to_flip = [s.pk for s in slips if s.status == 'generated']
        if to_flip:
            Payslip.objects.filter(pk__in=to_flip).update(status='downloaded', updated_at=timezone.now())
            for s in slips:
                if s.pk in to_flip:
                    s.status = 'downloaded'

    slip_contexts = [_build_payslip_print_context(s) for s in slips]

    context = {
        'slip_contexts': slip_contexts,
        'count': len(slip_contexts),
        'truncated': truncated,
        'max_combined': MAX_COMBINED,
        'auto_print': request.POST.get('auto_print') == '1',
    }
    return render(request, 'hrm/payslip_bulk_print.html', context)


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
    import calendar as _cal
    sel_month = request.GET.get('month', '')
    sel_year = request.GET.get('year', '')
    try:
        month = int(sel_month) if sel_month else now.month
        year = int(sel_year) if sel_year else now.year
        if month < 1 or month > 12 or year < 2000 or year > 2100:
            raise ValueError
    except (ValueError, TypeError):
        month, year = now.month, now.year

    month_name = _cal.month_name[month]
    # Cycle dates from first to last day of selected month
    first_day = dt_date(year, month, 1)
    last_day = dt_date(year, month, _cal.monthrange(year, month)[1])
    cycle_start = first_day
    cycle_end = last_day

    from .models import EmployeeSalary, PayrollSetting as _PS2
    _ps = _PS2.get_settings()
    bd = _calculate_payroll_breakdown(
        employee=employee,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        salary_record=salary,
        payroll_settings=_ps,
    )

    from .models import Bonus as _Bonus2
    _bonus_qs2 = _Bonus2.objects.filter(employee=employee, month=month, year=year, status__in=['approved', 'paid'])
    earnings = list(bd['earnings_list'])
    deductions = list(bd['deductions_list'])
    for _b in _bonus_qs2:
        earnings.append({'name': f"Bonus ({_b.get_bonus_type_display()})", 'amount': _b.amount})

    # Advance deduction preview
    from .models import AdvancePayment as _AdvPay2, Payslip as _PS3
    advance_deduction = Decimal('0')
    _existing_slip2 = _PS3.objects.filter(
        employee=employee,
        payroll_run__pay_period_start__lte=last_day,
        payroll_run__pay_period_end__gte=first_day,
    ).order_by('-payroll_run__pay_date').first()
    if _existing_slip2 and (_existing_slip2.advance_deduction or Decimal('0')) > 0:
        advance_deduction = _existing_slip2.advance_deduction
        deductions.append({'name': 'Advance Deduction (Applied)', 'amount': advance_deduction})
    else:
        for _adv2 in _AdvPay2.objects.filter(employee=employee, status__in=['disbursed', 'repaying']):
            _rem2 = max((_adv2.amount or Decimal('0')) - (_adv2.amount_repaid or Decimal('0')), Decimal('0'))
            _inst2 = _adv2.installment_amount or Decimal('0')
            if _adv2.repayment_mode in ('salary_deduction', 'installments') or not _adv2.repayment_mode:
                _ded2 = min(_inst2 if _inst2 > 0 else _rem2, _rem2)
            elif _adv2.repayment_mode == 'lump_sum':
                _ded2 = _rem2
            else:
                _ded2 = min(_inst2 if _inst2 > 0 else _rem2, _rem2)
            _ded2 = _ded2.quantize(Decimal('0.01'))
            if _ded2 > 0:
                advance_deduction += _ded2
                deductions.append({'name': f'Advance ({_adv2.advance_number})', 'amount': _ded2})

    total_earnings = bd['total_earnings']
    total_deductions_amount = bd['other_deductions_total'] + advance_deduction
    salary_comp_deductions = bd['other_deductions_total']
    net_salary = max(total_earnings - total_deductions_amount, Decimal('0'))

    # Build attendance_data list for the calendar display
    attendance_data = []
    for _rec in bd['att_records']:
        _is_weekend = _rec.date.weekday() in bd['weekend_day_nums']
        _is_holiday = _rec.date in bd['holiday_dates_non_weekend']
        overtime_display = str(_rec.overtime_hours) + 'h' if _rec.overtime_hours > 0 else '-'
        status_tags = []
        if _rec.status == 'present':
            status_tags.append(('Present', 'present'))
        elif _rec.status == 'absent':
            status_tags.append(('Absent', 'absent'))
        elif _rec.status == 'late':
            status_tags.append(('Present', 'present'))
            status_tags.append(('Late', 'late'))
        elif _rec.status == 'half_day':
            status_tags.append(('Half Day', 'half_day'))
        elif _rec.status == 'on_leave':
            status_tags.append(('On Leave', 'on_leave'))
        if _rec.is_early_departure and _rec.status not in ('absent', 'on_leave', 'half_day'):
            status_tags.append(('Early', 'early'))
        attendance_data.append({
            'date': _rec.date,
            'clock_in': _rec.clock_in,
            'clock_out': _rec.clock_out,
            'overtime': overtime_display,
            'status_tags': status_tags,
        })

    # Available months dropdown (last 12)
    available_months = []
    for i in range(12):
        m = now.month - i
        y = now.year
        if m <= 0:
            m += 12
            y -= 1
        label = f"{_cal.month_name[m]} {y} Payroll ({m}/1/{y} - {m}/{_cal.monthrange(y, m)[1]}/{y})"
        available_months.append({'month': m, 'year': y, 'label': label, 'selected': (m == month and y == year)})

    context = {
        'page_title': f'Payroll Calculation - {employee.full_name}',
        'salary': salary,
        'employee': employee,
        'month': month,
        'year': year,
        'month_name': month_name,
        # Attendance (Layer 1)
        'calendar_days': bd['calendar_days'],
        'weekend_days': bd['weekend_days'],
        'holiday_days': bd['holiday_days'],
        'working_days': bd['working_days'],
        'present_days': bd['present_working_days'],
        'paid_leave_days': bd['paid_leave_days'],
        'absent_days': bd['absent_days'],
        'half_days': bd['half_days'],
        'weekend_worked_days': bd['weekend_worked_days'],
        'holiday_worked_days': bd['holiday_worked_days'],
        'total_overtime_hours': bd['total_ot_hours'],
        'ot_hours': bd['total_ot_hours'],
        'sandwich_days': bd['sandwich_days'],
        # Salary (Layer 2)
        'basic_salary': salary.basic_salary if salary else Decimal('0'),
        'daily_rate': bd['daily_rate'],
        'salary_divisor': bd['salary_divisor'],
        'divisor_label': bd['divisor_label'],
        'absent_deduction': bd['absent_deduction'],
        'weekend_pay': bd['weekend_pay'],
        'holiday_pay': bd['holiday_pay'],
        'ot_pay': bd['ot_pay'],
        'pay_ot': bd['pay_ot'],
        'net_basic': bd['net_basic'],
        'weekend_multiplier': bd['weekend_multiplier'],
        'holiday_multiplier': bd['holiday_multiplier'],
        'ot_multiplier': bd['ot_multiplier'],
        # Components
        'earnings': earnings,
        'deductions': deductions,
        'total_earnings': total_earnings,
        'total_deductions_amount': total_deductions_amount,
        'salary_comp_deductions': salary_comp_deductions,
        'advance_deduction': advance_deduction,
        'net_salary': net_salary,
        # Calendar display
        'attendance_data': attendance_data,
        'available_months': available_months,
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

    # Auto-sync on page load, same as attendance_list, so status (incl. half-day)
    # reflects the latest policy/threshold instead of stale computed values.
    # Never let a sync failure take down the whole report page — log it and
    # fall back to showing existing records.
    try:
        _sync_biometric_to_attendance()
    except Exception:
        adms_logger.exception('attendance_report: biometric auto-sync failed, showing existing records')

    # ── Filters from GET ────────────────────────────────────────────────────
    search     = request.GET.get('search', '').strip()
    date_from  = request.GET.get('date_from', '')
    date_to    = request.GET.get('date_to', '')
    department = request.GET.get('department', '')
    status     = request.GET.get('status', '')
    per_page   = request.GET.get('per_page', 50)
    export     = request.GET.get('export', '')

    try:
        per_page = int(per_page)
        if per_page not in [10, 25, 50, 100]:
            per_page = 50
    except (ValueError, TypeError):
        per_page = 50

    qs = AttendanceRecord.objects.select_related(
        'employee', 'employee__department', 'employee__branch', 'shift'
    ).filter(is_deleted=False).order_by('-date', 'employee__full_name')

    if search:
        import re
        match = re.search(r'\(([^)]+)\)$', search)
        if match:
            extracted_id = match.group(1).strip()
            qs = qs.filter(employee__employee_id__iexact=extracted_id)
        else:
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

    _weekend_nums_cache = {}

    def _get_weekend_nums_for_employee(emp_id, start, end):
        """Get the set of weekday numbers that are weekends for this employee.
        Memoised — the same employee is looked up several times per request."""
        if not start or not end:
            return {5, 6}
        if emp_id in _weekend_nums_cache:
            return _weekend_nums_cache[emp_id]
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
        _weekend_nums_cache[emp_id] = weekend_nums
        return weekend_nums

    def _count_weekend_days_for_employee(emp_id, start, end):
        """Count how many days in [start..end] fall on the employee's weekend days."""
        if not start or not end:
            return 0
        weekend_nums = _get_weekend_nums_for_employee(emp_id, _eff_from, _eff_to)
        count = 0
        current = start
        one_day = timedelta(days=1)
        while current <= end:
            if current.weekday() in weekend_nums:
                count += 1
            current += one_day
        return count

    # ── Aggregate stats over the filtered queryset ───────────────────────────
    stats = qs.aggregate(
        total=Count('id'),
        present=Count('id', filter=Q(status='present')),
        absent=Count('id', filter=Q(status='absent')),
        late=Count('id', filter=Q(status='late')),
        half_day=Count('id', filter=Q(status='half_day')),
        on_leave=Count('id', filter=Q(status='on_leave')),
        incomplete=Count('id', filter=Q(status='incomplete')),
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
            incomplete=Count('id', filter=Q(status='incomplete')),
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

    # ── Attribute each dated record to a bucket ──────────────────────────────
    # 'present_on_off' spans the whole range (it is a display column), while the
    # duty buckets stop at yesterday to match elapsed_duty_days, which Absent is
    # derived from. Counting a record for today against a total that stops at
    # yesterday would silently cancel out a genuine past absence.
    _today = dt_date.today()
    _yesterday = _today - timedelta(days=1)
    _elapsed_to = min(_eff_to, _yesterday) if _eff_from and _eff_to else None

    emp_extra = {}  # emp_id -> per-employee day buckets
    if _eff_from and _eff_to:
        _dated = qs.filter(
            status__in=['present', 'late', 'half_day', 'on_leave', 'incomplete'],
        ).values_list('employee__id', 'date', 'status', 'is_holiday')

        emp_dated = defaultdict(list)
        for emp_id, rec_date, rec_status, is_hol in _dated:
            emp_dated[emp_id].append((rec_date, rec_status, is_hol))

        for emp_id, rows in emp_dated.items():
            weekend_nums = _get_weekend_nums_for_employee(emp_id, _eff_from, _eff_to)
            buckets = {'present_on_off': 0, 'present_on_duty': 0,
                       'leave_on_duty': 0, 'misc_on_duty': 0}
            for rec_date, rec_status, is_hol in rows:
                is_off_day = rec_date.weekday() in weekend_nums or is_hol
                if is_off_day and rec_status in ('present', 'late', 'half_day'):
                    buckets['present_on_off'] += 1
                if is_off_day or rec_date > _elapsed_to:
                    continue
                if rec_status in ('present', 'late', 'half_day'):
                    buckets['present_on_duty'] += 1
                elif rec_status == 'on_leave':
                    buckets['leave_on_duty'] += 1
                elif rec_status == 'incomplete':
                    buckets['misc_on_duty'] += 1
            emp_extra[emp_id] = buckets

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

        # Present on weekend/day off/holiday, plus the elapsed-window buckets
        extra = emp_extra.get(emp_id, {})
        present_on_off = extra.get('present_on_off', 0)
        present_on_duty = extra.get('present_on_duty', 0)
        leave_on_duty = extra.get('leave_on_duty', 0)
        misc_on_duty = extra.get('misc_on_duty', 0)

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
            'absent': max(
                elapsed_duty_days - present_on_duty - leave_on_duty - misc_on_duty, 0,
            ),
            'misc_days': e['incomplete'],
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
            'incomplete': e['incomplete'],
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
            'Present Days', 'Half Day', 'Present On Holiday', 'Present On Day Off/Night Off/Weekend',
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
                row['half_day'],
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
    elif period == 'custom':
        ref_date_to_str = request.GET.get('ref_date_to', '')
        date_from = ref_date
        try:
            date_to = date.fromisoformat(ref_date_to_str) if ref_date_to_str else date_from
        except ValueError:
            date_to = date_from
    else:
        # A Bikram Sambat month never lines up with an AD calendar month, so
        # when the client is in BS mode it converts the chosen BS month itself
        # and sends the resulting AD range explicitly. Without an explicit end
        # we fall back to the AD calendar month that ref_date lands in.
        month_to_str = request.GET.get('ref_date_to', '')
        try:
            month_to = date.fromisoformat(month_to_str) if month_to_str else None
        except ValueError:
            month_to = None
        if month_to and 28 <= (month_to - ref_date).days + 1 <= 32:
            date_from, date_to = ref_date, month_to
        else:
            date_from = ref_date.replace(day=1)
            _, last_day = calendar.monthrange(ref_date.year, ref_date.month)
            date_to = ref_date.replace(day=last_day)

    records_qs = AttendanceRecord.objects.filter(
        employee=employee, date__gte=date_from, date__lte=date_to, is_deleted=False
    ).select_related('shift').order_by('date')

    records_data = []
    for rec in records_qs:
        records_data.append({
            'date': rec.date.isoformat(),
            'date_display': rec.date.strftime('%d %b %Y'),
            'day_name': rec.date.strftime('%A'),
            'day_short': rec.date.strftime('%a'),
            'day_num': rec.date.day,
            'clock_in': rec.clock_in.strftime('%I:%M %p') if rec.clock_in else None,
            'clock_out': rec.clock_out.strftime('%I:%M %p') if rec.clock_out else None,
            'working_hours': float(rec.working_hours) if rec.working_hours else 0,
            'overtime_hours': float(rec.overtime_hours) if rec.overtime_hours else 0,
            'status': rec.status,
            'status_display': rec.get_status_display(),
            'shift': rec.shift.name if rec.shift else None,
            'is_late_arrival': rec.is_late_arrival,
            'is_early_departure': rec.is_early_departure,
            'is_holiday': rec.is_holiday,
            'is_incomplete': rec.is_incomplete_punch,
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

    # Present count (present + late + half_day) — whole range, display only
    present_cnt = sc.get('present', 0) + sc.get('late', 0) + sc.get('half_day', 0)

    # Misc days: incomplete-punch days (clocked in but never out, or the
    # reverse). The employee did show up, so the day is not Absent, but it
    # cannot count as Present either — it gets its own bucket rather than
    # silently inflating Absent.
    misc_days = sc.get('incomplete', 0)

    # ── Bucket records against the elapsed duty window ───────────────────────
    # Absent is derived from elapsed_duty_days, which stops at yesterday, so
    # everything subtracted from it must stop there too. Counting a record for
    # today (or any future day in the range) against a total that never included
    # it would silently cancel out a real past absence.
    _elapsed_to = min(date_to, yesterday)
    present_on_duty = leave_on_duty = misc_on_duty = 0
    for r in records_data:
        _rd = date.fromisoformat(r['date'])
        if _rd > _elapsed_to or _rd.weekday() in weekend_nums or r['is_holiday']:
            continue
        if r['status'] in ('present', 'late', 'half_day'):
            present_on_duty += 1
        elif r['status'] == 'on_leave':
            leave_on_duty += 1
        elif r['status'] == 'incomplete':
            misc_on_duty += 1

    # Present on holiday
    present_on_holiday = sum(1 for r in records_data if r['is_holiday'] and r['status'] in ('present', 'late', 'half_day'))

    # Present on day off / night off / weekend
    present_on_off = 0
    for r in records_data:
        rec_date = date.fromisoformat(r['date'])
        if r['status'] in ('present', 'late', 'half_day'):
            if rec_date.weekday() in weekend_nums or r['is_holiday']:
                present_on_off += 1

    # Absent = elapsed duty days left over once present, approved leave and
    # misc (incomplete punch) days on those same days are accounted for.
    absent_days = max(
        elapsed_duty_days - present_on_duty - leave_on_duty - misc_on_duty, 0,
    )

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

    # Derived from the resolved range rather than the AD calendar, so a BS
    # month (29-32 days) renders the right number of cells.
    month_days = ((date_to - date_from).days + 1) if period == 'month' else None

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
            'absent': absent_days,
            'late': sc.get('late', 0),
            'half_day': sc.get('half_day', 0),
            'on_leave': sc.get('on_leave', 0),
            'incomplete': misc_days,
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
            'half_day': sc.get('half_day', 0),
            'present_on_holiday': present_on_holiday,
            'present_on_off': present_on_off,
            'absent_days': absent_days,
            'misc_days': misc_days,
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
        is_deleted=False,
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
            incomplete=Count('id', filter=Q(status='incomplete')),
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

    # Per-employee dated records, used both for the "present on day off" column
    # and to bucket days against the elapsed duty window below. One pass over
    # the range covers every status we need to attribute.
    emp_dated = defaultdict(list)
    for emp_id, rec_date, rec_status, is_hol in att_qs.filter(
        status__in=['present', 'late', 'half_day', 'on_leave', 'incomplete'],
    ).values_list('employee_id', 'date', 'status', 'is_holiday'):
        emp_dated[emp_id].append((rec_date, rec_status, is_hol))

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
    _elapsed_to = min(_eff_to, _yesterday)
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

        # Present on off — whole range, display column
        present_on_off = 0
        # Buckets restricted to the elapsed duty window that Absent is derived
        # from. Counting a record for today against elapsed_duty_days (which
        # stops at yesterday) would cancel out a genuine past absence.
        present_on_duty = leave_on_duty = misc_on_duty = 0
        for rec_date, rec_status, is_hol in emp_dated.get(emp_id, ()):
            is_off_day = rec_date.weekday() in weekend_nums or is_hol
            if is_off_day and rec_status in ('present', 'late', 'half_day'):
                present_on_off += 1
            if is_off_day or rec_date > _elapsed_to:
                continue
            if rec_status in ('present', 'late', 'half_day'):
                present_on_duty += 1
            elif rec_status == 'on_leave':
                leave_on_duty += 1
            elif rec_status == 'incomplete':
                misc_on_duty += 1

        misc_days = a.get('incomplete', 0)
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
            'half_day': a.get('half_day', 0),
            'present_on_holiday': a.get('holiday_present', 0),
            'present_on_off': present_on_off,
            'absent': max(
                elapsed_duty_days - present_on_duty - leave_on_duty - misc_on_duty, 0,
            ),
            'misc_days': misc_days,
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
        from dashboard.timezone_utils import get_nepali_now
        year = int(request.POST.get('year', get_nepali_now().year))
        employees = Employee.objects.filter(employee_status='active')
        leave_types = LeaveType.objects.filter(is_active=True)
        
        created_count = 0
        updated_count = 0
        
        for emp in employees:
            for lt in leave_types:
                # Calculate used days for this employee and leave type
                # No start_date upper bound: a leave request booked for a
                # future date still consumes the balance. Cutting the count off
                # at "today" let an employee book far more days than they have
                # and only discover it as the dates arrived.
                used = LeaveRequest.objects.filter(
                    employee=emp,
                    leave_type=lt,
                    start_date__year=year,
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

def _is_bonus_admin(user):
    """Administrators/superusers may edit or delete a bonus after it's been
    approved or paid; everyone else is limited to pending/rejected bonuses."""
    return user.is_superuser or user.role == 'administrator'


def _finalized_payslip_note(action):
    return (
        f'A finalized payslip for this period {action} — unfinalize it first '
        'if this should be reflected there.'
    )


def _validate_bonus_fields(bonus_type, month_str, year_str):
    """Shared validation for bonus_create/bonus_update: bonus_type must be one
    of the model's real choices, and month/year must be sane calendar values.
    Returns (month, year, error) -- error is None on success."""
    from .models import Bonus
    valid_types = [c[0] for c in Bonus.BONUS_TYPE_CHOICES]
    if bonus_type not in valid_types:
        return None, None, 'Invalid bonus type.'
    try:
        month = int(month_str)
        year = int(year_str)
    except (TypeError, ValueError):
        return None, None, 'Invalid month or year.'
    if not (1 <= month <= 12):
        return None, None, 'Month must be between 1 and 12.'
    if not (2000 <= year <= 2100):
        return None, None, 'Year is out of range.'
    return month, year, None


def heal_payslip_bonus_snapshot(slip, current_bonus_total=None):
    """Repair a payslip whose bonus accounting predates (or was corrupted by)
    the bonus_total_included bookkeeping, so no manual data-fix script is
    needed in production.

    Two populations exist in any database written before that bookkeeping
    landed:

      AT RISK    generated with a bonus folded into gross_salary but no
                 marker recorded. _sync_bonus_to_payslip() would read the
                 missing marker as zero and add the same bonus a second time
                 on the next download.

      CORRUPTED  that second addition already happened, so gross_salary and
                 net_salary are inflated by exactly one bonus amount.

    Both are healed here by reconciling against a recomputed baseline, and the
    payslip is stamped with `gross_before_bonus` so every later reconciliation
    is exact rather than inferred. A payslip that matches neither shape is left
    completely alone and logged -- a manual adjustment or an attendance change
    since generation can move gross legitimately, and guessing at those would
    do more harm than the bug.

    Safe to call repeatedly; healed payslips short-circuit on the stamp.
    Returns 'healthy', 'immunised', 'repaired', 'unexplained' or 'skipped'.
    """
    from .models import EmployeeSalary, PayrollSetting, Bonus
    from decimal import Decimal
    import calendar as _cal
    from datetime import date as _date

    struct = slip.salary_structure or {}
    if struct.get('gross_before_bonus') is not None:
        return 'healthy'          # already reconcilable exactly

    declined = struct.get('bonus_heal_declined') or {}
    if declined.get('gross') == str(slip.gross_salary):
        # Already assessed at this gross and deliberately left alone. Without
        # this the full payroll breakdown would be recomputed, and a warning
        # logged, on every single download of an unexplained payslip.
        return 'unexplained'
    if not struct.get('earnings_list') and not struct.get('deductions_list'):
        return 'skipped'          # pre-snapshot slip; download recomputes it live

    run = slip.payroll_run
    if run.pay_period_start and run.pay_period_end:
        cycle_start, cycle_end = run.pay_period_start, run.pay_period_end
    elif run.month and run.year:
        cycle_start = _date(run.year, run.month, 1)
        cycle_end = _date(run.year, run.month, _cal.monthrange(run.year, run.month)[1])
    else:
        return 'skipped'

    if current_bonus_total is None:
        current_bonus_total = sum(
            b.amount for b in Bonus.objects.filter(
                employee=slip.employee, month=cycle_start.month,
                year=cycle_start.year, status__in=['approved', 'paid'],
            )
        ) or Decimal('0')
        current_bonus_total = Decimal(str(current_bonus_total)).quantize(Decimal('0.01'))

    try:
        bd = _calculate_payroll_breakdown(
            employee=slip.employee, cycle_start=cycle_start, cycle_end=cycle_end,
            salary_record=(
                EmployeeSalary.objects.filter(employee=slip.employee, is_active=True)
                .prefetch_related('components').order_by('-effective_date').first()
            ),
            payroll_settings=PayrollSetting.get_settings(),
        )
    except Exception as exc:
        hrm_logger.warning(
            "heal_payslip_bonus_snapshot: could not recompute %s: %s",
            slip.payslip_number, exc,
        )
        return 'skipped'

    baseline = bd['total_earnings'].quantize(Decimal('0.01'))
    adj_earnings = Decimal(str(struct.get('adj_earnings_included', '0')))
    correct_gross = (baseline + adj_earnings + current_bonus_total).quantize(Decimal('0.01'))
    drift = (slip.gross_salary - correct_gross).quantize(Decimal('0.01'))
    marker = struct.get('bonus_total_included')

    if drift == 0:
        # Gross is right; just stamp it so the next sync can't double-add.
        struct['gross_before_bonus'] = str(baseline)
        struct['bonus_total_included'] = str(current_bonus_total)
        struct.pop('bonus_heal_declined', None)
        slip.salary_structure = struct
        slip.save(update_fields=['salary_structure', 'updated_at'])
        return 'immunised'

    if marker is not None and drift == Decimal(str(marker)) and drift > 0:
        # Inflated by exactly the bonus the buggy sync added a second time.
        new_net = max(slip.net_salary - drift, Decimal('0')).quantize(Decimal('0.01'))
        hrm_logger.info(
            "heal_payslip_bonus_snapshot: repairing %s — gross %s -> %s, net %s -> %s",
            slip.payslip_number, slip.gross_salary, correct_gross,
            slip.net_salary, new_net,
        )
        struct['gross_before_bonus'] = str(baseline)
        struct['bonus_total_included'] = str(current_bonus_total)
        struct.pop('bonus_heal_declined', None)
        slip.salary_structure = struct
        slip.gross_salary = correct_gross
        slip.net_salary = new_net
        slip.save(update_fields=[
            'salary_structure', 'gross_salary', 'net_salary', 'updated_at'])
        _refresh_payroll_run_totals(slip.payroll_run)
        return 'repaired'

    hrm_logger.warning(
        "heal_payslip_bonus_snapshot: leaving %s alone — stored gross %s, "
        "recomputed %s (drift %s), bonus %s, marker %s",
        slip.payslip_number, slip.gross_salary, correct_gross, drift,
        current_bonus_total, marker,
    )
    # Remember the verdict against this gross so the assessment isn't repeated
    # on every download. If gross moves later, it gets reassessed.
    struct['bonus_heal_declined'] = {'gross': str(slip.gross_salary), 'drift': str(drift)}
    slip.salary_structure = struct
    slip.save(update_fields=['salary_structure', 'updated_at'])
    return 'unexplained'


def _sync_bonus_to_payslip(employee, month, year):
    """Fold approved/paid bonuses for an employee/month/year into that
    employee's already-generated payslip (gross_salary/net_salary), so the
    payslip stays correct even when a bonus is approved after the payroll
    run was generated. Mirrors the base/delta approach used by
    _recalculate_payslip for manual adjustments.

    Returns {'applied': bool, 'finalized_skipped': bool} so callers (bonus
    approve/edit/delete) can tell the admin when a bonus change couldn't reach
    a payslip because it's already finalized (finalized payslips are
    intentionally locked from further edits), instead of silently no-op'ing.
    """
    from .models import Payslip, Bonus
    from decimal import Decimal

    base_qs = Payslip.objects.filter(employee=employee).filter(
        Q(payroll_run__month=month, payroll_run__year=year) |
        Q(payroll_run__pay_period_start__year=year, payroll_run__pay_period_start__month=month)
    )
    slip = base_qs.exclude(is_finalized=True).first()
    if not slip:
        return {'applied': False, 'finalized_skipped': base_qs.filter(is_finalized=True).exists()}

    current_bonus_total = sum(
        b.amount for b in Bonus.objects.filter(
            employee=employee, month=month, year=year, status__in=['approved', 'paid']
        )
    ) or Decimal('0')
    current_bonus_total = Decimal(str(current_bonus_total)).quantize(Decimal('0.01'))

    # Bring pre-bookkeeping payslips up to date before trusting the marker:
    # without this a slip generated before bonus_total_included existed reads
    # as "no bonus included" and gets the same bonus added all over again.
    heal_payslip_bonus_snapshot(slip, current_bonus_total)
    slip.refresh_from_db(fields=['salary_structure', 'gross_salary', 'net_salary'])

    struct = slip.salary_structure or {}
    included_bonus = Decimal(str(struct.get('bonus_total_included', '0')))
    delta = current_bonus_total - included_bonus
    if delta == 0:
        return {'applied': False, 'finalized_skipped': False}

    struct['bonus_total_included'] = str(current_bonus_total)
    slip.salary_structure = struct
    slip.gross_salary = (slip.gross_salary + delta).quantize(Decimal('0.01'))
    slip.net_salary = max(slip.net_salary + delta, Decimal('0')).quantize(Decimal('0.01'))
    slip.save(update_fields=['salary_structure', 'gross_salary', 'net_salary', 'updated_at'])
    _refresh_payroll_run_totals(slip.payroll_run)
    return {'applied': True, 'finalized_skipped': False}


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

    status_counts = bonuses.aggregate(
        pending=Count('id', filter=Q(status='pending')),
        approved=Count('id', filter=Q(status='approved')),
        paid=Count('id', filter=Q(status='paid')),
    )

    now = timezone.now()
    years = list(range(now.year - 2, now.year + 2))
    is_admin = _is_bonus_admin(request.user)

    if now.month == 1:
        default_month, default_year = 12, now.year - 1
    else:
        default_month, default_year = now.month - 1, now.year

    context = {
        'page_title': 'Bonus Management',
        'is_admin': is_admin,
        'bonuses': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'month_filter': month_filter,
        'year_filter': year_filter,
        'per_page': per_page,
        'total_bonuses': paginator.count,
        'pending_count': status_counts['pending'],
        'approved_count': status_counts['approved'],
        'paid_count': status_counts['paid'],
        'bonus_type_choices': Bonus.BONUS_TYPE_CHOICES,
        'status_choices': Bonus.STATUS_CHOICES,
        'months': [(i, _cal.month_name[i]) for i in range(1, 13)],
        'years': years,
        'current_year': default_year,
        'current_month': default_month,
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

    month, year, error = _validate_bonus_fields(bonus_type, month, year)
    if error:
        return JsonResponse({'success': False, 'error': error})

    if apply_for_all:
        # Create bonus for all active employees
        active_employees = Employee.objects.filter(employee_status='active')
        count = 0
        for e in active_employees:
            Bonus.objects.create(
                employee=e,
                bonus_type=bonus_type,
                amount=amt,
                month=month,
                year=year,
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
            month=month,
            year=year,
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

    if bonus.status in ('approved', 'paid') and not _is_bonus_admin(request.user):
        return JsonResponse({'success': False, 'error': 'Cannot edit an approved or paid bonus.'})

    amount = request.POST.get('amount', '').strip()
    bonus_type = request.POST.get('bonus_type', bonus.bonus_type)

    try:
        from decimal import Decimal
        amt = Decimal(amount)
        if amt <= 0:
            return JsonResponse({'success': False, 'error': 'Amount must be positive.'})
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid amount.'})

    month, year, error = _validate_bonus_fields(
        bonus_type, request.POST.get('month', '').strip(), request.POST.get('year', '').strip())
    if error:
        return JsonResponse({'success': False, 'error': error})

    old_month, old_year, old_status = bonus.month, bonus.year, bonus.status

    bonus.bonus_type = bonus_type
    bonus.amount = amt
    bonus.month = month
    bonus.year = year
    bonus.remarks = request.POST.get('remarks', '').strip()
    bonus.save()

    # Editing an already approved/paid bonus changes an amount that may already
    # be baked into a payslip -- re-sync so the payslip doesn't go stale, and
    # cover the period it moved out of if month/year changed too.
    finalized_skipped = False
    if old_status in ('approved', 'paid'):
        r1 = _sync_bonus_to_payslip(bonus.employee, old_month, old_year)
        finalized_skipped = r1['finalized_skipped']
        if (bonus.month, bonus.year) != (old_month, old_year):
            r2 = _sync_bonus_to_payslip(bonus.employee, bonus.month, bonus.year)
            finalized_skipped = finalized_skipped or r2['finalized_skipped']

    resp = {'success': True, 'message': 'Bonus updated successfully!'}
    if finalized_skipped:
        resp['note'] = _finalized_payslip_note('was not changed')
    return JsonResponse(resp)


@login_required
def bonus_delete(request, pk):
    from .models import Bonus
    bonus = get_object_or_404(Bonus, pk=pk)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    if bonus.status in ('approved', 'paid') and not _is_bonus_admin(request.user):
        return JsonResponse({'success': False, 'error': 'Cannot delete an approved or paid bonus.'})

    employee, month, year, status = bonus.employee, bonus.month, bonus.year, bonus.status
    bonus.delete()

    # If this bonus was already folded into a payslip, remove its amount again.
    resp = {'success': True, 'message': 'Bonus deleted successfully!'}
    if status in ('approved', 'paid'):
        result = _sync_bonus_to_payslip(employee, month, year)
        if result['finalized_skipped']:
            resp['note'] = _finalized_payslip_note('still includes the deleted bonus')

    return JsonResponse(resp)


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

    result = _sync_bonus_to_payslip(bonus.employee, bonus.month, bonus.year)

    resp = {'success': True, 'message': f'Bonus status updated to {bonus.get_status_display()}.'}
    if result['finalized_skipped']:
        resp['note'] = _finalized_payslip_note('was not changed')
    return JsonResponse(resp)


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
    """Recalculate gross_salary, total_deductions, net_salary based on manual adjustments.

    Applies only the *delta* since the last recalculation (tracked in
    salary_structure, the same way _sync_bonus_to_payslip tracks its own
    bonus delta) instead of resetting from a frozen base snapshot. A frozen
    base goes stale the moment anything else -- a bonus approval, an advance
    sync -- changes gross_salary/net_salary outside this function, and the
    next adjustment would silently wipe that other change out.
    """
    from decimal import Decimal

    adjustments = PayslipAdjustment.objects.filter(payslip=slip)
    extra_earnings = sum(a.amount for a in adjustments if a.adjustment_type == 'earning') or Decimal('0')
    extra_deductions = sum(a.amount for a in adjustments if a.adjustment_type == 'deduction') or Decimal('0')
    extra_earnings = Decimal(str(extra_earnings)).quantize(Decimal('0.01'))
    extra_deductions = Decimal(str(extra_deductions)).quantize(Decimal('0.01'))

    struct = slip.salary_structure or {}
    included_earnings = Decimal(str(struct.get('adj_earnings_included', '0')))
    included_deductions = Decimal(str(struct.get('adj_deductions_included', '0')))

    delta_earnings = extra_earnings - included_earnings
    delta_deductions = extra_deductions - included_deductions

    struct['adj_earnings_included'] = str(extra_earnings)
    struct['adj_deductions_included'] = str(extra_deductions)

    slip.salary_structure = struct
    slip.gross_salary = (slip.gross_salary + delta_earnings).quantize(Decimal('0.01'))
    slip.total_deductions = (slip.total_deductions + delta_deductions).quantize(Decimal('0.01'))
    slip.net_salary = max(
        slip.net_salary + delta_earnings - delta_deductions, Decimal('0')
    ).quantize(Decimal('0.01'))
    slip.save(update_fields=['salary_structure', 'gross_salary', 'total_deductions', 'net_salary', 'updated_at'])
    _refresh_payroll_run_totals(slip.payroll_run)


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




def _is_hrm_admin(user):
    return bool(user.is_superuser or user.role == 'administrator')


def _payslip_soft_delete_one(slip, deleted_by_user):
    """Move one payslip to the trash and hand back whatever advance
    repayment it charged (so a regenerate-after-restore-or-purge cycle
    doesn't double-deduct). Returns the number of advances credited back.
    Caller is responsible for finalized/already-trashed checks."""
    _run = slip.payroll_run
    reverted = _reverse_advance_deductions(slip)
    slip.soft_delete(deleted_by_user=deleted_by_user)
    _refresh_payroll_run_totals(_run)
    return reverted


@login_required
def payslip_delete(request, pk):
    """Soft-delete -- moves the payslip to the trash. Permanent removal only
    happens via payslip_permanent_delete, reachable from the trash."""
    slip = get_object_or_404(Payslip, pk=pk, is_deleted=False)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    if slip.is_finalized:
        return JsonResponse({'success': False, 'error': 'Cannot delete a finalized payslip. Unlock it first.'})

    reverted = _payslip_soft_delete_one(slip, request.user if request.user.is_authenticated else None)

    msg = 'Payslip moved to trash.'
    if reverted:
        msg += f' Advance repayment reversed for {reverted} advance(s).'
    return JsonResponse({'success': True, 'message': msg})


@login_required
def payslip_restore(request, pk):
    slip = get_object_or_404(Payslip, pk=pk, is_deleted=True)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    slip.restore()
    return JsonResponse({'success': True, 'message': f'Payslip {slip.payslip_number} restored.'})


@login_required
def payslip_permanent_delete(request, pk):
    """Irreversible -- restricted to admins and only reachable from the
    trash (a payslip must be soft-deleted first)."""
    if not _is_hrm_admin(request.user):
        return JsonResponse({'success': False, 'error': 'Only administrators can permanently delete payslips.'}, status=403)
    slip = get_object_or_404(Payslip, pk=pk, is_deleted=True)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    slip.delete()
    return JsonResponse({'success': True, 'message': 'Payslip permanently deleted.'})


@login_required
def payslip_bulk_action(request):
    """Bulk trash / restore / permanent-delete for the Payslips page
    checkbox toolbar. One endpoint, dispatched by 'action' so the frontend
    only has to POST the current selection once."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    action = request.POST.get('action')
    ids_raw = request.POST.get('ids', '')
    ids = [int(x) for x in ids_raw.split(',') if x.strip().isdigit()]
    if not ids:
        return JsonResponse({'success': False, 'error': 'No payslips selected.'}, status=400)

    if action == 'trash':
        slips = list(Payslip.objects.filter(pk__in=ids, is_deleted=False))
        locked = [s for s in slips if s.is_finalized]
        eligible = [s for s in slips if not s.is_finalized]
        for s in eligible:
            _payslip_soft_delete_one(s, request.user if request.user.is_authenticated else None)
        msg = f'{len(eligible)} payslip(s) moved to trash.'
        if locked:
            msg += f' {len(locked)} finalized payslip(s) were skipped -- unlock them first.'
        missing = len(ids) - len(slips)
        if missing:
            msg += f' {missing} payslip(s) were no longer available.'
        return JsonResponse({'success': True, 'message': msg, 'processed': len(eligible), 'skipped': len(locked) + missing})

    elif action == 'restore':
        slips = list(Payslip.objects.filter(pk__in=ids, is_deleted=True))
        for s in slips:
            s.restore()
        missing = len(ids) - len(slips)
        msg = f'{len(slips)} payslip(s) restored.'
        if missing:
            msg += f' {missing} were no longer in the trash.'
        return JsonResponse({'success': True, 'message': msg, 'processed': len(slips), 'skipped': missing})

    elif action == 'permanent_delete':
        if not _is_hrm_admin(request.user):
            return JsonResponse({'success': False, 'error': 'Only administrators can permanently delete payslips.'}, status=403)
        slips = list(Payslip.objects.filter(pk__in=ids, is_deleted=True))
        count = len(slips)
        Payslip.objects.filter(pk__in=[s.pk for s in slips]).delete()
        missing = len(ids) - count
        msg = f'{count} payslip(s) permanently deleted.'
        if missing:
            msg += f' {missing} were not eligible (not in the trash).'
        return JsonResponse({'success': True, 'message': msg, 'processed': count, 'skipped': missing})

    return JsonResponse({'success': False, 'error': 'Unknown action.'}, status=400)


@login_required
def payslip_filtered_ids(request):
    """Returns every Payslip PK matching the current search/status/view
    filters (not just the current page) -- backs the "Select all N matching"
    bulk-selection option on the Payslips page."""
    from .models import Payslip
    view = request.GET.get('view', 'active')
    is_trash = (view == 'trash')
    qs = Payslip.objects.filter(is_deleted=is_trash)

    search_query = request.GET.get('search', '').strip()
    if search_query:
        qs = qs.filter(
            Q(employee__full_name__icontains=search_query) |
            Q(payslip_number__icontains=search_query)
        )
    status_filter = request.GET.get('status', '').strip()
    if status_filter and not is_trash:
        qs = qs.filter(status=status_filter)

    ids = list(qs.values_list('pk', flat=True))
    return JsonResponse({'success': True, 'ids': ids, 'count': len(ids)})
