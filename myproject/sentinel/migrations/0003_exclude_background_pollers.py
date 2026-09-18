"""
Backfill the background-poller exclusions onto any VaultSettings row that already
exists. Changing the field default in 0002 only affects rows created afterwards,
so a vault installed before this fix would keep writing a row a minute per open
browser tab.

Appends only the lines that are missing, so an admin's own customisations to
excluded_paths survive.
"""

from django.db import migrations

POLLER_PATHS = [
    '/chat/api/unread-count/',
    '/api/active-notice/',
    '/hrm/attendance/incomplete/?count_only=1',
]


def add_poller_exclusions(apps, schema_editor):
    VaultSettings = apps.get_model('sentinel', 'VaultSettings')
    for config in VaultSettings.objects.all():
        existing = [line.strip() for line in (config.excluded_paths or '').splitlines()]
        missing = [path for path in POLLER_PATHS if path not in existing]
        if not missing:
            continue
        lines = [line for line in existing if line] + missing
        config.excluded_paths = '\n'.join(lines)
        config.save(update_fields=['excluded_paths'])


def remove_poller_exclusions(apps, schema_editor):
    VaultSettings = apps.get_model('sentinel', 'VaultSettings')
    for config in VaultSettings.objects.all():
        lines = [line for line in (config.excluded_paths or '').splitlines()
                 if line.strip() and line.strip() not in POLLER_PATHS]
        config.excluded_paths = '\n'.join(lines)
        config.save(update_fields=['excluded_paths'])


class Migration(migrations.Migration):

    dependencies = [
        ('sentinel', '0002_alter_vaultsettings_excluded_paths'),
    ]

    operations = [
        migrations.RunPython(add_poller_exclusions, remove_poller_exclusions),
    ]
