from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q, Sum
from decimal import Decimal, InvalidOperation
from django.utils import timezone
from datetime import datetime
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.core.files.base import ContentFile
from PIL import Image
from django.core.files.storage import default_storage
from io import BytesIO
import logging
import re

from .models import Role

logger = logging.getLogger(__name__)


User = get_user_model()

def administrator_required(view_func):
    decorated_view = user_passes_test(
        lambda u: u.is_authenticated and (u.is_superuser or u.role == 'administrator'),
        login_url='/admin/login/'
    )(view_func)
    return decorated_view


# ==================== ROLE MANAGEMENT ====================

@login_required
@administrator_required
def role_list(request):
    """Display all roles with create functionality"""
    roles = Role.objects.all()
    context = {
        'roles': roles,
    }
    return render(request, 'accounts/role_list.html', context)


@login_required
@administrator_required
def role_create(request):
    """Create a new role"""
    if request.method == 'POST':
        display_name = request.POST.get('display_name', '').strip()
        description = request.POST.get('description', '').strip()

        if not display_name:
            messages.error(request, 'Role name is required.')
            return redirect('role_list')

        # Generate internal name from display name (lowercase, underscores)
        name = re.sub(r'[^a-z0-9]+', '_', display_name.lower()).strip('_')

        if not name:
            messages.error(request, 'Invalid role name. Use letters and numbers.')
            return redirect('role_list')

        if Role.objects.filter(name=name).exists():
            messages.error(request, f'A role with the name "{display_name}" already exists.')
            return redirect('role_list')

        Role.objects.create(
            name=name,
            display_name=display_name,
            description=description,
            is_system=False,
        )
        messages.success(request, f'Role "{display_name}" created successfully.')
        return redirect('role_list')

    return redirect('role_list')


@login_required
@administrator_required
def role_delete(request, role_id):
    """Delete a role with protection measures"""
    role = get_object_or_404(Role, id=role_id)

    if request.method != 'POST':
        return redirect('role_list')

    # Check if any users are assigned to this role
    assigned_users = User.objects.filter(role=role.name, is_deleted=False).count()
    if assigned_users > 0:
        messages.error(
            request,
            f'Cannot delete role "{role.display_name}". '
            f'{assigned_users} active user(s) are assigned to this role. '
            f'Reassign them first.'
        )
        return redirect('role_list')

    # ALL roles require password confirmation
    confirm_password = request.POST.get('confirm_password', '').strip()
    if not confirm_password or not request.user.check_password(confirm_password):
        messages.error(request, 'Incorrect password. Role deletion requires password confirmation.')
        return redirect('role_list')

    # System roles additionally require typing the role name
    if role.is_system:
        confirm_name = request.POST.get('confirm_name', '').strip()
        if confirm_name != role.display_name:
            messages.error(
                request,
                f'Confirmation failed. You must type "{role.display_name}" exactly to delete a system role.'
            )
            return redirect('role_list')

    role_name = role.display_name
    role.delete()
    messages.success(request, f'Role "{role_name}" has been deleted.')
    return redirect('role_list')


# ==================== USER MANAGEMENT ====================

@login_required
@administrator_required
def user_list(request):
    """Display active users"""
    search = request.GET.get('search', '')
    role_filter = request.GET.get('role', '')
    
    users = User.objects.filter(is_deleted=False)
    
    if search:
        users = users.filter(
            Q(username__icontains=search) |
            Q(email__icontains=search) |
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search)
        )
    
    if role_filter:
        users = users.filter(role=role_filter)
    
    users = users.order_by('-date_joined')

    # Build dynamic role counts from Role model
    all_roles = Role.objects.all()
    role_counts = {}
    for r in all_roles:
        role_counts[r.name] = User.objects.filter(role=r.name, is_deleted=False).count()

    deleted_count = User.objects.filter(is_deleted=True).count()

    context = {
        'users': users,
        'search': search,
        'role_filter': role_filter,
        'role_counts': role_counts,
        'roles': all_roles,
        'deleted_count': deleted_count,
    }
    return render(request, 'accounts/user_list.html', context)

@login_required
@administrator_required
def user_trash(request):
    """Display trashed users"""
    users = User.objects.filter(is_deleted=True).order_by('-deleted_at')
    context = {'users': users}
    return render(request, 'accounts/user_trash.html', context)

