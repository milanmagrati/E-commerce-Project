"""
Sentinel Vault business logic.

Everything that writes to the vault goes through here, so there is exactly one
place where "recording an action must never break the action being recorded"
is enforced — every public function swallows its own exceptions and logs them.
An audit system that can 500 the checkout page is worse than no audit system.
"""

import logging
from datetime import timedelta

from django.db import IntegrityError
from django.db.models import Count, F, Q
from django.utils import timezone

from dashboard.timezone_utils import convert_to_nepali
from .context import get_request, get_session_record, set_session_record, suppress_capture
from .models import (
    AuditEvent, DeviceKind, DeviceSession, EventType, KnownDevice,
    SecurityAlert, Severity, VaultSettings,
)
from . import registry, utils

logger = logging.getLogger('sentinel')

# The settings row is read on essentially every request; cache it briefly rather
# than adding a query to each one. 30s is short enough that a toggle in the UI
# feels immediate.
_settings_cache = {'value': None, 'expires': None}
_SETTINGS_TTL = 30


def get_settings(force_refresh=False):
    now = timezone.now()
    if not force_refresh and _settings_cache['value'] is not None and _settings_cache['expires'] > now:
        return _settings_cache['value']
    try:
        value = VaultSettings.load()
    except Exception:
        # Pre-migration or DB hiccup — fall back to an unsaved default so callers
        # can keep working instead of exploding.
        value = VaultSettings()
    _settings_cache['value'] = value
    _settings_cache['expires'] = now + timedelta(seconds=_SETTINGS_TTL)
    return value


def invalidate_settings_cache():
    _settings_cache['value'] = None
    _settings_cache['expires'] = None


# ─────────────────────────────────────────────────────────────────────────────
# Risk scoring
# ─────────────────────────────────────────────────────────────────────────────

_BASE_RISK = {
    EventType.DELETE: 35,
    EventType.LOGIN_FAILED: 25,
    EventType.DENIED: 30,
    EventType.PASSWORD: 30,
    EventType.PERMISSION: 45,
    EventType.EXPORT: 25,
    EventType.IMPORT: 20,
    EventType.SESSION_KILLED: 20,
    EventType.ERROR: 15,
    EventType.CREATE: 8,
    EventType.UPDATE: 10,
    EventType.LOGIN: 5,
    EventType.VIEW: 0,
}


def score_event(event_type, *, object_type='', changes=None, session=None, when=None, config=None):
    """Return (score 0-100, list of human-readable flags).

    The flags matter as much as the number: an admin reading the stream needs to
    know *why* a row is red, not just that it is.
    """
    config = config or get_settings()
    score = _BASE_RISK.get(event_type, 5)
    flags = []

    weight = registry.object_weight(object_type)
    if weight:
        score += weight
        flags.append(f'Sensitive record ({object_type.split(".")[-1]})')

    if changes:
        sensitive = [f for f in changes if registry.is_sensitive_field(f)]
        if sensitive:
            score += 20
            flags.append('Money/permission field changed: ' + ', '.join(sensitive[:4]))

    if session is not None:
        if session.is_new_device:
            score += 15
            flags.append('Unrecognised device')
        if session.device_kind == DeviceKind.BOT:
            score += 10
            flags.append('Automated client')

    when = when or timezone.now()
    if config.alert_on_off_hours and _is_off_hours(when, config):
        score += 15
        flags.append('Outside office hours')

    return max(0, min(100, score)), flags


def _as_time(value, fallback):
    """Coerce an office-hours bound to a real `time`.

    Defence in depth for the risk scorer: a str/None slipping through here used to
    raise TypeError on comparison, which log_event swallowed — silently dropping
    the event it was trying to record.
    """
    from datetime import datetime, time as time_cls

    if isinstance(value, time_cls):
        return value
    if isinstance(value, str):
        for fmt in ('%H:%M:%S', '%H:%M'):
            try:
                return datetime.strptime(value, fmt).time()
            except ValueError:
                continue
    return fallback


def _is_off_hours(when, config):
    from datetime import time as time_cls

    local_time = convert_to_nepali(when).time()
    start = _as_time(config.office_hours_start, time_cls(7, 0))
    end = _as_time(config.office_hours_end, time_cls(21, 0))
    if start <= end:
        return not (start <= local_time <= end)
    # Window spans midnight (e.g. 21:00 → 07:00).
    return end < local_time < start


def severity_for(score):
    if score >= 70:
        return Severity.CRITICAL
    if score >= 40:
        return Severity.WARNING
    if score >= 20:
        return Severity.NOTICE
    return Severity.INFO


# ─────────────────────────────────────────────────────────────────────────────
# Recording events
# ─────────────────────────────────────────────────────────────────────────────

