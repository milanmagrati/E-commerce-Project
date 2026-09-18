"""
Flag every pre-existing rtv_marked_at value as untrusted.

Until now the RTV sync wrote NCM's *order creation* date into rtv_marked_at as
a "fallback display date", and every repair query filtered on
rtv_marked_at IS NULL — so a row that got the wrong value never got looked at
again. After the fact there is no way to tell a comment-sourced value apart
from a created_date-sourced one: both are just datetimes, and ncm_created_date
is empty for every legacy row, so no heuristic exists.

Marking them all 'order_created' (known-wrong / lowest trusted rank) is the
conservative choice: the dates stay visible in the UI (flagged approximate),
and every row re-enters the repair queue so `manage.py repair_rtv_marked_at`
can replace them with the real NCM "RTV marked" comment time. Any correct
value that gets re-fetched simply confirms itself.

No network calls here — heuristics and API work belong in the management
command, not in a migration.
"""
from django.db import migrations

BATCH_SIZE = 5000


def flag_legacy_dates_untrusted(apps, schema_editor):
    RTVOrder = apps.get_model('dashboard', 'RTVOrder')

    # Keyset pagination rather than one bare UPDATE: on shared cPanel MySQL
    # with STRICT_TRANS_TABLES a single multi-100k-row update risks a lock
    # timeout mid-deploy.
    for value, rows in (
        ('order_created', RTVOrder.objects.filter(rtv_marked_at__isnull=False)),
        ('', RTVOrder.objects.filter(rtv_marked_at__isnull=True)),
    ):
        last_id = 0
        while True:
            ids = list(
                rows.filter(id__gt=last_id)
                .order_by('id')
                .values_list('id', flat=True)[:BATCH_SIZE]
            )
            if not ids:
                break
            RTVOrder.objects.filter(id__in=ids).update(rtv_marked_at_source=value)
            last_id = ids[-1]


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0078_orderactivitylog_event_at_rtvorder_ncm_created_date_and_more'),
    ]

    operations = [
        migrations.RunPython(flag_legacy_dates_untrusted, migrations.RunPython.noop),
    ]