@login_required
@administrator_required
def user_create(request):
    """Create new user with custom permissions"""
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        role = request.POST.get('role')
        phone = request.POST.get('phone', '')
        is_active = request.POST.get('is_active') == 'on'
        
        # Validation
        if not username or not password or not email:
            messages.error(request, '❌ Username, email, and password are required!')
            return render(request, 'accounts/user_create.html', {
                'roles': Role.objects.all(),
            })
        
        if User.objects.filter(username=username).exists():
            messages.error(request, f'❌ Username "{username}" already exists!')
            return render(request, 'accounts/user_create.html', {'roles': Role.objects.all()})

        if User.objects.filter(email=email).exists():
            messages.error(request, f'❌ Email "{email}" is already registered!')
            return render(request, 'accounts/user_create.html', {'roles': Role.objects.all()})
        
        try:
            # Create user
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                role=role,
                phone=phone,
                is_active=is_active,
                created_by=request.user
            )
            
            # Set default permissions first
            user.set_default_permissions_by_role()
            
            # Administrator gets everything (skip custom permissions)
            if role == 'administrator':
                user.is_staff = True
                user.is_superuser = True
                # Grant all permissions
                user.can_view_orders = True
                user.can_create_orders = True
                user.can_edit_orders = True
                user.can_delete_orders = True
                user.can_cancel_orders = True
                user.can_view_on_hold_orders = True
                
                user.can_view_products = True
                user.can_create_products = True
                user.can_edit_products = True
                user.can_delete_products = True
                
                user.can_view_customers = True
                user.can_create_customers = True
                user.can_edit_customers = True
                user.can_delete_customers = True
                
                user.can_view_returns = True
                user.can_create_returns = True
                user.can_edit_returns = True
                user.can_delete_returns = True
                user.can_approve_returns = True
                user.can_process_refunds = True

                user.can_view_targets = True
                user.can_set_targets = True
                user.can_edit_targets = True
                user.can_delete_targets = True
                user.can_view_own_targets = True

                user.can_view_dispatch = True
                user.can_manage_dispatch = True
                user.can_delete_dispatch = True
                user.can_scan_barcodes = True

                user.can_view_inventory = True
                user.can_manage_inventory = True
                user.can_adjust_stock = True
                user.can_view_inventory_cost = True
                
                user.can_view_reports = True
                user.can_view_sales_reports = True
                user.can_view_financial_reports = True
                user.can_export_data = True
                
                user.can_view_cost_price = True
                user.can_edit_prices = True
                user.can_give_discounts = True
                user.max_discount_percent = Decimal('100')

                user.can_view_purchases = True
                user.can_create_purchases = True
                user.can_manage_suppliers = True
                user.can_make_supplier_payments = True

                user.can_view_staff_performance = True

                # NCM LOGISTICS MODULE
                user.can_view_ncm_orders = True
                user.can_create_ncm_orders = True
                user.can_edit_ncm_orders = True
                user.can_delete_ncm_orders = True
                user.can_view_ncm_bulk_logs = True
                user.can_manage_ncm_bulk_logs = True
                user.can_view_ncm_trash = True
                user.can_sync_ncm_orders = True
                user.can_view_ncm_branches = True
                user.can_manage_ncm_branches = True
                
                # Dashboard Module
                user.can_view_dashboard = True
                user.can_view_total_revenue = True
            else:
                # Apply custom permissions from checkboxes
                # Dashboard Module
                user.can_view_dashboard = request.POST.get('can_view_dashboard') == 'on'
                user.can_view_total_revenue = request.POST.get('can_view_total_revenue') == 'on'
                
                # Orders Module
                user.can_view_orders = request.POST.get('can_view_orders') == 'on'
                user.can_create_orders = request.POST.get('can_create_orders') == 'on'
                user.can_edit_orders = request.POST.get('can_edit_orders') == 'on'
                user.can_delete_orders = request.POST.get('can_delete_orders') == 'on'
                user.can_cancel_orders = request.POST.get('can_cancel_orders') == 'on'
                user.can_view_on_hold_orders = request.POST.get('can_view_on_hold_orders') == 'on'
                
                # Products Module
                user.can_view_products = request.POST.get('can_view_products') == 'on'
                user.can_create_products = request.POST.get('can_create_products') == 'on'
                user.can_edit_products = request.POST.get('can_edit_products') == 'on'
                user.can_delete_products = request.POST.get('can_delete_products') == 'on'
                
                # Customers Module
                user.can_view_customers = request.POST.get('can_view_customers') == 'on'
                user.can_create_customers = request.POST.get('can_create_customers') == 'on'
                user.can_edit_customers = request.POST.get('can_edit_customers') == 'on'
                user.can_delete_customers = request.POST.get('can_delete_customers') == 'on'
                
                # Returns Module
                user.can_view_returns = request.POST.get('can_view_returns') == 'on'
                user.can_create_returns = request.POST.get('can_create_returns') == 'on'
                user.can_edit_returns = request.POST.get('can_edit_returns') == 'on'
                user.can_delete_returns = request.POST.get('can_delete_returns') == 'on'
                user.can_approve_returns = request.POST.get('can_approve_returns') == 'on'
                user.can_process_refunds = request.POST.get('can_process_refunds') == 'on'

                # Staff Targets Module
                user.can_view_targets = request.POST.get('can_view_targets') == 'on'
                user.can_set_targets = request.POST.get('can_set_targets') == 'on'
                user.can_edit_targets = request.POST.get('can_edit_targets') == 'on'
                user.can_delete_targets = request.POST.get('can_delete_targets') == 'on'
                user.can_view_own_targets = request.POST.get('can_view_own_targets') == 'on'

                # Dispatch Module
                user.can_view_dispatch = request.POST.get('can_view_dispatch') == 'on'
                user.can_manage_dispatch = request.POST.get('can_manage_dispatch') == 'on'
                user.can_delete_dispatch = request.POST.get('can_delete_dispatch') == 'on'
                user.can_scan_barcodes = request.POST.get('can_scan_barcodes') == 'on'

                # Inventory Module
                user.can_view_inventory = request.POST.get('can_view_inventory') == 'on'
                user.can_manage_inventory = request.POST.get('can_manage_inventory') == 'on'
                user.can_adjust_stock = request.POST.get('can_adjust_stock') == 'on'
                user.can_view_inventory_cost = request.POST.get('can_view_inventory_cost') == 'on'
                
                # Reports Module
                user.can_view_reports = request.POST.get('can_view_reports') == 'on'
                user.can_view_sales_reports = request.POST.get('can_view_sales_reports') == 'on'
                user.can_view_financial_reports = request.POST.get('can_view_financial_reports') == 'on'
                user.can_export_data = request.POST.get('can_export_data') == 'on'
                
                # Pricing Module
                user.can_view_cost_price = request.POST.get('can_view_cost_price') == 'on'
                user.can_edit_prices = request.POST.get('can_edit_prices') == 'on'
                user.can_give_discounts = request.POST.get('can_give_discounts') == 'on'

                # Max Discount
                max_discount = request.POST.get('max_discount_percent', '0')
                try:
                    user.max_discount_percent = Decimal(max_discount)
                except:
                    user.max_discount_percent = Decimal('0')

                # Purchase Management Module
                user.can_view_purchases = request.POST.get('can_view_purchases') == 'on'
                user.can_create_purchases = request.POST.get('can_create_purchases') == 'on'
                user.can_manage_suppliers = request.POST.get('can_manage_suppliers') == 'on'
                user.can_make_supplier_payments = request.POST.get('can_make_supplier_payments') == 'on'

                # Staff Performance Module
                user.can_view_staff_performance = request.POST.get('can_view_staff_performance') == 'on'

                # City Management Module
                user.can_view_cities = request.POST.get('can_view_cities') == 'on'
                user.can_add_cities = request.POST.get('can_add_cities') == 'on'
                user.can_edit_cities = request.POST.get('can_edit_cities') == 'on'
                user.can_delete_cities = request.POST.get('can_delete_cities') == 'on'

                # NCM LOGISTICS MODULE
                user.can_view_ncm_orders = request.POST.get('can_view_ncm_orders') == 'on'
                user.can_create_ncm_orders = request.POST.get('can_create_ncm_orders') == 'on'
                user.can_edit_ncm_orders = request.POST.get('can_edit_ncm_orders') == 'on'
                user.can_delete_ncm_orders = request.POST.get('can_delete_ncm_orders') == 'on'
                user.can_view_ncm_bulk_logs = request.POST.get('can_view_ncm_bulk_logs') == 'on'
                user.can_manage_ncm_bulk_logs = request.POST.get('can_manage_ncm_bulk_logs') == 'on'
                user.can_view_ncm_trash = request.POST.get('can_view_ncm_trash') == 'on'
                user.can_sync_ncm_orders = request.POST.get('can_sync_ncm_orders') == 'on'
                user.can_view_ncm_branches = request.POST.get('can_view_ncm_branches') == 'on'
                user.can_manage_ncm_branches = request.POST.get('can_manage_ncm_branches') == 'on'
            
            user.save()
            
            messages.success(request, f'✅ User "{username}" created successfully with role: {role.title()}!')
            return redirect('user_list')
        
        except Exception as e:
            messages.error(request, f'❌ Error creating user: {str(e)}')
            return render(request, 'accounts/user_create.html', {'roles': Role.objects.all()})

    # GET request - show form
    return render(request, 'accounts/user_create.html', {
        'roles': Role.objects.all(),
    })