@suppress_capture()
def log_event(*, event_type, action, request=None, actor=None, module='', object_type='',
              object_id='', object_label='', changes=None, context=None, status_code=None,
              duration_ms=None, severity=None, session=None, raise_alerts=True, force=False):
    """Write one audit row. Returns the AuditEvent, or None if recording failed.

    Safe to call from anywhere — views, signals, Celery tasks, management commands.
    Outside a request cycle just omit `request` and pass `actor` explicitly.

    `force=True` records the event even with no signed-in actor. Use it for actual
    data changes: the capture_anonymous setting is about whether to log anonymous
    *browsing*, and it must not suppress the record of a row being modified. An
    order whose status a logistics webhook changed still needs an answer to "who
    changed this?", even if that answer is "the NCM webhook, not a person".
    """
    try:
        request = request or get_request()
        config = get_settings()

        if actor is None and request is not None:
            candidate = getattr(request, 'user', None)
            if candidate is not None and getattr(candidate, 'is_authenticated', False):
                actor = candidate

        if actor is None and not force and not config.capture_anonymous and event_type not in (
                EventType.LOGIN_FAILED, EventType.SYSTEM, EventType.JOB, EventType.ERROR):
            return None

        session = session or get_session_record()

        ip = user_agent = None
        facts = {}
        path = method = view_name = ''
        if request is not None:
            ip, _chain = utils.get_client_ip(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')[:600]
            facts = utils.parse_user_agent(user_agent, utils.read_client_hints(request))
            path = request.get_full_path()[:512]
            method = request.method or ''
            match = getattr(request, 'resolver_match', None)
            if match is not None:
                view_name = (match.view_name or '')[:160]
        elif session is not None:
            ip = session.ip_address
            user_agent = session.user_agent
            facts = {
                'browser': session.browser,
                'operating_system': session.operating_system,
                'device_kind': session.device_kind,
            }

        changes = changes or {}
        when = timezone.now()
        score, flags = score_event(
            event_type, object_type=object_type, changes=changes,
            session=session, when=when, config=config,
        )

        # Distinguish "a signed-out visitor did this" from "no request was involved
        # at all" — a webhook or Celery task reads very differently to a shopper.
        fallback_actor = 'anonymous' if request is not None else 'system'

        event = AuditEvent.objects.create(
            actor=actor,
            actor_username=(getattr(actor, 'username', None) or fallback_actor)[:150],
            actor_role=(getattr(actor, 'role', '') or '')[:50],
            session=session,
            event_type=event_type,
            severity=severity or severity_for(score),
            module=(module or _module_from(object_type, view_name))[:50],
            action=action[:255],
            object_type=object_type[:100],
            object_id=str(object_id)[:64],
            object_label=(object_label or '')[:255],
            changes=changes,
            path=path,
            method=method[:10],
            view_name=view_name,
            status_code=status_code,
            duration_ms=duration_ms,
            ip_address=ip,
            user_agent=user_agent or '',
            device_kind=facts.get('device_kind') or DeviceKind.UNKNOWN,
            browser=(facts.get('browser') or '')[:60],
            operating_system=(facts.get('operating_system') or '')[:60],
            risk_score=score,
            risk_flags=flags,
            context=context or {},
            created_at=when,
        )

        # Tell the middleware this request has already described itself. Without
        # it every deliberate action writes two rows — the rich one from here
        # plus a generic "Submitted Revoke session" from the catch-all write
        # branch — which doubles the table for exactly the actions worth keeping.
        if request is not None:
            try:
                request._sentinel_explicit = True
            except Exception:
                pass

        if raise_alerts:
            _evaluate_alerts(event, config)
        return event

    except Exception:
        logger.exception('Sentinel failed to record event: %s', action)
        return None


def _module_from(object_type, view_name):
    if object_type and '.' in object_type:
        return registry.module_label(object_type.split('.')[0])
    if view_name and ':' in view_name:
        return registry.module_label(view_name.split(':')[0])
    return ''


def log_job(name, *, status='completed', detail='', actor=None, context=None, severity=None):
    """Record a background job / management command / integration run."""
    return log_event(
        event_type=EventType.JOB,
        action=f'{name} — {status}',
        actor=actor,
        module='System',
        context={'job': name, 'status': status, 'detail': detail, **(context or {})},
        severity=severity,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sessions & devices
# ─────────────────────────────────────────────────────────────────────────────

def inspect_request(request):
    """Everything we know about the machine behind one request.

    Shared by session opening and session repair so both always agree on how a
    device is described.
    """
    meta = getattr(request, 'META', None) or {}
    ip, chain = utils.get_client_ip(request)
    user_agent = (meta.get('HTTP_USER_AGENT') or '')[:600]
    hints = utils.read_client_hints(request)
    facts = utils.parse_user_agent(user_agent, hints)
    return {
        'ip': ip,
        'forwarded_chain': chain,
        'user_agent': user_agent,
        'facts': facts,
        'hints': hints,
        'fingerprint': utils.device_fingerprint(user_agent, ip, facts),
    }


def _is_real_browser_request(probe):
    """False for the synthetic requests scripts and test clients build.

    `django.test.Client.login()` and any management command that calls
    `django.contrib.auth.login()` hand the signal a bare `HttpRequest` with an
    empty META. A real request always carries REMOTE_ADDR from the WSGI server
    and almost always a user agent, so "neither" means nobody actually signed
    in — and letting those through is what filled the live list with phantom
    'Unknown on Unknown' rows that no human could be asked about.
    """
    return bool(probe['user_agent'] or probe['ip'])


def _device_facts_for(facts):
    return {
        'label': utils.device_label(facts),
        'browser': (facts.get('browser') or '')[:60],
        'operating_system': (facts.get('operating_system') or '')[:60],
        'device_kind': facts.get('device_kind', DeviceKind.UNKNOWN),
    }


@suppress_capture()
def open_session(request, user):
    """Create the DeviceSession for a fresh login and remember the device."""
    try:
        probe = inspect_request(request)
        if not _is_real_browser_request(probe):
            logger.debug('Sentinel ignored a login for %s with no request identity', user)
            return None, None, False

        facts, fingerprint = probe['facts'], probe['fingerprint']
        hints = probe['hints']
        session_key = getattr(getattr(request, 'session', None), 'session_key', '') or ''

        device_defaults = _device_facts_for(facts)
        try:
            device, created = KnownDevice.objects.get_or_create(
                user=user, fingerprint=fingerprint, defaults=device_defaults)
        except IntegrityError:
            # Two tabs signing in at once both miss the SELECT and both INSERT;
            # unique_together rejects the loser. The row now exists — take it.
            # Without this the whole session record is lost and that login goes
            # unattributed.
            device = KnownDevice.objects.filter(user=user, fingerprint=fingerprint).first()
            created = False

        if device is not None:
            update = {'last_seen': timezone.now(), 'login_count': F('login_count') + 1}
            if not created and _is_vague(device.operating_system, device.device_kind):
                # An earlier sign-in was recorded before the hints arrived. Now
                # that we can actually name the machine, correct the registry
                # entry instead of leaving 'Unknown browser on Unknown OS'
                # against it forever.
                update.update(device_defaults)
            # F() rather than device.login_count + 1: concurrent logins on the same
            # device must not each write the same stale count back.
            KnownDevice.objects.filter(pk=device.pk).update(**update)
            device.login_count += 1

        # Django cycles the session key on login, but a re-login without a
        # logout (or a login that reuses the key) would otherwise leave the old
        # row flagged live forever, so one person shows up as several.
        if session_key:
            DeviceSession.objects.filter(session_key=session_key, is_active=True).update(
                is_active=False, ended_at=timezone.now(),
                end_reason=DeviceSession.EndReason.REPLACED)

        session = DeviceSession.objects.create(
            user=user,
            username_snapshot=user.username[:150],
            role_snapshot=(getattr(user, 'role', '') or '')[:50],
            session_key=session_key,
            device=device,
            fingerprint=fingerprint,
            ip_address=probe['ip'],
            forwarded_chain=probe['forwarded_chain'],
            network_label=utils.network_label(probe['ip']),
            user_agent=probe['user_agent'],
            browser=(facts.get('browser') or '')[:60],
            browser_version=(facts.get('browser_version') or '')[:30],
            operating_system=(facts.get('operating_system') or '')[:60],
            os_version=(facts.get('os_version') or '')[:30],
            device_kind=facts.get('device_kind', DeviceKind.UNKNOWN),
            device_brand=(facts.get('device_brand') or '')[:40],
            timezone_name=(hints.get('timezone') or '')[:64],
            screen=(hints.get('screen') or '')[:24],
            language=(hints.get('language') or '')[:32],
            is_new_device=created,
        )
        set_session_record(session)
        return session, device, created
    except Exception:
        logger.exception('Sentinel could not open a session record for %s', user)
        return None, None, False


def _is_vague(operating_system, device_kind):
    return (not operating_system or operating_system == 'Unknown'
            or device_kind == DeviceKind.UNKNOWN)


def session_needs_repair(session):
    """Cheap in-memory test — no query — for a session we could describe better."""
    if session is None:
        return False
    return (not session.user_agent or session.ip_address is None
            or _is_vague(session.operating_system, session.device_kind)
            or not session.screen)


@suppress_capture()
def repair_session(session, request):
    """Fill in what a session is missing from the request in hand.

    A session's identity is captured once, at login — but the hints that make it
    readable can arrive later (the cookie our snippet writes is not there yet on
    a first-ever visit, and sessions adopted by `resolve_session` never had a
    login request at all). Rather than leave those rows permanently unnamed,
    top them up the next time the same session makes a request.
    """
    if session is None or request is None:
        return session
    try:
        probe = inspect_request(request)
        facts = probe['facts']
        updates = {}

        if not session.user_agent and probe['user_agent']:
            updates['user_agent'] = probe['user_agent']
        if session.ip_address is None and probe['ip']:
            updates['ip_address'] = probe['ip']
            updates['forwarded_chain'] = probe['forwarded_chain']
            updates['network_label'] = utils.network_label(probe['ip'])
        if _is_vague(session.operating_system, session.device_kind) and not _is_vague(
                facts.get('operating_system'), facts.get('device_kind')):
            updates.update({
                'browser': (facts.get('browser') or '')[:60],
                'browser_version': (facts.get('browser_version') or '')[:30],
                'operating_system': (facts.get('operating_system') or '')[:60],
                'os_version': (facts.get('os_version') or '')[:30],
                'device_kind': facts.get('device_kind', DeviceKind.UNKNOWN),
                'device_brand': (facts.get('device_brand') or '')[:40],
            })
        hints = probe['hints']
        if not session.screen and hints.get('screen'):
            updates['screen'] = hints['screen'][:24]
        if not session.timezone_name and hints.get('timezone'):
            updates['timezone_name'] = hints['timezone'][:64]
        if not session.language and hints.get('language'):
            updates['language'] = hints['language'][:32]

        if not updates:
            return session

        DeviceSession.objects.filter(pk=session.pk).update(**updates)
        for field, value in updates.items():
            setattr(session, field, value)

        # Keep the registry entry in step, otherwise the device list keeps
        # showing the vague label while the session row shows the real one.
        if session.device_id and 'device_kind' in updates:
            KnownDevice.objects.filter(pk=session.device_id).update(
                **_device_facts_for(facts))
        return session
    except Exception:
        logger.debug('Sentinel could not repair session %s',
                     getattr(session, 'pk', '?'), exc_info=True)
        return session


@suppress_capture()
def close_session(session, reason=DeviceSession.EndReason.LOGOUT):
    if session is None:
        return
    try:
        DeviceSession.objects.filter(pk=session.pk, is_active=True).update(
            is_active=False, ended_at=timezone.now(), end_reason=reason)
    except Exception:
        logger.exception('Sentinel could not close session %s', getattr(session, 'pk', '?'))


@suppress_capture()
def touch_session(session, *, is_write=False):
    """Cheap heartbeat: one UPDATE, no SELECT, no full model save."""
    if session is None:
        return
    try:
        fields = {'last_activity': timezone.now(), 'request_count': F('request_count') + 1}
        if is_write:
            fields['write_count'] = F('write_count') + 1
        DeviceSession.objects.filter(pk=session.pk).update(**fields)
    except Exception:
        logger.debug('Sentinel session heartbeat failed', exc_info=True)


def resolve_session(request):
    """Find the DeviceSession for an in-flight request, creating one if needed.

    Sessions established before Sentinel was installed (or after a server restart
    that lost the in-memory link) still have a valid Django session cookie, so we
    adopt them rather than leaving their activity unattributed.
    """
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    session_key = getattr(getattr(request, 'session', None), 'session_key', '') or ''
    if not session_key:
        return None
    try:
        matches = list(DeviceSession.objects.filter(
            session_key=session_key, user=user, is_active=True).order_by('-started_at')[:5])
        if not matches:
            record, _device, _created = open_session(request, user)
            return record

        record = matches[0]
        if len(matches) > 1:
            # One browser session can only be one live session. Anything older
            # sharing this key is a leftover that would otherwise be counted as
            # another person online.
            DeviceSession.objects.filter(pk__in=[s.pk for s in matches[1:]]).update(
                is_active=False, ended_at=timezone.now(),
                end_reason=DeviceSession.EndReason.REPLACED)
        if session_needs_repair(record):
            record = repair_session(record, request)
        return record
    except Exception:
        logger.debug('Sentinel could not resolve a session record', exc_info=True)
        return None


@suppress_capture()
def revoke_session(session, actor, note=''):
    """Force-terminate a session: kill the Django session row, then mark ours dead."""
    from django.contrib.sessions.models import Session

    if session.session_key:
        Session.objects.filter(session_key=session.session_key).delete()
    DeviceSession.objects.filter(pk=session.pk).update(
        is_active=False, ended_at=timezone.now(), end_reason=DeviceSession.EndReason.REVOKED)

    log_event(
        event_type=EventType.SESSION_KILLED,
        action=f'Revoked {session.username_snapshot}\'s session on {session.device_display}',
        actor=actor,
        module='Sentinel Vault',
        object_type='sentinel.DeviceSession',
        object_id=session.pk,
        object_label=str(session),
        context={'reason': note, 'ip': session.ip_address, 'target_user': session.username_snapshot},
        severity=Severity.WARNING,
    )


_last_reap = {'at': None}
_REAP_INTERVAL = 60  # seconds


def reap_stale_sessions(idle_minutes=None, force=False):
    """Mark sessions dead once their Django session row is gone or they've gone quiet.

    Called opportunistically from the vault's own views — there is no always-on
    scheduler in this project, so piggy-backing on page loads keeps "who is online
    right now" honest without requiring Celery beat.

    Throttled to once a minute per process: the sweep reads every live session key
    out of django_session, and running that on every page load (plus every 20s poll
    from an open monitor tab) would be a self-inflicted load problem. Presence data
    a minute stale is still accurate enough to act on.
    """
    from django.contrib.sessions.models import Session
    from django.conf import settings as django_settings

    now = timezone.now()
    if not force and _last_reap['at'] is not None:
        if (now - _last_reap['at']).total_seconds() < _REAP_INTERVAL:
            return 0
    _last_reap['at'] = now

    try:
        idle_minutes = idle_minutes or max(60, django_settings.SESSION_COOKIE_AGE // 60)
        cutoff = timezone.now() - timedelta(minutes=idle_minutes)

        stale = DeviceSession.objects.filter(is_active=True, last_activity__lt=cutoff)
        expired_count = stale.update(
            is_active=False, ended_at=timezone.now(),
            end_reason=DeviceSession.EndReason.EXPIRED)

        # Phantoms: a browser always presents at least an address, so a live
        # session with neither an address nor a user agent was opened by a
        # script or a test client, not a person. Left alone these sit in the
        # live list forever as 'Unknown on Unknown' and inflate every count on
        # the page. Give them a moment first, in case a real request is on its
        # way to repair one.
        phantom_cutoff = timezone.now() - timedelta(minutes=5)
        expired_count += DeviceSession.objects.filter(
            is_active=True, user_agent='', ip_address__isnull=True,
            started_at__lt=phantom_cutoff,
        ).update(is_active=False, ended_at=timezone.now(),
                 end_reason=DeviceSession.EndReason.EXPIRED)

        # Duplicates: several live rows sharing one session key means one
        # person is being counted several times. Keep the newest of each.
        duplicated = (DeviceSession.objects.filter(is_active=True).exclude(session_key='')
                      .values('session_key').annotate(n=Count('id')).filter(n__gt=1)
                      .values_list('session_key', flat=True))
        for key in list(duplicated)[:200]:
            keep = (DeviceSession.objects.filter(session_key=key, is_active=True)
                    .order_by('-started_at').values_list('pk', flat=True).first())
            expired_count += DeviceSession.objects.filter(
                session_key=key, is_active=True).exclude(pk=keep).update(
                    is_active=False, ended_at=timezone.now(),
                    end_reason=DeviceSession.EndReason.REPLACED)

        # Also catch sessions whose cookie was destroyed out from under us.
        live_keys = set(
            Session.objects.filter(expire_date__gt=timezone.now())
            .values_list('session_key', flat=True)
        )
        orphans = DeviceSession.objects.filter(is_active=True).exclude(session_key='')
        orphan_ids = [s.pk for s in orphans.only('pk', 'session_key')
                      if s.session_key not in live_keys]
        if orphan_ids:
            expired_count += DeviceSession.objects.filter(pk__in=orphan_ids).update(
                is_active=False, ended_at=timezone.now(),
                end_reason=DeviceSession.EndReason.EXPIRED)
        return expired_count
    except Exception:
        logger.debug('Sentinel session reaping failed', exc_info=True)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Deletion and storage reclamation
#
# A vault you cannot empty eventually fills the disk, and this project has no
# scheduler to run `sentinel_prune` for it — so the trail has to be prunable
# from the UI. What keeps that compatible with an audit trail is the rule
# enforced in `purge()`: the data may go, but the fact that somebody removed it
# may not. Every deletion writes its own receipt, after the fact, so no sweep
# can ever take out the record of itself.
# ─────────────────────────────────────────────────────────────────────────────

PURGE_CHUNK = 2000

# What each purge target means, in one place, so the views, the settings page
# and the management command cannot drift apart on the definitions.
PURGE_TARGETS = ('events', 'page_views', 'sessions', 'alerts', 'devices')


def chunked_delete(queryset, chunk=PURGE_CHUNK, limit=None):
    """Delete a queryset in batches. Returns the number of rows removed.

    Batched because a single DELETE over a year of audit rows is one enormous
    MySQL transaction — long enough to lock out the writes the rest of the shop
    is trying to make while it runs.
    """
    model = queryset.model
    total = 0
    while True:
        if limit is not None and total >= limit:
            return total
        size = chunk if limit is None else min(chunk, limit - total)
        ids = list(queryset.values_list('pk', flat=True)[:size])
        if not ids:
            return total
        model.objects.filter(pk__in=ids).delete()
        total += len(ids)


@suppress_capture()
def purge(queryset, *, what, actor=None, request=None, detail='', context=None, limit=None):
    """Delete vault rows and record that it happened. Returns rows removed."""
    try:
        removed = chunked_delete(queryset, limit=limit)
    except Exception:
        logger.exception('Sentinel purge failed for %s', what)
        return 0

    if not removed:
        return 0

    # Written after the delete on purpose: a receipt created first would be
    # inside its own sweep's range and delete itself.
    log_event(
        event_type=EventType.DELETE,
        action=f'Purged {removed:,} {what} from the vault'
               + (f' ({detail})' if detail else ''),
        request=request, actor=actor, module='Sentinel Vault',
        # No object_type: a receipt covers N rows across a table, not one record,
        # and filing it against sentinel.AuditEvent would break the rule that the
        # vault never records itself — the rule that stops it logging its own
        # logging. `context['target']` carries what was swept.
        severity=Severity.WARNING,
        context={'purged': removed, 'target': what, 'filter': detail, **(context or {})},
        force=True,
        raise_alerts=False,     # a deliberate purge is not a bulk-delete anomaly
    )
    logger.info('Sentinel purge: %s removed %s %s (%s)',
                getattr(actor, 'username', 'system'), removed, what, detail or 'no filter')
    return removed


def retention_querysets(config=None, days=None):
    """The rows the configured retention window says are past their life.

    Shared by the settings page and `manage.py sentinel_prune` so "what the
    button does" and "what the cron job does" are the same thing by construction.
    """
    config = config or get_settings()
    event_days = days or config.retention_days
    view_days = min(days or config.page_view_retention_days, config.page_view_retention_days)
    now = timezone.now()
    event_cutoff = now - timedelta(days=event_days)
    view_cutoff = now - timedelta(days=view_days)

    return {
        'page_views': AuditEvent.objects.filter(
            created_at__lt=view_cutoff, event_type=EventType.VIEW),
        'events': AuditEvent.objects.filter(created_at__lt=event_cutoff),
        'alerts': SecurityAlert.objects.filter(
            status__in=[SecurityAlert.Status.RESOLVED, SecurityAlert.Status.DISMISSED],
            created_at__lt=event_cutoff),
        'sessions': DeviceSession.objects.filter(
            is_active=False, started_at__lt=event_cutoff),
    }, {'event_days': event_days, 'view_days': view_days}


def run_retention_sweep(actor=None, request=None, days=None):
    """Apply the configured retention window now. Returns {target: removed}."""
    querysets, window = retention_querysets(days=days)
    removed = {}
    # Page views first: they are the cheapest rows to lose and by far the most
    # numerous, so on a big table this is where the space actually comes back.
    for target in ('page_views', 'events', 'alerts', 'sessions'):
        removed[target] = purge(
            querysets[target], what=target.replace('_', ' '),
            actor=actor, request=request,
            detail=f'retention sweep, {window["event_days"]}-day window')
    return removed


def storage_report():
    """Row counts and on-disk size per vault table.

    Sizes come from the database itself rather than an estimate — "you will get
    140 MB back" is the only form of this number worth showing someone who is
    trying to free space.
    """
    from django.db import connection

    models = {
        'events': AuditEvent, 'sessions': DeviceSession,
        'alerts': SecurityAlert, 'devices': KnownDevice,
    }
    report = {}
    for key, model in models.items():
        report[key] = {'rows': model.objects.count(),
                       'table': model._meta.db_table, 'bytes': 0}

    try:
        tables = [entry['table'] for entry in report.values()]
        with connection.cursor() as cursor:
            if connection.vendor == 'mysql':
                cursor.execute(
                    'SELECT table_name, data_length + index_length '
                    'FROM information_schema.TABLES '
                    'WHERE table_schema = DATABASE() AND table_name IN %s',
                    [tuple(tables)])
            elif connection.vendor == 'sqlite':
                cursor.execute(
                    "SELECT name, pgsize FROM dbstat WHERE name IN (%s)"
                    % ','.join(['%s'] * len(tables)), tables)
            else:
                cursor = None
            if cursor is not None:
                sizes = {name: int(size or 0) for name, size in cursor.fetchall()}
                for entry in report.values():
                    entry['bytes'] = sizes.get(entry['table'], 0)
    except Exception:
        # Size reporting is a nicety; never let a permissions quirk on
        # information_schema take down the settings page.
        logger.debug('Sentinel could not measure table sizes', exc_info=True)

    report['total_bytes'] = sum(entry['bytes'] for entry in report.values()
                                if isinstance(entry, dict))
    return report


def reclaim_space(actor=None, request=None):
    """Return freed pages to the filesystem after a purge.

    Deleting rows does not shrink the file on either backend this project runs:
    InnoDB keeps the pages in the tablespace and SQLite keeps them on its free
    list. Without this step someone who just deleted a million rows to free a
    disk sees no change at all and reasonably concludes the delete did nothing.
    """
    from django.db import connection

    tables = [AuditEvent._meta.db_table, DeviceSession._meta.db_table,
              SecurityAlert._meta.db_table, KnownDevice._meta.db_table]
    before = storage_report().get('total_bytes', 0)
    try:
        with connection.cursor() as cursor:
            if connection.vendor == 'mysql':
                for table in tables:
                    # Table names are our own model metadata, never user input.
                    cursor.execute(f'OPTIMIZE TABLE `{table}`')
                    cursor.fetchall()
            elif connection.vendor == 'sqlite':
                cursor.execute('VACUUM')
            else:
                return None
    except Exception:
        logger.exception('Sentinel could not reclaim table space')
        return None

    after = storage_report().get('total_bytes', 0)
    log_event(
        event_type=EventType.SYSTEM,
        action='Reclaimed unused space in the vault tables',
        request=request, actor=actor, module='Sentinel Vault',
        severity=Severity.NOTICE,
        context={'before_bytes': before, 'after_bytes': after,
                 'freed_bytes': max(0, before - after)},
        force=True, raise_alerts=False,
    )
    return {'before': before, 'after': after, 'freed': max(0, before - after)}


# ─────────────────────────────────────────────────────────────────────────────
# Alerting
# ─────────────────────────────────────────────────────────────────────────────

@suppress_capture()
def raise_alert(kind, title, *, severity=Severity.WARNING, subject_user=None, subject_username='',
                detail='', evidence=None, session=None, event=None, ip=None, dedupe_key='',
                dedupe_window_minutes=60):
    """Create or fold into an alert.

    Deduping is the difference between a usable inbox and 400 identical rows: a
    repeat of the same condition inside the window bumps a counter instead of
    creating noise.
    """
    try:
        now = timezone.now()
        key = (dedupe_key or f'{kind}:{subject_username or "system"}:{ip or ""}')[:120]

        existing = SecurityAlert.objects.filter(
            dedupe_key=key,
            status__in=[SecurityAlert.Status.OPEN, SecurityAlert.Status.ACKNOWLEDGED],
            last_seen_at__gte=now - timedelta(minutes=dedupe_window_minutes),
        ).first()

        if existing is not None:
            SecurityAlert.objects.filter(pk=existing.pk).update(
                occurrence_count=F('occurrence_count') + 1, last_seen_at=now)
            return existing

        return SecurityAlert.objects.create(
            kind=kind, severity=severity, title=title[:200], detail=detail,
            subject_user=subject_user,
            subject_username=(subject_username or getattr(subject_user, 'username', ''))[:150],
            session=session, event=event, ip_address=ip,
            evidence=evidence or {}, dedupe_key=key,
            created_at=now, last_seen_at=now,
        )
    except Exception:
        logger.exception('Sentinel could not raise alert %s', kind)
        return None


def _evaluate_alerts(event, config):
    """Run the anomaly rules against a freshly written event."""
    try:
        if event.event_type == EventType.LOGIN_FAILED:
            _check_brute_force(event, config)
        elif event.event_type == EventType.DENIED:
            _check_denial_storm(event, config)
        elif event.event_type == EventType.DELETE:
            _check_bulk_delete(event, config)
        elif event.event_type == EventType.EXPORT:
            raise_alert(
                SecurityAlert.Kind.DATA_EXPORT,
                f'{event.actor_username} exported data',
                severity=Severity.NOTICE,
                subject_user=event.actor, subject_username=event.actor_username,
                detail=event.action, event=event, ip=event.ip_address,
                evidence={'path': event.path},
            )

        if event.risk_score >= 70:
            raise_alert(
                SecurityAlert.Kind.PERMISSION_CHANGE if event.event_type == EventType.PERMISSION
                else SecurityAlert.Kind.OFF_HOURS,
                f'High-risk action by {event.actor_username}',
                severity=Severity.CRITICAL,
                subject_user=event.actor, subject_username=event.actor_username,
                detail=event.action, event=event, ip=event.ip_address,
                evidence={'flags': event.risk_flags, 'score': event.risk_score},
                dedupe_key=f'highrisk:{event.actor_username}:{event.object_type}',
                dedupe_window_minutes=30,
            )
    except Exception:
        logger.debug('Sentinel alert evaluation failed', exc_info=True)


def _check_brute_force(event, config):
    window = timezone.now() - timedelta(minutes=config.failed_login_window_minutes)
    attempts = AuditEvent.objects.filter(
        event_type=EventType.LOGIN_FAILED, created_at__gte=window)
    if event.ip_address:
        attempts = attempts.filter(ip_address=event.ip_address)
    else:
        attempts = attempts.filter(actor_username=event.actor_username)
    count = attempts.count()

    if count >= config.failed_login_threshold:
        raise_alert(
            SecurityAlert.Kind.BRUTE_FORCE,
            f'{count} failed sign-ins from {event.ip_address or "unknown IP"}',
            severity=Severity.CRITICAL,
            subject_username=event.actor_username, ip=event.ip_address, event=event,
            detail=(f'{count} failed attempts in the last '
                    f'{config.failed_login_window_minutes} minutes.'),
            evidence={
                'attempts': count,
                'usernames_tried': list(
                    attempts.values_list('actor_username', flat=True).distinct()[:10]),
            },
            dedupe_key=f'brute:{event.ip_address or event.actor_username}',
        )


def _check_denial_storm(event, config):
    window = timezone.now() - timedelta(hours=1)
    count = AuditEvent.objects.filter(
        event_type=EventType.DENIED, actor=event.actor, created_at__gte=window).count()
    if count >= config.denial_threshold:
        raise_alert(
            SecurityAlert.Kind.DENIAL_STORM,
            f'{event.actor_username} hit {count} permission denials in an hour',
            severity=Severity.WARNING,
            subject_user=event.actor, subject_username=event.actor_username,
            ip=event.ip_address, event=event,
            detail='Repeated attempts to reach areas this account cannot access.',
            evidence={'denials': count, 'last_path': event.path},
            dedupe_key=f'denials:{event.actor_username}',
        )


def _check_bulk_delete(event, config):
    window = timezone.now() - timedelta(hours=1)
    count = AuditEvent.objects.filter(
        event_type=EventType.DELETE, actor=event.actor, created_at__gte=window).count()
    if count >= config.bulk_delete_threshold:
        raise_alert(
            SecurityAlert.Kind.BULK_DELETE,
            f'{event.actor_username} deleted {count} records in an hour',
            severity=Severity.CRITICAL,
            subject_user=event.actor, subject_username=event.actor_username,
            ip=event.ip_address, event=event,
            detail='Unusually high deletion volume for a single user.',
            evidence={'deletions': count, 'latest': event.object_label},
            dedupe_key=f'bulkdel:{event.actor_username}',
        )


def check_login_anomalies(session, device, is_new_device, event, config):
    """Login-time rules that need the session/device, not just the event."""
    try:
        if device is not None and device.is_blocked:
            raise_alert(
                SecurityAlert.Kind.BLOCKED_DEVICE,
                f'{session.username_snapshot} signed in from a blocked device',
                severity=Severity.CRITICAL,
                subject_user=session.user, subject_username=session.username_snapshot,
                session=session, event=event, ip=session.ip_address,
                detail=f'Device: {session.device_display}',
                evidence={'fingerprint': session.fingerprint},
            )

        if is_new_device and config.alert_on_new_device:
            raise_alert(
                SecurityAlert.Kind.NEW_DEVICE,
                f'{session.username_snapshot} signed in from an unrecognised device',
                severity=Severity.WARNING,
                subject_user=session.user, subject_username=session.username_snapshot,
                session=session, event=event, ip=session.ip_address,
                detail=f'{session.device_display} from {session.ip_address or "unknown IP"} '
                       f'({session.network_label}).',
                evidence={'fingerprint': session.fingerprint, 'user_agent': session.user_agent},
                dedupe_key=f'newdev:{session.username_snapshot}:{session.fingerprint}',
                dedupe_window_minutes=1440,
            )

        if config.alert_on_off_hours and _is_off_hours(session.started_at, config):
            raise_alert(
                SecurityAlert.Kind.OFF_HOURS,
                f'{session.username_snapshot} signed in outside office hours',
                severity=Severity.NOTICE,
                subject_user=session.user, subject_username=session.username_snapshot,
                session=session, event=event, ip=session.ip_address,
                detail=(f'Sign-in at {convert_to_nepali(session.started_at):%H:%M} Nepal time '
                        f'(office hours {config.office_hours_start:%H:%M}–'
                        f'{config.office_hours_end:%H:%M}).'),
                dedupe_key=f'offhours:{session.username_snapshot}',
                dedupe_window_minutes=240,
            )

        # Same account, several live sessions, different devices.
        concurrent = DeviceSession.objects.filter(
            user=session.user, is_active=True).exclude(pk=session.pk)
        distinct_devices = concurrent.values('fingerprint').distinct().count()
        if distinct_devices >= 2:
            raise_alert(
                SecurityAlert.Kind.CONCURRENT,
                f'{session.username_snapshot} has {distinct_devices + 1} devices signed in',
                severity=Severity.WARNING,
                subject_user=session.user, subject_username=session.username_snapshot,
                session=session, ip=session.ip_address,
                detail='One account active on several devices at once — possible shared credentials.',
                evidence={'active_devices': distinct_devices + 1},
                dedupe_key=f'concurrent:{session.username_snapshot}',
                dedupe_window_minutes=180,
            )
    except Exception:
        logger.debug('Sentinel login anomaly checks failed', exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Analytics used by the dashboard views
# ─────────────────────────────────────────────────────────────────────────────

def activity_timeline(days=14, actor=None):
    """Per-day event counts split by weight, for the command-centre chart.

    Bucketed in Python rather than SQL on purpose: with USE_TZ and a Kathmandu
    TIME_ZONE, Django compiles date grouping to CONVERT_TZ(), which returns NULL
    on this server because the MySQL timezone tables are not loaded (see
    dashboard/timezone_utils.nepali_day_start). Reading three raw columns with
    values_list keeps the cost to the data itself — no model instantiation, no
    per-row Python object — so this stays cheap as the table grows.
    """
    start = timezone.now() - timedelta(days=days - 1)
    queryset = AuditEvent.objects.filter(created_at__gte=start)
    if actor is not None:
        queryset = queryset.filter(actor=actor)

    write_types = {EventType.CREATE, EventType.UPDATE, EventType.DELETE}
    buckets = {}
    rows = queryset.values_list('created_at', 'event_type', 'risk_score')
    for created_at, event_type, risk_score in rows.iterator(chunk_size=5000):
        day = convert_to_nepali(created_at).date()
        bucket = buckets.setdefault(day, {'total': 0, 'writes': 0, 'risky': 0})
        bucket['total'] += 1
        if event_type in write_types:
            bucket['writes'] += 1
        if (risk_score or 0) >= 40:
            bucket['risky'] += 1

    today = convert_to_nepali(timezone.now()).date()
    labels, totals, writes, risky = [], [], [], []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        bucket = buckets.get(day, {'total': 0, 'writes': 0, 'risky': 0})
        labels.append(day.strftime('%d %b'))
        totals.append(bucket['total'])
        writes.append(bucket['writes'])
        risky.append(bucket['risky'])
    return {'labels': labels, 'totals': totals, 'writes': writes, 'risky': risky}


def hourly_heatmap(days=7, actor=None):
    """24-hour activity profile in Nepal time — shows when the team actually works.

    Single-column values_list for the same reason as activity_timeline().
    """
    start = timezone.now() - timedelta(days=days)
    queryset = AuditEvent.objects.filter(created_at__gte=start)
    if actor is not None:
        queryset = queryset.filter(actor=actor)

    counts = [0] * 24
    for (created_at,) in queryset.values_list('created_at').iterator(chunk_size=5000):
        counts[convert_to_nepali(created_at).hour] += 1
    return counts


def top_actors(days=7, limit=8):
    start = timezone.now() - timedelta(days=days)
    return list(
        AuditEvent.objects.filter(created_at__gte=start, actor__isnull=False)
        .values('actor_id', 'actor_username', 'actor_role')
        .annotate(
            events=Count('id'),
            writes=Count('id', filter=Q(event_type__in=[
                EventType.CREATE, EventType.UPDATE, EventType.DELETE])),
            risky=Count('id', filter=Q(risk_score__gte=40)),
        )
        .order_by('-events')[:limit]
    )


def module_breakdown(days=7, limit=8):
    start = timezone.now() - timedelta(days=days)
    return list(
        AuditEvent.objects.filter(created_at__gte=start).exclude(module='')
        .values('module').annotate(events=Count('id')).order_by('-events')[:limit]
    )


def event_type_breakdown(days=7):
    start = timezone.now() - timedelta(days=days)
    rows = (AuditEvent.objects.filter(created_at__gte=start)
            .values('event_type').annotate(events=Count('id')).order_by('-events'))
    labels = dict(EventType.choices)
    return [{'label': labels.get(r['event_type'], r['event_type']),
             'key': r['event_type'], 'events': r['events']} for r in rows]
