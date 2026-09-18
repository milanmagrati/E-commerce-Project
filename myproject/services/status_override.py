"""Manual status overrides — keeping a hand-set order status from being
overwritten by the next logistics sync.

An order's status is written from two directions:

* **Staff, by hand** — the order edit page, the status form on the order
  detail page, and the "Mark as ..." bulk action on the orders list.
* **NCM, automatically** — the webhook, the per-order sync that fires on
  every order-detail page load, the background bulk sync (``ncm/bulk_sync``),
  and the admin "sync all statuses" run.

Without a tiebreaker the automatic side always wins, because it re-derives
the status from NCM's answer every time it runs. Setting an order that NCM
still calls "Pickup Order Created" back to "Confirmed" therefore lasted only
until the order page reloaded — the page-load sync re-applied NCM's answer
and the status snapped back.

The tiebreaker here is deliberately narrow. A manual change records *which*
raw NCM status was in force when it was made; the sync paths then leave the
order's status alone only while NCM keeps reporting that exact same status.
As soon as the parcel really moves — NCM reports a different raw status, or
an event stamped later than the manual change — NCM wins again and the hold
is dropped. So an override can delay NCM, but can never strand an order on a
status the parcel has left behind.

Note that a hold only protects the *system* status fields. ``ncm_status``
(the raw provider string) is still recorded on every sync, so the order
timeline and the NCM panel keep telling the truth about where the parcel is.
"""

from django.utils import timezone

#: The fields written by `stamp_manual_override` / `clear_manual_status_override`,
#: for callers that save with `update_fields`.
OVERRIDE_FIELDS = ('manual_status_override_at', 'manual_status_override_ncm_status')


def normalize_status(value):
    """Normalize a status to the stored form ("Pickup Created" -> "pickup_created")."""
    return str(value or '').strip().lower().replace(' ', '_')


def stamp_manual_override(order):
    """Record that the order's status was just set by hand.

    Captures the raw NCM status in force right now, which is what
    `manual_override_holds` later compares against to decide whether the
    parcel has moved on. Returns the changed field names.
    """
    order.manual_status_override_at = timezone.now()
    order.manual_status_override_ncm_status = order.ncm_status or ''
    return list(OVERRIDE_FIELDS)


def clear_manual_status_override(order):
    """Drop any manual hold. Returns the changed field names (empty if none)."""
    if not order.manual_status_override_at and not order.manual_status_override_ncm_status:
        return []
    order.manual_status_override_at = None
    order.manual_status_override_ncm_status = ''
    return list(OVERRIDE_FIELDS)


def apply_manual_status(order, status_name, status_setup=None):
    """Write a hand-set status onto `order` and stamp the manual hold.

    `status_name` may be a Setup name ("Pickup Created") or an already
    normalized value ("pickup_created"); it is stored normalized either way.

    Both `status` and `order_status` are written. They are duplicates of each
    other (see the "Rename from order_status" note on `Order.status`), and the
    manual paths used to write only `order_status`. That half-write was the
    other half of this bug: the sync paths compare against `status`, so an
    order left at `status='pickup_created'` still looked untouched to them
    even after staff had marked it confirmed.

    A save that leaves the status where it already was writes nothing and
    stamps no hold — otherwise editing an order's address would start
    shielding its status from NCM for no reason. "Already was" is judged on
    the normalized value, because the NCM sync stores some statuses in display
    form ("Pickup Created") where the manual paths store them normalized
    ("pickup_created"); those are the same status, and rewriting one into the
    other would read as a change to the next sync and log a phantom one.

    Returns the changed field names, for `save(update_fields=...)`; callers
    that save the whole object can ignore the return value.
    """
    normalized = normalize_status(status_name)
    if not normalized:
        return []

    changed = (normalize_status(order.order_status) != normalized
               or normalize_status(order.status) != normalized
               or (status_setup is not None
                   and order.status_setup_id != status_setup.id))
    if not changed:
        return []

    fields = ['order_status', 'status']
    order.order_status = normalized
    order.status = normalized
    if status_setup is not None:
        order.status_setup = status_setup
        fields.append('status_setup')

    fields.extend(stamp_manual_override(order))
    return list(dict.fromkeys(fields))


def manual_override_holds(order, incoming_ncm_status=None, event_at=None):
    """True when a manual status change should survive this sync.

    Args:
        order: the order being synced.
        incoming_ncm_status: the raw status NCM is reporting now. A value
            different from the one held releases the hold — the parcel moved.
        event_at: NCM's own timestamp for that status, when the caller knows
            it. An event that happened *after* the manual change also releases
            the hold, even if the raw status string is unchanged.

    An order with no recorded manual change always returns False, so every
    sync path behaves exactly as before for orders staff never touched.
    """
    held_at = getattr(order, 'manual_status_override_at', None)
    if not held_at:
        return False

    incoming = normalize_status(incoming_ncm_status)
    held_ncm = normalize_status(getattr(order, 'manual_status_override_ncm_status', ''))
    if incoming and incoming != held_ncm:
        return False

    if event_at and event_at > held_at:
        return False

    return True