@login_required
@administrator_required
def user_edit(request, user_id):
    """Edit user and permissions"""
    edit_user = get_object_or_404(User, id=user_id, is_deleted=False)
    
    if request.method == 'POST':
        # Basic Information
        edit_user.username = request.POST.get('username')
        edit_user.email = request.POST.get('email')
        edit_user.first_name = request.POST.get('first_name', '')
        edit_user.last_name = request.POST.get('last_name', '')
        edit_user.role = request.POST.get('role')
        edit_user.phone = request.POST.get('phone', '')
        edit_user.is_active = request.POST.get('is_active') == 'on'
        
        # Update password if provided
        new_password = request.POST.get('password')
        if new_password:
            edit_user.set_password(new_password)
            messages.info(request, '🔐 Password updated successfully!')
        
        # Update permissions based on role
        if edit_user.role == 'administrator':
            # Administrator gets all permissions automatically
            edit_user.is_staff = True
            edit_user.is_superuser = True
            
            # Grant all permissions
            edit_user.can_view_orders = True
            edit_user.can_create_orders = True
            edit_user.can_edit_orders = True
            edit_user.can_delete_orders = True
            edit_user.can_cancel_orders = True
            edit_user.can_view_on_hold_orders = True
            
            edit_user.can_view_products = True
            edit_user.can_create_products = True
            edit_user.can_edit_products = True
            edit_user.can_delete_products = True
            
            edit_user.can_view_customers = True
            edit_user.can_create_customers = True
            edit_user.can_edit_customers = True
            edit_user.can_delete_customers = True
            
            edit_user.can_view_returns = True
            edit_user.can_create_returns = True
            edit_user.can_edit_returns = True
            edit_user.can_delete_returns = True
            edit_user.can_approve_returns = True
            edit_user.can_process_refunds = True

            edit_user.can_view_targets = True
            edit_user.can_set_targets = True
            edit_user.can_edit_targets = True
            edit_user.can_delete_targets = True
            edit_user.can_view_own_targets = True

            edit_user.can_view_dispatch = True
            edit_user.can_manage_dispatch = True
            edit_user.can_delete_dispatch = True
            edit_user.can_scan_barcodes = True

            edit_user.can_view_inventory = True
            edit_user.can_manage_inventory = True
            edit_user.can_adjust_stock = True
            edit_user.can_view_inventory_cost = True
            
            edit_user.can_view_reports = True
            edit_user.can_view_sales_reports = True
            edit_user.can_view_daily_sales_reports = True
            edit_user.can_view_product_sales_reports = True
            edit_user.can_view_financial_reports = True
            edit_user.can_export_data = True
            
            edit_user.can_view_cost_price = True
            edit_user.can_edit_prices = True
            edit_user.can_give_discounts = True
            edit_user.max_discount_percent = Decimal('100')

            edit_user.can_view_purchases = True
            edit_user.can_create_purchases = True
            edit_user.can_manage_suppliers = True
            edit_user.can_make_supplier_payments = True

            edit_user.can_view_staff_performance = True

            edit_user.can_view_cities = True
            edit_user.can_add_cities = True
            edit_user.can_edit_cities = True
            edit_user.can_delete_cities = True

            # NCM LOGISTICS MODULE
            edit_user.can_view_ncm_orders = True
            edit_user.can_create_ncm_orders = True
            edit_user.can_edit_ncm_orders = True
            edit_user.can_delete_ncm_orders = True
            edit_user.can_view_ncm_bulk_logs = True
            edit_user.can_manage_ncm_bulk_logs = True
            edit_user.can_view_ncm_trash = True
            edit_user.can_sync_ncm_orders = True
            edit_user.can_view_ncm_branches = True
            edit_user.can_manage_ncm_branches = True

            # Dashboard Module
            edit_user.can_view_dashboard = True
            edit_user.can_view_total_revenue = True

            messages.info(request, '👑 Administrator role - All permissions granted automatically')
        else:
            # Remove admin privileges
            edit_user.is_staff = False
            edit_user.is_superuser = False
            
            # Dashboard Module
            edit_user.can_view_dashboard = request.POST.get('can_view_dashboard') == 'on'
            edit_user.can_view_total_revenue = request.POST.get('can_view_total_revenue') == 'on'
            
            # Orders Module
            edit_user.can_view_orders = request.POST.get('can_view_orders') == 'on'
            edit_user.can_create_orders = request.POST.get('can_create_orders') == 'on'
            edit_user.can_edit_orders = request.POST.get('can_edit_orders') == 'on'
            edit_user.can_delete_orders = request.POST.get('can_delete_orders') == 'on'
            edit_user.can_cancel_orders = request.POST.get('can_cancel_orders') == 'on'
            edit_user.can_view_on_hold_orders = request.POST.get('can_view_on_hold_orders') == 'on'
            
            # Products Module
            edit_user.can_view_products = request.POST.get('can_view_products') == 'on'
            edit_user.can_create_products = request.POST.get('can_create_products') == 'on'
            edit_user.can_edit_products = request.POST.get('can_edit_products') == 'on'
            edit_user.can_delete_products = request.POST.get('can_delete_products') == 'on'
            
            # Customers Module
            edit_user.can_view_customers = request.POST.get('can_view_customers') == 'on'
            edit_user.can_create_customers = request.POST.get('can_create_customers') == 'on'
            edit_user.can_edit_customers = request.POST.get('can_edit_customers') == 'on'
            edit_user.can_delete_customers = request.POST.get('can_delete_customers') == 'on'
            
            # Returns Module
            edit_user.can_view_returns = request.POST.get('can_view_returns') == 'on'
            edit_user.can_create_returns = request.POST.get('can_create_returns') == 'on'
            edit_user.can_edit_returns = request.POST.get('can_edit_returns') == 'on'
            edit_user.can_delete_returns = request.POST.get('can_delete_returns') == 'on'
            edit_user.can_approve_returns = request.POST.get('can_approve_returns') == 'on'
            edit_user.can_process_refunds = request.POST.get('can_process_refunds') == 'on'

            # Staff Targets Module
            edit_user.can_view_targets = request.POST.get('can_view_targets') == 'on'
            edit_user.can_set_targets = request.POST.get('can_set_targets') == 'on'
            edit_user.can_edit_targets = request.POST.get('can_edit_targets') == 'on'
            edit_user.can_delete_targets = request.POST.get('can_delete_targets') == 'on'
            edit_user.can_view_own_targets = request.POST.get('can_view_own_targets') == 'on'

            # Dispatch Module
            edit_user.can_view_dispatch = request.POST.get('can_view_dispatch') == 'on'
            edit_user.can_manage_dispatch = request.POST.get('can_manage_dispatch') == 'on'
            edit_user.can_delete_dispatch = request.POST.get('can_delete_dispatch') == 'on'
            edit_user.can_scan_barcodes = request.POST.get('can_scan_barcodes') == 'on'

            # Inventory Module
            edit_user.can_view_inventory = request.POST.get('can_view_inventory') == 'on'
            edit_user.can_manage_inventory = request.POST.get('can_manage_inventory') == 'on'
            edit_user.can_adjust_stock = request.POST.get('can_adjust_stock') == 'on'
            edit_user.can_view_inventory_cost = request.POST.get('can_view_inventory_cost') == 'on'
            
            # Reports Module
            edit_user.can_view_reports = request.POST.get('can_view_reports') == 'on'
            edit_user.can_view_sales_reports = request.POST.get('can_view_sales_reports') == 'on'
            edit_user.can_view_daily_sales_reports = request.POST.get('can_view_daily_sales_reports') == 'on'
            edit_user.can_view_product_sales_reports = request.POST.get('can_view_product_sales_reports') == 'on'
            edit_user.can_view_financial_reports = request.POST.get('can_view_financial_reports') == 'on'
            edit_user.can_export_data = request.POST.get('can_export_data') == 'on'
            
            # Pricing & Discount Module
            edit_user.can_view_cost_price = request.POST.get('can_view_cost_price') == 'on'
            edit_user.can_edit_prices = request.POST.get('can_edit_prices') == 'on'
            edit_user.can_give_discounts = request.POST.get('can_give_discounts') == 'on'

            # Max Discount Percentage
            max_discount = request.POST.get('max_discount_percent', '0')
            try:
                edit_user.max_discount_percent = Decimal(max_discount)
            except (ValueError, InvalidOperation):
                edit_user.max_discount_percent = Decimal('0')

            # Purchase Management Module
            edit_user.can_view_purchases = request.POST.get('can_view_purchases') == 'on'
            edit_user.can_create_purchases = request.POST.get('can_create_purchases') == 'on'
            edit_user.can_manage_suppliers = request.POST.get('can_manage_suppliers') == 'on'
            edit_user.can_make_supplier_payments = request.POST.get('can_make_supplier_payments') == 'on'

            # Staff Performance Module
            edit_user.can_view_staff_performance = request.POST.get('can_view_staff_performance') == 'on'

            # City Management Module
            edit_user.can_view_cities = request.POST.get('can_view_cities') == 'on'
            edit_user.can_add_cities = request.POST.get('can_add_cities') == 'on'
            edit_user.can_edit_cities = request.POST.get('can_edit_cities') == 'on'
            edit_user.can_delete_cities = request.POST.get('can_delete_cities') == 'on'

            # NCM LOGISTICS MODULE
            edit_user.can_view_ncm_orders = request.POST.get('can_view_ncm_orders') == 'on'
            edit_user.can_create_ncm_orders = request.POST.get('can_create_ncm_orders') == 'on'
            edit_user.can_edit_ncm_orders = request.POST.get('can_edit_ncm_orders') == 'on'
            edit_user.can_delete_ncm_orders = request.POST.get('can_delete_ncm_orders') == 'on'
            edit_user.can_view_ncm_bulk_logs = request.POST.get('can_view_ncm_bulk_logs') == 'on'
            edit_user.can_manage_ncm_bulk_logs = request.POST.get('can_manage_ncm_bulk_logs') == 'on'
            edit_user.can_view_ncm_trash = request.POST.get('can_view_ncm_trash') == 'on'
            edit_user.can_sync_ncm_orders = request.POST.get('can_sync_ncm_orders') == 'on'
            edit_user.can_view_ncm_branches = request.POST.get('can_view_ncm_branches') == 'on'
            edit_user.can_manage_ncm_branches = request.POST.get('can_manage_ncm_branches') == 'on'
        
        try:
            edit_user.save()
            messages.success(request, f'✅ User "{edit_user.username}" updated successfully!')
            return redirect('user_list')
        except Exception as e:
            messages.error(request, f'❌ Error updating user: {str(e)}')
    
    # GET request - render form
    return render(request, 'accounts/user_edit.html', {
        'edit_user': edit_user,
        'roles': Role.objects.all(),
    })
