# Pure state migration: DB changes already applied manually

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0051_add_rtv_status_field'),
    ]

    operations = [
        # All DB changes already applied via direct SQL.
        # This migration only updates Django's state tracking.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='RTVStatus',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('name', models.CharField(max_length=100, unique=True)),
                        ('color', models.CharField(default='#667eea', help_text='Hex color code (e.g. #667eea)', max_length=7)),
                        ('description', models.TextField(blank=True, default='')),
                        ('is_active', models.BooleanField(default=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                    ],
                    options={
                        'verbose_name': 'RTV Status',
                        'verbose_name_plural': 'RTV Statuses',
                        'ordering': ['name'],
                    },
                ),
                migrations.RemoveField(model_name='rtvorder', name='rtv_status'),
                migrations.AddField(
                    model_name='rtvorder',
                    name='rtv_status',
                    field=models.ForeignKey(
                        blank=True,
                        help_text='Locally assigned status for this RTV',
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='rtv_orders',
                        to='dashboard.rtvstatus',
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
