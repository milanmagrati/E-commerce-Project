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

function jsonOutput(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function getSheetByNameOrActive_(ss, sheetName) {
  var sheet = sheetName ? ss.getSheetByName(sheetName) : ss.getActiveSheet();
  if (!sheet) {
    sheet = ss.getActiveSheet();
  }
  return sheet;
}

function normalizeValue_(value) {
  if (value === null || value === undefined) return '';
  return value;
}

function buildRowObjects_(headers, values) {
  var rows = [];
  for (var i = 1; i < values.length; i++) {
    var obj = {};
    for (var j = 0; j < headers.length; j++) {
      obj[headers[j]] = j < values[i].length ? values[i][j] : '';
    }
    rows.push(obj);
  }
  return rows;
}

function doGet(e) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var params = e && e.parameter ? e.parameter : {};
    var action = params.action || 'read';
    var sheet = getSheetByNameOrActive_(ss, params.sheet);

    if (action === 'ping' || action === 'health') {
      return jsonOutput({
        success: true,
        message: 'Apps Script web app is working',
        spreadsheet_id: ss.getId(),
        spreadsheet_name: ss.getName(),
        sheet_name: sheet.getName(),
        timestamp: new Date().toISOString()
      });
    }

    var dataRange = sheet.getDataRange();
    var values = dataRange.getValues();
    var headers = values.length > 0 ? values[0] : [];
    var rows = buildRowObjects_(headers, values);

    return jsonOutput({
      success: true,
      spreadsheet_id: ss.getId(),
      spreadsheet_name: ss.getName(),
      sheet_name: sheet.getName(),
      sheet_names: ss.getSheets().map(function(s) { return s.getName(); }),
      total_rows: Math.max(values.length - 1, 0),
      total_columns: headers.length,
      headers: headers,
      rows: rows
    });

  } catch (err) {
    return jsonOutput({
      success: false,
      error: String(err),
      stack: err && err.stack ? String(err.stack) : ''
    });
  }
}