@login_required
@administrator_required
def user_soft_delete(request, user_id):
    user = get_object_or_404(User, id=user_id, is_deleted=False)
    
    if user.id == request.user.id:
        messages.error(request, '❌ You cannot delete yourself!')
        return redirect('user_list')
    
    if user.is_superuser:
        messages.error(request, '❌ Cannot delete superuser!')
        return redirect('user_list')
    
    username = user.username
    user.soft_delete(deleted_by_user=request.user)
    messages.success(request, f'🗑️ User "{username}" moved to trash!')
    return redirect('user_list')

@login_required
@administrator_required
def user_restore(request, user_id):
    user = get_object_or_404(User, id=user_id, is_deleted=True)
    username = user.username
    user.restore()
    messages.success(request, f'♻️ User "{username}" restored!')
    return redirect('user_trash')

@login_required
@administrator_required
def user_hard_delete(request, user_id):
    user = get_object_or_404(User, id=user_id, is_deleted=True)
    
    if user.id == request.user.id:
        messages.error(request, '❌ You cannot delete yourself!')
        return redirect('user_trash')
    
    username = user.username
    user.delete()
    messages.success(request, f'🔥 User "{username}" permanently deleted!')
    return redirect('user_trash')

@login_required
@administrator_required
def user_toggle_status(request, user_id):
    user = get_object_or_404(User, id=user_id, is_deleted=False)
    
    if user.id == request.user.id:
        messages.error(request, '❌ You cannot deactivate yourself!')
        return redirect('user_list')
    
    user.is_active = not user.is_active
    user.save()
    
    status = "activated" if user.is_active else "deactivated"
    messages.success(request, f'✅ User "{user.username}" {status}!')
    return redirect('user_list')



