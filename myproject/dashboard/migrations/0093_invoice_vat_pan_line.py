"""Retire the Bill To landmark line in favour of the buyer's VAT / PAN number.

The order form no longer collects a landmark, so the seeded ``customer.landmark``
line would print nothing on every new invoice for ever. Installs seeded before
this migration only ever get the stock lines once (``seed_defaults`` returns
early when any row exists), so the swap has to happen here rather than in
``DEFAULT_ELEMENTS`` alone.

Only the *built-in* landmark row is touched, and only when the customizer does
not already carry a VAT/PAN line — a shop that renamed, moved or deliberately
kept that line keeps whatever it chose.
"""

from django.db import migrations


def swap_line(apps, schema_editor):
    InvoiceElement = apps.get_model('dashboard', 'InvoiceElement')

    for element in InvoiceElement.objects.filter(
        is_builtin=True, source='field', token='customer.landmark', section='bill_to',
    ):
        already = InvoiceElement.objects.filter(
            template_id=element.template_id, token='customer.vat_pan',
        ).exists()
        if already:
            continue
        element.token = 'customer.vat_pan'
        element.label = 'VAT / PAN :-'
        element.save(update_fields=['token', 'label'])


def restore_line(apps, schema_editor):
    InvoiceElement = apps.get_model('dashboard', 'InvoiceElement')
    InvoiceElement.objects.filter(
        is_builtin=True, source='field', token='customer.vat_pan', section='bill_to',
    ).update(token='customer.landmark', label='Landmark :-')


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0092_order_vat_pan'),
    ]

    operations = [
        migrations.RunPython(swap_line, restore_line),
    ]
