"""Give APISettings the state the background sync scheduler needs.

Two things happen here:

1. `webhook_check_interval` is renamed to `page_refresh_interval`. The old name
   described a feature that was never built - nothing in the codebase ever read
   that column, and its intended consumer (`api_check_pending_ncm_updates`) had
   no callers. The column now carries the cadence at which an open page re-reads
   order status from the local DB, which is what the Settings UI has always
   implied it did. This is written as an explicit RenameField rather than left
   to `makemigrations`, which would otherwise be free to guess drop+add and
   silently throw away whatever interval the admin had configured.

2. Four new columns hold the scheduler's lock and clock. See
   `ncm/scheduler.py` for how they are used.

`order_sync_interval`'s default also drops from 14400 (4 hours) to 900 (15 min).
Defaults only apply to newly created rows, so this does not touch the existing
singleton - it just stops a fresh install from shipping with a 4-hour sync.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0082_seed_landing_page_defaults'),
    ]

    operations = [
        migrations.RenameField(
            model_name='apisettings',
            old_name='webhook_check_interval',
            new_name='page_refresh_interval',
        ),
        migrations.AlterField(
            model_name='apisettings',
            name='page_refresh_interval',
            field=models.PositiveIntegerField(
                default=30,
                help_text='How often (in seconds) an open page re-reads order status from the '
                          'local database to repaint badges. Costs no NCM requests. Default: 30.',
            ),
        ),
        migrations.AlterField(
            model_name='apisettings',
            name='order_sync_interval',
            field=models.PositiveIntegerField(
                default=900,
                help_text='How often (in seconds) the SERVER runs the background NCM bulk status '
                          'sync. This is the real API cadence - it costs NCM requests. Default: 900 (15 min).',
            ),
        ),
        migrations.AddField(
            model_name='apisettings',
            name='last_bulk_sync_started_at',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text="When the most recent background bulk sync began. The due-check "
                          "measures from here (start-to-start), so a run that outlasts the "
                          "interval can't immediately retrigger itself.",
            ),
        ),
        migrations.AddField(
            model_name='apisettings',
            name='last_bulk_sync_finished_at',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='When the most recent background bulk sync finished. Display only.',
            ),
        ),
        migrations.AddField(
            model_name='apisettings',
            name='bulk_sync_running_since',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text="Non-null while a bulk sync holds the lock. Bumped periodically by the "
                          "running sync so a long run isn't mistaken for a crashed one; a value "
                          "older than the stale window is treated as abandoned and taken over.",
            ),
        ),
        migrations.AddField(
            model_name='apisettings',
            name='last_bulk_sync_summary',
            field=models.JSONField(
                default=dict, blank=True,
                help_text='Result of the most recent background bulk sync '
                          '(total_orders / updated_count / errors).',
            ),
        ),
    ]
