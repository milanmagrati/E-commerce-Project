from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('hrm', '0050_copy_half_day_hours_to_shifts'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='attendancepolicy',
            name='half_day_hours',
        ),
    ]
