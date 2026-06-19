"""
Google Sheets Service Layer — No API Key Required
Supports two simple approaches:
  1. Published CSV URL   → Read-only from Sheet (zero setup)
  2. Apps Script Web App → Two-way sync (user deploys one script)
"""
import csv
import io
import json
import logging
import time

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

# ─── Field Definitions ────────────────────────────────────────────────────────

MODEL_FIELD_DEFS = {
    'orders': [
        {'key': 'order_number',     'label': 'Order Number'},
        {'key': 'customer_name',    'label': 'Customer Name'},
        {'key': 'customer_phone',   'label': 'Phone'},
        {'key': 'customer_email',   'label': 'Email'},
        {'key': 'shipping_address', 'label': 'Address'},
        {'key': 'order_from',       'label': 'Order Source'},
        {'key': 'status',           'label': 'Status'},
        {'key': 'payment_method',   'label': 'Payment Method'},
        {'key': 'payment_status',   'label': 'Payment Status'},
        {'key': 'total_amount',     'label': 'Total Amount'},
        {'key': 'notes',            'label': 'Notes'},
        {'key': 'tracking_number',  'label': 'Tracking Number'},
        {'key': 'created_at',       'label': 'Created At'},
    ],
    'products': [
        {'key': 'name',         'label': 'Product Name'},
        {'key': 'price',        'label': 'Price'},
        {'key': 'cost_price',   'label': 'Cost Price'},
        {'key': 'stock',        'label': 'Stock'},
        {'key': 'barcode',      'label': 'Barcode'},
        {'key': 'description',  'label': 'Description'},
        {'key': 'stock_status', 'label': 'Stock Status'},
        {'key': 'created_at',   'label': 'Created At'},
    ],
    'customers': [
        {'key': 'name',            'label': 'Full Name'},
        {'key': 'phone',           'label': 'Phone'},
        {'key': 'alternate_phone', 'label': 'Alternate Phone'},
        {'key': 'email',           'label': 'Email'},
        {'key': 'city',            'label': 'City'},
        {'key': 'address',         'label': 'Address'},
        {'key': 'customer_type',   'label': 'Customer Type'},
        {'key': 'notes',           'label': 'Notes'},
        {'key': 'created_at',      'label': 'Created At'},
    ],
    'custom': [],
}

# ─── Apps Script Template (shown to users) ───────────────────────────────────

APPS_SCRIPT_CODE = '''// Google Apps Script — Paste this in your Sheet's Script Editor
// Then click Deploy → New Deployment → Web App → Execute as: Me → Who has access: Anyone
// Copy the Web App URL and paste it into Django Imports page.

function doGet(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var rows = [];
  for (var i = 1; i < data.length; i++) {
    var row = {};
    for (var j = 0; j < headers.length; j++) {
      row[headers[j]] = data[i][j];
    }
    rows.push(row);
  }
  return ContentService.createTextOutput(JSON.stringify({
    success: true,
    headers: headers,
    rows: rows,
    total: rows.length
  })).setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var payload = JSON.parse(e.postData.contents);
  var action = payload.action;

  if (action === 'write_all') {
    var headers = payload.headers;
    var rows = payload.rows;
    sheet.clearContents();
    sheet.appendRow(headers);
    rows.forEach(function(row) {
      sheet.appendRow(headers.map(function(h) { return row[h] || ''; }));
    });
    return ContentService.createTextOutput(JSON.stringify({success: true, rows_written: rows.length}))
      .setMimeType(ContentService.MimeType.JSON);
  }

  if (action === 'update_cell') {
    var cell = sheet.getRange(payload.row, payload.col);
    cell.setValue(payload.value);
    return ContentService.createTextOutput(JSON.stringify({success: true}))
      .setMimeType(ContentService.MimeType.JSON);
  }

  return ContentService.createTextOutput(JSON.stringify({success: false, error: 'Unknown action'}))
    .setMimeType(ContentService.MimeType.JSON);
}
'''


# ─── Helpers ─────────────────────────────────────────────────────────────────

def credentials_exist():
    """Always returns True — no credentials needed for CSV/Apps Script approach."""
    return True


def _make_published_csv_url(spreadsheet_id, gid='0'):
    """Build a published CSV export URL from a spreadsheet ID."""
    return (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        f"/export?format=csv&gid={gid}"
    )


def extract_spreadsheet_id(url_or_id):
    """Extract spreadsheet ID from a Google Sheets URL."""
    import re
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url_or_id)
    if match:
        return match.group(1)
    return url_or_id.strip()


