from django.db import migrations


def backfill_failure_codes(apps, schema_editor):
    """Give historic DispatchItem rows a failure_code + reason.

    Before this release only `failure_reason` was written (and only for two of
    the failure paths), so the detail page had nothing to categorise or explain
    unmatched scans with.
    """
    DispatchItem = apps.get_model('dashboard', 'DispatchItem')

    DispatchItem.objects.filter(
        dispatch_status='failed',
        failure_code='',
        failure_reason__icontains='already dispatched',
    ).update(failure_code='already_dispatched')

    DispatchItem.objects.filter(
        dispatch_status='failed',
        failure_code='',
        failure_reason__icontains='changed manually',
    ).update(failure_code='status_reverted')

    DispatchItem.objects.filter(
        dispatch_status='failed',
        failure_code='',
    ).update(failure_code='error')

    DispatchItem.objects.filter(
        dispatch_status='not_found',
    ).update(failure_code='not_found')

    DispatchItem.objects.filter(
        dispatch_status='not_found',
        failure_reason='',
    ).update(failure_reason='Order ID not found in the system')

    # `failed_at` is unknown for historic rows; scanned_at is the closest
    # truthful stand-in and keeps the timeline from showing blanks.
    for item in DispatchItem.objects.filter(
        failed_at__isnull=True,
    ).exclude(dispatch_status='success').iterator(chunk_size=500):
        item.failed_at = item.scanned_at
        item.save(update_fields=['failed_at'])


def unbackfill(apps, schema_editor):
    DispatchItem = apps.get_model('dashboard', 'DispatchItem')
    DispatchItem.objects.exclude(failure_code='').update(failure_code='')


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0084_dispatchitem_failed_at_dispatchitem_failure_code_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_failure_codes, unbackfill),
    ]
