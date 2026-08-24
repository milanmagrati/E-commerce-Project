from django.db import migrations, models


def backfill_sort_order(apps, schema_editor):
    Setup = apps.get_model('dashboard', 'Setup')
    for setup_type, in Setup.objects.values_list('setup_type').distinct():
        for index, setup_id in enumerate(
            Setup.objects.filter(setup_type=setup_type).order_by('name').values_list('id', flat=True)
        ):
            Setup.objects.filter(id=setup_id).update(sort_order=index)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0086_order_manual_status_override_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='setup',
            name='sort_order',
            field=models.IntegerField(default=0, help_text='Manual drag-and-drop display order within a setup type'),
        ),
        migrations.AlterModelOptions(
            name='setup',
            options={'ordering': ['setup_type', 'sort_order', 'name'], 'verbose_name': 'Setup', 'verbose_name_plural': 'Setups'},
        ),
        migrations.RunPython(backfill_sort_order, noop),
    ]
