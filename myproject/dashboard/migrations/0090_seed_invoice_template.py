"""Seed the invoice singleton and its stock lines.

The customizer treats built-in lines as ordinary rows, so the default layout
has to exist in the database before the first invoice prints.
"""

from django.db import migrations

from dashboard.invoice_config import DEFAULT_ELEMENTS


def seed(apps, schema_editor):
    InvoiceTemplate = apps.get_model('dashboard', 'InvoiceTemplate')
    InvoiceElement = apps.get_model('dashboard', 'InvoiceElement')

    template, _ = InvoiceTemplate.objects.get_or_create(pk=1)
    if InvoiceElement.objects.filter(template=template).exists():
        return
    InvoiceElement.objects.bulk_create([
        InvoiceElement(template=template, is_builtin=True, **spec) for spec in DEFAULT_ELEMENTS
    ])


def unseed(apps, schema_editor):
    InvoiceElement = apps.get_model('dashboard', 'InvoiceElement')
    InvoiceElement.objects.filter(is_builtin=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0089_invoicetemplate_invoiceelement'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
