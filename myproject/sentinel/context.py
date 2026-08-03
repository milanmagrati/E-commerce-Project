"""
Per-request context, shared between the middleware and the model signals.

Model signals fire deep inside save()/delete() with no access to the request, so
the middleware parks the active request on a thread-local and the signals read it
back. Always cleared in the middleware's finally-block: with CONN_MAX_AGE reuse
and a threaded server, a leaked reference would attribute one user's writes to
whoever previously used that worker thread.
"""

import threading

_state = threading.local()


def bind_request(request):
    _state.request = request
    _state.session_record = None
    _state.captured = []
    _state.snapshots = {}
    _state.suppressed = False


def clear_request():
    for attr in ('request', 'session_record', 'captured', 'snapshots', 'suppressed'):
        if hasattr(_state, attr):
            delattr(_state, attr)


def get_request():
    return getattr(_state, 'request', None)


def set_session_record(record):
    _state.session_record = record


def get_session_record():
    return getattr(_state, 'session_record', None)


def add_captured(event_type, payload):
    """Signals push model-level changes here; the middleware drains them at response time."""
    if getattr(_state, 'suppressed', False):
        return
    captured = getattr(_state, 'captured', None)
    if captured is None:
        captured = []
        _state.captured = captured
    # Hard cap: a bulk import that touches 50k rows must not build a 50k-item list
    # in memory, and nobody reads past the first few dozen anyway.
    if len(captured) < 200:
        captured.append((event_type, payload))


def drain_captured():
    captured = getattr(_state, 'captured', []) or []
    _state.captured = []
    return captured


# ── pre_save snapshots ───────────────────────────────────────────────────────
# These MUST be per-thread. A module-level dict keyed by (model, pk) is shared by
# every worker thread in the process, so two staff saving the same order at once
# would overwrite each other's "before" snapshot — one request records another
# request's old values (a false audit record), and the other records no diff at
# all. Keeping them on the thread-local makes each request's snapshots private.
_SNAPSHOT_LIMIT = 500


def put_snapshot(key, data):
    snapshots = getattr(_state, 'snapshots', None)
    if snapshots is None:
        snapshots = {}
        _state.snapshots = snapshots
    # Bounded: a bulk import touching thousands of rows must not grow without limit.
    if len(snapshots) >= _SNAPSHOT_LIMIT:
        snapshots.clear()
    snapshots[key] = data


def pop_snapshot(key):
    snapshots = getattr(_state, 'snapshots', None)
    if not snapshots:
        return None
    return snapshots.pop(key, None)


class suppress_capture:
    """Context manager / decorator to stop Sentinel recording its own writes.

    Used around the vault's own inserts and around the prune command, which would
    otherwise generate an audit event for every audit event it deletes.
    """

    def __enter__(self):
        self._previous = getattr(_state, 'suppressed', False)
        _state.suppressed = True
        return self

    def __exit__(self, *exc_info):
        _state.suppressed = self._previous
        return False

    def __call__(self, func):
        from functools import wraps

        @wraps(func)
        def wrapper(*args, **kwargs):
            with self.__class__():
                return func(*args, **kwargs)
        return wrapper


def is_suppressed():
    return getattr(_state, 'suppressed', False)
