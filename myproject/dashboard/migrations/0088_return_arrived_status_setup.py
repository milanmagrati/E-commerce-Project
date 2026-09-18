"""Add the 'Return Arrived' order status and backfill the orders already in it.

NCM reports "Arrived at RETURN (BRANCH)" once a returned parcel has finished
the journey back and is sitting at its return counter. That used to fall into
the keyword fallback and resolve to 'return_processing' ("still on its way
back"), which was both wrong on the order detail page and — because the
Possible Redirection page reads the raw "Arrived ..." wording — left the parcel
listed as redirectable long after NCM would accept a redirect for it.

The backfill only touches orders whose stored NCM status still says exactly
that, whose status was not set by hand, and whose two status strings BOTH read
'return_processing'. That last condition is the conservative one: the two are
duplicates that every sync path writes together, so a row where they disagree
has been left in an odd state by something else and must not have one of them
overwritten here — 'return_processing' is not in bulk sync's terminal set, so
the ordinary sync (or `manage.py repair_return_stage`) re-derives it anyway.
"""
from django.db import migrations


def create_return_arrived_status(apps, schema_editor):
    Setup = apps.get_model('dashboard', 'Setup')
    Order = apps.get_model('dashboard', 'Order')

    setup, _ = Setup.objects.get_or_create(
        setup_type='status',
        name='Return Arrived',
        defaults={
            'description': (
                'Returned parcel has arrived at the courier\'s return branch/counter — '
                'no longer redirectable, not yet physically received by us.'
            ),
            'is_active': True,
            'is_default': False,
        },
    )

    Order.objects.filter(
        is_deleted=False,
        manual_status_override_at__isnull=True,
        ncm_status__istartswith='arrived',
        ncm_status__icontains='return',
        status__iexact='return_processing',
        order_status__iexact='return_processing',
    ).update(
        status='return_arrived',
        order_status='return_arrived',
        status_setup=setup,
    )


def reverse_return_arrived_status(apps, schema_editor):
    Setup = apps.get_model('dashboard', 'Setup')
    Order = apps.get_model('dashboard', 'Order')

    processing = Setup.objects.filter(setup_type='status', name='Return Processing').first()
    Order.objects.filter(
        status__iexact='return_arrived',
        order_status__iexact='return_arrived',
    ).update(
        status='return_processing',
        order_status='return_processing',
        status_setup=processing,
    )
    # status_setup is SET_NULL, so any row still pointing here (one whose status
    # strings were changed by hand in the meantime) loses the FK rather than
    # blocking the delete.
    Setup.objects.filter(setup_type='status', name='Return Arrived').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0087_setup_sort_order'),
    ]

    operations = [
        migrations.RunPython(create_return_arrived_status, reverse_return_arrived_status),
    ]
