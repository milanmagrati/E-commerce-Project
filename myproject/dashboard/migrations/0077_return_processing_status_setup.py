from django.db import migrations


def create_return_processing_status(apps, schema_editor):
    Setup = apps.get_model('dashboard', 'Setup')
    Setup.objects.get_or_create(
        setup_type='status',
        name='Return Processing',
        defaults={
            'description': 'Order marked for return by NCM and still in transit back to the vendor (not yet physically received).',
            'is_active': True,
            'is_default': False,
        },
    )


def reverse_return_processing_status(apps, schema_editor):
    Setup = apps.get_model('dashboard', 'Setup')
    Setup.objects.filter(setup_type='status', name='Return Processing').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0076_apisettings_bulk_sync_included_statuses'),
    ]

    operations = [
        migrations.RunPython(create_return_processing_status, reverse_return_processing_status),
    ]
