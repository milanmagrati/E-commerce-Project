from django.db import migrations
from django.db.models import Q


def backfill_admin_low_stock(apps, schema_editor):
    CustomUser = apps.get_model('accounts', 'CustomUser')
    CustomUser.objects.filter(
        Q(role='administrator') | Q(is_superuser=True),
        is_deleted=False,
        can_view_low_stock_alerts=False,
    ).update(can_view_low_stock_alerts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0029_customuser_can_view_low_stock_alerts'),
    ]

    operations = [
        migrations.RunPython(backfill_admin_low_stock, migrations.RunPython.noop),
    ]
