from django.db import migrations


def copy_half_day_hours(apps, schema_editor):
    """Backfill Shift.half_day_hours from the old policy-level value, since the
    threshold used to live on AttendancePolicy and is now configured per-shift."""
    AttendancePolicy = apps.get_model('hrm', 'AttendancePolicy')
    Shift = apps.get_model('hrm', 'Shift')

    policy = AttendancePolicy.objects.filter(is_active=True).order_by('id').first() \
        or AttendancePolicy.objects.order_by('id').first()
    if policy is not None:
        Shift.objects.update(half_day_hours=policy.half_day_hours)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('hrm', '0049_move_half_day_hours_to_shift'),
    ]

    operations = [
        migrations.RunPython(copy_half_day_hours, noop_reverse),
    ]