@login_required
def profile_view(request):
    """Display user profile page with statistics and permissions"""
    user = request.user
    
    # Calculate days active
    days_active = (timezone.now().date() - user.date_joined.date()).days
    
    # Calculate days since last login
    if user.last_login:
        days_since_login = (timezone.now() - user.last_login).days
    else:
        days_since_login = None
    
    # Initialize statistics
    stats = {
        'orders_count': 0,
        'orders_value': Decimal('0'),
        'customers_count': 0,
        'products_count': 0,
        'returns_count': 0,
        'dispatch_count': 0,
        'low_stock_count': 0,
    }
    
    try:
        # Use models from the dashboard app (single app structure)
        from dashboard.models import Order, Customer, Product, ReturnRequest
        
        # Orders Statistics (only if user has permission)
        if user.can_view_orders or user.role == 'administrator':
            if user.role == 'administrator':
                orders = Order.objects.filter(is_deleted=False)
            elif user.role == 'sales':
                # Sales can see their own orders
                orders = Order.objects.filter(created_by=user, is_deleted=False)
            else:
                orders = Order.objects.filter(is_deleted=False)
            
            stats['orders_count'] = orders.count()
            stats['orders_value'] = orders.aggregate(
                total=Sum('total_amount')
            )['total'] or Decimal('0')
        
        # Customers Statistics
        if user.can_view_customers or user.role == 'administrator':
            if user.role == 'sales':
                stats['customers_count'] = Customer.objects.filter(
                    created_by=user, 
                    is_deleted=False
                ).count()
            else:
                stats['customers_count'] = Customer.objects.filter(
                    is_deleted=False
                ).count()
        
        # Products Statistics
        if user.can_view_products or user.role == 'administrator':
            stats['products_count'] = Product.objects.filter(
                is_deleted=False
            ).count()
        
        # Returns Statistics
        if user.can_view_returns or user.role == 'administrator':
            if user.role == 'administrator':
                returns = ReturnRequest.objects.filter(is_deleted=False)
            else:
                returns = ReturnRequest.objects.filter(created_by=user, is_deleted=False)
            
            stats['returns_count'] = returns.count()
            stats['pending_returns'] = returns.filter(return_status='pending').count()
            stats['approved_returns'] = returns.filter(return_status='approved').count()
        
        # Dispatch Statistics (fallback to orders with dispatched/in_transit status)
        if user.can_view_dispatch or user.role == 'administrator':
            stats['dispatch_count'] = Order.objects.filter(
                is_deleted=False,
                order_status__in=['dispatched', 'in_transit']
            ).count()
        
        # Inventory Statistics
        if user.can_view_inventory or user.role == 'administrator':
            stats['low_stock_count'] = Product.objects.filter(
                is_deleted=False,
                stock__lte=10
            ).count()
    
    except ImportError as e:
        # Models not available, use default values
        pass
    except Exception as e:
        # Handle other errors gracefully
        pass

    # Recent Activity (last 7 days)
    recent_activity = []
    try:
        from dashboard.models import Order, ReturnRequest
        
        # Recent orders
        if user.can_view_orders or user.role == 'administrator':
            recent_orders = Order.objects.filter(
                created_at__gte=timezone.now() - timezone.timedelta(days=7),
                is_deleted=False
            ).order_by('-created_at')[:5]
            
            for order in recent_orders:
                recent_activity.append({
                    'type': 'order',
                    'icon': 'shopping-cart',
                    'color': 'primary',
                    'title': f'Order #{order.order_number}',
                    'description': f'रू {order.total_amount}',
                    'date': order.created_at
                })
        
        # Recent returns
        if user.can_view_returns or user.role == 'administrator':
            recent_returns = ReturnRequest.objects.filter(
                created_at__gte=timezone.now() - timezone.timedelta(days=7),
                is_deleted=False
            ).order_by('-created_at')[:5]
            
            for ret in recent_returns:
                recent_activity.append({
                    'type': 'return',
                    'icon': 'undo-alt',
                    'color': 'danger',
                    'title': f'Return #{ret.rma_number}',
                    'description': f'Order #{ret.order.order_number}',
                    'date': ret.created_at
                })
        
        # Sort by date
        recent_activity = sorted(recent_activity, key=lambda x: x['date'], reverse=True)[:10]
    
    except:
        pass
    
    # Permission Summary
    permission_summary = {
        'total_permissions': 0,
        'enabled_permissions': 0,
    }
    
    # Count all permission fields
    permission_fields = [
        'can_view_orders', 'can_create_orders', 'can_edit_orders', 'can_delete_orders', 'can_cancel_orders', 'can_view_on_hold_orders',
        'can_view_products', 'can_create_products', 'can_edit_products', 'can_delete_products',
        'can_view_customers', 'can_create_customers', 'can_edit_customers', 'can_delete_customers',
        'can_view_returns', 'can_create_returns', 'can_edit_returns', 'can_delete_returns', 'can_approve_returns', 'can_process_refunds',
        'can_view_targets', 'can_set_targets', 'can_edit_targets', 'can_delete_targets', 'can_view_own_targets',
        'can_view_dispatch', 'can_manage_dispatch', 'can_delete_dispatch', 'can_scan_barcodes',
        'can_view_inventory', 'can_manage_inventory', 'can_adjust_stock',
        'can_view_reports', 'can_view_sales_reports', 'can_view_financial_reports', 'can_export_data',
        'can_view_cost_price', 'can_edit_prices', 'can_give_discounts',
        'can_view_purchases', 'can_create_purchases', 'can_manage_suppliers', 'can_make_supplier_payments',
        'can_view_staff_performance',
    ]
    
    permission_summary['total_permissions'] = len(permission_fields)
    permission_summary['enabled_permissions'] = sum(
        1 for field in permission_fields if getattr(user, field, False)
    )
    permission_summary['percentage'] = round(
        (permission_summary['enabled_permissions'] / permission_summary['total_permissions']) * 100, 1
    ) if permission_summary['total_permissions'] > 0 else 0
    
    context = {
        'user': user,
        'days_active': days_active,
        'days_since_login': days_since_login,
        'stats': stats,
        'recent_activity': recent_activity,
        'permission_summary': permission_summary,
        'login_count': 0,  # Implement login tracking if needed (requires custom model)
        'tasks_completed': 0,  # Implement task tracking if needed
    }
    
    return render(request, 'accounts/profile_page.html', context)
