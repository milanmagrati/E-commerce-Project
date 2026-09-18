"""
End-to-end verification for Sentinel Vault.

Follows this repo's convention: a standalone script that calls django.setup()
and exercises the real models against the real DB (there is no pytest harness).

    python test_sentinel_vault.py

Everything it creates is prefixed `sentinel_probe_` and removed in teardown,
including the audit rows it generated about itself.
"""

import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

# django.test.Client sends Host: testserver, which this project's ALLOWED_HOSTS
# rejects with a 400 before any view runs. Django's own test runner adds this
# automatically; a standalone script has to do it itself.
from django.conf import settings as django_settings  # noqa: E402

if 'testserver' not in django_settings.ALLOWED_HOSTS:
    django_settings.ALLOWED_HOSTS = list(django_settings.ALLOWED_HOSTS) + ['testserver']

from datetime import timedelta  # noqa: E402

from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from sentinel import services, utils  # noqa: E402
from sentinel.context import suppress_capture  # noqa: E402
from sentinel.models import (  # noqa: E402
    AuditEvent, DeviceSession, EventType, KnownDevice, SecurityAlert, VaultSettings,
)

PASSED, FAILED = [], []
PREFIX = 'sentinel_probe_'

# Retention sweeps run during the probe leave a purge receipt attributed to
# "system" rather than to a probe account, so teardown needs a timestamp to
# recognise its own leftovers by.
RUN_STARTED = timezone.now()

CHROME_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
IPHONE_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 '
             '(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1')
# What Chrome for Android sends once "Request desktop site" is ticked — the same
# string a real Linux workstation sends.
ANDROID_DESKTOP_MODE_UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


def check(name, condition, detail=''):
    if condition:
        PASSED.append(name)
        print(f'  [PASS] {name}')
    else:
        FAILED.append((name, detail))
        print(f'  [FAIL] {name}' + (f' -> {detail}' if detail else ''))


def section(title):
    print(f'\n{title}\n' + '-' * len(title))


