"""
Automatic capture.

Two families of signal:
  * Auth signals — login / logout / failed login. Using the signals rather than
    patching dashboard.views.login_view means storefront logins, admin-site
    logins and any future login path are all covered for free.
  * Model signals — pre_save snapshots the old row so post_save can diff it, and
    post_delete records what was destroyed. This is what makes "what did they
    actually change?" answerable without touching a 23k-line views.py.

The diff is captured into a thread-local by the signal and flushed to the DB by
the middleware after the response, so a request that ultimately rolls back does
not leave phantom audit rows.
"""

import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from . import registry, utils
from .context import (
    add_captured, get_request, get_session_record, is_suppressed,
    pop_snapshot, put_snapshot, set_session_record,
)
from .models import EventType, Severity

logger = logging.getLogger('sentinel')


# ─────────────────────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────────────────────

@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    from . import services

    try:
        # Django cycles the session key on login; make sure it exists before we store it.
        if request is not None and not request.session.session_key:
            request.session.save()

        session, device, is_new = services.open_session(request, user)
        if session is None:
            return

        event = services.log_event(
            event_type=EventType.LOGIN,
            action=f'{user.username} signed in from {session.device_display}',
            request=request,
            actor=user,
            module='Access Control',
            object_type='accounts.CustomUser',
            object_id=user.pk,
            object_label=user.username,
            session=session,
            context={
                'ip': session.ip_address,
                'network': session.network_label,
                'device': session.device_display,
                'new_device': is_new,
            },
            raise_alerts=False,
        )
        services.check_login_anomalies(session, device, is_new, event, services.get_settings())
    except Exception:
        logger.exception('Sentinel login capture failed')


@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    from . import services
    from .models import DeviceSession

    if user is None:
        return
    try:
        session = get_session_record()
        if session is None and request is not None:
            key = request.session.session_key
            if key:
                session = DeviceSession.objects.filter(
                    session_key=key, user=user, is_active=True).first()

        services.log_event(
            event_type=EventType.LOGOUT,
            action=f'{user.username} signed out',
            request=request,
            actor=user,
            module='Access Control',
            session=session,
            context={'duration': session.duration_display if session else None},
        )
        services.close_session(session, DeviceSession.EndReason.LOGOUT)
        set_session_record(None)
    except Exception:
        logger.exception('Sentinel logout capture failed')


@receiver(user_login_failed)
def on_user_login_failed(sender, credentials, request=None, **kwargs):
    from . import services

    try:
        attempted = (credentials or {}).get('username') or 'unknown'
        services.log_event(
            event_type=EventType.LOGIN_FAILED,
            action=f'Failed sign-in attempt for "{attempted}"',
            request=request,
            actor=None,
            module='Access Control',
            object_type='accounts.CustomUser',
            object_label=str(attempted)[:255],
            severity=Severity.WARNING,
            context={'attempted_username': str(attempted)[:150]},
        )
    except Exception:
        logger.exception('Sentinel failed-login capture failed')


# ─────────────────────────────────────────────────────────────────────────────
# Model changes
# ─────────────────────────────────────────────────────────────────────────────

def _snapshot(instance):
    """Field values of a model instance, skipping noise and relations we can't cheaply read."""
    data = {}
    for field in instance._meta.concrete_fields:
        name = field.name
        if name in registry.IGNORED_FIELDS:
            continue
        try:
            # attname on FKs gives us the raw *_id — no extra query to fetch the object.
            value = getattr(instance, field.attname, None)
        except Exception:
            continue
        data[name] = (utils.redact_value(value) if utils.is_sensitive(name)
                      else utils.serialise_value(value))
    return data


def _diff(before, after):
    changed = {}
    for key, new_value in after.items():
        old_value = before.get(key)
        if old_value != new_value:
            changed[key] = {'old': old_value, 'new': new_value}
    return changed


def _should_capture(sender):
    if is_suppressed():
        return False
    if get_request() is None:
        # Writes from shells, migrations and Celery tasks have no actor; capturing
        # them here would attribute them to nobody and flood the stream. Background
        # jobs that matter call services.log_job() explicitly.
        return False
    return registry.is_tracked(sender)


@receiver(pre_save)
def capture_pre_save(sender, instance, **kwargs):
    if not _should_capture(sender):
        return
    if instance.pk is None:
        return
    try:
        from . import services

        # Re-reading the row costs one SELECT per save of every tracked model.
        # If the admin has turned field diffs off there is nothing to compare
        # against, so skip the query entirely rather than paying for a snapshot
        # that post_save will discard.
        if not services.get_settings().capture_field_diffs:
            return

        old = sender.objects.filter(pk=instance.pk).first()
        if old is not None:
            put_snapshot((registry.model_key(sender), instance.pk), _snapshot(old))
    except Exception:
        logger.debug('Sentinel pre_save snapshot failed for %s', sender, exc_info=True)


@receiver(post_save)
def capture_post_save(sender, instance, created, **kwargs):
    if not _should_capture(sender):
        return
    try:
        from . import services

        key = registry.model_key(sender)
        label = str(instance)[:255]
        noun = registry.humanise_model(sender)

        if created:
            add_captured(EventType.CREATE, {
                'action': f'Created {noun}: {label}',
                'object_type': key,
                'object_id': instance.pk,
                'object_label': label,
                'module': registry.module_label(sender._meta.app_label),
                'changes': {},
            })
            return

        before = pop_snapshot((key, instance.pk))
        if before is None:
            add_captured(EventType.UPDATE, {
                'action': f'Updated {noun}: {label}',
                'object_type': key, 'object_id': instance.pk, 'object_label': label,
                'module': registry.module_label(sender._meta.app_label),
                'changes': {},
            })
            return

        changes = _diff(before, _snapshot(instance))
        if not changes:
            return  # A save() that changed nothing is not news.

        if not services.get_settings().capture_field_diffs:
            changes = {field: {} for field in changes}

        # Permission/credential edits get their own event type so they're filterable.
        event_type = EventType.UPDATE
        if key == 'accounts.CustomUser':
            if 'password' in changes:
                event_type = EventType.PASSWORD
            elif any(registry.is_sensitive_field(f) for f in changes):
                event_type = EventType.PERMISSION

        summary = ', '.join(list(changes)[:3])
        if len(changes) > 3:
            summary += f' +{len(changes) - 3} more'

        add_captured(event_type, {
            'action': f'Updated {noun}: {label} ({summary})',
            'object_type': key,
            'object_id': instance.pk,
            'object_label': label,
            'module': registry.module_label(sender._meta.app_label),
            'changes': changes,
        })
    except Exception:
        logger.debug('Sentinel post_save capture failed for %s', sender, exc_info=True)


@receiver(post_delete)
def capture_post_delete(sender, instance, **kwargs):
    if not _should_capture(sender):
        return
    try:
        key = registry.model_key(sender)
        label = str(instance)[:255]
        add_captured(EventType.DELETE, {
            'action': f'Deleted {registry.humanise_model(sender)}: {label}',
            'object_type': key,
            'object_id': instance.pk,
            'object_label': label,
            'module': registry.module_label(sender._meta.app_label),
            # The whole row is the evidence here — once it's gone this snapshot is
            # the only remaining record of what it contained.
            'changes': {f: {'old': v, 'new': None} for f, v in _snapshot(instance).items()},
        })
    except Exception:
        logger.debug('Sentinel post_delete capture failed for %s', sender, exc_info=True)
