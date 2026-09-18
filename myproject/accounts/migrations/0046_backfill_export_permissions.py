from django.db import migrations


def backfill_export_permissions(apps, schema_editor):
    """Seed the new dedicated export permissions from whatever gated each export
    before this split, so existing roles/users keep the access they already had.

    Before: Follow-ups export was gated on `can_access_follow_ups`, Logistics
    Orders export on `can_export_orders`, and Bulk Logs export on
    `can_view_ncm_bulk_logs`. Carry each forward one-to-one.
    """
    CustomUser = apps.get_model('accounts', 'CustomUser')
    Role = apps.get_model('accounts', 'Role')

    CustomUser.objects.filter(can_access_follow_ups=True).update(can_export_follow_ups=True)
    CustomUser.objects.filter(can_export_orders=True).update(can_export_logistics_orders=True)
    CustomUser.objects.filter(can_view_ncm_bulk_logs=True).update(can_export_logistics_bulk_logs=True)

    # Keep role defaults in step so a later "sync role" doesn't strip the access.
    pairs = [
        ('can_access_follow_ups', 'can_export_follow_ups'),
        ('can_export_orders', 'can_export_logistics_orders'),
        ('can_view_ncm_bulk_logs', 'can_export_logistics_bulk_logs'),
    ]
    for role in Role.objects.all():
        defaults = role.default_permissions or {}
        perms = list(defaults.get('permissions', []))
        changed = False
        for source, target in pairs:
            if source in perms and target not in perms:
                perms.append(target)
                changed = True
        if changed:
            defaults['permissions'] = perms
            role.default_permissions = defaults
            role.save(update_fields=['default_permissions'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0045_customuser_can_export_follow_ups_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_export_permissions, migrations.RunPython.noop),
    ]
