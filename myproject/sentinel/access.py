"""
Access rules for the vault.

Follows this project's convention (boolean attributes on CustomUser checked by a
decorator, administrators bypassing everything) rather than Django's permission
framework — see accounts/decorators.py.

The one addition: `scope_to_visible()`. A user with can_view_audit_trail but
without can_view_all_users_activity gets a real, working page — scoped to their
own trail — instead of a redirect. Letting staff see their own history is a
feature, not a leak.
"""

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def is_admin(user):
    return bool(getattr(user, 'is_superuser', False) or getattr(user, 'role', '') == 'administrator')


def can(user, permission):
    return is_admin(user) or bool(getattr(user, permission, False))


def can_see_everyone(user):
    return can(user, 'can_view_all_users_activity')


def vault_access(*permissions):
    """Require login plus at least one of `permissions` (default: can_view_audit_trail)."""
    required = permissions or ('can_view_audit_trail',)

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect('login')
            if not any(can(user, permission) for permission in required):
                messages.error(
                    request,
                    '❌ You do not have permission to access Sentinel Vault.',
                    extra_tags='permission_denied',
                )
                return redirect('dashboard')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def scope_to_visible(queryset, user, field='actor'):
    """Narrow a queryset to what `user` is allowed to see."""
    if can_see_everyone(user):
        return queryset
    return queryset.filter(**{field: user})


# ─────────────────────────────────────────────────────────────────────────────
# Who may act *on* whom
# ─────────────────────────────────────────────────────────────────────────────

def may_act_on(actor, subject):
    """Can `actor` take a disruptive action against `subject`?

    can_manage_sessions is a normal staff permission — it exists so a supervisor
    can kick a stuck terminal. Without this rule it also lets that supervisor
    revoke the owner's session or block the owner's only device, which is a
    lockout of the person who administers the system. Administrators are only
    actionable by other administrators.
    """
    if subject is None:
        return True
    if is_admin(actor):
        return True
    if actor is not None and getattr(subject, 'pk', None) == getattr(actor, 'pk', None):
        return True          # acting on yourself is always allowed
    return not is_admin(subject)


def may_purge(actor):
    """Who may delete records from the vault?

    Administrators only — not `can_configure_audit`, which is a tuning
    permission granted to supervisors. Erasing the trail is the one action a
    person under investigation would most want, so it stays with the rank that
    can already grant and revoke every other permission. Everything a purge
    removes is gone for good; there is no undo to fall back on.
    """
    return is_admin(actor)


def may_judge_alert(actor, alert):
    """Can `actor` close/dismiss this alert?

    Nobody below administrator may clear an alert that is *about them* — an
    auditee silencing their own finding is the one failure that makes the whole
    console worthless. They can still read it; only the disposition is blocked.
    """
    if is_admin(actor):
        return True
    subject_id = getattr(alert, 'subject_user_id', None)
    return subject_id is None or subject_id != getattr(actor, 'pk', None)