def extract_gid(url):
    """Extract the gid (sheet tab ID) from a Google Sheets URL."""
    import re
    match = re.search(r'[#&?]gid=(\d+)', url)
    return match.group(1) if match else '0'


# ─── CSV (Published URL) ──────────────────────────────────────────────────────

def fetch_csv_from_url(csv_url, timeout=15):
    """
    Fetch a published Google Sheet CSV URL and return list of dicts.
    The sheet must be published: File → Share → Publish to web → CSV.
    """
    headers_req = {
        'User-Agent': 'Mozilla/5.0 (compatible; DjangoImports/1.0)',
    }
    resp = requests.get(csv_url, headers=headers_req, timeout=timeout)
    resp.raise_for_status()

    content = resp.content.decode('utf-8-sig')  # handles BOM
    reader = csv.DictReader(io.StringIO(content))
    rows = [dict(row) for row in reader]
    return rows


def preview_csv_url(csv_url, num_rows=5):
    """Return raw rows (list of lists) for preview."""
    headers_req = {'User-Agent': 'Mozilla/5.0 (compatible; DjangoImports/1.0)'}
    resp = requests.get(csv_url, headers=headers_req, timeout=15)
    resp.raise_for_status()

    content = resp.content.decode('utf-8-sig')
    reader = csv.reader(io.StringIO(content))
    rows = [row for row in reader]
    return rows[:num_rows]


# ─── Apps Script Web App ──────────────────────────────────────────────────────

