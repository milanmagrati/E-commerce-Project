"""
Sentinel Vault views.

Six screens plus a small JSON API:
  command_center  — the overview: pulse, risk posture, charts, live feed
  activity_stream — the full searchable ledger with a detail drawer
  session_monitor — who is signed in right now, on what, from where
  alert_console   — anomalies awaiting a human decision
  user_dossier    — one person's complete history, devices and behaviour profile
  vault_settings  — capture depth, retention and alert thresholds
"""

import csv
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Max, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import CustomUser
from dashboard.timezone_utils import convert_to_nepali, get_nepali_now
from . import services
from .access import (
    can, can_see_everyone, is_admin, may_act_on, may_judge_alert, may_purge,
    scope_to_visible, vault_access,
)
from .context import suppress_capture
from .models import (
    AuditEvent, DeviceKind, DeviceSession, EventType, KnownDevice,
    SecurityAlert, Severity, VaultSettings,
)

PAGE_SIZE = 40
RISK_BANDS = {
    'critical': (70, 100),
    'elevated': (40, 69),
    'low': (20, 39),
    'minimal': (0, 19),
}


# ─────────────────────────────────────────────────────────────────────────────
# Shared filtering
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(value):
    from datetime import datetime
    for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(value, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _filter_events(request, queryset):
    """Apply the shared filter bar to an AuditEvent queryset. Returns (queryset, active_filters)."""
    from dashboard.timezone_utils import nepali_day_end_exclusive, nepali_day_start

    params = request.GET
    active = {}

    search = (params.get('q') or '').strip()
    if search:
        queryset = queryset.filter(
            Q(action__icontains=search)
            | Q(actor_username__icontains=search)
            | Q(object_label__icontains=search)
            | Q(object_type__icontains=search)
            | Q(path__icontains=search)
            | Q(ip_address__icontains=search)
        )
        active['q'] = search

    # Char columns take any string safely; actor_id is an integer FK and Django
    # raises ValueError (a 500) on a non-numeric value, so it is validated here.
    # These are user-supplied query parameters — a crafted link must not 500.
    for key, field in (('event_type', 'event_type'), ('severity', 'severity'),
                       ('module', 'module')):
        value = (params.get(key) or '').strip()
        if value:
            queryset = queryset.filter(**{field: value})
            active[key] = value

    actor = (params.get('actor') or '').strip()
    if actor.isdigit():
        queryset = queryset.filter(actor_id=int(actor))
        active['actor'] = actor

    risk = (params.get('risk') or '').strip()
    if risk in RISK_BANDS:
        low, high = RISK_BANDS[risk]
        queryset = queryset.filter(risk_score__gte=low, risk_score__lte=high)
        active['risk'] = risk

    ip = (params.get('ip') or '').strip()
    if ip:
        # Validate before hitting a GenericIPAddressField — some backends error on
        # malformed input rather than simply matching nothing.
        import ipaddress as _ipaddress
        try:
            _ipaddress.ip_address(ip)
        except ValueError:
            queryset = queryset.filter(ip_address__startswith=ip[:45])
        else:
            queryset = queryset.filter(ip_address=ip)
        active['ip'] = ip

    device = (params.get('device') or '').strip()
    if device in dict(DeviceKind.choices):
        queryset = queryset.filter(device_kind=device)
        active['device'] = device

    session_id = (params.get('session') or '').strip()
    if session_id.isdigit():
        queryset = queryset.filter(session_id=int(session_id))
        active['session'] = session_id

    object_type = (params.get('object_type') or '').strip()
    if object_type:
        queryset = queryset.filter(object_type=object_type)
        active['object_type'] = object_type

    date_from = _parse_date(params.get('from'))
    if date_from:
        queryset = queryset.filter(created_at__gte=nepali_day_start(date_from))
        active['from'] = params.get('from')

    date_to = _parse_date(params.get('to'))
    if date_to:
        queryset = queryset.filter(created_at__lt=nepali_day_end_exclusive(date_to))
        active['to'] = params.get('to')

    preset = (params.get('range') or '').strip()
    preset_windows = {'1h': 1 / 24, '24h': 1, '7d': 7, '30d': 30, '90d': 90}
    if preset in preset_windows and not (date_from or date_to):
        queryset = queryset.filter(
            created_at__gte=timezone.now() - timedelta(days=preset_windows[preset]))
        active['range'] = preset

    return queryset, active


def _querystring(request):
    """Current GET parameters minus `page`, for the paginator to re-append.

    Without it, turning to page 2 of a filtered or searched list silently drops
    every filter — you end up on page 2 of something else entirely.
    """
    params = request.GET.copy()
    params.pop('page', None)
    return params.urlencode()


def _filter_choices(user):
    """Options for the filter bar, restricted to what this user may see."""
    actors = AuditEvent.objects.exclude(actor__isnull=True)
    actors = scope_to_visible(actors, user)
    return {
        'event_types': EventType.choices,
        'severities': Severity.choices,
        'modules': list(
            scope_to_visible(AuditEvent.objects.exclude(module=''), user)
            .values_list('module', flat=True).distinct().order_by('module')[:40]
        ),
        'actor_options': list(
            actors.values('actor_id', 'actor_username').distinct().order_by('actor_username')[:100]
        ),
        'risk_bands': list(RISK_BANDS.keys()),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Command centre
# ─────────────────────────────────────────────────────────────────────────────

@vault_access()
def command_center(request):
    user = request.user
    everyone = can_see_everyone(user)
    now = timezone.now()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    prev_day = now - timedelta(hours=48)

    # Keep "who's online" honest without a scheduler — see services.reap_stale_sessions.
    services.reap_stale_sessions()

    events = scope_to_visible(AuditEvent.objects.all(), user)
    today_events = events.filter(created_at__gte=day_ago)
    yesterday_events = events.filter(created_at__gte=prev_day, created_at__lt=day_ago)

    sessions = DeviceSession.objects.all()
    if not everyone:
        sessions = sessions.filter(user=user)
    live_sessions = sessions.filter(is_active=True).select_related('user', 'device')

    today_count = today_events.count()
    yesterday_count = yesterday_events.count()
    if yesterday_count:
        trend = round(((today_count - yesterday_count) / yesterday_count) * 100)
    else:
        trend = 100 if today_count else 0

    open_alerts = SecurityAlert.objects.filter(
        status__in=[SecurityAlert.Status.OPEN, SecurityAlert.Status.ACKNOWLEDGED])
    if not everyone:
        open_alerts = open_alerts.filter(subject_user=user)

    risk_average = today_events.aggregate(value=Avg('risk_score'))['value'] or 0

    stats = {
        'events_24h': today_count,
        'events_trend': trend,
        'live_sessions': live_sessions.count(),
        'live_users': live_sessions.values('user_id').distinct().count(),
        'open_alerts': open_alerts.count(),
        'critical_alerts': open_alerts.filter(severity=Severity.CRITICAL).count(),
        'failed_logins_24h': today_events.filter(event_type=EventType.LOGIN_FAILED).count(),
        'denials_24h': today_events.filter(event_type=EventType.DENIED).count(),
        'writes_24h': today_events.filter(
            event_type__in=[EventType.CREATE, EventType.UPDATE, EventType.DELETE]).count(),
        'deletes_24h': today_events.filter(event_type=EventType.DELETE).count(),
        'risk_average': round(risk_average),
        'devices_tracked': KnownDevice.objects.count() if everyone else
                           KnownDevice.objects.filter(user=user).count(),
        'new_devices_7d': (KnownDevice.objects.filter(first_seen__gte=week_ago).count()
                           if everyone else
                           KnownDevice.objects.filter(user=user, first_seen__gte=week_ago).count()),
    }

    # Posture: one number a manager can read at a glance.
    posture = 100
    posture -= min(40, stats['critical_alerts'] * 12)
    posture -= min(20, (stats['open_alerts'] - stats['critical_alerts']) * 3)
    posture -= min(20, stats['failed_logins_24h'] * 2)
    posture -= min(10, stats['denials_24h'])
    posture -= min(10, max(0, stats['risk_average'] - 20) // 3)
    posture = max(0, posture)

    context = {
        'stats': stats,
        'posture': posture,
        'posture_label': ('Secure' if posture >= 85 else 'Watchful' if posture >= 65
                          else 'Elevated' if posture >= 40 else 'Critical'),
        'timeline': services.activity_timeline(14, None if everyone else user),
        'heatmap': services.hourly_heatmap(7),
        'top_actors': services.top_actors(7) if everyone else [],
        'modules': services.module_breakdown(7),
        'event_mix': services.event_type_breakdown(7),
        'recent_events': events.select_related('actor')[:15],
        'live_sessions_list': live_sessions.order_by('-last_activity')[:8],
        'recent_alerts': open_alerts.select_related('subject_user')[:6],
        'high_risk': events.filter(risk_score__gte=40, created_at__gte=week_ago)
                           .select_related('actor')[:8],
        'can_see_everyone': everyone,
        'nepali_now': get_nepali_now(),
        'latest_event_id': events.aggregate(value=Max('id'))['value'] or 0,
        'vault_tab': 'home',
        'vault_title': 'Command Centre',
    }
    return render(request, 'sentinel/command_center.html', context)


# ─────────────────────────────────────────────────────────────────────────────
# Activity stream
# ─────────────────────────────────────────────────────────────────────────────

@vault_access()
def activity_stream(request):
    user = request.user
    events = scope_to_visible(
        AuditEvent.objects.select_related('actor', 'session'), user)
    events, active = _filter_events(request, events)

    total = events.count()
    summary = events.aggregate(
        writes=Count('id', filter=Q(event_type__in=[
            EventType.CREATE, EventType.UPDATE, EventType.DELETE])),
        risky=Count('id', filter=Q(risk_score__gte=40)),
        actors=Count('actor_id', distinct=True),
    )

    paginator = Paginator(events, PAGE_SIZE)
    page = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page,
        'total': total,
        'summary': summary,
        'active': active,
        'choices': _filter_choices(user),
        'querystring': _querystring(request),
        'can_export': can(user, 'can_export_audit_logs'),
        'can_purge': may_purge(user),
        'can_see_everyone': can_see_everyone(user),
        'vault_tab': 'stream',
        'vault_title': 'Activity Ledger',
        'vault_tagline': 'Every recorded action, searchable down to the field that changed.',
    }
    return render(request, 'sentinel/activity_stream.html', context)


def _safe_next(request, fallback):
    """Where to send the browser after a POST, without trusting the form.

    Every action view here takes a `next` so the admin lands back on the page
    (and filter) they acted from. Handing that straight to redirect() means a
    crafted form can bounce a signed-in administrator to an external site that
    looks like the vault and asks them to sign in again. Only same-site paths
    are honoured; anything else falls back.
    """
    target = (request.POST.get('next') or '').strip()
    if not target:
        return fallback
    # '//evil.com' and 'https://evil.com' are both absolute; '/sentinel/...' is not.
    if target.startswith('/') and not target.startswith('//') and '\\' not in target:
        return target
    return fallback


def _deny_purge(request):
    messages.error(request, '❌ Only an administrator can delete records from the vault.',
                   extra_tags='permission_denied')
    return redirect(_safe_next(request, 'sentinel:home'))


@vault_access('can_configure_audit')
@require_POST
def purge_events(request):
    """Delete audit rows: the ones ticked, or everything the filter bar selects.

    Deliberately operates on the *filtered* queryset rather than a date box of
    its own — what you are about to delete is exactly the list you are looking
    at, which is the only version of this that is safe to hand someone.
    """
    if not may_purge(request.user):
        return _deny_purge(request)

    back = _safe_next(request, 'sentinel:stream')
    scope = request.POST.get('scope', 'selected')
    events = scope_to_visible(AuditEvent.objects.all(), request.user)

    if scope == 'selected':
        ids = [value for value in request.POST.getlist('ids') if value.isdigit()][:PAGE_SIZE * 5]
        if not ids:
            messages.info(request, 'Nothing was selected.')
            return redirect(back)
        queryset = events.filter(pk__in=ids)
        detail = f'{len(ids)} hand-picked row(s)'
    elif scope == 'filtered':
        queryset, active = _filter_events(request, events)
        if not active:
            # Without a filter this button means "delete the entire trail", which
            # is a different decision and belongs behind the settings page where
            # it is spelled out.
            messages.error(request, '❌ Narrow the view with at least one filter first. '
                                    'To clear the whole trail, use Vault Settings → Storage.')
            return redirect(back)
        detail = ', '.join(f'{key}={value}' for key, value in active.items())
    else:
        messages.error(request, 'Unknown delete scope.')
        return redirect(back)

    removed = services.purge(queryset, what='audit event(s)', actor=request.user,
                             request=request, detail=detail)
    if removed:
        messages.success(request, f'Deleted {removed:,} audit record(s). '
                                  'Reclaim the disk space from Vault Settings → Storage.')
    else:
        messages.info(request, 'Nothing matched — no records were deleted.')
    return redirect(back)


@vault_access()
def event_detail(request, event_id):
    """JSON payload for the slide-in detail drawer."""
    events = scope_to_visible(AuditEvent.objects.select_related('actor', 'session'), request.user)
    event = get_object_or_404(events, pk=event_id)

    changes = []
    if isinstance(event.changes, dict):
        for field, delta in event.changes.items():
            if isinstance(delta, dict):
                changes.append({
                    'field': field.replace('_', ' ').title(),
                    'raw_field': field,
                    'old': delta.get('old'),
                    'new': delta.get('new'),
                })
            else:
                changes.append({'field': field.replace('_', ' ').title(),
                                'raw_field': field, 'old': None, 'new': None})

    session = event.session
    related = (AuditEvent.objects.filter(session=session)
               .exclude(pk=event.pk).order_by('-created_at')[:6]) if session else []

    return JsonResponse({
        'id': event.id,
        'action': event.action,
        'event_type': event.get_event_type_display(),
        'event_key': event.event_type,
        'severity': event.get_severity_display(),
        'severity_key': event.severity,
        'module': event.module,
        'actor': event.actor_username,
        'actor_role': event.actor_role,
        'actor_id': event.actor_id,
        'timestamp': convert_to_nepali(event.created_at).strftime('%d %b %Y, %I:%M:%S %p'),
        'timestamp_iso': event.created_at.isoformat(),
        'object_type': event.object_type,
        'object_id': event.object_id,
        'object_label': event.object_label,
        'path': event.path,
        'method': event.method,
        'view_name': event.view_name,
        'status_code': event.status_code,
        'duration_ms': event.duration_ms,
        'ip_address': event.ip_address,
        'browser': event.browser,
        'operating_system': event.operating_system,
        'device_kind': event.get_device_kind_display(),
        'user_agent': event.user_agent,
        'risk_score': event.risk_score,
        'risk_band': event.risk_band,
        'risk_flags': event.risk_flags or [],
        'changes': changes,
        'context': event.context or {},
        'icon': event.icon,
        'tone': event.tone,
        'session_id': session.id if session else None,
        'session_label': str(session) if session else None,
        'related': [
            {'id': r.id, 'action': r.action, 'icon': r.icon, 'tone': r.tone,
             'time': convert_to_nepali(r.created_at).strftime('%I:%M %p')}
            for r in related
        ],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Sessions & devices
# ─────────────────────────────────────────────────────────────────────────────

@vault_access()
def session_monitor(request):
    user = request.user
    everyone = can_see_everyone(user)
    services.reap_stale_sessions()

    sessions = DeviceSession.objects.select_related('user', 'device')
    if not everyone:
        sessions = sessions.filter(user=user)

    # Held before the search narrows things: the "delete all closed sessions"
    # button acts on every closed row, not just the ones matching the box, so
    # counting the filtered set would put a number on it that it does not honour.
    closed_total = sessions.filter(is_active=False).count()

    tab = request.GET.get('tab', 'live')
    search = (request.GET.get('q') or '').strip()
    if search:
        sessions = sessions.filter(
            Q(username_snapshot__icontains=search)
            | Q(ip_address__icontains=search)
            | Q(browser__icontains=search)
            | Q(operating_system__icontains=search))

    if tab == 'live':
        rows = sessions.filter(is_active=True).order_by('-last_activity')
    elif tab == 'devices':
        rows = sessions.none()
    else:
        rows = sessions.filter(is_active=False).order_by('-started_at')

    devices = KnownDevice.objects.select_related('user')
    if not everyone:
        devices = devices.filter(user=user)
    if search and tab == 'devices':
        devices = devices.filter(
            Q(user__username__icontains=search) | Q(label__icontains=search))

    paginator = Paginator(devices if tab == 'devices' else rows, PAGE_SIZE)
    page = paginator.get_page(request.GET.get('page'))

    active_sessions = sessions.filter(is_active=True)
    # Count idle in SQL rather than walking the rows — .only() cannot be combined
    # with the select_related above, and this avoids loading every live session.
    idle_cutoff = timezone.now() - timedelta(seconds=900)

    context = {
        'tab': tab,
        'page_obj': page,
        'search': search,
        'stats': {
            'live': active_sessions.count(),
            'users_online': active_sessions.values('user_id').distinct().count(),
            'idle': active_sessions.filter(last_activity__lt=idle_cutoff).count(),
            'devices': devices.count(),
            'untrusted': devices.filter(is_trusted=False).count(),
            'blocked': devices.filter(is_blocked=True).count(),
            'today': sessions.filter(
                started_at__gte=timezone.now() - timedelta(hours=24)).count(),
        },
        'device_mix': list(
            active_sessions.values('device_kind').annotate(count=Count('id')).order_by('-count')),
        'can_manage': can(user, 'can_manage_sessions'),
        'can_purge': may_purge(user),
        'closed_total': closed_total,
        'can_see_everyone': everyone,
        'querystring': _querystring(request),
        'vault_tab': 'sessions',
        'vault_title': 'Sessions & Devices',
        'vault_tagline': 'Who is connected right now, from which machine, and on whose network.',
    }
    return render(request, 'sentinel/session_monitor.html', context)


@vault_access('can_manage_sessions')
@require_POST
def revoke_session(request, session_id):
    session = get_object_or_404(DeviceSession, pk=session_id, is_active=True)

    if session.session_key and session.session_key == request.session.session_key:
        messages.warning(request, 'That is your own current session — use Sign Out instead.')
        return redirect('sentinel:sessions')

    if not may_act_on(request.user, session.user):
        messages.error(request, '❌ Only an administrator can revoke an administrator’s session.',
                       extra_tags='permission_denied')
        return redirect(_safe_next(request, 'sentinel:sessions'))

    services.revoke_session(session, request.user, note=request.POST.get('note', ''))
    messages.success(
        request, f'Signed {session.username_snapshot} out of {session.device_display}.')
    return redirect(_safe_next(request, 'sentinel:sessions'))


@vault_access('can_manage_sessions')
@require_POST
def revoke_all_sessions(request, user_id):
    """Emergency stop for one account — kills every live session it holds."""
    target = get_object_or_404(CustomUser, pk=user_id)

    if not may_act_on(request.user, target):
        messages.error(request, '❌ Only an administrator can sign out an administrator.',
                       extra_tags='permission_denied')
        return redirect(_safe_next(request, 'sentinel:sessions'))

    sessions = DeviceSession.objects.filter(user=target, is_active=True)
    count = 0
    for session in sessions:
        if session.session_key == request.session.session_key:
            continue
        services.revoke_session(session, request.user, note='Bulk revoke')
        count += 1
    messages.success(request, f'Revoked {count} session(s) for {target.username}.')
    return redirect(_safe_next(request, 'sentinel:sessions'))


@vault_access('can_configure_audit')
@require_POST
def purge_sessions(request):
    """Clear closed sessions from the history tab.

    Only closed ones: a live session is presence data, and deleting the row
    would not sign anybody out — it would just hide them. Use Revoke for that.
    """
    if not may_purge(request.user):
        return _deny_purge(request)

    back = _safe_next(request, 'sentinel:sessions')
    closed = DeviceSession.objects.filter(is_active=False)
    scope = request.POST.get('scope', 'selected')

    if scope == 'selected':
        ids = [value for value in request.POST.getlist('ids') if value.isdigit()][:PAGE_SIZE * 5]
        if not ids:
            messages.info(request, 'Nothing was selected.')
            return redirect(back)
        queryset, detail = closed.filter(pk__in=ids), f'{len(ids)} selected'
    elif scope == 'all':
        queryset, detail = closed, 'every closed session'
    elif scope == 'older':
        days = request.POST.get('days', '')
        days = int(days) if days.isdigit() and 0 < int(days) <= 3650 else 30
        queryset = closed.filter(started_at__lt=timezone.now() - timedelta(days=days))
        detail = f'closed and older than {days} days'
    else:
        messages.error(request, 'Unknown delete scope.')
        return redirect(back)

    removed = services.purge(queryset, what='closed session(s)', actor=request.user,
                             request=request, detail=detail)
    messages.success(request, f'Deleted {removed:,} session record(s).' if removed
                     else 'No closed sessions matched.')
    return redirect(back)


@vault_access('can_manage_sessions')
@require_POST
def device_action(request, device_id):
    device = get_object_or_404(KnownDevice, pk=device_id)
    action = request.POST.get('action')

    if not may_act_on(request.user, device.user):
        messages.error(request, '❌ Only an administrator can change an administrator’s device.',
                       extra_tags='permission_denied')
        return redirect(_safe_next(request, 'sentinel:sessions'))

    # Blocking the device you are sitting at ends your own access on the next click.
    if action in ('block', 'forget') and device.user_id == request.user.id:
        current = DeviceSession.objects.filter(
            session_key=request.session.session_key, is_active=True).first()
        if current is not None and current.fingerprint == device.fingerprint:
            messages.error(request, '❌ That is the device you are using right now — '
                                    f'{"blocking" if action == "block" else "forgetting"} it '
                                    'would lock you out.')
            return redirect(_safe_next(request, 'sentinel:sessions'))

    if action == 'forget':
        return _forget_device(request, device)

    with suppress_capture():
        if action == 'trust':
            device.is_trusted, device.is_blocked = True, False
        elif action == 'untrust':
            device.is_trusted = False
        elif action == 'block':
            device.is_blocked, device.is_trusted = True, False
        elif action == 'unblock':
            device.is_blocked = False
        else:
            messages.error(request, 'Unknown device action.')
            return redirect('sentinel:sessions')
        device.save(update_fields=['is_trusted', 'is_blocked'])

    services.log_event(
        event_type=EventType.PERMISSION,
        action=f'Marked device "{device.label}" of {device.user.username} as {action}',
        request=request, actor=request.user, module='Sentinel Vault',
        object_type='sentinel.KnownDevice', object_id=device.pk, object_label=device.label,
        severity=Severity.NOTICE,
    )

    if action == 'block':
        killed = 0
        for session in DeviceSession.objects.filter(
                user=device.user, fingerprint=device.fingerprint, is_active=True):
            services.revoke_session(session, request.user, note='Device blocked')
            killed += 1
        messages.warning(request, f'Device blocked and {killed} live session(s) terminated.')
    else:
        messages.success(request, f'Device marked as {action}.')
    return redirect(_safe_next(request, 'sentinel:sessions'))


def _forget_device(request, device):
    """Drop a registry entry without touching the sessions that reference it.

    The registry only ever grew, so entries a superseded fingerprint left behind
    — anything recorded before a device could be identified properly — sat there
    permanently, describing hardware nobody uses. Sessions keep their history:
    the foreign key is SET_NULL, so the sign-in record survives the device row.
    """
    if DeviceSession.objects.filter(
            user=device.user, fingerprint=device.fingerprint, is_active=True).exists():
        messages.error(request, '❌ That device has a live session. Revoke it first, '
                                'otherwise it will simply be registered again.')
        return redirect(_safe_next(request, 'sentinel:sessions'))

    label, owner = device.label or device.fingerprint[:12], device.user.username
    services.log_event(
        event_type=EventType.DELETE,
        action=f'Removed device "{label}" from {owner}\'s registry',
        request=request, actor=request.user, module='Sentinel Vault',
        object_type='sentinel.KnownDevice', object_id=device.pk, object_label=label,
        severity=Severity.NOTICE,
        context={'fingerprint': device.fingerprint, 'sign_ins': device.login_count,
                 'owner': owner},
    )
    with suppress_capture():
        device.delete()
    messages.success(request, f'Forgot “{label}”. It will be registered again, correctly, '
                              f'the next time {owner} signs in from it.')
    return redirect(_safe_next(request, 'sentinel:sessions'))


@vault_access()
def session_detail(request, session_id):
    sessions = DeviceSession.objects.select_related('user', 'device')
    if not can_see_everyone(request.user):
        sessions = sessions.filter(user=request.user)
    session = get_object_or_404(sessions, pk=session_id)

    events = session.events.select_related('actor').order_by('-created_at')
    paginator = Paginator(events, PAGE_SIZE)

    context = {
        'session': session,
        'page_obj': paginator.get_page(request.GET.get('page')),
        'event_count': events.count(),
        'breakdown': list(
            events.values('event_type').annotate(count=Count('id')).order_by('-count')),
        'peak_risk': events.aggregate(value=Max('risk_score'))['value'] or 0,
        'alerts': session.alerts.all()[:10],
        'can_manage': can(request.user, 'can_manage_sessions'),
        'querystring': _querystring(request),
        'vault_tab': 'sessions',
        'vault_title': 'Session Replay',
        'vault_tagline': 'The complete sequence of actions taken within one sign-in.',
    }
    return render(request, 'sentinel/session_detail.html', context)


# ─────────────────────────────────────────────────────────────────────────────
# Alerts
# ─────────────────────────────────────────────────────────────────────────────

@vault_access()
def alert_console(request):
    user = request.user
    alerts = SecurityAlert.objects.select_related('subject_user', 'session', 'event')
    if not can_see_everyone(user):
        alerts = alerts.filter(subject_user=user)

    status = request.GET.get('status', 'open')
    if status == 'open':
        alerts = alerts.filter(status__in=[SecurityAlert.Status.OPEN,
                                           SecurityAlert.Status.ACKNOWLEDGED])
    elif status in dict(SecurityAlert.Status.choices):
        alerts = alerts.filter(status=status)

    kind = request.GET.get('kind')
    if kind:
        alerts = alerts.filter(kind=kind)
    severity = request.GET.get('severity')
    if severity:
        alerts = alerts.filter(severity=severity)

    base = SecurityAlert.objects.all()
    if not can_see_everyone(user):
        base = base.filter(subject_user=user)
    open_qs = base.filter(status__in=[SecurityAlert.Status.OPEN,
                                      SecurityAlert.Status.ACKNOWLEDGED])

    paginator = Paginator(alerts, 25)
    page = paginator.get_page(request.GET.get('page'))

    # Mark per-row whether this viewer may dispose of the alert, so the UI shows
    # the same rule the view enforces instead of offering buttons that will fail.
    viewer_is_admin = is_admin(user)
    for alert in page:
        alert.viewer_may_judge = viewer_is_admin or alert.subject_user_id != user.id

    context = {
        'page_obj': page,
        'status': status,
        'kind': kind,
        'severity': severity,
        'kinds': SecurityAlert.Kind.choices,
        'severities': Severity.choices,
        'statuses': SecurityAlert.Status.choices,
        'stats': {
            'open': open_qs.count(),
            'critical': open_qs.filter(severity=Severity.CRITICAL).count(),
            'warning': open_qs.filter(severity=Severity.WARNING).count(),
            'resolved_7d': base.filter(
                status=SecurityAlert.Status.RESOLVED,
                resolved_at__gte=timezone.now() - timedelta(days=7)).count(),
        },
        'by_kind': list(open_qs.values('kind').annotate(count=Count('id')).order_by('-count')),
        'can_manage': can(user, 'can_manage_sessions') or is_admin(user),
        'can_purge': may_purge(user),
        'closed_total': base.filter(status__in=[SecurityAlert.Status.RESOLVED,
                                                SecurityAlert.Status.DISMISSED]).count(),
        'querystring': _querystring(request),
        'vault_tab': 'alerts',
        'vault_title': 'Threat Console',
        'vault_tagline': 'Anomalies the vault flagged, and what was decided about them.',
    }
    return render(request, 'sentinel/alert_console.html', context)


@vault_access('can_manage_sessions')
@require_POST
def alert_action(request, alert_id):
    alert = get_object_or_404(SecurityAlert, pk=alert_id)
    action = request.POST.get('action')
    note = (request.POST.get('note') or '').strip()

    mapping = {
        'ack': SecurityAlert.Status.ACKNOWLEDGED,
        'resolve': SecurityAlert.Status.RESOLVED,
        'dismiss': SecurityAlert.Status.DISMISSED,
        'reopen': SecurityAlert.Status.OPEN,
    }
    if action not in mapping:
        messages.error(request, 'Unknown alert action.')
        return redirect('sentinel:alerts')

    if not may_judge_alert(request.user, alert):
        messages.error(request, '❌ You cannot close an alert raised about your own account. '
                                'An administrator must review it.',
                       extra_tags='permission_denied')
        return redirect(_safe_next(request, 'sentinel:alerts'))

    with suppress_capture():
        alert.status = mapping[action]
        alert.resolution_note = note or alert.resolution_note
        if action in ('resolve', 'dismiss'):
            alert.resolved_at = timezone.now()
            alert.resolved_by = request.user
        elif action == 'reopen':
            alert.resolved_at = None
            alert.resolved_by = None
        alert.save(update_fields=['status', 'resolution_note', 'resolved_at', 'resolved_by'])

    services.log_event(
        event_type=EventType.SYSTEM,
        action=f'{action.title()}d security alert: {alert.title}',
        request=request, actor=request.user, module='Sentinel Vault',
        object_type='sentinel.SecurityAlert', object_id=alert.pk, object_label=alert.title,
        context={'note': note}, severity=Severity.NOTICE,
    )
    messages.success(request, f'Alert {alert.get_status_display().lower()}.')
    return redirect(_safe_next(request, 'sentinel:alerts'))


@vault_access('can_manage_sessions')
@require_POST
def bulk_alert_action(request):
    ids = [i for i in request.POST.getlist('alert_ids') if i.isdigit()]
    action = request.POST.get('action')
    mapping = {'ack': SecurityAlert.Status.ACKNOWLEDGED,
               'resolve': SecurityAlert.Status.RESOLVED,
               'dismiss': SecurityAlert.Status.DISMISSED}

    if action == 'delete':
        return _purge_alerts(request, ids)

    if action not in mapping or not ids:
        messages.error(request, 'Nothing to update.')
        return redirect('sentinel:alerts')

    targets = SecurityAlert.objects.filter(pk__in=ids)
    # Same rule as the single-alert path — bulk must not become the way around it.
    if not is_admin(request.user):
        targets = targets.exclude(subject_user=request.user)
    skipped = len(ids) - targets.count()

    with suppress_capture():
        if action == 'ack':
            # Acknowledging is "I have seen this", not "this is closed" — stamping
            # resolved_at/resolved_by here would falsely show it as dispositioned.
            updated = targets.update(status=mapping[action])
        else:
            updated = targets.update(status=mapping[action],
                                     resolved_at=timezone.now(), resolved_by=request.user)

    if updated:
        services.log_event(
            event_type=EventType.SYSTEM,
            action=f'Bulk {action} on {updated} security alerts',
            request=request, actor=request.user, module='Sentinel Vault',
            severity=Severity.NOTICE, context={'alert_ids': ids[:50]},
        )
        messages.success(request, f'{updated} alert(s) updated.')
    if skipped:
        messages.warning(request, f'{skipped} alert(s) skipped — you cannot close alerts '
                                  'raised about your own account.')
    return redirect('sentinel:alerts')


def _purge_alerts(request, ids):
    """Delete alerts — the ticked ones, or every closed one.

    Open alerts are never deletable in bulk: an unreviewed finding must be
    dispositioned, not quietly dropped. Resolve or dismiss it first, which
    leaves a record of who decided what.
    """
    if not may_purge(request.user):
        return _deny_purge(request)

    closed = SecurityAlert.objects.filter(
        status__in=[SecurityAlert.Status.RESOLVED, SecurityAlert.Status.DISMISSED])

    if request.POST.get('scope') == 'closed':
        queryset, detail = closed, 'every resolved or dismissed alert'
    elif ids:
        queryset, detail = closed.filter(pk__in=ids), f'{len(ids)} selected'
    else:
        messages.error(request, 'Nothing was selected.')
        return redirect('sentinel:alerts')

    open_count = SecurityAlert.objects.filter(pk__in=ids).exclude(
        pk__in=closed.values('pk')).count() if ids else 0

    removed = services.purge(queryset, what='security alert(s)', actor=request.user,
                             request=request, detail=detail)
    if removed:
        messages.success(request, f'Deleted {removed:,} alert(s).')
    else:
        messages.info(request, 'No closed alerts matched.')
    if open_count:
        messages.warning(request, f'{open_count} still-open alert(s) were kept — resolve or '
                                  'dismiss them first so the decision is on record.')
    return redirect('sentinel:alerts')


# ─────────────────────────────────────────────────────────────────────────────
# User dossier
# ─────────────────────────────────────────────────────────────────────────────

@vault_access()
def user_dossier(request, user_id):
    if not can_see_everyone(request.user) and int(user_id) != request.user.id:
        messages.error(request, '❌ You can only view your own activity.',
                       extra_tags='permission_denied')
        return redirect('sentinel:home')

    subject = get_object_or_404(CustomUser, pk=user_id)
    now = timezone.now()
    week_ago, month_ago = now - timedelta(days=7), now - timedelta(days=30)

    events = AuditEvent.objects.filter(actor=subject)
    sessions = DeviceSession.objects.filter(user=subject)
    devices = KnownDevice.objects.filter(user=subject).order_by('-last_seen')

    recent = events.filter(created_at__gte=month_ago)
    logins = events.filter(event_type=EventType.LOGIN)

    # Behaviour profile: the hour of day this person is usually active.
    hour_counts = services.hourly_heatmap(30, subject)
    busiest_hour = hour_counts.index(max(hour_counts)) if any(hour_counts) else None

    filtered, active = _filter_events(request, events.select_related('session'))
    paginator = Paginator(filtered, 25)

    context = {
        'subject': subject,
        'page_obj': paginator.get_page(request.GET.get('page')),
        'active': active,
        'choices': _filter_choices(request.user),
        'stats': {
            'total_events': events.count(),
            'events_7d': events.filter(created_at__gte=week_ago).count(),
            'events_30d': recent.count(),
            'writes_30d': recent.filter(event_type__in=[
                EventType.CREATE, EventType.UPDATE, EventType.DELETE]).count(),
            'deletes_30d': recent.filter(event_type=EventType.DELETE).count(),
            'logins': logins.count(),
            'failed_30d': AuditEvent.objects.filter(
                actor_username=subject.username, event_type=EventType.LOGIN_FAILED,
                created_at__gte=month_ago).count(),
            'denials_30d': recent.filter(event_type=EventType.DENIED).count(),
            'devices': devices.count(),
            'live_sessions': sessions.filter(is_active=True).count(),
            'avg_risk': round(recent.aggregate(value=Avg('risk_score'))['value'] or 0),
            'peak_risk': recent.aggregate(value=Max('risk_score'))['value'] or 0,
        },
        'first_seen': logins.order_by('created_at').values_list('created_at', flat=True).first(),
        'last_seen': events.values_list('created_at', flat=True).first(),
        'devices': devices,
        'live_sessions': sessions.filter(is_active=True).order_by('-last_activity'),
        'recent_sessions': sessions.filter(is_active=False).order_by('-started_at')[:10],
        'timeline': services.activity_timeline(14, subject),
        'hour_counts': hour_counts,
        'busiest_hour': busiest_hour,
        'module_mix': list(recent.exclude(module='').values('module')
                           .annotate(count=Count('id')).order_by('-count')[:8]),
        'event_mix': list(recent.values('event_type')
                          .annotate(count=Count('id')).order_by('-count')[:10]),
        'touched_objects': list(recent.exclude(object_type='').values('object_type')
                                .annotate(count=Count('id')).order_by('-count')[:8]),
        'alerts': SecurityAlert.objects.filter(subject_user=subject)[:8],
        'can_manage': can(request.user, 'can_manage_sessions'),
        'event_type_labels': dict(EventType.choices),
        'querystring': _querystring(request),
        'vault_tab': 'people',
        'vault_title': 'Dossier',
        'vault_tagline': 'One person’s complete footprint: devices, hours, records touched.',
    }
    return render(request, 'sentinel/user_dossier.html', context)


@vault_access()
def people_index(request):
    """Roster ranked by activity — the entry point into individual dossiers."""
    if not can_see_everyone(request.user):
        return redirect('sentinel:dossier', user_id=request.user.id)

    now = timezone.now()
    month_ago = now - timedelta(days=30)
    search = (request.GET.get('q') or '').strip()

    people = CustomUser.objects.filter(is_deleted=False)
    if search:
        people = people.filter(
            Q(username__icontains=search) | Q(first_name__icontains=search)
            | Q(last_name__icontains=search) | Q(email__icontains=search))

    total_people = people.count()

    # Four aggregates over four relations used to be four annotate() calls on one
    # queryset — which makes the DB build the cartesian product of every user's
    # events x sessions x devices x alerts and then collapse it with DISTINCT.
    # Cost grew with each user's history, not with the number of users. Four
    # separate GROUP BY queries against indexed FKs are cheap and stay flat.
    ordering = list(people.order_by('username').values_list('id', flat=True))

    def grouped(model, field, base_filter=None):
        queryset = model.objects.filter(**{f'{field}__in': ordering})
        if base_filter is not None:
            queryset = queryset.filter(base_filter)
        return dict(queryset.values_list(field).annotate(n=Count('id')).values_list(field, 'n'))

    event_counts = grouped(AuditEvent, 'actor_id', Q(created_at__gte=month_ago))
    live_counts = grouped(DeviceSession, 'user_id', Q(is_active=True))
    device_counts = grouped(KnownDevice, 'user_id')
    alert_counts = grouped(SecurityAlert, 'subject_user_id', Q(status__in=[
        SecurityAlert.Status.OPEN, SecurityAlert.Status.ACKNOWLEDGED]))
    last_events = dict(
        AuditEvent.objects.filter(actor_id__in=ordering)
        .values_list('actor_id').annotate(latest=Max('created_at'))
        .values_list('actor_id', 'latest'))

    roster = list(people)
    for person in roster:
        person.events_30d = event_counts.get(person.id, 0)
        person.live_count = live_counts.get(person.id, 0)
        person.device_count = device_counts.get(person.id, 0)
        person.open_alerts = alert_counts.get(person.id, 0)
        person.last_event = last_events.get(person.id)
    roster.sort(key=lambda p: (-p.events_30d, p.username.lower()))

    paginator = Paginator(roster, 30)
    return render(request, 'sentinel/people_index.html', {
        'page_obj': paginator.get_page(request.GET.get('page')),
        'search': search,
        'total_people': total_people,
        'querystring': _querystring(request),
        'vault_tab': 'people',
        'vault_title': 'People Dossiers',
        'vault_tagline': 'Everyone with an account, ranked by how much they have been doing.',
    })


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

@vault_access('can_configure_audit')
def vault_settings(request):
    config = VaultSettings.load()

    if request.method == 'POST':
        booleans = ['capture_page_views', 'capture_field_diffs', 'capture_anonymous',
                    'alert_on_off_hours', 'alert_on_new_device']
        integers = ['retention_days', 'page_view_retention_days', 'failed_login_threshold',
                    'failed_login_window_minutes', 'bulk_delete_threshold', 'denial_threshold']

        with suppress_capture():
            for field in booleans:
                setattr(config, field, request.POST.get(field) == 'on')
            for field in integers:
                raw = request.POST.get(field)
                if raw and raw.isdigit():
                    setattr(config, field, max(1, int(raw)))
            for field in ('office_hours_start', 'office_hours_end'):
                value = request.POST.get(field)
                if value:
                    setattr(config, field, value)
            config.excluded_paths = request.POST.get('excluded_paths', '')
            config.updated_by = request.user
            config.save()

        services.invalidate_settings_cache()
        services.log_event(
            event_type=EventType.SYSTEM,
            action='Updated Sentinel Vault capture settings',
            request=request, actor=request.user, module='Sentinel Vault',
            severity=Severity.NOTICE,
            context={'retention_days': config.retention_days,
                     'capture_page_views': config.capture_page_views},
        )
        messages.success(request, 'Sentinel Vault settings saved.')
        return redirect('sentinel:settings')

    now = timezone.now()
    report = services.storage_report()
    expiring, window = services.retention_querysets(config)
    storage = {
        'events': report['events']['rows'],
        'sessions': report['sessions']['rows'],
        'alerts': report['alerts']['rows'],
        'devices': report['devices']['rows'],
        'oldest': AuditEvent.objects.order_by('created_at')
                                    .values_list('created_at', flat=True).first(),
        'expiring': AuditEvent.objects.filter(
            created_at__lt=now - timedelta(days=config.retention_days)).count(),
        'page_views': AuditEvent.objects.filter(event_type=EventType.VIEW).count(),
        'closed_sessions': DeviceSession.objects.filter(is_active=False).count(),
        'closed_alerts': SecurityAlert.objects.filter(status__in=[
            SecurityAlert.Status.RESOLVED, SecurityAlert.Status.DISMISSED]).count(),
        'bytes': report.get('total_bytes', 0),
        'sizes': {key: report[key]['bytes'] for key in ('events', 'sessions', 'alerts', 'devices')},
        'sweep': {key: queryset.count() for key, queryset in expiring.items()},
        'window': window,
    }
    return render(request, 'sentinel/vault_settings.html', {
        'config': config, 'storage': storage,
        'can_purge': may_purge(request.user),
        'vault_tab': 'settings',
        'vault_title': 'Vault Settings',
        'vault_tagline': 'Capture depth, retention windows and anomaly thresholds.',
    })


@vault_access('can_configure_audit')
@require_POST
def vault_storage(request):
    """The housekeeping actions on the settings page.

    `sentinel_prune` already existed for this, but it needs a scheduler and this
    project has none — so the table grew until somebody noticed. These are the
    same operations, reachable by the person who actually sees the problem.
    """
    if not may_purge(request.user):
        return _deny_purge(request)

    action = request.POST.get('action')

    if action == 'sweep':
        removed = services.run_retention_sweep(actor=request.user, request=request)
        total = sum(removed.values())
        if total:
            messages.success(request, 'Retention sweep removed {:,} row(s): {}.'.format(
                total, ', '.join(f'{count:,} {name.replace("_", " ")}'
                                 for name, count in removed.items() if count)))
        else:
            messages.info(request, 'Nothing is past its retention window yet.')

    elif action == 'page_views':
        removed = services.purge(
            AuditEvent.objects.filter(event_type=EventType.VIEW), what='page-view record(s)',
            actor=request.user, request=request, detail='all page views')
        messages.success(request, f'Deleted {removed:,} page-view record(s). Turn off '
                                  '“Record page visits” above to stop them coming back.'
                         if removed else 'There were no page-view records to delete.')

    elif action == 'closed_sessions':
        removed = services.purge(
            DeviceSession.objects.filter(is_active=False), what='closed session(s)',
            actor=request.user, request=request, detail='every closed session')
        messages.success(request, f'Deleted {removed:,} closed session record(s).' if removed
                         else 'There were no closed sessions to delete.')

    elif action == 'closed_alerts':
        removed = services.purge(
            SecurityAlert.objects.filter(status__in=[SecurityAlert.Status.RESOLVED,
                                                     SecurityAlert.Status.DISMISSED]),
            what='closed alert(s)', actor=request.user, request=request,
            detail='every resolved or dismissed alert')
        messages.success(request, f'Deleted {removed:,} closed alert(s).' if removed
                         else 'There were no closed alerts to delete.')

    elif action == 'older_than':
        raw = request.POST.get('days', '')
        if not raw.isdigit() or not 0 < int(raw) <= 3650:
            messages.error(request, 'Enter a number of days between 1 and 3650.')
            return redirect('sentinel:settings')
        days = int(raw)
        cutoff = timezone.now() - timedelta(days=days)
        removed = services.purge(
            AuditEvent.objects.filter(created_at__lt=cutoff), what='audit event(s)',
            actor=request.user, request=request, detail=f'older than {days} days')
        messages.success(request, f'Deleted {removed:,} record(s) older than {days} days.'
                         if removed else f'Nothing is older than {days} days.')

    elif action == 'reclaim':
        result = services.reclaim_space(actor=request.user, request=request)
        if result is None:
            messages.warning(request, 'This database backend cannot reclaim space on demand.')
        elif result['freed'] > 0:
            messages.success(request, f'Returned {_filesizeformat(result["freed"])} to the disk.')
        else:
            messages.info(request, 'Tables rebuilt — there was no unused space to return.')

    else:
        messages.error(request, 'Unknown storage action.')

    return redirect('sentinel:settings')


def _filesizeformat(value):
    from django.template.defaultfilters import filesizeformat
    return filesizeformat(value)


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

@vault_access('can_export_audit_logs')
def export_events(request):
    """Stream the current filter selection as CSV. Capped so one click can't OOM the box."""
    LIMIT = 25000
    events = scope_to_visible(AuditEvent.objects.select_related('actor'), request.user)
    events, active = _filter_events(request, events)
    events = events[:LIMIT]

    stamp = get_nepali_now().strftime('%Y%m%d_%H%M')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="sentinel_audit_{stamp}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Timestamp (Nepal)', 'Actor', 'Role', 'Event Type', 'Severity', 'Risk', 'Module',
        'Action', 'Object Type', 'Object ID', 'Object', 'Changed Fields',
        'IP Address', 'Device', 'Browser', 'OS', 'Method', 'Path', 'Status', 'Duration (ms)',
    ])
    for event in events.iterator(chunk_size=1000):
        writer.writerow([
            convert_to_nepali(event.created_at).strftime('%Y-%m-%d %H:%M:%S'),
            event.actor_username, event.actor_role,
            event.get_event_type_display(), event.get_severity_display(), event.risk_score,
            event.module, event.action, event.object_type, event.object_id, event.object_label,
            '; '.join(event.changes.keys()) if isinstance(event.changes, dict) else '',
            event.ip_address or '', event.get_device_kind_display(),
            event.browser, event.operating_system,
            event.method, event.path, event.status_code or '', event.duration_ms or '',
        ])

    services.log_event(
        event_type=EventType.EXPORT,
        action='Exported Sentinel Vault audit trail to CSV',
        request=request, actor=request.user, module='Sentinel Vault',
        severity=Severity.WARNING, context={'filters': active},
    )
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Live JSON API (polled by the UI; excluded from capture in the middleware)
# ─────────────────────────────────────────────────────────────────────────────

@vault_access()
def api_pulse(request):
    """Small payload for the command centre's live tiles and feed."""
    user = request.user
    everyone = can_see_everyone(user)
    since_id = request.GET.get('since')
    now = timezone.now()

    events = scope_to_visible(AuditEvent.objects.select_related('actor'), user)
    sessions = DeviceSession.objects.filter(is_active=True)
    if not everyone:
        sessions = sessions.filter(user=user)

    fresh = []
    if since_id and since_id.isdigit():
        for event in events.filter(id__gt=int(since_id)).order_by('-id')[:25]:
            fresh.append({
                'id': event.id,
                'actor': event.actor_username,
                'action': event.action,
                'icon': event.icon,
                'tone': event.tone,
                'risk': event.risk_score,
                'band': event.risk_band,
                'module': event.module,
                'time': convert_to_nepali(event.created_at).strftime('%I:%M:%S %p'),
            })

    open_alerts = SecurityAlert.objects.filter(
        status__in=[SecurityAlert.Status.OPEN, SecurityAlert.Status.ACKNOWLEDGED])
    if not everyone:
        open_alerts = open_alerts.filter(subject_user=user)

    day_ago = now - timedelta(hours=24)
    return JsonResponse({
        'events': fresh,
        'latest_id': events.aggregate(value=Max('id'))['value'] or 0,
        'stats': {
            'live_sessions': sessions.count(),
            'live_users': sessions.values('user_id').distinct().count(),
            'events_24h': events.filter(created_at__gte=day_ago).count(),
            'open_alerts': open_alerts.count(),
            'critical_alerts': open_alerts.filter(severity=Severity.CRITICAL).count(),
            'failed_logins_24h': events.filter(
                created_at__gte=day_ago, event_type=EventType.LOGIN_FAILED).count(),
        },
        'server_time': convert_to_nepali(now).strftime('%I:%M:%S %p'),
    })


@vault_access()
def api_live_sessions(request):
    """Powers the auto-refreshing presence list on the session monitor."""
    # Sweep first. Without this the poll keeps reporting sessions that ended
    # minutes ago, so the live tile drifts away from the tab badge the page was
    # rendered with and neither number can be trusted.
    services.reap_stale_sessions()

    sessions = DeviceSession.objects.filter(is_active=True).select_related('user')
    if not can_see_everyone(request.user):
        sessions = sessions.filter(user=request.user)

    idle_cutoff = timezone.now() - timedelta(seconds=900)
    rows = list(sessions.order_by('-last_activity')[:50])

    return JsonResponse({
        # Counted, not len(rows): the row list is capped for payload size and
        # reporting the cap as the headline figure would silently understate a
        # busy morning.
        'count': sessions.count(),
        'people': sessions.values('user_id').distinct().count(),
        'idle': sessions.filter(last_activity__lt=idle_cutoff).count(),
        'sessions': [
            {
                'id': s.id,
                'user': s.username_snapshot,
                'role': s.role_snapshot,
                'ip': s.ip_address,
                'device': s.device_display,
                'kind': s.device_kind,
                'duration': s.duration_display,
                'idle': s.is_idle,
                'requests': s.request_count,
                'writes': s.write_count,
                'last_activity': convert_to_nepali(s.last_activity).strftime('%I:%M:%S %p'),
            }
            for s in rows
        ],
    })