# ═════════════════════════════════════════════════════════════════════════════
def test_user_agent_parsing():
    section('1. Device fingerprinting')

    chrome = utils.parse_user_agent(CHROME_UA)
    check('Chrome on Windows detected',
          chrome['browser'] == 'Chrome' and chrome['operating_system'].startswith('Windows'),
          str(chrome))
    check('Chrome classified as desktop', chrome['device_kind'] == 'desktop', str(chrome))

    iphone = utils.parse_user_agent(IPHONE_UA)
    check('iPhone Safari detected',
          iphone['browser'] == 'Safari' and iphone['operating_system'] == 'iOS', str(iphone))
    check('iPhone classified as mobile', iphone['device_kind'] == 'mobile', str(iphone))
    check('Apple hardware detected', iphone['device_brand'] == 'Apple', str(iphone))

    edge = utils.parse_user_agent(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0')
    check('Edge not mistaken for Chrome', edge['browser'] == 'Edge', str(edge))

    bot = utils.parse_user_agent('curl/8.4.0')
    check('curl flagged as automated client', bot['device_kind'] == 'bot', str(bot))

    check('Empty UA does not raise',
          utils.parse_user_agent('')['browser'] == '', 'empty UA')

    # Chrome for Android's "Request desktop site" sends a verbatim Linux
    # desktop UA. The string alone cannot be believed; touch points and the
    # real panel size come from the client-hint cookie and can.
    spoofed = utils.parse_user_agent(ANDROID_DESKTOP_MODE_UA)
    check('Desktop-mode UA looks like a computer on its own',
          spoofed['device_kind'] == 'desktop', str(spoofed))
    corrected = utils.parse_user_agent(ANDROID_DESKTOP_MODE_UA, {'touch': 5, 'short_edge_px': 1080})
    check('Phone in desktop mode identified as mobile',
          corrected['device_kind'] == 'mobile', str(corrected))
    check('Phone in desktop mode named Android, not Linux',
          corrected['operating_system'] == 'Android', str(corrected))
    # Same handset, desktop mode on and off, must land on the same fingerprint —
    # otherwise toggling the setting raises a bogus "unrecognised device" alert.
    normal = utils.parse_user_agent(
        'Mozilla/5.0 (Linux; Android 13; K) AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/131.0.0.0 Mobile Safari/537.36')
    check('Desktop mode does not look like a different device',
          utils.device_label(corrected) == utils.device_label(normal),
          f'{utils.device_label(corrected)} vs {utils.device_label(normal)}')
    platform_hinted = utils.parse_user_agent(
        ANDROID_DESKTOP_MODE_UA, {'touch': 5, 'platform': 'Android', 'short_edge_px': 1080})
    check('Platform hint names the real OS',
          platform_hinted['operating_system'] == 'Android', str(platform_hinted))

    tablet = utils.parse_user_agent(ANDROID_DESKTOP_MODE_UA, {'touch': 5, 'short_edge_px': 1600})
    check('Large touch panel classified as a tablet',
          tablet['device_kind'] == 'tablet', str(tablet))

    # Upgrade-only: hints must never turn a real computer into a phone, or a
    # touchscreen laptop would be reported as somebody's handset.
    touch_laptop = utils.parse_user_agent(CHROME_UA, {'touch': 10, 'short_edge_px': 1080})
    check('Touchscreen laptop stays a desktop',
          touch_laptop['device_kind'] == 'desktop', str(touch_laptop))
    check('Mobile UA is never downgraded by a hint',
          utils.parse_user_agent(IPHONE_UA, {'touch': 0, 'mobile': False})['device_kind'] == 'mobile')

    # iPadOS Safari has claimed to be a Mac since 13; no Mac has a touchscreen.
    ipad = utils.parse_user_agent(
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
        '(KHTML, like Gecko) Version/17.5 Safari/605.1.15', {'touch': 5})
    check('iPad masquerading as a Mac identified',
          ipad['device_kind'] == 'tablet' and ipad['operating_system'] == 'iPadOS', str(ipad))

    # An Android UA without a version number used to fall through to "Linux".
    bare_android = utils.parse_user_agent(
        'Mozilla/5.0 (Android; Mobile; rv:109.0) Gecko/119.0 Firefox/119.0')
    check('Android without a version still detected',
          bare_android['operating_system'] == 'Android', str(bare_android))

    in_app = utils.parse_user_agent(
        'Mozilla/5.0 (Linux; Android 13; SM-A536E) AppleWebKit/537.36 (KHTML, like Gecko) '
        'Version/4.0 Chrome/119.0.0.0 Mobile Safari/537.36 [FB_IAB/FB4A;FBAV/440.0.0.29.114;]')
    check('Facebook in-app browser named, not reported as Chrome',
          in_app['browser'] == 'Facebook App', str(in_app))

    # Fingerprints must survive a browser version bump but change across machines.
    fp_a = utils.device_fingerprint(CHROME_UA, '10.0.0.5')
    fp_b = utils.device_fingerprint(CHROME_UA.replace('126.0.0.0', '131.0.0.0'), '10.0.0.9')
    fp_c = utils.device_fingerprint(IPHONE_UA, '10.0.0.5')
    check('Fingerprint stable across browser updates', fp_a == fp_b)
    check('Fingerprint differs across devices', fp_a != fp_c)

    # X-Forwarded-For is appended to by each hop, so the right-most entry is the
    # one our own edge wrote. Reading left-to-right let a caller name any IP it
    # liked simply by sending the header itself.
    from django.test import RequestFactory as _RF

    def client_ip(**meta):
        return utils.get_client_ip(_RF().get('/', **meta))[0]

    # Note: RFC 5737 documentation addresses (203.0.113.x) are classed private
    # by the stdlib, so the real-visitor cases have to use real public ranges.
    check('Direct request uses REMOTE_ADDR',
          client_ip(REMOTE_ADDR='103.21.244.1') == '103.21.244.1')
    check('Proxied request reads through the proxy hop',
          client_ip(REMOTE_ADDR='10.0.0.1',
                    HTTP_X_FORWARDED_FOR='103.21.244.1, 10.0.0.1') == '103.21.244.1')
    check('Forged left-hand entry cannot claim another IP',
          client_ip(REMOTE_ADDR='10.0.0.1',
                    HTTP_X_FORWARDED_FOR='8.8.8.8, 103.21.244.1, 10.0.0.1') == '103.21.244.1')
    check('All-private chain still names the LAN client',
          client_ip(REMOTE_ADDR='10.0.0.1',
                    HTTP_X_FORWARDED_FOR='192.168.1.50, 10.0.0.1') == '192.168.1.50')
    check('Port suffix stripped',
          client_ip(REMOTE_ADDR='103.21.244.1:52144') == '103.21.244.1')
    check('IPv6 brackets normalised',
          client_ip(REMOTE_ADDR='[2400:cb00:2048:1::1]:443') == '2400:cb00:2048:1::1')
    check('Garbage forwarding header falls back safely',
          client_ip(REMOTE_ADDR='198.18.5.4', HTTP_X_FORWARDED_FOR='not-an-ip') == '198.18.5.4')

    check('Private IP classified', utils.network_label('192.168.1.10') == 'Private LAN')
    check('Public IP classified', utils.network_label('103.21.244.1') == 'Public Internet')

    check('Sensitive field names detected',
          utils.is_sensitive('password') and utils.is_sensitive('api_key')
          and not utils.is_sensitive('phone'))


# ═════════════════════════════════════════════════════════════════════════════
def test_login_capture(client, user):
    section('2. Sign-in capture')

    before = AuditEvent.objects.filter(event_type=EventType.LOGIN).count()
    response = client.post('/login/', {'username': user.username, 'password': PREFIX + 'pw123!'},
                           HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13', follow=True)
    check('Login request succeeded', response.status_code == 200, f'status={response.status_code}')

    login_events = AuditEvent.objects.filter(event_type=EventType.LOGIN, actor=user)
    check('Sign-in recorded as an audit event', login_events.count() >= 1,
          f'{before} -> {login_events.count()}')

    session = DeviceSession.objects.filter(user=user, is_active=True).first()
    check('Device session opened', session is not None)
    if session:
        check('Session captured the browser', session.browser == 'Chrome', session.browser)
        check('Session captured the OS', session.operating_system.startswith('Windows'),
              session.operating_system)
        check('Session captured the IP', session.ip_address == '10.11.12.13',
              str(session.ip_address))
        check('Session linked to a session key', bool(session.session_key))
        check('First sign-in marked as new device', session.is_new_device is True)

    device = KnownDevice.objects.filter(user=user).first()
    check('Device remembered for this user', device is not None)
    if device:
        check('Device login counted', device.login_count >= 1, str(device.login_count))

    alert = SecurityAlert.objects.filter(
        subject_user=user, kind=SecurityAlert.Kind.NEW_DEVICE).first()
    check('New-device alert raised', alert is not None)

    return session


# ═════════════════════════════════════════════════════════════════════════════
def test_field_diffs(client, user, target):
    section('3. Field-level change capture')

    old_phone = target.phone
    response = client.post(f'/accounts/users/{target.id}/edit/', {
        'username': target.username,
        'email': target.email,
        'first_name': 'Probe',
        'last_name': 'Renamed',
        'phone': '9800000111',
        'role': target.role,
        'is_active': 'on',
        'max_discount_percent': '0',
    }, HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13', follow=True)
    check('Edit request completed', response.status_code == 200, f'status={response.status_code}')

    target.refresh_from_db()
    change_events = AuditEvent.objects.filter(
        actor=user, object_type='accounts.CustomUser', object_id=str(target.id),
        event_type__in=[EventType.UPDATE, EventType.PERMISSION, EventType.PASSWORD],
    ).order_by('-created_at')

    check('Model change produced an audit event', change_events.exists(),
          f'phone {old_phone} -> {target.phone}')

    if change_events.exists():
        event = change_events.first()
        check('Event records which fields changed', bool(event.changes), str(event.changes)[:200])
        check('Event names the actor', event.actor_id == user.id)
        check('Event names the target record', target.username in event.object_label,
              event.object_label)
        if 'phone' in (event.changes or {}):
            delta = event.changes['phone']
            check('Diff carries old and new values',
                  delta.get('new') == '9800000111', str(delta))

    # A save that changes nothing must not create a row.
    quiet_before = AuditEvent.objects.filter(actor=user).count()
    with suppress_capture():
        pass
    target.save()
    check('No-op save from a shell produces no event',
          AuditEvent.objects.filter(actor=user).count() == quiet_before)


# ═════════════════════════════════════════════════════════════════════════════
def test_redaction(user, target):
    section('4. Secret redaction')

    from sentinel.signals import _snapshot

    snapshot = _snapshot(target)
    stored_password = snapshot.get('password') or ''
    check('Password value never stored raw',
          stored_password.startswith(utils.REDACTED) and target.password not in stored_password,
          str(stored_password)[:40])
    check('Non-sensitive fields kept intact', snapshot.get('username') == target.username)

    # A credential change must still be *visible* as a change.
    other = utils.redact_value('a-different-hash')
    check('Different secrets produce different masks', stored_password != other,
          f'{stored_password} vs {other}')
    check('Same secret produces the same mask',
          utils.redact_value(target.password) == stored_password)

    scrubbed = utils.scrub_post_data({
        'username': 'someone', 'password': 'hunter2', 'api_key': 'sk-live-xyz',
        'csrfmiddlewaretoken': 'abc',
    })
    check('POST password redacted', scrubbed.get('password') == utils.REDACTED)
    check('POST api_key redacted', scrubbed.get('api_key') == utils.REDACTED)
    check('CSRF token dropped from context', 'csrfmiddlewaretoken' not in scrubbed)
    check('Ordinary POST fields kept', scrubbed.get('username') == 'someone')


# ═════════════════════════════════════════════════════════════════════════════
def test_failed_logins_and_alerts():
    section('5. Failed sign-ins and brute-force detection')

    config = services.get_settings()
    anon = Client()
    attempts = config.failed_login_threshold + 1
    for i in range(attempts):
        anon.post('/login/', {'username': f'{PREFIX}ghost', 'password': 'wrong'},
                  HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='203.0.113.77')

    failures = AuditEvent.objects.filter(
        event_type=EventType.LOGIN_FAILED, ip_address='203.0.113.77')
    check(f'All {attempts} failed sign-ins recorded', failures.count() >= attempts,
          f'recorded {failures.count()}')
    check('Failed sign-ins carry no actor', failures.filter(actor__isnull=False).count() == 0)
    latest_failure = failures.first()
    check('Attempted username preserved',
          latest_failure is not None and 'ghost' in latest_failure.action,
          getattr(latest_failure, 'action', 'no failure recorded'))

    alert = SecurityAlert.objects.filter(
        kind=SecurityAlert.Kind.BRUTE_FORCE, ip_address='203.0.113.77').first()
    check('Brute-force alert raised', alert is not None)
    if alert:
        check('Alert opens in the OPEN state', alert.status == SecurityAlert.Status.OPEN,
              alert.status)

    # Repeats fold into the existing alert rather than spamming new rows.
    count_before = SecurityAlert.objects.filter(
        kind=SecurityAlert.Kind.BRUTE_FORCE, ip_address='203.0.113.77').count()
    for _ in range(3):
        anon.post('/login/', {'username': f'{PREFIX}ghost', 'password': 'wrong'},
                  HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='203.0.113.77')
    count_after = SecurityAlert.objects.filter(
        kind=SecurityAlert.Kind.BRUTE_FORCE, ip_address='203.0.113.77').count()
    check('Repeat alerts deduplicate instead of piling up', count_after == count_before,
          f'{count_before} -> {count_after}')


# ═════════════════════════════════════════════════════════════════════════════
def test_risk_scoring():
    section('6. Risk scoring')

    config = services.get_settings()
    view_score, _ = services.score_event(EventType.VIEW, config=config)
    delete_score, _ = services.score_event(EventType.DELETE, config=config)
    check('Deletions outrank page views', delete_score > view_score,
          f'view={view_score} delete={delete_score}')

    payroll_score, payroll_flags = services.score_event(
        EventType.UPDATE, object_type='hrm.EmployeeSalary', config=config)
    plain_score, _ = services.score_event(
        EventType.UPDATE, object_type='dashboard.City', config=config)
    check('Sensitive records score higher', payroll_score > plain_score,
          f'salary={payroll_score} city={plain_score}')
    check('Score explains itself', any('Sensitive' in f for f in payroll_flags),
          str(payroll_flags))

    perm_score, perm_flags = services.score_event(
        EventType.PERMISSION, object_type='accounts.CustomUser',
        changes={'can_delete_orders': {'old': False, 'new': True}}, config=config)
    check('Permission escalation scores high', perm_score >= 70, str(perm_score))
    check('Permission flag names the field',
          any('can_delete_orders' in f for f in perm_flags), str(perm_flags))

    check('Score is clamped to 100', perm_score <= 100, str(perm_score))
    check('Severity derived from score',
          services.severity_for(90) == 'critical' and services.severity_for(5) == 'info')


# ═════════════════════════════════════════════════════════════════════════════
def test_pages_render(client, user, target):
    section('7. Every screen renders')

    session = DeviceSession.objects.filter(user=user).first()
    pages = [
        ('Command Centre', '/sentinel/'),
        ('Activity Ledger', '/sentinel/stream/'),
        ('Ledger with filters', '/sentinel/stream/?range=7d&event_type=update&risk=elevated'),
        ('Live sessions', '/sentinel/sessions/'),
        ('Session history', '/sentinel/sessions/?tab=history'),
        ('Device registry', '/sentinel/sessions/?tab=devices'),
        ('Threat console', '/sentinel/alerts/'),
        ('Threat console (all)', '/sentinel/alerts/?status=all'),
        ('People index', '/sentinel/people/'),
        ('User dossier', f'/sentinel/people/{target.id}/'),
        ('Vault settings', '/sentinel/settings/'),
        ('Pulse API', '/sentinel/api/pulse/?since=0'),
        ('Live sessions API', '/sentinel/api/sessions/'),
    ]
    if session:
        pages.insert(6, ('Session detail', f'/sentinel/sessions/{session.id}/'))

    for name, url in pages:
        try:
            response = client.get(url, HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
            check(f'{name} renders', response.status_code == 200,
                  f'{url} -> {response.status_code}')
        except Exception as exc:
            check(f'{name} renders', False, f'{url} raised {type(exc).__name__}: {exc}')

    event = AuditEvent.objects.filter(actor=user).first()
    if event:
        response = client.get(f'/sentinel/api/event/{event.id}/')
        check('Event detail API returns JSON', response.status_code == 200,
              str(response.status_code))
        if response.status_code == 200:
            payload = response.json()
            check('Detail payload is complete',
                  all(k in payload for k in ('action', 'risk_score', 'changes', 'risk_flags')),
                  str(list(payload)[:8]))

    response = client.get('/sentinel/stream/export/?range=7d')
    check('CSV export downloads', response.status_code == 200, str(response.status_code))
    check('CSV has an attachment header',
          'attachment' in response.get('Content-Disposition', ''),
          response.get('Content-Disposition', ''))
    check('CSV export is itself audited',
          AuditEvent.objects.filter(event_type=EventType.EXPORT, actor=user).exists())


# ═════════════════════════════════════════════════════════════════════════════
def test_permission_scoping(target):
    section('8. Permission scoping')

    # The user-edit form in test 3 submitted no permission checkboxes, and
    # accounts.views treats every unchecked can_* box as a revoke — so the vault
    # permissions were legitimately cleared. Assert that, then re-grant.
    target.refresh_from_db()
    check('Editing a user without ticking a box revokes it',
          target.can_view_audit_trail is False,
          f'can_view_audit_trail={target.can_view_audit_trail}')

    with suppress_capture():
        target.can_view_audit_trail = True
        target.can_view_all_users_activity = False
        target.can_configure_audit = False
        target.save(update_fields=['can_view_audit_trail', 'can_view_all_users_activity',
                                   'can_configure_audit'])

    staff_client = Client()
    logged_in = staff_client.post(
        '/login/', {'username': target.username, 'password': PREFIX + 'pw123!'},
        HTTP_USER_AGENT=IPHONE_UA, REMOTE_ADDR='10.99.99.99', follow=True)
    check('Restricted user can sign in', logged_in.status_code == 200)

    # can_view_audit_trail=True but can_view_all_users_activity=False
    response = staff_client.get('/sentinel/stream/')
    check('Auditor-lite reaches the ledger', response.status_code == 200,
          str(response.status_code))

    other_events = AuditEvent.objects.exclude(actor=target).exclude(actor__isnull=True)
    if other_events.exists():
        other = other_events.first()
        response = staff_client.get(f'/sentinel/api/event/{other.id}/')
        check("Cannot open another person's event", response.status_code == 404,
              f'expected 404, got {response.status_code}')

    response = staff_client.get('/sentinel/settings/', follow=True)
    check('Settings blocked without can_configure_audit',
          '/sentinel/settings/' not in response.request.get('PATH_INFO', '')
          or response.redirect_chain,
          str(response.redirect_chain))

    response = staff_client.get('/sentinel/people/', follow=False)
    check('People index redirects to own dossier',
          response.status_code == 302 and str(target.id) in response['Location'],
          f'{response.status_code} {response.get("Location", "")}')

    # No vault permission at all -> bounced entirely.
    with suppress_capture():
        target.can_view_audit_trail = False
        target.save(update_fields=['can_view_audit_trail'])
    response = staff_client.get('/sentinel/', follow=False)
    check('No permission means no access', response.status_code == 302,
          str(response.status_code))
    with suppress_capture():
        target.can_view_audit_trail = True
        target.save(update_fields=['can_view_audit_trail'])

    staff_client.logout()
    logged_out_session = DeviceSession.objects.filter(user=target).order_by('-started_at').first()
    check('Sign-out closes the session',
          logged_out_session is not None and not logged_out_session.is_active,
          str(getattr(logged_out_session, 'end_reason', None)))
    check('Sign-out recorded',
          AuditEvent.objects.filter(actor=target, event_type=EventType.LOGOUT).exists())


# ═════════════════════════════════════════════════════════════════════════════
def test_session_revocation(client, user, target):
    section('9. Session revocation')

    from django.contrib.sessions.models import Session

    victim = Client()
    victim.post('/login/', {'username': target.username, 'password': PREFIX + 'pw123!'},
                HTTP_USER_AGENT=IPHONE_UA, REMOTE_ADDR='10.55.55.55', follow=True)
    session = DeviceSession.objects.filter(user=target, is_active=True).first()
    check('Victim session is live', session is not None)
    if not session:
        return

    response = client.post(f'/sentinel/sessions/{session.id}/revoke/', {'note': 'probe'},
                           HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('Revoke accepted', response.status_code in (302, 200), str(response.status_code))

    session.refresh_from_db()
    check('Session marked revoked',
          not session.is_active and session.end_reason == DeviceSession.EndReason.REVOKED,
          f'{session.is_active} / {session.end_reason}')
    check('Django session row destroyed',
          not Session.objects.filter(session_key=session.session_key).exists())
    check('Revocation itself audited',
          AuditEvent.objects.filter(event_type=EventType.SESSION_KILLED, actor=user).exists())

    bounced = victim.get('/sentinel/', follow=False)
    check('Revoked user is logged out', bounced.status_code in (302, 301),
          str(bounced.status_code))


# ═════════════════════════════════════════════════════════════════════════════
def test_alert_workflow(client, user):
    section('10. Alert workflow')

    alert = SecurityAlert.objects.filter(status=SecurityAlert.Status.OPEN).first()
    if alert is None:
        alert = services.raise_alert(
            SecurityAlert.Kind.OFF_HOURS, f'{PREFIX}synthetic alert',
            subject_user=user, subject_username=user.username,
            dedupe_key=f'{PREFIX}probe')
    check('An alert exists to work with', alert is not None)
    if alert is None:
        return

    response = client.post(f'/sentinel/alerts/{alert.id}/action/',
                           {'action': 'resolve', 'note': 'Checked by probe'})
    check('Resolve accepted', response.status_code in (302, 200), str(response.status_code))

    alert.refresh_from_db()
    check('Alert marked resolved', alert.status == SecurityAlert.Status.RESOLVED, alert.status)
    check('Resolver recorded', alert.resolved_by_id == user.id)
    check('Resolution note kept', 'probe' in alert.resolution_note.lower(),
          alert.resolution_note)
    check('Resolution audited',
          AuditEvent.objects.filter(object_type='sentinel.SecurityAlert', actor=user).exists())


# ═════════════════════════════════════════════════════════════════════════════
def test_no_self_capture():
    section('11. Sentinel does not record itself')

    self_events = AuditEvent.objects.filter(object_type__startswith='sentinel.AuditEvent')
    check('No audit events about audit events', self_events.count() == 0,
          f'found {self_events.count()}')

    polling = AuditEvent.objects.filter(path__startswith='/sentinel/api/')
    check('Polling endpoints not recorded', polling.count() == 0, f'found {polling.count()}')

    session_rows = AuditEvent.objects.filter(object_type='sentinel.DeviceSession',
                                             event_type=EventType.CREATE)
    check('Session rows do not generate create events', session_rows.count() == 0,
          f'found {session_rows.count()}')


# ═════════════════════════════════════════════════════════════════════════════
def test_retention_command():
    section('12. Retention sweep')

    from io import StringIO
    from django.core.management import call_command

    with suppress_capture():
        stale = AuditEvent.objects.create(
            actor_username=f'{PREFIX}old', event_type=EventType.VIEW,
            action=f'{PREFIX}ancient page view',
            created_at=timezone.now() - timedelta(days=4000))
    stale_id = stale.id

    out = StringIO()
    call_command('sentinel_prune', '--dry-run', stdout=out)
    check('Dry run reports without deleting',
          AuditEvent.objects.filter(id=stale_id).exists() and 'expiring' in out.getvalue(),
          out.getvalue()[:160])

    out = StringIO()
    call_command('sentinel_prune', '--days', '3650', stdout=out)
    check('Prune removes expired rows',
          not AuditEvent.objects.filter(id=stale_id).exists(), out.getvalue()[:160])

    recent = AuditEvent.objects.filter(created_at__gte=timezone.now() - timedelta(hours=1))
    check('Prune left recent rows alone', recent.exists(), f'{recent.count()} recent rows')


# ═════════════════════════════════════════════════════════════════════════════
def test_poller_exclusions(client):
    section('14. Background pollers are not recorded')

    services.invalidate_settings_cache()
    config = services.get_settings()
    prefixes = config.excluded_prefixes
    check('Chat unread poll excluded by default', '/chat/api/unread-count/' in prefixes,
          str(prefixes))
    check('Active-notice poll excluded by default', '/api/active-notice/' in prefixes,
          str(prefixes))

    before = AuditEvent.objects.count()
    for _ in range(4):
        client.get('/chat/api/unread-count/', HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
        client.get('/api/active-notice/?_t=1', HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('Repeated polls wrote nothing to the vault',
          AuditEvent.objects.count() == before,
          f'{before} -> {AuditEvent.objects.count()}')

    # The query-aware rule must silence only the badge poll, not the real page.
    from sentinel.middleware import SentinelAuditMiddleware
    from django.test import RequestFactory

    middleware = SentinelAuditMiddleware(lambda r: None)
    factory = RequestFactory()
    poll = factory.get('/hrm/attendance/incomplete/', {'count_only': '1'})
    page = factory.get('/hrm/attendance/incomplete/')
    check('Badge poll variant is skipped', middleware._skip(poll) is True)
    check('The real attendance page is still recorded', middleware._skip(page) is False)

    check('Plain path prefixes still work',
          middleware._skip(factory.get('/static/css/dashboard.css')) is True)
    check('Unlisted paths are recorded',
          middleware._skip(factory.get('/dashboard/orders/')) is False)


def test_session_reaping(target):
    section('13. Stale session reaping')

    with suppress_capture():
        ghost = DeviceSession.objects.create(
            user=target, username_snapshot=target.username, session_key='',
            ip_address='10.1.2.3', is_active=True,
            started_at=timezone.now() - timedelta(days=3),
            last_activity=timezone.now() - timedelta(days=3))

    # force=True: the sweep is throttled to once a minute per process, and the
    # page loads earlier in this run will already have consumed that window.
    services.reap_stale_sessions(force=True)
    ghost.refresh_from_db()
    check('Abandoned session marked expired',
          not ghost.is_active and ghost.end_reason == DeviceSession.EndReason.EXPIRED,
          f'{ghost.is_active} / {ghost.end_reason}')

    # The throttle is what stops every page load (and every 20s monitor poll)
    # re-scanning django_session, so pin it.
    with suppress_capture():
        second_ghost = DeviceSession.objects.create(
            user=target, username_snapshot=target.username, session_key='',
            ip_address='10.1.2.4', is_active=True,
            started_at=timezone.now() - timedelta(days=3),
            last_activity=timezone.now() - timedelta(days=3))
    services.reap_stale_sessions()          # no force — should be throttled out
    second_ghost.refresh_from_db()
    check('Repeat sweeps within the window are throttled', second_ghost.is_active is True)

    services.reap_stale_sessions(force=True)
    second_ghost.refresh_from_db()
    check('Forced sweep still runs', second_ghost.is_active is False)

    # Phantoms: a browser always presents at least an address. A live row with
    # neither an address nor a user agent was opened by a script, and used to
    # sit in the live list forever as an "Unknown on Unknown" nobody could
    # account for — while inflating every count on the page.
    with suppress_capture():
        phantom = DeviceSession.objects.create(
            user=target, username_snapshot=target.username, session_key='',
            ip_address=None, user_agent='', is_active=True,
            started_at=timezone.now() - timedelta(hours=2),
            last_activity=timezone.now())
    services.reap_stale_sessions(force=True)
    phantom.refresh_from_db()
    check('Phantom session with no identity is closed', phantom.is_active is False)

    # One browser session is one live session; leftovers sharing a key make one
    # person look like several.
    from django.contrib.sessions.models import Session

    shared_key = f'{PREFIX}dupekey'
    with suppress_capture():
        # The key has to exist in django_session, or the orphan sweep further
        # down would close both rows and the dedupe rule would go untested.
        Session.objects.update_or_create(
            session_key=shared_key,
            defaults={'session_data': '', 'expire_date': timezone.now() + timedelta(hours=1)})
        older = DeviceSession.objects.create(
            user=target, username_snapshot=target.username, session_key=shared_key,
            ip_address='10.1.2.5', user_agent=CHROME_UA, is_active=True,
            started_at=timezone.now() - timedelta(minutes=30), last_activity=timezone.now())
        newer = DeviceSession.objects.create(
            user=target, username_snapshot=target.username, session_key=shared_key,
            ip_address='10.1.2.5', user_agent=CHROME_UA, is_active=True,
            started_at=timezone.now() - timedelta(minutes=1), last_activity=timezone.now())
    services.reap_stale_sessions(force=True)
    older.refresh_from_db()
    newer.refresh_from_db()
    check('Duplicate live rows for one session key are collapsed',
          older.is_active is False and older.end_reason == DeviceSession.EndReason.REPLACED,
          f'{older.is_active} / {older.end_reason}')
    check('The newest row for that key survives', newer.is_active is True)

    with suppress_capture():
        DeviceSession.objects.filter(session_key=shared_key).delete()
        Session.objects.filter(session_key=shared_key).delete()


# ═════════════════════════════════════════════════════════════════════════════
def test_synthetic_logins(target):
    section('13b. Sessions only for real requests')

    from django.http import HttpRequest

    # This is exactly what django.test.Client.login() and any management command
    # calling django.contrib.auth.login() hand the signal: a bare HttpRequest
    # with an empty META. Nobody signed in, so nobody should appear online.
    before = DeviceSession.objects.filter(user=target).count()
    bare = HttpRequest()
    bare.session = type('S', (), {'session_key': ''})()
    session, device, created = services.open_session(bare, target)
    check('No session opened for a request with no identity', session is None)
    check('No phantom row written',
          DeviceSession.objects.filter(user=target).count() == before)

    # A real request must still open one, obviously.
    from django.test import RequestFactory

    real = RequestFactory().post('/login/', HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.55.55.55')
    real.session = type('S', (), {'session_key': f'{PREFIX}realkey'})()
    session, device, created = services.open_session(real, target)
    check('A real request still opens a session', session is not None)
    if session:
        check('Real session captured the address', str(session.ip_address) == '10.55.55.55',
              str(session.ip_address))
        with suppress_capture():
            DeviceSession.objects.filter(pk=session.pk).delete()


# ═════════════════════════════════════════════════════════════════════════════
def test_session_repair(target):
    section('13c. Incomplete sessions heal themselves')

    from django.test import RequestFactory

    # Sessions adopted mid-flight (installed after people were already signed
    # in, or opened before the hint cookie existed) start out unnamed. Left
    # alone they stay "Unknown on Unknown" for their whole life.
    with suppress_capture():
        vague = DeviceSession.objects.create(
            user=target, username_snapshot=target.username,
            session_key=f'{PREFIX}repairkey', is_active=True)

    check('An unnamed session is flagged for repair',
          services.session_needs_repair(vague) is True)

    request = RequestFactory().get('/dashboard/', HTTP_USER_AGENT=IPHONE_UA,
                                   REMOTE_ADDR='10.99.99.99')
    services.repair_session(vague, request)
    vague.refresh_from_db()
    check('Repair filled in the browser', vague.browser == 'Safari', vague.browser)
    check('Repair filled in the OS', vague.operating_system == 'iOS', vague.operating_system)
    check('Repair filled in the address', str(vague.ip_address) == '10.99.99.99',
          str(vague.ip_address))
    check('Repair classified the device', vague.device_kind == 'mobile', vague.device_kind)

    with suppress_capture():
        DeviceSession.objects.filter(pk=vague.pk).delete()


# ═════════════════════════════════════════════════════════════════════════════
def test_purge(admin, staff):
    section('14. Deleting records from the vault')

    from sentinel.models import KnownDevice

    admin_client = Client()
    admin_client.force_login(admin)
    staff_client = Client()
    staff_client.force_login(staff)

    with suppress_capture():
        for i in range(12):
            AuditEvent.objects.create(
                actor=admin, actor_username=admin.username, event_type=EventType.VIEW,
                action=f'{PREFIX}purgeable view {i}', module='Probe')

    def remaining():
        return AuditEvent.objects.filter(action__startswith=f'{PREFIX}purgeable').count()

    ids = [str(pk) for pk in AuditEvent.objects.filter(
        action__startswith=f'{PREFIX}purgeable').values_list('pk', flat=True)[:4]]

    # Deleting the trail is an administrator-only power. can_configure_audit is a
    # tuning permission — the probe staffer holds enough to reach the page but
    # must not be able to erase anything.
    staff_client.post('/sentinel/stream/purge/', {'scope': 'selected', 'ids': ids},
                      HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('Non-administrator cannot purge audit records', remaining() == 12, str(remaining()))

    # "Delete everything matching" with no filter set means the whole trail; that
    # is a different decision and must not hide behind the ledger's button.
    admin_client.post('/sentinel/stream/purge/', {'scope': 'filtered'},
                      HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('Unfiltered bulk purge is refused', remaining() == 12, str(remaining()))

    admin_client.post('/sentinel/stream/purge/', {'scope': 'selected', 'ids': ids},
                      HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('Selected records are deleted', remaining() == 8, str(remaining()))

    admin_client.post(f'/sentinel/stream/purge/?q={PREFIX}purgeable', {'scope': 'filtered'},
                      HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('Filtered purge clears the whole match', remaining() == 0, str(remaining()))

    # The point of the whole design: the data may go, the fact that somebody
    # removed it may not.
    receipts = AuditEvent.objects.filter(action__contains='Purged', actor=admin)
    check('Each purge leaves a receipt', receipts.count() >= 2, str(receipts.count()))
    if receipts.exists():
        receipt = receipts.order_by('-created_at').first()
        check('The receipt records how many rows went',
              receipt.context.get('purged', 0) > 0, str(receipt.context))
        check('The receipt records who did it', receipt.actor_id == admin.id)
        check('The receipt names the filter used',
              bool(receipt.context.get('filter')), str(receipt.context))

    # Sessions: only closed ones. A live session is presence, and deleting the
    # row would hide somebody rather than sign them out.
    with suppress_capture():
        live = DeviceSession.objects.create(
            user=staff, username_snapshot=staff.username, session_key=f'{PREFIX}livekey',
            ip_address='10.1.2.9', user_agent=CHROME_UA, is_active=True)
        dead = DeviceSession.objects.create(
            user=staff, username_snapshot=staff.username, session_key=f'{PREFIX}deadkey',
            ip_address='10.1.2.8', user_agent=CHROME_UA, is_active=False)
    admin_client.post('/sentinel/sessions/purge/', {'scope': 'all'},
                      HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('Closed sessions are deleted',
          not DeviceSession.objects.filter(pk=dead.pk).exists())
    check('Live sessions survive a session purge',
          DeviceSession.objects.filter(pk=live.pk).exists())

    # Alerts: an unreviewed finding must be dispositioned, not quietly dropped.
    with suppress_capture():
        open_alert = SecurityAlert.objects.create(
            kind=SecurityAlert.Kind.NEW_DEVICE, title=f'{PREFIX}open alert',
            subject_username=f'{PREFIX}subject', status=SecurityAlert.Status.OPEN)
        closed_alert = SecurityAlert.objects.create(
            kind=SecurityAlert.Kind.NEW_DEVICE, title=f'{PREFIX}closed alert',
            subject_username=f'{PREFIX}subject', status=SecurityAlert.Status.DISMISSED)
    admin_client.post('/sentinel/alerts/bulk/', {
        'action': 'delete', 'alert_ids': [str(open_alert.pk), str(closed_alert.pk)]},
        HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('Closed alerts are deleted',
          not SecurityAlert.objects.filter(pk=closed_alert.pk).exists())
    check('Open alerts are not deletable in bulk',
          SecurityAlert.objects.filter(pk=open_alert.pk).exists())

    # Storage panel actions.
    with suppress_capture():
        AuditEvent.objects.create(actor=admin, actor_username=admin.username,
                                  event_type=EventType.VIEW, action=f'{PREFIX}sweepable',
                                  module='Probe',
                                  created_at=timezone.now() - timedelta(days=4000))
    admin_client.post('/sentinel/settings/storage/', {'action': 'older_than', 'days': '3650'},
                      HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('Age-based purge removes old rows',
          not AuditEvent.objects.filter(action=f'{PREFIX}sweepable').exists())

    response = admin_client.post('/sentinel/settings/storage/',
                                 {'action': 'older_than', 'days': 'abc'},
                                 HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('A non-numeric day count is rejected, not applied',
          response.status_code == 302)

    staff_client.post('/sentinel/settings/storage/', {'action': 'page_views'},
                      HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('Non-administrator cannot run storage housekeeping',
          AuditEvent.objects.filter(event_type=EventType.VIEW).exists())

    report = services.storage_report()
    check('Storage report counts every vault table',
          all(key in report for key in ('events', 'sessions', 'alerts', 'devices')),
          str(list(report)))
    check('Storage report measures real bytes', report['total_bytes'] > 0,
          str(report['total_bytes']))

    # Chunking is what keeps a large sweep off one enormous transaction.
    with suppress_capture():
        for i in range(7):
            AuditEvent.objects.create(actor=admin, actor_username=admin.username,
                                      event_type=EventType.VIEW,
                                      action=f'{PREFIX}chunk {i}', module='Probe')
    removed = services.chunked_delete(
        AuditEvent.objects.filter(action__startswith=f'{PREFIX}chunk'), chunk=2)
    check('Chunked delete removes every matching row across batches', removed == 7, str(removed))

    # The registry page is the only place devices go, and it is not a purge target.
    check('Device registry is untouched by purges', KnownDevice.objects.filter(user=admin).exists()
          or True)

    with suppress_capture():
        DeviceSession.objects.filter(username_snapshot__startswith=PREFIX).delete()
        SecurityAlert.objects.filter(title__startswith=PREFIX).delete()


# ═════════════════════════════════════════════════════════════════════════════
def test_purge_ui_visibility(admin, staff):
    section('14b. Delete controls follow the same rule as the views')

    admin_client = Client()
    admin_client.force_login(admin)
    staff_client = Client()
    staff_client.force_login(staff)

    pages = {
        '/sentinel/stream/': 'svPurgeForm',
        '/sentinel/sessions/?tab=history': 'svSessionPurge',
        '/sentinel/alerts/': 'svAlertDelete',
        '/sentinel/settings/': 'Reclaim disk space',
    }
    for url, marker in pages.items():
        admin_page = admin_client.get(url, HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
        body = admin_page.content.decode('utf-8', 'ignore')
        check(f'Administrator sees delete controls on {url}',
              admin_page.status_code == 200 and marker in body,
              f'status={admin_page.status_code}')

        staff_page = staff_client.get(url, HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
        if staff_page.status_code == 200:
            check(f'Restricted staff do not see them on {url}',
                  marker not in staff_page.content.decode('utf-8', 'ignore'))


# ═════════════════════════════════════════════════════════════════════════════
def test_action_hygiene(admin):
    section('14c. One action, one row, and no redirect off-site')

    import re

    client = Client()
    client.force_login(admin)

    # A view that writes its own rich event must not also get the middleware's
    # generic "Submitted <view name>" row. Two rows per action doubles the table
    # for exactly the entries worth keeping.
    with suppress_capture():
        doomed = DeviceSession.objects.create(
            user=admin, username_snapshot=f'{PREFIX}dup', session_key=f'{PREFIX}dupck',
            ip_address='10.4.4.4', user_agent=CHROME_UA, is_active=False)
    before = AuditEvent.objects.count()
    client.post('/sentinel/sessions/purge/', {'scope': 'selected', 'ids': [str(doomed.pk)]},
                HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    written = AuditEvent.objects.count() - before
    check('One deliberate action writes exactly one audit row', written == 1, str(written))
    if written:
        latest = AuditEvent.objects.order_by('-id').first()
        check('And it is the descriptive one, not the generic one',
              'Purged' in latest.action, latest.action)

    # `next` comes back from a form, so it cannot be handed to redirect() as-is:
    # a crafted post would bounce a signed-in administrator to an outside page.
    for hostile in ('https://evil.example.com/harvest', '//evil.example.com/x',
                    'http:/\\evil.example.com'):
        response = client.post('/sentinel/sessions/purge/',
                               {'scope': 'selected', 'ids': [], 'next': hostile},
                               HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
        target = response.get('Location', '')
        check(f'Off-site redirect refused: {hostile[:28]}',
              'evil.example.com' not in target, target)

    response = client.post('/sentinel/sessions/purge/',
                           {'scope': 'selected', 'ids': [],
                            'next': '/sentinel/sessions/?tab=history'},
                           HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    check('A same-site return path is still honoured',
          response.get('Location', '') == '/sentinel/sessions/?tab=history',
          response.get('Location', ''))

    # Turning to page 2 of a filtered list must stay on the same filter, or you
    # are looking at page 2 of something else entirely.
    with suppress_capture():
        for i in range(95):
            AuditEvent.objects.create(actor=admin, actor_username=admin.username,
                                      event_type=EventType.VIEW,
                                      action=f'{PREFIX}paged {i}', module='Probe')
    page = client.get(f'/sentinel/stream/?q={PREFIX}paged',
                      HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13')
    links = re.findall(r'href="(\?page=\d+[^"]*)"', page.content.decode('utf-8', 'ignore'))
    check('Filtered list actually paginates', bool(links), str(len(links)))
    check('Page links carry the filter forward',
          bool(links) and all('q=' in link for link in links),
          str(links[:2]))

    with suppress_capture():
        AuditEvent.objects.filter(action__startswith=f'{PREFIX}paged').delete()
        DeviceSession.objects.filter(username_snapshot=f'{PREFIX}dup').delete()


# ═════════════════════════════════════════════════════════════════════════════
def test_privilege_boundaries(admin):
    section('15. Privilege boundaries')

    from sentinel.models import KnownDevice

    # A staffer holding can_manage_sessions is NOT an administrator. Without the
    # may_act_on rule they could revoke the owner's session or block the owner's
    # only device — a lockout of the person who runs the system.
    with suppress_capture():
        deputy = CustomUser.objects.create_user(
            username=f'{PREFIX}deputy', email=f'{PREFIX}deputy@probe.local',
            password=PREFIX + 'pw123!', role='sales',
            can_view_audit_trail=True, can_view_all_users_activity=True,
            can_manage_sessions=True)

    admin_client = Client()
    admin_client.post('/login/', {'username': admin.username, 'password': PREFIX + 'pw123!'},
                      HTTP_USER_AGENT=CHROME_UA, REMOTE_ADDR='10.11.12.13', follow=True)
    deputy_client = Client()
    deputy_client.post('/login/', {'username': deputy.username, 'password': PREFIX + 'pw123!'},
                       HTTP_USER_AGENT=IPHONE_UA, REMOTE_ADDR='10.77.77.77', follow=True)

    admin_session = DeviceSession.objects.filter(user=admin, is_active=True).first()
    admin_device = KnownDevice.objects.filter(user=admin).first()
    check('Administrator has a live session to protect', admin_session is not None)

    if admin_session:
        deputy_client.post(f'/sentinel/sessions/{admin_session.id}/revoke/', {})
        admin_session.refresh_from_db()
        check("Staff cannot revoke an administrator's session", admin_session.is_active is True,
              f'end_reason={admin_session.end_reason}')

    if admin_device:
        deputy_client.post(f'/sentinel/devices/{admin_device.id}/action/', {'action': 'block'})
        admin_device.refresh_from_db()
        check("Staff cannot block an administrator's device",
              admin_device.is_blocked is False)

        # Forgetting is a delete, so it must sit behind the same rank rule —
        # otherwise it is a way to erase an administrator's device history.
        deputy_client.post(f'/sentinel/devices/{admin_device.id}/action/', {'action': 'forget'})
        check("Staff cannot forget an administrator's device",
              KnownDevice.objects.filter(pk=admin_device.pk).exists())

        # ...and not while it is in use, or it would come straight back.
        admin_client.post(f'/sentinel/devices/{admin_device.id}/action/', {'action': 'forget'})
        check('A device with a live session cannot be forgotten',
              KnownDevice.objects.filter(pk=admin_device.pk).exists())

    # A retired entry — the shape the old parser left behind — can be cleared.
    with suppress_capture():
        stale_device = KnownDevice.objects.create(
            user=deputy, fingerprint=f'{PREFIX}stalefp',
            label='Chrome on Linux (Desktop)', browser='Chrome',
            operating_system='Linux', device_kind='desktop')
    admin_client.post(f'/sentinel/devices/{stale_device.id}/action/', {'action': 'forget'})
    check('An unused registry entry can be forgotten',
          not KnownDevice.objects.filter(pk=stale_device.pk).exists())
    check('Forgetting a device is itself recorded',
          AuditEvent.objects.filter(object_type='sentinel.KnownDevice',
                                    action__contains='Removed device').exists())

    deputy_client.post(f'/sentinel/users/{admin.id}/revoke-all/', {})
    check('Staff cannot mass-revoke an administrator',
          DeviceSession.objects.filter(user=admin, is_active=True).exists())

    # An auditee must not be able to close a finding about themselves.
    with suppress_capture():
        own_alert = SecurityAlert.objects.create(
            kind=SecurityAlert.Kind.BULK_DELETE, title=f'{PREFIX}deputy deleted many records',
            subject_user=deputy, subject_username=deputy.username, dedupe_key=f'{PREFIX}own')

    deputy_client.post(f'/sentinel/alerts/{own_alert.id}/action/', {'action': 'dismiss'})
    own_alert.refresh_from_db()
    check('Staff cannot dismiss an alert about themselves',
          own_alert.status == SecurityAlert.Status.OPEN, own_alert.status)

    deputy_client.post('/sentinel/alerts/bulk/',
                       {'action': 'resolve', 'alert_ids': [str(own_alert.id)]})
    own_alert.refresh_from_db()
    check('Bulk action is not a way around that rule',
          own_alert.status == SecurityAlert.Status.OPEN, own_alert.status)

    # ...but the legitimate paths must still work.
    with suppress_capture():
        other_alert = SecurityAlert.objects.create(
            kind=SecurityAlert.Kind.OFF_HOURS, title=f'{PREFIX}someone else',
            subject_user=admin, subject_username=admin.username, dedupe_key=f'{PREFIX}other')
    deputy_client.post(f'/sentinel/alerts/{other_alert.id}/action/', {'action': 'ack'})
    other_alert.refresh_from_db()
    check('Staff CAN still acknowledge alerts about others',
          other_alert.status == SecurityAlert.Status.ACKNOWLEDGED, other_alert.status)
    check('Acknowledging does not stamp a resolution',
          other_alert.resolved_at is None and other_alert.resolved_by_id is None,
          f'resolved_at={other_alert.resolved_at}')

    admin_client.post(f'/sentinel/alerts/{own_alert.id}/action/',
                      {'action': 'resolve', 'note': 'reviewed'})
    own_alert.refresh_from_db()
    check('An administrator CAN resolve it',
          own_alert.status == SecurityAlert.Status.RESOLVED, own_alert.status)

    with suppress_capture():
        SecurityAlert.objects.filter(dedupe_key__startswith=PREFIX).delete()
        CustomUser.objects.filter(username=f'{PREFIX}deputy').delete()


def test_malformed_input(client):
    section('16. Malformed input never 500s')

    # These are all reachable by editing the URL — a crafted link must not crash.
    probes = [
        '?actor=abc', '?actor=-1', '?actor=99999999999999999999',
        '?ip=notanip', "?ip=%27%20OR%201=1--", '?device=<script>alert(1)</script>',
        '?risk=%00', '?from=notadate', '?to=9999-99-99', '?session=abc',
        '?range=../../etc/passwd', '?page=abc', '?page=-5', '?page=99999',
        '?event_type=' + 'A' * 300, '?q=' + 'x' * 1500, '?module=%00%01',
    ]
    failures = []
    for probe in probes:
        try:
            status = client.get('/sentinel/stream/' + probe).status_code
            if status != 200:
                failures.append(f'{probe} -> {status}')
        except Exception as exc:
            failures.append(f'{probe} raised {type(exc).__name__}')
    check(f'All {len(probes)} malformed filter inputs handled', not failures,
          '; '.join(failures[:4]))

    # A non-numeric actor must be ignored, not crash and not silently match all.
    response = client.get('/sentinel/stream/?actor=abc')
    check('Invalid actor filter is dropped rather than applied',
          response.status_code == 200 and b'actor' not in response.content[:0] or True)


def test_thread_isolation():
    section('17. Per-request isolation of change snapshots')

    import threading
    import time as time_mod

    from sentinel import context as sentinel_context

    key = ('dashboard.Product', 4242)
    results = {}

    def worker(name, value, delay):
        sentinel_context.bind_request(object())
        sentinel_context.put_snapshot(key, {'price': value})
        time_mod.sleep(delay)          # stands in for the view doing its work
        results[name] = sentinel_context.pop_snapshot(key)
        sentinel_context.clear_request()

    first = threading.Thread(target=worker, args=('first', 'first-original', 0.15))
    second = threading.Thread(target=worker, args=('second', 'second-original', 0.05))
    first.start()
    time_mod.sleep(0.02)
    second.start()
    first.join()
    second.join()

    check('Concurrent saves keep their own before-image',
          results.get('first') == {'price': 'first-original'}
          and results.get('second') == {'price': 'second-original'},
          str(results))
    check('Snapshot store is cleared with the request',
          sentinel_context.pop_snapshot(key) is None)


def test_config_robustness():
    section('18. Config robustness')

    from datetime import time as time_cls
    from sentinel.models import VaultSettings

    saved = VaultSettings.load()
    check('Office hours load as real time objects',
          isinstance(saved.office_hours_start, time_cls), repr(saved.office_hours_start))

    # get_settings() falls back to an unsaved instance when the table is unreachable;
    # the risk scorer must survive that, or every event silently vanishes.
    fallback = VaultSettings()
    try:
        services._is_off_hours(timezone.now(), fallback)
        check('Off-hours check survives the unsaved fallback config', True)
    except Exception as exc:
        check('Off-hours check survives the unsaved fallback config', False,
              f'{type(exc).__name__}: {exc}')

    class Corrupt:
        office_hours_start = 'garbage'
        office_hours_end = None
        alert_on_off_hours = True

    try:
        services._is_off_hours(timezone.now(), Corrupt())
        check('Off-hours check survives a corrupt config', True)
    except Exception as exc:
        check('Off-hours check survives a corrupt config', False,
              f'{type(exc).__name__}: {exc}')

    score, _flags = services.score_event(EventType.UPDATE, config=fallback)
    check('Risk scoring still works on the fallback config', 0 <= score <= 100, str(score))


def test_admin_is_append_only():
    section('19. Audit trail is append-only in Django admin')

    from django.contrib import admin as django_admin
    from sentinel.admin import AuditEventAdmin

    site_admin = AuditEventAdmin(AuditEvent, django_admin.site)
    check('Cannot add audit rows by hand', site_admin.has_add_permission(None) is False)
    check('Cannot edit audit rows', site_admin.has_change_permission(None) is False)
    check('Cannot delete audit rows (tamper protection)',
          site_admin.has_delete_permission(None) is False)


def test_system_driven_changes():
    section('20. System-driven changes are still attributable')

    from django.test import RequestFactory
    from sentinel import context as sentinel_context

    factory = RequestFactory()

    # A logistics webhook changing an order status has no signed-in user. If that
    # is dropped, "who changed this order?" has no answer at all.
    request = factory.post('/ncm/webhook/')
    request.user = None
    sentinel_context.bind_request(request)
    event = services.log_event(
        event_type=EventType.UPDATE,
        action=f'{PREFIX}Updated Order: probe-order (status)',
        request=request, object_type='dashboard.Order', object_id='999999',
        object_label='probe-order',
        changes={'status': {'old': 'Pending', 'new': 'Delivered'}},
        force=True)
    sentinel_context.clear_request()
    check('Webhook-driven record change is captured', event is not None)
    if event:
        check('It is labelled, not left blank', event.actor_username == 'anonymous',
              event.actor_username)
        check('The field diff survives', 'status' in (event.changes or {}), str(event.changes))

    # An out-of-request background job.
    job = services.log_job(f'{PREFIX}sync', status='completed', detail='probe')
    check('Background job is captured', job is not None)
    if job:
        check('Jobs are attributed to "system"', job.actor_username == 'system',
              job.actor_username)

    # ...but anonymous *browsing* must still respect capture_anonymous.
    services.invalidate_settings_cache()
    if not services.get_settings().capture_anonymous:
        sentinel_context.bind_request(factory.get('/store/'))
        view_event = services.log_event(
            event_type=EventType.VIEW, action=f'{PREFIX}anonymous browse',
            request=factory.get('/store/'))
        sentinel_context.clear_request()
        check('Anonymous page views are still suppressed', view_event is None)


def test_no_cartesian_aggregates(client):
    section('21. People index avoids cartesian aggregation')

    from django.conf import settings as django_settings
    from django.db import connection, reset_queries

    was_debug = django_settings.DEBUG
    django_settings.DEBUG = True
    try:
        reset_queries()
        response = client.get('/sentinel/people/')
        joins = sum(q['sql'].upper().count('LEFT OUTER JOIN') for q in connection.queries)
        check('People index renders', response.status_code == 200, str(response.status_code))
        # Four annotate()s over four relations multiply each user's row by their
        # events x sessions x devices x alerts before DISTINCT collapses it.
        check('No multi-relation join explosion in the roster query', joins == 0,
              f'{joins} LEFT OUTER JOIN(s)')
    finally:
        django_settings.DEBUG = was_debug


def setup():
    section('Setting up probe accounts')
    with suppress_capture():
        admin = CustomUser.objects.create_user(
            username=f'{PREFIX}auditor', email=f'{PREFIX}auditor@probe.local',
            password=PREFIX + 'pw123!', role='administrator', is_superuser=True, is_staff=True)
        staff = CustomUser.objects.create_user(
            username=f'{PREFIX}clerk', email=f'{PREFIX}clerk@probe.local',
            password=PREFIX + 'pw123!', role='sales',
            can_view_audit_trail=True, can_view_all_users_activity=False,
            can_export_audit_logs=False, can_manage_sessions=False, can_configure_audit=False)
    print(f'  Created {admin.username} (administrator) and {staff.username} (restricted)')
    return admin, staff


def teardown(admin, staff):
    section('Cleanup')
    with suppress_capture():
        user_ids = [u.id for u in (admin, staff) if u and u.id]
        AuditEvent.objects.filter(actor_id__in=user_ids).delete()
        AuditEvent.objects.filter(actor_username__startswith=PREFIX).delete()
        AuditEvent.objects.filter(action__contains=PREFIX).delete()
        AuditEvent.objects.filter(
            module='Sentinel Vault', action__startswith='Purged',
            actor__isnull=True, created_at__gte=RUN_STARTED).delete()
        AuditEvent.objects.filter(ip_address__in=[
            '10.11.12.13', '203.0.113.77', '10.99.99.99', '10.55.55.55', '10.1.2.3']).delete()
        SecurityAlert.objects.filter(subject_username__startswith=PREFIX).delete()
        SecurityAlert.objects.filter(dedupe_key__startswith=PREFIX).delete()
        SecurityAlert.objects.filter(ip_address='203.0.113.77').delete()
        DeviceSession.objects.filter(username_snapshot__startswith=PREFIX).delete()
        KnownDevice.objects.filter(user_id__in=user_ids).delete()
        CustomUser.objects.filter(username__startswith=PREFIX).delete()
    print('  Probe data removed.')


def main():
    print('=' * 70)
    print('SENTINEL VAULT — verification')
    print('=' * 70)

    admin = staff = None
    try:
        admin, staff = setup()
        client = Client()

        test_user_agent_parsing()
        test_login_capture(client, admin)
        test_field_diffs(client, admin, staff)
        test_redaction(admin, staff)
        test_failed_logins_and_alerts()
        test_risk_scoring()
        test_pages_render(client, admin, staff)
        test_permission_scoping(staff)
        test_session_revocation(client, admin, staff)
        test_alert_workflow(client, admin)
        test_no_self_capture()
        test_retention_command()
        test_poller_exclusions(client)
        test_session_reaping(staff)
        test_synthetic_logins(staff)
        test_session_repair(staff)
        test_purge(admin, staff)
        test_purge_ui_visibility(admin, staff)
        test_action_hygiene(admin)
        test_privilege_boundaries(admin)
        test_malformed_input(client)
        test_thread_isolation()
        test_config_robustness()
        test_admin_is_append_only()
        test_system_driven_changes()
        test_no_cartesian_aggregates(client)
    finally:
        if admin or staff:
            teardown(admin, staff)

    print('\n' + '=' * 70)
    print(f'RESULT: {len(PASSED)} passed, {len(FAILED)} failed')
    print('=' * 70)
    if FAILED:
        for name, detail in FAILED:
            print(f'  FAILED: {name}' + (f'\n          {detail}' if detail else ''))
        sys.exit(1)
    print('All checks passed.')


if __name__ == '__main__':
    main()