function doPost(e) {
  var lock = LockService.getScriptLock();

  try {
    lock.waitLock(30000);

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var raw = e && e.postData && e.postData.contents ? e.postData.contents : '{}';
    var payload = JSON.parse(raw);

    var action = payload.action || '';
    var sheet = getSheetByNameOrActive_(ss, payload.sheet);

    if (action === 'ping' || action === 'health') {
      return jsonOutput({
        success: true,
        message: 'Apps Script POST is working',
        spreadsheet_id: ss.getId(),
        spreadsheet_name: ss.getName(),
        sheet_name: sheet.getName(),
        timestamp: new Date().toISOString()
      });
    }

    if (action === 'read_all') {
      var readValues = sheet.getDataRange().getValues();
      var readHeaders = readValues.length > 0 ? readValues[0] : [];
      var readRows = buildRowObjects_(readHeaders, readValues);

      return jsonOutput({
        success: true,
        spreadsheet_id: ss.getId(),
        spreadsheet_name: ss.getName(),
        sheet_name: sheet.getName(),
        sheet_names: ss.getSheets().map(function(s) { return s.getName(); }),
        total_rows: Math.max(readValues.length - 1, 0),
        total_columns: readHeaders.length,
        headers: readHeaders,
        rows: readRows
      });
    }

    if (action === 'write_all') {
      var headers = Array.isArray(payload.headers) ? payload.headers : [];
      var rows = Array.isArray(payload.rows) ? payload.rows : [];

      if (!headers.length) {
        return jsonOutput({
          success: false,
          error: 'Missing headers for write_all'
        });
      }

      var values = [headers];
      for (var i = 0; i < rows.length; i++) {
        var rowObj = rows[i] || {};
        var rowValues = [];
        for (var j = 0; j < headers.length; j++) {
          rowValues.push(normalizeValue_(rowObj[headers[j]]));
        }
        values.push(rowValues);
      }

      sheet.clearContents();

      if (sheet.getMaxRows() < values.length) {
        sheet.insertRowsAfter(sheet.getMaxRows(), values.length - sheet.getMaxRows());
      }
      if (sheet.getMaxColumns() < headers.length) {
        sheet.insertColumnsAfter(sheet.getMaxColumns(), headers.length - sheet.getMaxColumns());
      }

      sheet.getRange(1, 1, values.length, headers.length).setValues(values);
      SpreadsheetApp.flush();

      return jsonOutput({
        success: true,
        action: 'write_all',
        rows_written: rows.length,
        total_columns: headers.length,
        sheet_name: sheet.getName()
      });
    }

    if (action === 'update_cell') {
      var row = Number(payload.row);
      var col = Number(payload.col);
      var value = normalizeValue_(payload.value);

      if (!row || !col) {
        return jsonOutput({
          success: false,
          error: 'Missing row or col for update_cell'
        });
      }

      sheet.getRange(row, col).setValue(value);
      SpreadsheetApp.flush();

      return jsonOutput({
        success: true,
        action: 'update_cell',
        row: row,
        col: col
      });
    }

    if (action === 'resize_column') {
      var resizeCol = Number(payload.col);
      var resizeWidth = Number(payload.width);

      if (!resizeCol || !resizeWidth) {
        return jsonOutput({
          success: false,
          error: 'Missing col or width for resize_column'
        });
      }

      sheet.setColumnWidth(resizeCol, resizeWidth);

      return jsonOutput({
        success: true,
        action: 'resize_column',
        col: resizeCol,
        width: resizeWidth
      });
    }

    if (action === 'resize_row') {
      var resizeRow = Number(payload.row);
      var resizeHeight = Number(payload.height);

      if (!resizeRow || !resizeHeight) {
        return jsonOutput({
          success: false,
          error: 'Missing row or height for resize_row'
        });
      }

      sheet.setRowHeight(resizeRow, resizeHeight);

      return jsonOutput({
        success: true,
        action: 'resize_row',
        row: resizeRow,
        height: resizeHeight
      });
    }

    if (action === 'insert_row') {
      var insertRowIndex = Number(payload.index);
      if (!insertRowIndex) {
        return jsonOutput({
          success: false,
          error: 'Missing index for insert_row'
        });
      }

      sheet.insertRowBefore(insertRowIndex);

      return jsonOutput({
        success: true,
        action: 'insert_row',
        index: insertRowIndex
      });
    }

    if (action === 'delete_row') {
      var deleteRowIndex = Number(payload.index);
      if (!deleteRowIndex) {
        return jsonOutput({
          success: false,
          error: 'Missing index for delete_row'
        });
      }

      sheet.deleteRow(deleteRowIndex);

      return jsonOutput({
        success: true,
        action: 'delete_row',
        index: deleteRowIndex
      });
    }

    if (action === 'insert_column') {
      var insertColIndex = Number(payload.index);
      if (!insertColIndex) {
        return jsonOutput({
          success: false,
          error: 'Missing index for insert_column'
        });
      }

      sheet.insertColumnBefore(insertColIndex);

      return jsonOutput({
        success: true,
        action: 'insert_column',
        index: insertColIndex
      });
    }

    if (action === 'delete_column') {
      var deleteColIndex = Number(payload.index);
      if (!deleteColIndex) {
        return jsonOutput({
          success: false,
          error: 'Missing index for delete_column'
        });
      }

      sheet.deleteColumn(deleteColIndex);

      return jsonOutput({
        success: true,
        action: 'delete_column',
        index: deleteColIndex
      });
    }

    return jsonOutput({
      success: false,
      error: 'Unknown action'
    });

  } catch (err) {
    return jsonOutput({
      success: false,
      error: String(err),
      stack: err && err.stack ? String(err.stack) : ''
    });
  } finally {
    try {
      lock.releaseLock();
    } catch (e2) {}
  }
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

def apps_script_read(web_app_url, sheet_name=None, timeout=30):
    """Fetch all rows and metadata from the active Apps Script sheet or specific tab."""
    payload = {'action': 'read'}
    if sheet_name:
        payload['sheet'] = sheet_name
        
    try:
        # Apps Script doGet doesn't strictly need a payload, but we use requests.get
        # If we need to pass parameters to doGet, we append them to the URL
        url = web_app_url
        if sheet_name:
            import urllib.parse
            url += f"?sheet={urllib.parse.quote(sheet_name)}"
            
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Failed to connect to Apps Script: {e}")

    data = resp.json()
    if not data.get('success'):
        raise ValueError(data.get('error', 'Apps Script returned an error'))
    return {
        'rows': data.get('rows', []), 
        'headers': data.get('headers', []), 
        'raw_data': data.get('raw_data'),
        'col_widths': data.get('col_widths', []),
        'row_heights': data.get('row_heights', []),
        'sheet_names': data.get('sheet_names', []),
        'current_sheet': data.get('sheet_name', '')
    }


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
    return resp.json().get('success', False)


def apps_script_update_dimension(web_app_url, dimension, index, size, sheet_name=None, timeout=10):
    """Resize a row or column via Apps Script."""
    action = 'resize_row' if dimension == 'row' else 'resize_column'
    payload = {'action': action}
    if dimension == 'row':
        payload['row'] = index
        payload['height'] = size
    else:
        payload['col'] = index
        payload['width'] = size
    if sheet_name:
        payload['sheet'] = sheet_name
    
    resp = requests.post(web_app_url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get('success', False)

def apps_script_structure_action(web_app_url, action, index, sheet_name=None, timeout=10):
    """Insert or delete a row or column."""
    payload = {'action': action, 'index': index}
    if sheet_name:
        payload['sheet'] = sheet_name
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
        # Custom model has no Django queryset to push — skip silently
        if connection.sync_model == 'custom':
            result['rows_synced'] = 0
            result['duration_seconds'] = round(time.time() - start, 2)
            return result

        field_mapping = connection.field_mapping  # {header_label: django_field}
        if not field_mapping:
            raise ValueError(
                "No column mapping configured. Click Edit on this connection, "
                "enter the URL, click Test, map your columns, then Save."
            )

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
        # ── Fetch raw rows from the sheet ──────────────────────────────
        if connection.connection_type == 'apps_script' and connection.apps_script_url:
            apps_data = apps_script_read(connection.apps_script_url)
            raw_rows = apps_data['rows']
        elif connection.connection_type == 'csv' and connection.csv_url:
            raw_rows = fetch_csv_from_url(connection.csv_url)
        else:
            raise ValueError("No valid URL configured for this connection.")

        # ── Custom model: just verify connectivity, report row count ───
        if connection.sync_model == 'custom':
            result['rows_synced'] = len(raw_rows)
            result['status'] = 'success'
            result['duration_seconds'] = round(time.time() - start, 2)
            return result

        # ── Require field mapping for named models ─────────────────────
        from dashboard.models import Order, Product, Customer
        field_mapping = connection.field_mapping  # {sheet_header: django_field}

        if not field_mapping:
            raise ValueError(
                "No column mapping configured. Click Edit on this connection, "
                "enter the URL, click Test, map your columns, then Save."
            )

        MODEL_MAP    = {'orders': Order, 'products': Product, 'customers': Customer}
        UNIQUE_FIELD = {'orders': 'order_number', 'products': 'name', 'customers': 'phone'}

        model_cls    = MODEL_MAP.get(connection.sync_model)
        unique_field = UNIQUE_FIELD.get(connection.sync_model)

        if not model_cls:
            raise ValueError(f"Unsupported sync model: {connection.sync_model}")

        for raw_row in raw_rows:
            try:
                # Map sheet headers → django fields
                obj_data = {}
                for sheet_header, django_field in field_mapping.items():
                    val = str(raw_row.get(sheet_header, '') or '').strip()
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
