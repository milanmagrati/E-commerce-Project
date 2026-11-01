"""NCM shipment label (sticker) printing for bulk-send batches and orders.

Fetches label metadata from NCM's `/api/v2/vendor/order/label/` endpoint (which
takes `{"ids": [<ncm_order_id>, ...]}` and returns a list of label dicts) and
renders the official NCM sticker design with both:
  * a QR code of the NCM order ID (`orderid`, e.g. 27620066), and
  * a Code128 barcode of our local order number (`order_number`, e.g. T18320).

Supports:
  * A4 (2 per page) — default, matching NCM's Bulk Print Orders layout
  * Thermal 100x150mm (1 per page) — 4x6 inch thermal sticker printer
  * A4 (4 per page)
  * A4 (1 per page)
"""

import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import permission_required
from dashboard.models import LogisticsAPIConfig, Order
from dashboard.timezone_utils import get_nepali_now
from ncm.models import NCMBulkLog, NCMBulkLogOrder
from services.ncm_service import NCMService

logger = logging.getLogger('ncm')

MAX_LABELS = 500

PRINT_LAYOUTS = (
    ('a4_2', 'A4 (2 per page)'),
    ('thermal_1', 'Thermal 100×150mm (1 per page)'),
    ('a4_4', 'A4 (4 per page)'),
    ('a4_1', 'A4 (1 per page)'),
)
VALID_LAYOUT_KEYS = {k for k, _ in PRINT_LAYOUTS}


def _parse_int_list(raw_values):
    """Parse comma-separated or repeated integer query/POST parameters."""
    seen = set()
    out = []
    for val in raw_values or []:
        for part in str(val or '').split(','):
            part = part.strip().lstrip('#')
            if not part.isdigit():
                continue
            num = int(part)
            if num not in seen:
                seen.add(num)
                out.append(num)
    return out