@login_required
def profile_update(request):
    """Update user profile information"""
    if request.method == 'POST':
        user = request.user
        
        # Get form data
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        
        # Validation flags
        has_errors = False
        
        # Validate email uniqueness (excluding current user)
        if email and email != user.email:
            from .models import User
            if User.objects.filter(email=email).exclude(id=user.id).exists():
                messages.error(request, '❌ This email is already registered to another user.')
                has_errors = True
        
        # Validate email format
        if email:
            import re
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, email):
                messages.error(request, '❌ Please enter a valid email address.')
                has_errors = True
        
        # Validate phone number (if provided)
        if phone:
            # Remove any spaces or special characters
            phone_cleaned = ''.join(filter(str.isdigit, phone))
            if len(phone_cleaned) < 10 or len(phone_cleaned) > 15:
                messages.error(request, '❌ Phone number must be between 10-15 digits.')
                has_errors = True
        
        # If validation errors, redirect back
        if has_errors:
            return redirect('profile')
        
        # Update basic fields
        user.first_name = first_name
        user.last_name = last_name
        user.email = email
        
        # Update phone if the field exists in your User model
        if hasattr(user, 'phone'):
            user.phone = phone
        
        # Handle profile picture upload
        if 'profile_picture' in request.FILES:
            profile_picture = request.FILES['profile_picture']
            
            # Validate file size (2MB max)
            max_size = 2 * 1024 * 1024  # 2MB
            if profile_picture.size > max_size:
                messages.error(request, '❌ Profile picture must be less than 2MB.')
                return redirect('profile')
            
            # Validate file type
            allowed_types = ['image/jpeg', 'image/png', 'image/jpg', 'image/webp']
            if profile_picture.content_type not in allowed_types:
                messages.error(request, '❌ Only JPG, PNG, and WebP images are allowed.')
                return redirect('profile')
            
            try:
                # Open and validate image
                img = Image.open(profile_picture)
                img.verify()  # Verify it's a valid image
                
                # Re-open for processing
                profile_picture.seek(0)
                img = Image.open(profile_picture)
                
                # Convert RGBA to RGB if necessary
                if img.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = background
                
                # Resize image if too large (max 800x800)
                max_dimension = 800
                if img.width > max_dimension or img.height > max_dimension:
                    img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                
                # Save optimized image
                output = BytesIO()
                img.save(output, format='JPEG', quality=85, optimize=True)
                output.seek(0)
                
                # Generate unique filename
                import uuid
                file_extension = 'jpg'
                filename = f'profile_pictures/{user.id}_{uuid.uuid4().hex[:8]}.{file_extension}'
                
                # Delete old profile picture if exists
                if hasattr(user, 'profile_picture') and user.profile_picture:
                    try:
                        if default_storage.exists(user.profile_picture.name):
                            default_storage.delete(user.profile_picture.name)
                    except Exception as e:
                        pass

                # Save new profile picture
                if hasattr(user, 'profile_picture'):
                    user.profile_picture.save(
                        filename,
                        ContentFile(output.read()),
                        save=False
                    )
                    messages.success(request, '📸 Profile picture updated successfully!')
            
            except Exception as e:
                messages.error(request, f'❌ Error processing image: Invalid image file.')
                return redirect('profile')
        
        try:
            user.save()
            messages.success(request, '✅ Your profile has been updated successfully!')
        except Exception as e:
            messages.error(request, f'❌ Error updating profile: {str(e)}')
    
    return redirect('profile')

