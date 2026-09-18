"""Bulk invoice printing for the orders list.

Tick any number of orders on Orders → Order List and print their invoices in
one pass. Each invoice is the *same* document `dashboard.views.order_invoice`
prints — the shared `invoice/_document.html` partial, rendered from the same
`invoice_config.build_invoice_context()` dictionary — so a change made in
Setup → Invoice Customizer shows up here without a second thought.

Two things this module is careful about:

* **The styling is emitted once.** `InvoiceTemplate` is a singleton, so every
  invoice on the sheet resolves to identical CSS; repeating it per order would
  multiply a ~270-line stylesheet by the number of orders printed.
* **Selection is not authorisation.** The ids arrive from the browser, so the
  queryset is filtered by the same rule `order_invoice` uses (staff who are not
  admins/managers only ever print their own orders) and the page says plainly
  how many of the ticked orders it dropped.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.decorators import permission_required
from dashboard import invoice_config
from dashboard.models import Order

#: Printing more than this in one go builds a page big enough to hang the
#: browser's print dialog. The extras are dropped and the sheet says so.
MAX_INVOICES = 200

LAYOUTS = {'page', 'flow'}


def _requested_ids(request):
    """Order ids from either a POSTed form or a `?ids=1,2,3` link.

    POST is how the orders list submits (a tick list can outgrow a URL); GET
    keeps the printed sheet re-openable and bookmarkable.
    """
    raw = []
    if request.method == 'POST':
        raw = request.POST.getlist('order_ids')
    if not raw:
        raw = [chunk for value in request.GET.getlist('ids') for chunk in value.split(',')]

    seen = set()
    ids = []
    for value in raw:
        value = (value or '').strip()
        if not value.isdigit():
            continue
        number = int(value)
        if number in seen:
            continue
        seen.add(number)
        ids.append(number)
    return ids


def _visible_orders(request, ids):
    """The subset of `ids` this user may print, in the order they were ticked."""
    queryset = Order.objects.select_related(
        'api_config', 'status_setup', 'payment_setup', 'payment_status_setup'
    ).prefetch_related(
        'items__product__bundle_components__component_product',
        'items__product_variation',
    ).filter(id__in=ids, is_deleted=False)

    user = request.user
    if not (user.role in ('admin', 'manager') or user.is_staff or user.is_superuser):
        queryset = queryset.filter(created_by=user)

    by_id = {order.id: order for order in queryset}
    return [by_id[order_id] for order_id in ids if order_id in by_id]


@login_required
@permission_required('can_view_orders')
def orders_bulk_invoice(request):
    """Render every selected order's invoice onto one printable sheet."""
    ids = _requested_ids(request)
    if not ids:
        messages.error(request, 'Select at least one order to print.')
        return redirect('orders_list')

    truncated = max(0, len(ids) - MAX_INVOICES)
    orders = _visible_orders(request, ids[:MAX_INVOICES])
    if not orders:
        messages.error(request, 'None of the selected orders could be printed.')
        return redirect('orders_list')

    cfg = invoice_config.get_template()
    invoices = []
    for order in orders:
        context = invoice_config.build_invoice_context(
            order,
            list(order.items.all()),
            cfg=cfg,
            user=request.user,
            labels={
                'status': (order.status_setup.name if order.status_setup
                           else (order.order_status or order.status or 'pending')),
                'payment_status': (order.payment_status_setup.name if order.payment_status_setup
                                   else (order.payment_status or 'pending')),
                'payment_method': (order.payment_setup.name if order.payment_setup
                                   else (order.payment_method or 'N/A')),
            },
        )
        context['hide_actions'] = True  # one toolbar for the sheet, not one per invoice
        invoices.append({
            'context': context,
            'order_number': order.order_number,
            'customer_name': order.customer_name,
            'total': context['totals'][-1]['value'] if context['totals'] else '',
        })

    layout = request.POST.get('layout') or request.GET.get('layout') or 'page'
    if layout not in LAYOUTS:
        layout = 'page'

    first = invoices[0]['context']
    return render(request, 'order_invoice_bulk.html', {
        'invoices': invoices,
        'cfg': cfg,
        'style': first['style'],
        'brand': first['brand'],
        'layout': layout,
        'count': len(invoices),
        'skipped': len(ids[:MAX_INVOICES]) - len(orders),
        'truncated': truncated,
        'max_invoices': MAX_INVOICES,
        'ids_param': ','.join(str(order.id) for order in orders),
    })
