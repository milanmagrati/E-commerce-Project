from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0051_add_rtv_status_field'),
    ]

    operations = [
        migrations.CreateModel(
            name='RTVStatusOption',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('color', models.CharField(default='#667eea', max_length=7)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'RTV Status Option',
                'verbose_name_plural': 'RTV Status Options',
                'ordering': ['sort_order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='rtvorder',
            name='rtv_status_option',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='rtv_orders',
                to='dashboard.rtvstatusoption',
            ),
        ),
    ]