@login_required
def profile_picture_delete(request):
    """Delete user profile picture"""
    if request.method == 'POST':
        user = request.user
        
        if hasattr(user, 'profile_picture') and user.profile_picture:
            try:
                # Delete file from storage
                if default_storage.exists(user.profile_picture.name):
                    default_storage.delete(user.profile_picture.name)
                
                # Clear the field
                user.profile_picture = None
                user.save()
                
                messages.success(request, '🗑️ Profile picture deleted successfully!')
            except Exception as e:
                messages.error(request, f'❌ Error deleting profile picture: {str(e)}')
        else:
            messages.warning(request, '⚠️ No profile picture to delete.')
    
    return redirect('profile')


@login_required
def change_password(request):
    """Change user password using Django's built-in form"""
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        
        if form.is_valid():
            user = form.save()
            
            # Keep the user logged in after password change
            update_session_auth_hash(request, user)
            
            messages.success(request, '✅ Your password has been changed successfully!')
            return redirect('profile')
        else:
            # Display all form errors
            for field, errors in form.errors.items():
                for error in errors:
                    if field == '__all__':
                        messages.error(request, f'❌ {error}')
                    else:
                        field_name = field.replace('_', ' ').title()
                        messages.error(request, f'❌ {field_name}: {error}')
    
    return redirect('profile')