def _format_cod(value):
    """Format COD charge as '1,300.00'."""
    try:
        dec = Decimal(str(value or 0)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        dec = Decimal('0.00')
    return f"{dec:,.2f}"


def _format_date_ddmmyyyy(dt):
    """Format a datetime as DD-MM-YYYY in Nepal time."""
    if not dt:
        dt = get_nepali_now()
    try:
        from dashboard.timezone_utils import to_nepali_time
        dt = to_nepali_time(dt)
    except Exception:
        pass
    try:
        return dt.strftime('%d-%m-%Y')
    except Exception:
        return get_nepali_now().strftime('%d-%m-%Y')


def _build_local_item_description(order):
    """Fallback product description ('1x Black Bottle shampoo') from local OrderItems."""
    if not order:
        return ''
    try:
        items = list(order.items.select_related('product_variation').all())
        if not items:
            return ''
        parts = []
        for item in items:
            qty = getattr(item, 'quantity', 1) or 1
            name = (item.product_name or 'Item').strip()
            var_name = item.variation_name or (
                item.product_variation.variation_name if item.product_variation else None
            )
            if var_name:
                name = f"{name} ({var_name})"
            parts.append(f"{qty}x {name}")
        return ', '.join(parts)
    except Exception:
        return ''


def _space_letters(text):
    """Turn 'TINKUNE' into 'T I N K U N E' for the NCM header typography."""
    cleaned = (text or '').strip().upper()
    return ' '.join(list(cleaned)) if cleaned else ''


def fetch_labels_across_configs(ncm_ids, config_by_ncm_id=None, default_config_id=None):
    """Fetch NCM label dicts for `ncm_ids`, routing each ID to its NCM portal.

    1. Groups IDs by their known `api_config_id` (from the linked Order or the
       batch's `send_options`).
    2. If any requested ID is missing from that response (e.g. an older batch
       without `api_config_id`, or a mixed-portal selection), sweeps the
       remaining active NCM `LogisticsAPIConfig` accounts and the default
       settings account so valid orders are still found.

    Returns (labels_by_ncm_id: dict[int, dict], errors: list[str]).
    """
    config_by_ncm_id = config_by_ncm_id or {}
    groups = {}
    for oid in ncm_ids:
        cfg_id = config_by_ncm_id.get(oid, default_config_id)
        groups.setdefault(cfg_id, []).append(oid)

    labels_by_id = {}
    errors = []
    tried_configs_by_id = {oid: set() for oid in ncm_ids}

    def _call_config(cfg_id, target_ids):
        if not target_ids:
            return
        for oid in target_ids:
            tried_configs_by_id.setdefault(oid, set()).add(cfg_id)
        try:
            svc = NCMService(api_config_id=cfg_id) if cfg_id else NCMService()
            res = svc.get_order_labels(target_ids)
        except Exception as exc:
            logger.warning("NCM label fetch failed for config %s: %s", cfg_id, exc)
            errors.append(str(exc))
            return
        for item in res.get('labels') or []:
            raw_oid = item.get('orderid') or item.get('order_id') or item.get('id')
            try:
                labels_by_id[int(raw_oid)] = item
            except (TypeError, ValueError):
                continue
        for err in res.get('errors') or []:
            if err and err not in errors:
                errors.append(err)

    # Pass 1: each order's preferred NCM account
    for cfg_id, group_ids in groups.items():
        _call_config(cfg_id, group_ids)

    # Pass 2: sweep remaining active NCM accounts for any IDs still missing
    missing_ids = [oid for oid in ncm_ids if oid not in labels_by_id]
    if missing_ids:
        active_cfg_ids = list(
            LogisticsAPIConfig.objects.filter(
                logistics_provider='ncm', is_active=True
            ).values_list('id', flat=True)
        )
        candidate_configs = active_cfg_ids + [None]
        for cfg_id in candidate_configs:
            still_missing = [
                oid for oid in ncm_ids
                if oid not in labels_by_id and cfg_id not in tried_configs_by_id.get(oid, set())
            ]
            if not still_missing:
                break
            _call_config(cfg_id, still_missing)

    return labels_by_id, errors


def _normalize_label(ncm_id, api_label, local_order=None, log_order=None, batch=None):
    """Combine NCM's `/vendor/order/label/` dict with local Order fallbacks."""
    api_label = api_label if isinstance(api_label, dict) else {}
    from_branch = api_label.get('from_branch') if isinstance(api_label.get('from_branch'), dict) else {}
    to_branch = api_label.get('to_branch') if isinstance(api_label.get('to_branch'), dict) else {}
    sender = api_label.get('from') if isinstance(api_label.get('from'), dict) else {}
    receiver = api_label.get('receiver') if isinstance(api_label.get('receiver'), dict) else {}
    desc_obj = api_label.get('description') if isinstance(api_label.get('description'), dict) else {}

    # Local order number for the Code128 barcode (e.g. T18320).
    local_order_num = ''
    if local_order and local_order.order_number:
        local_order_num = local_order.order_number.strip()
    elif log_order and log_order.order_number:
        local_order_num = log_order.order_number.strip()

    vendor_oid = str(desc_obj.get('vendor_orderid') or '').strip()
    barcode_value = local_order_num or (
        vendor_oid if vendor_oid and vendor_oid.lower() not in ('none', 'null', 'out valley', 'in valley')
        else str(ncm_id)
    )

    from_branch_name = (
        from_branch.get('name')
        or (batch.from_branch if batch else '')
        or (local_order.ncm_from_branch if local_order else '')
        or 'TINKUNE'
    ).strip().upper()

    to_branch_name = (
        to_branch.get('name')
        or (log_order.destination_branch if log_order else '')
        or (local_order.ncm_destination_branch if local_order else '')
        or (local_order.branch.name if (local_order and getattr(local_order, 'branch_id', None)) else '')
    ).strip().upper()

    to_branch_code = (to_branch.get('code') or '').strip().upper()

    from_district = (from_branch.get('district') or '').strip().upper()
    to_district = (to_branch.get('district') or '').strip().upper()
    if from_district and to_district:
        district_line = f"{from_district} - {to_district}"
    else:
        district_line = from_district or to_district

    zone = (
        api_label.get('zone')
        or to_branch.get('zone')
        or ''
    ).strip().upper()

    # Sender details
    sender_name = (
        sender.get('name')
        or (local_order.api_config.api_name if (local_order and local_order.api_config) else '')
        or 'Trendy Shopping'
    ).strip()
    sender_phone = (sender.get('phone') or sender.get('phone2') or '').strip()

    # Delivery type & COD
    delivery_type = (api_label.get('delivery_type') or '').strip()
    if not delivery_type:
        raw_dt = (batch.delivery_type if batch else '') or (local_order.ncm_delivery_type if local_order else '')
        delivery_type = 'Branch' if 'branch' in str(raw_dt).lower() and not str(raw_dt).lower().endswith('door') else 'Home'

    cod_raw = api_label.get('cod_charge')
    if cod_raw is None:
        if log_order is not None and log_order.cod_amount is not None:
            cod_raw = log_order.cod_amount
        elif local_order is not None:
            cod_raw = local_order.amount_due
        else:
            cod_raw = 0
    cod_formatted = _format_cod(cod_raw)

    # Receiver details
    receiver_name = (
        receiver.get('name')
        or (log_order.customer_name if log_order else '')
        or (local_order.customer_name if local_order else '')
    ).strip()
    receiver_phone = (
        receiver.get('phone')
        or (log_order.customer_phone if log_order else '')
        or (local_order.customer_phone if local_order else '')
    ).strip()
    receiver_phone2 = (receiver.get('phone2') or '').strip()
    receiver_address = (
        receiver.get('address')
        or (log_order.shipping_address if log_order else '')
        or (local_order.shipping_address if local_order else '')
    ).strip()

    # Product description & delivery instruction
    description_text = (desc_obj.get('description') or '').strip()
    if not description_text and local_order:
        description_text = _build_local_item_description(local_order)

    instruction_text = (desc_obj.get('delivery_instruction') or '').strip()
    if not instruction_text and local_order and local_order.notes:
        instruction_text = local_order.notes.strip()

    # Date (DD-MM-YYYY)
    ref_dt = (
        (local_order.ncm_created_at if local_order and local_order.ncm_created_at else None)
        or (batch.created_at if batch else None)
        or (log_order.created_at if log_order else None)
    )
    date_str = _format_date_ddmmyyyy(ref_dt)

    return {
        'ncm_order_id': ncm_id,
        'order_number': local_order_num or barcode_value,
        'barcode_value': barcode_value,
        'date_str': date_str,
        'zone': zone,
        'from_branch_name': from_branch_name,
        'from_branch_spaced': _space_letters(from_branch_name),
        'to_branch_code': to_branch_code,
        'to_branch_code_spaced': _space_letters(to_branch_code),
        'to_branch_name': to_branch_name,
        'district_line': district_line,
        'sender_name': sender_name,
        'sender_phone': sender_phone,
        'delivery_type': delivery_type,
        'cod_formatted': cod_formatted,
        'receiver_name': receiver_name,
        'receiver_phone': receiver_phone,
        'receiver_phone2': receiver_phone2,
        'receiver_address': receiver_address,
        'description': description_text,
        'instruction': instruction_text,
        'from_api': bool(api_label),
    }


def _render_label_page(request, batches=None, explicit_ncm_ids=None):
    """Shared renderer for single-batch, multi-batch, and explicit-ID label pages."""
    batches = list(batches or [])
    layout = (request.GET.get('layout') or request.POST.get('layout') or 'a4_2').strip()
    if layout not in VALID_LAYOUT_KEYS:
        layout = 'a4_2'

    # Collect candidate rows in batch order
    log_order_by_ncm_id = {}
    batch_by_ncm_id = {}
    default_cfg_by_ncm_id = {}
    skipped_rows = []
    ordered_ncm_ids = []
    seen_ncm_ids = set()

    if batches:
        batch_ids = [b.id for b in batches]
        batch_map = {b.id: b for b in batches}
        log_orders = (
            NCMBulkLogOrder.objects.filter(batch_id__in=batch_ids)
            .select_related('order__api_config', 'batch')
            .order_by('batch_id', 'created_at', 'id')
        )
        for lo in log_orders:
            b = batch_map.get(lo.batch_id)
            ncm_id = lo.ncm_order_id or (lo.order.ncm_order_id if lo.order else None)
            if not ncm_id:
                skipped_rows.append({
                    'order_number': lo.order_number,
                    'reason': lo.message or f'Not sent to NCM ({lo.get_status_display()})',
                })
                continue
            if lo.order and (
                lo.order.is_deleted
                or (lo.order.order_status or '').lower() == 'cancelled'
                or 'cancel' in (lo.order.ncm_status or '').lower()
            ):
                skipped_rows.append({
                    'order_number': lo.order_number,
                    'reason': 'Order is cancelled or deleted',
                })
                continue
            ncm_id = int(ncm_id)
            if ncm_id not in seen_ncm_ids:
                seen_ncm_ids.add(ncm_id)
                ordered_ncm_ids.append(ncm_id)
            log_order_by_ncm_id[ncm_id] = lo
            batch_by_ncm_id[ncm_id] = b
            if b and isinstance(b.send_options, dict) and b.send_options.get('api_config_id'):
                default_cfg_by_ncm_id[ncm_id] = b.send_options.get('api_config_id')

    # If caller also passed explicit `ncm_ids`, either filter the batch to those
    # IDs or use them directly when no batch was specified.
    if explicit_ncm_ids:
        if ordered_ncm_ids:
            keep = set(explicit_ncm_ids)
            ordered_ncm_ids = [oid for oid in ordered_ncm_ids if oid in keep]
        else:
            ordered_ncm_ids = list(explicit_ncm_ids)

    truncated_count = 0
    if len(ordered_ncm_ids) > MAX_LABELS:
        truncated_count = len(ordered_ncm_ids) - MAX_LABELS
        ordered_ncm_ids = ordered_ncm_ids[:MAX_LABELS]

    # Load linked local Orders (with items for description fallback)
    local_orders_by_ncm_id = {}
    if ordered_ncm_ids:
        for o in (
            Order.objects.filter(ncm_order_id__in=ordered_ncm_ids, is_deleted=False)
            .select_related('api_config', 'branch')
            .prefetch_related('items__product_variation')
        ):
            if o.ncm_order_id:
                local_orders_by_ncm_id[int(o.ncm_order_id)] = o

    # Map each NCM order ID to its preferred LogisticsAPIConfig id
    config_by_ncm_id = {}
    for oid in ordered_ncm_ids:
        lo = local_orders_by_ncm_id.get(oid)
        if lo and lo.api_config_id:
            config_by_ncm_id[oid] = lo.api_config_id
        elif oid in default_cfg_by_ncm_id:
            config_by_ncm_id[oid] = default_cfg_by_ncm_id[oid]

    api_labels_by_id, api_errors = (
        fetch_labels_across_configs(ordered_ncm_ids, config_by_ncm_id=config_by_ncm_id)
        if ordered_ncm_ids else ({}, [])
    )

    labels = []
    missing_from_api_ids = []
    for oid in ordered_ncm_ids:
        api_lbl = api_labels_by_id.get(oid)
        if not api_lbl:
            missing_from_api_ids.append(oid)
        labels.append(
            _normalize_label(
                oid,
                api_lbl,
                local_order=local_orders_by_ncm_id.get(oid),
                log_order=log_order_by_ncm_id.get(oid),
                batch=batch_by_ncm_id.get(oid),
            )
        )

    single_batch = batches[0] if len(batches) == 1 else None
    back_url = (
        f"/ncm-bulk-logs/{single_batch.id}/"
        if single_batch
        else "/logistics/bulk-logs/?provider=ncm"
    )

    context = {
        'batches': batches,
        'bulk_log': single_batch,
        'labels': labels,
        'label_count': len(labels),
        'skipped_rows': skipped_rows,
        'missing_from_api_ids': missing_from_api_ids,
        'api_errors': api_errors,
        'truncated_count': truncated_count,
        'layout': layout,
        'print_layouts': PRINT_LAYOUTS,
        'back_url': back_url,
    }
    return render(request, 'ncm_label_print.html', context)


@login_required
@permission_required('can_view_ncm_bulk_logs')
def ncm_bulk_log_labels(request, log_id):
    """Print NCM shipment labels for all sent orders in a single batch."""
    bulk_log = get_object_or_404(NCMBulkLog, id=log_id, is_deleted=False)
    explicit_ids = _parse_int_list(
        request.POST.getlist('ncm_ids') or request.GET.getlist('ids')
    )
    return _render_label_page(request, batches=[bulk_log], explicit_ncm_ids=explicit_ids)


@login_required
@permission_required('can_view_ncm_bulk_logs')
def ncm_labels_print(request):
    """Print NCM shipment labels for multiple selected batches, Orders, or NCM order IDs."""
    batch_ids = _parse_int_list(
        request.POST.getlist('log_ids')
        or request.POST.getlist('batch_ids')
        or request.GET.getlist('batch_ids')
    )
    order_ids = _parse_int_list(
        request.POST.getlist('order_ids')
        or request.GET.getlist('order_ids')
    )
    explicit_ids = _parse_int_list(
        request.POST.getlist('ncm_ids')
        or request.GET.getlist('ids')
    )

    if order_ids:
        for o in Order.objects.filter(id__in=order_ids, is_deleted=False):
            if (o.order_status or '').lower() == 'cancelled' or 'cancel' in (o.ncm_status or '').lower():
                continue
            if o.ncm_order_id and int(o.ncm_order_id) not in explicit_ids:
                explicit_ids.append(int(o.ncm_order_id))

    batches = []
    if batch_ids:
        batches = list(
            NCMBulkLog.objects.filter(id__in=batch_ids, is_deleted=False).order_by('-created_at')
        )

    if not batches and not explicit_ids:
        messages.warning(request, 'Please select at least one NCM batch or order to print labels.')
        return redirect('/logistics/bulk-logs/?provider=ncm')

    return _render_label_page(request, batches=batches, explicit_ncm_ids=explicit_ids)
