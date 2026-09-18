from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps


def is_admin(user):
    """Whether `user` bypasses permission checks entirely.

    The single definition of that rule. Every decorator below applies it, and
    so do the JSON endpoints that can't use a decorator (see has_any_permission).
    """
    return bool(getattr(user, 'is_superuser', False)) or getattr(user, 'role', None) == 'administrator'


def has_any_permission(user, *permissions):
    """True if `user` is an admin or holds at least one of `permissions`.

    For endpoints that must refuse in JSON. The decorators here answer a
    refusal with a redirect *and* a queued Django message, which is right for a
    page but wrong for anything a page polls or calls over AJAX: the caller
    gets HTML it can't parse, and the message resurfaces as a stray error toast
    on whatever page the user happens to load next. Such views check
    permissions with this and return their own JsonResponse.
    """
    if is_admin(user):
        return True
    return any(getattr(user, perm, False) for perm in permissions)


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
            if is_admin(user):
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

            if not has_any_permission(user, *permissions):
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
        if not is_admin(request.user):
            messages.error(request, '❌ Only administrators can access this page.', extra_tags='permission_denied')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper
