"""Setup → Store Button Labels.

One screen, one row: the wording printed on every storefront button. The shop
sells in Nepali and in English, and which of those a button should speak is a
shopkeeper's decision, not a template's — so the words live on the
`store.StoreLabel` singleton and the pages read them through
`store/labels.py`.

A box left empty is not an empty button: it resolves back to the wording that
ships, which is what the placeholder on each box shows. Every write busts the
storefront's label cache, the same way the Delivery Charge and Product Page
Theme screens bust theirs.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import admin_only
from store import labels as store_labels
from store.models import StoreLabel


def _fields(row):
    """`labels.FIELD_SPECS` with this row's saved value on each entry."""
    return [
        dict(spec, value=(getattr(row, spec['key'], '') or ''),
             default=store_labels.DEFAULTS[spec['key']])
        for spec in store_labels.FIELD_SPECS
    ]


@login_required
@admin_only
def store_label_setup(request):
    row = StoreLabel.get_solo()
    fields = _fields(row)
    return render(request, 'dashboard/store_label_setup.html', {
        'page_title': 'Store Button Labels',
        'row': row,
        # Grouped for the screen; one flat list would put the order form's
        # buttons under the product page's heading.
        'groups': [
            {'name': name, 'fields': [f for f in fields if f['group'] == name]}
            for name in store_labels.GROUPS
        ],
        'customised': sum(1 for f in fields if f['value'].strip()),
        'total': len(fields),
        'preview': store_labels.snapshot(),
    })


@login_required
@admin_only
@require_POST
def store_label_save(request):
    row = StoreLabel.get_solo()
    for spec in store_labels.FIELD_SPECS:
        key = spec['key']
        # Trimmed, because a button labelled with a space is a blank button
        # that does not look blank in the box that wrote it.
        setattr(row, key, (request.POST.get(key) or '').strip())
    row.save()
    store_labels.invalidate_cache()
    messages.success(request, 'Button labels saved.')
    return redirect('store_label_setup')


@login_required
@admin_only
@require_POST
def store_label_reset(request):
    """Back to the wording that ships — every box cleared at once."""
    row = StoreLabel.get_solo()
    for spec in store_labels.FIELD_SPECS:
        setattr(row, spec['key'], '')
    row.save()
    store_labels.invalidate_cache()
    messages.success(request, 'Every button is back to its default wording.')
    return redirect('store_label_setup')
