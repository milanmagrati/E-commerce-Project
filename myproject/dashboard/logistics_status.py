"""Presentation rules for logistics (NCM / Pick & Drop) status badges.

The Logistics Orders list renders these badges server-side, and its status
column is also patched live over AJAX as the background sync writes new
statuses. Both paths must agree, so the colour rules live here once: the
template reaches them through the `logistics_badge_class` filter, and
`ncm.realtime_api.api_get_orders_status_batch` returns the computed class
alongside each status so the browser only has to assign it.

Keeping this in Python rather than mirroring the branches in JavaScript is
deliberate - a duplicated colour table drifts the first time somebody adds a
status to one copy and not the other.
"""

#: Terminal success.
_DELIVERED = ('Delivered', 'Confirmed')

#: Moving through the network.
_IN_TRANSIT = (
    'In Transit', 'Dispatched', 'Arrived', 'Sent for Delivery', 'Out for Delivery',
)

#: Coming back to us.
_RETURNING = (
    'Returned', 'Return Initiated', 'Return Approved', 'Order Marked Return',
    'Sent to Vendor', 'Returned to Warehouse',
)

#: Created but not yet moving.
_CREATED = (
    'Order Created', 'Pickup Order Created', 'Drop off Order Created',
    'Pickup Complete', 'Drop off Order Collected',
)

_CANCELLED = ('Cancelled',)

def _is_returning(status):
    """True for the branch-qualified return statuses, which match no fixed key.

    NCM appends the branch to its movement statuses and marks the return leg by
    prefixing that branch with RETURN - "Dispatched to RETURN ( TINKUNE)",
    "Arrived at RETURN NAYA BUSPARK", "Returned to Warehouse (TINKUNE)". None of
    those equal an entry in _RETURNING, so they used to render as an anonymous
    grey "unknown" badge: exactly the wrong signal for the statuses that mean a
    parcel is coming back.

    Two rules, because NCM qualifies the wording in two different places - a
    suffix on a known return status, and the RETURN marker on the branch name.
    The second matches on the whole word, so a branch merely spelled with those
    letters cannot trip it.
    """
    lowered = status.lower()
    if any(lowered.startswith(known.lower()) for known in _RETURNING):
        return True
    return 'return' in {word.strip('()[],.').lower() for word in status.split()}


DEFAULT_NCM_STATUS = 'Pickup Order Created'
DEFAULT_PND_STATUS = 'Order Created'


def logistics_badge_class(status, logistics=None):
    """Bootstrap badge classes for a provider status string.

    `logistics` is accepted for callers that have it, but the rules are shared
    between providers - both use NCM-style status wording - so it only decides
    which default applies to a blank status.
    """
    status = (status or '').strip()
    if not status:
        status = DEFAULT_PND_STATUS if logistics == 'pick_and_drop' else DEFAULT_NCM_STATUS

    if status in _DELIVERED:
        return 'bg-success'
    if status in _IN_TRANSIT:
        return 'bg-primary'
    if status in _CANCELLED:
        return 'bg-danger'
    if _is_returning(status):
        return 'bg-danger'
    if status in _CREATED:
        return 'bg-warning text-dark'
    return 'bg-secondary'


def logistics_status_text(status, logistics=None):
    """The status string as displayed, filling in the provider's default when blank."""
    status = (status or '').strip()
    if status:
        return status
    return DEFAULT_PND_STATUS if logistics == 'pick_and_drop' else DEFAULT_NCM_STATUS
