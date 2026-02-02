from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps


def permission_required(*permissions):
    """
    Decorator to check if user has specific permissions
    Usage: @permission_required('can_create_orders', 'can_view_orders')
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = request.user
            
            # ✅ Check if Administrator or Superuser
            if user.is_superuser or user.role == 'administrator':
                return view_func(request, *args, **kwargs)
            
            # Check if user has ALL required permissions
            for permission in permissions:
                if not getattr(user, permission, False):
                    # ✅ ADD SPECIAL FLAG FOR MODAL
                    messages.error(request, '❌ You do not have permission to access this page.', extra_tags='permission_denied')
                    return redirect('dashboard')
            
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def admin_or_permission_required(*permissions):
    """
    Decorator: Admin OR has specific permissions
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = request.user
            
            if user.is_superuser or user.role == 'administrator':
                return view_func(request, *args, **kwargs)
            
            # Check if user has ANY of the permissions
            has_permission = any(getattr(user, perm, False) for perm in permissions)
            
            if not has_permission:
                messages.error(request, '❌ You do not have permission to access this page.', extra_tags='permission_denied')
                return redirect('dashboard')
            
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def admin_only(view_func):
    """
    Only administrators can access
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # ✅ Check if Administrator or Superuser
        if not (request.user.is_superuser or request.user.role == 'administrator'):
            messages.error(request, '❌ Only administrators can access this page.', extra_tags='permission_denied')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper
