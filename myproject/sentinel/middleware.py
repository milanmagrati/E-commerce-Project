"""
The capture middleware.

Runs *after* AuthenticationMiddleware so request.user is populated, binds the
request onto a thread-local for the model signals to find, and flushes whatever
those signals captured once the response is ready.

Flushing after the view (rather than inside the signal) is deliberate: if the
view raised and the transaction rolled back, the model change never happened and
must not appear in the trail.
"""

import logging
import re
import time

from django.utils.deprecation import MiddlewareMixin

from . import services, utils
from .context import bind_request, clear_request, drain_captured, get_session_record, set_session_record
from .models import EventType, Severity

logger = logging.getLogger('sentinel')

# Never recorded regardless of settings: Sentinel's own polling endpoints (which
# would generate an event per poll, forever) and asset requests.
#
# The /ncm/api/ entries are here for the same reason: every authenticated page
# pings the sync heartbeat, and the logistics list polls for status changes, so
# left in they would write an audit row per poll per open tab and bury real
# activity. This lives in code rather than in VaultSettings.excluded_paths
# because that is a DB column - editing its default would do nothing to an
# installation whose row already exists.
HARD_EXCLUDED = (
    '/sentinel/api/', '/static/', '/media/', '/favicon.ico', '/iclock/',
    '/ncm/api/heartbeat/', '/ncm/api/orders/batch-status/',
)

# Read-only pollers whose order id sits mid-path, so a prefix can't describe
# them. Matched for GET/HEAD only: the sibling POSTs under the same path
# (.../sync/, .../comments/add/) are real staff actions and must stay audited.
HARD_EXCLUDED_GET_PATTERNS = (
    re.compile(r'^/ncm/api/order/\d+/(status|activity|comments)/$'),
)

SAFE_METHODS = {'GET', 'HEAD'}

WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}

# Asking Chrome to send the device model and OS version alongside every request.
# Without this the only client hints we get are the low-entropy ones, which name
# the platform but never the handset.
ACCEPT_CH = 'Sec-CH-UA-Platform, Sec-CH-UA-Platform-Version, Sec-CH-UA-Mobile, Sec-CH-UA-Model'


class SentinelAuditMiddleware(MiddlewareMixin):

    def process_request(self, request):
        request._sentinel_start = time.monotonic()
        bind_request(request)
        return None

    def process_response(self, request, response):
        try:
            self._advertise_client_hints(response)
        except Exception:
            logger.debug('Sentinel could not advertise client hints', exc_info=True)
        try:
            self._record(request, response)
        except Exception:
            logger.exception('Sentinel middleware failed while recording a request')
        finally:
            # Must always run: a leaked thread-local would mis-attribute the next
            # request handled by this worker thread.
            clear_request()
        return response

    def process_exception(self, request, exception):
        """Log unhandled view errors — the audit trail should show the crash too."""
        try:
            if self._skip(request):
                return None
            services.log_event(
                event_type=EventType.ERROR,
                action=f'{type(exception).__name__} in {request.path}',
                request=request,
                severity=Severity.CRITICAL,
                module='System',
                context={'exception': str(exception)[:500], 'method': request.method},
            )
        except Exception:
            logger.debug('Sentinel exception capture failed', exc_info=True)
        return None

    # ── internals ────────────────────────────────────────────────────────────

    def _advertise_client_hints(self, response):
        """Opt in to the extra Sec-CH-UA-* headers, on HTML replies only.

        Browsers that don't implement client hints ignore the header entirely,
        so this is inert everywhere it isn't useful.
        """
        if not hasattr(response, 'headers'):
            return
        content_type = (response.get('Content-Type', '') or '').lower()
        if 'text/html' not in content_type:
            return
        response['Accept-CH'] = ACCEPT_CH

    def _skip(self, request):
        path = request.path or ''
        if path.startswith(HARD_EXCLUDED):
            return True

        if request.method in SAFE_METHODS:
            if any(p.match(path) for p in HARD_EXCLUDED_GET_PATTERNS):
                return True

        full_path = request.get_full_path() or path
        for prefix in services.get_settings().excluded_prefixes:
            # A prefix containing '?' matches against the query string too, so
            # '/hrm/attendance/incomplete/?count_only=1' can silence the badge poll
            # without also silencing the real page at the same path.
            if '?' in prefix:
                if full_path.startswith(prefix):
                    return True
            elif path.startswith(prefix):
                return True
        return False

    def _record(self, request, response):
        captured = drain_captured()

        if self._skip(request):
            return

        user = getattr(request, 'user', None)
        authenticated = user is not None and getattr(user, 'is_authenticated', False)
        config = services.get_settings()

        if not authenticated and not config.capture_anonymous and not captured:
            return

        session = get_session_record()
        if session is None and authenticated:
            session = services.resolve_session(request)
            set_session_record(session)

        is_write = request.method in WRITE_METHODS
        services.touch_session(session, is_write=is_write)

        duration_ms = None
        started = getattr(request, '_sentinel_start', None)
        if started is not None:
            duration_ms = int((time.monotonic() - started) * 1000)

        status = getattr(response, 'status_code', None)

        # 1. Flush model-level changes the signals captured during this request.
        #    force=True: a record was actually modified, so it is logged whether or
        #    not a person was signed in. Otherwise webhook-driven order updates and
        #    storefront orders would leave no trace at all.
        for event_type, payload in captured:
            services.log_event(
                event_type=event_type,
                request=request,
                session=session,
                status_code=status,
                duration_ms=duration_ms,
                force=True,
                **payload,
            )

        # 2. Record the request itself when it isn't already described by a model
        #    change or by the view's own explicit event.
        if captured:
            return

        if status == 403 or self._was_denied(request):
            services.log_event(
                event_type=EventType.DENIED,
                action=f'Access denied: {self._describe(request)}',
                request=request, session=session,
                status_code=status, duration_ms=duration_ms,
                severity=Severity.WARNING,
            )
            return

        # 3. The view already wrote its own event (a revoke, a purge, a settings
        #    change, a sign-in). The generic row below would say the same thing
        #    with less detail, so it is pure duplication — skip it.
        if getattr(request, '_sentinel_explicit', False):
            return

        if is_write:
            # A write that produced no tracked model change is still worth a row —
            # it may have hit an untracked model, an external API, or failed validation.
            services.log_event(
                event_type=self._classify_write(request),
                action=self._describe(request),
                request=request, session=session,
                status_code=status, duration_ms=duration_ms,
                context={'form': utils.scrub_post_data(request.POST)} if request.POST else {},
            )
            return

        if config.capture_page_views and authenticated and request.method == 'GET':
            if status and 200 <= status < 400:
                event_type = EventType.EXPORT if self._is_export(request, response) else EventType.VIEW
                services.log_event(
                    event_type=event_type,
                    action=self._describe(request),
                    request=request, session=session,
                    status_code=status, duration_ms=duration_ms,
                )

    def _was_denied(self, request):
        """Catch this project's RBAC denials, which redirect with a tagged message."""
        try:
            storage = getattr(request, '_messages', None)
            if storage is None:
                return False
            return any('permission_denied' in (m.extra_tags or '')
                       for m in getattr(storage, '_queued_messages', []))
        except Exception:
            return False

    def _is_export(self, request, response):
        disposition = response.get('Content-Disposition', '') if hasattr(response, 'get') else ''
        if 'attachment' in disposition.lower():
            return True
        content_type = (response.get('Content-Type', '') if hasattr(response, 'get') else '').lower()
        return any(t in content_type for t in ('spreadsheet', 'csv', 'excel', 'pdf'))

    def _classify_write(self, request):
        path = (request.path or '').lower()
        if request.method == 'DELETE' or '/delete' in path or '/remove' in path:
            return EventType.DELETE
        if 'import' in path or 'upload' in path or 'sync' in path:
            return EventType.IMPORT
        if 'export' in path or 'download' in path:
            return EventType.EXPORT
        if 'password' in path:
            return EventType.PASSWORD
        if '/create' in path or '/add' in path or '/new' in path:
            return EventType.CREATE
        return EventType.UPDATE

    def _describe(self, request):
        """Readable label from the resolved view name, falling back to the path."""
        match = getattr(request, 'resolver_match', None)
        if match is not None and match.url_name:
            label = match.url_name.replace('_', ' ').replace('-', ' ').strip().capitalize()
            if match.kwargs:
                identifier = (match.kwargs.get('pk') or match.kwargs.get('id')
                              or next(iter(match.kwargs.values()), None))
                if identifier is not None:
                    label = f'{label} #{identifier}'
            verb = 'Submitted' if request.method in WRITE_METHODS else 'Opened'
            return f'{verb} {label}'
        return f'{request.method} {request.path}'