def apps_script_read(web_app_url, timeout=20):
    """Read all rows from a Google Sheet via Apps Script Web App (GET)."""
    resp = requests.get(web_app_url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not data.get('success'):
        raise ValueError(data.get('error', 'Apps Script returned an error'))
    return data.get('rows', []), data.get('headers', [])


def apps_script_write_all(web_app_url, headers, rows, timeout=30):
    """Overwrite the entire sheet via Apps Script (POST with write_all action)."""
    payload = {
        'action': 'write_all',
        'headers': headers,
        'rows': rows,
    }
    resp = requests.post(web_app_url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not data.get('success'):
        raise ValueError(data.get('error', 'Apps Script write failed'))
    return data.get('rows_written', 0)


def apps_script_update_cell(web_app_url, row, col, value, timeout=10):
    """Update a single cell via Apps Script."""
    payload = {'action': 'update_cell', 'row': row, 'col': col, 'value': value}
    resp = requests.post(web_app_url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get('success', False)


# ─── Django Model Helpers ─────────────────────────────────────────────────────

def _get_queryset(sync_model):
    from dashboard.models import Order, Product, Customer
    qs_map = {
        'orders':    Order.objects.filter(is_deleted=False),
        'products':  Product.objects.filter(is_deleted=False),
        'customers': Customer.objects.all(),
    }
    return qs_map.get(sync_model, [])


def _serialize_obj(obj, field_mapping):
    """Flatten a Django model instance to a dict keyed by sheet column header."""
    row = {}
    for header, field_key in field_mapping.items():
        try:
            val = getattr(obj, field_key, '')
            if val is None:
                val = ''
            elif hasattr(val, 'strftime'):
                val = val.strftime('%Y-%m-%d %H:%M')
            else:
                val = str(val)
        except Exception:
            val = ''
        row[header] = val
    return row


# ─── Main Sync Operations ─────────────────────────────────────────────────────

def sync_django_to_sheet(connection):
    """Push Django records → Google Sheet."""
    start = time.time()
    result = {'rows_synced': 0, 'rows_failed': 0, 'error_message': '', 'status': 'success'}

    try:
        field_mapping = connection.field_mapping  # {header_label: django_field}
        if not field_mapping:
            raise ValueError("No field mapping configured. Please edit the connection and map columns.")

        qs = _get_queryset(connection.sync_model)
        headers = list(field_mapping.keys())
        rows = [_serialize_obj(obj, field_mapping) for obj in qs]

        if connection.connection_type == 'apps_script' and connection.apps_script_url:
            rows_written = apps_script_write_all(connection.apps_script_url, headers, rows)
            result['rows_synced'] = rows_written
        else:
            raise ValueError(
                "To push data to Google Sheets, use the Apps Script method. "
                "Published CSV is read-only."
            )
    except Exception as e:
        result['status'] = 'failed'
        result['error_message'] = str(e)
        logger.error(f"sync_django_to_sheet failed for connection {connection.id}: {e}")

    result['duration_seconds'] = round(time.time() - start, 2)
    return result


def sync_sheet_to_django(connection):
    """Pull Google Sheet data → Django DB."""
    start = time.time()
    result = {'rows_synced': 0, 'rows_failed': 0, 'rows_skipped': 0, 'error_message': '', 'status': 'success'}

    try:
        from dashboard.models import Order, Product, Customer
        field_mapping = connection.field_mapping  # {sheet_header: django_field}

        if not field_mapping:
            raise ValueError("No field mapping configured.")

        # Fetch data
        if connection.connection_type == 'apps_script' and connection.apps_script_url:
            raw_rows, _ = apps_script_read(connection.apps_script_url)
        elif connection.connection_type == 'csv' and connection.csv_url:
            raw_rows = fetch_csv_from_url(connection.csv_url)
        else:
            raise ValueError("No valid URL configured for this connection.")

        MODEL_MAP = {'orders': Order, 'products': Product, 'customers': Customer}
        UNIQUE_FIELD = {'orders': 'order_number', 'products': 'name', 'customers': 'phone'}

        model_cls = MODEL_MAP.get(connection.sync_model)
        unique_field = UNIQUE_FIELD.get(connection.sync_model)

        if not model_cls:
            raise ValueError(f"Unsupported sync model: {connection.sync_model}")

        for raw_row in raw_rows:
            try:
                # Map sheet headers → django fields
                obj_data = {}
                for sheet_header, django_field in field_mapping.items():
                    val = raw_row.get(sheet_header, '').strip()
                    obj_data[django_field] = val

                if not obj_data:
                    result['rows_skipped'] += 1
                    continue

                unique_val = obj_data.get(unique_field, '')
                if not unique_val:
                    result['rows_skipped'] += 1
                    continue

                obj, created = model_cls.objects.get_or_create(
                    **{unique_field: unique_val}
                )

                for field_name, value in obj_data.items():
                    if field_name == unique_field or not value:
                        continue
                    try:
                        field_obj = model_cls._meta.get_field(field_name)
                        if hasattr(field_obj, 'related_model') and field_obj.related_model:
                            continue
                        setattr(obj, field_name, value)
                    except Exception:
                        pass

                obj.save()
                result['rows_synced'] += 1

            except Exception as row_err:
                result['rows_failed'] += 1
                logger.warning(f"Row sync failed: {row_err}")

    except Exception as e:
        result['status'] = 'failed' if result['rows_synced'] == 0 else 'partial'
        result['error_message'] = str(e)
        logger.error(f"sync_sheet_to_django failed for connection {connection.id}: {e}")

    if result['rows_failed'] > 0 and result['rows_synced'] > 0:
        result['status'] = 'partial'
    elif result['rows_failed'] > 0 and result['rows_synced'] == 0:
        result['status'] = 'failed'

    result['duration_seconds'] = round(time.time() - start, 2)
    return result


def run_sync(connection, direction='from_sheet', user=None):
    """
    Main sync entry point. Creates a log and updates connection timestamps.
    direction: 'to_sheet' | 'from_sheet' | 'both'
    """
    from .models import GoogleSheetSyncLog

    result = {'rows_synced': 0, 'rows_failed': 0, 'rows_skipped': 0, 'error_message': '', 'status': 'success'}

    if direction in ('to_sheet', 'both'):
        r = sync_django_to_sheet(connection)
        result['rows_synced'] += r.get('rows_synced', 0)
        result['rows_failed'] += r.get('rows_failed', 0)
        if r.get('error_message'):
            result['error_message'] += f"[→ Sheet] {r['error_message']} "
        if r.get('status') == 'failed' and direction != 'both':
            result['status'] = 'failed'

    if direction in ('from_sheet', 'both'):
        r = sync_sheet_to_django(connection)
        result['rows_synced'] += r.get('rows_synced', 0)
        result['rows_failed'] += r.get('rows_failed', 0)
        result['rows_skipped'] = r.get('rows_skipped', 0)
        if r.get('error_message'):
            result['error_message'] += f"[Sheet →] {r['error_message']} "
        if r.get('status') == 'failed':
            result['status'] = 'failed' if result['rows_synced'] == 0 else 'partial'

    if result['rows_failed'] > 0 and result['rows_synced'] > 0:
        result['status'] = 'partial'

    log = GoogleSheetSyncLog.objects.create(
        connection=connection,
        direction=direction,
        status=result['status'],
        rows_synced=result['rows_synced'],
        rows_failed=result['rows_failed'],
        rows_skipped=result.get('rows_skipped', 0),
        error_message=result.get('error_message', ''),
        triggered_by=user,
    )

    connection.last_synced_at = timezone.now()
    connection.last_sync_direction = direction
    connection.last_sync_status = result['status']
    connection.save(update_fields=['last_synced_at', 'last_sync_direction', 'last_sync_status'])

    result['log_id'] = log.id
    return result
