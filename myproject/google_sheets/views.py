import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import services
from .models import GoogleSheetConnection, GoogleSheetSyncLog

logger = logging.getLogger(__name__)


# ─── Main Page ────────────────────────────────────────────────────────────────

@login_required
def google_imports_page(request):
    """Main Imports page — lists all connections with stats."""
    connections = GoogleSheetConnection.objects.all()
    active_count = connections.filter(is_active=True).count()
    total_syncs = GoogleSheetSyncLog.objects.filter(connection__in=connections).count()
    last_log = GoogleSheetSyncLog.objects.order_by('-synced_at').first()

    import json as _json
    return render(request, 'google_sheets/imports.html', {
        'connections': connections,
        'active_count': active_count,
        'total_syncs': total_syncs,
        'last_log': last_log,
        'apps_script_code': services.APPS_SCRIPT_CODE,
        'model_field_defs_json': _json.dumps(services.MODEL_FIELD_DEFS),
        'page_title': 'Imports — Google Sheets Sync',
    })


# ─── Connection CRUD ──────────────────────────────────────────────────────────

@login_required
@require_POST
def add_sheet_connection(request):
    """Create a new Google Sheet connection."""
    try:
        data = json.loads(request.body)
        conn = GoogleSheetConnection.objects.create(
            name=data.get('name', 'My Sheet').strip(),
            connection_type=data.get('connection_type', 'csv'),
            csv_url=data.get('csv_url', '').strip(),
            apps_script_url=data.get('apps_script_url', '').strip(),
            spreadsheet_url=data.get('spreadsheet_url', '').strip(),
            sheet_name=(data.get('sheet_name', 'Sheet1') or 'Sheet1').strip(),
            header_row=int(data.get('header_row', 1) or 1),
            sync_model=data.get('sync_model', 'orders'),
            field_mapping=data.get('field_mapping', {}),
            notes=data.get('notes', ''),
            is_active=True,
            created_by=request.user,
        )
        return JsonResponse({'success': True, 'id': conn.id, 'name': conn.name})
    except Exception as e:
        logger.error(f"add_sheet_connection error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def edit_sheet_connection(request, connection_id):
    """Update an existing connection."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    try:
        data = json.loads(request.body)
        conn.name = data.get('name', conn.name).strip()
        conn.connection_type = data.get('connection_type', conn.connection_type)
        conn.csv_url = data.get('csv_url', conn.csv_url).strip()
        conn.apps_script_url = data.get('apps_script_url', conn.apps_script_url).strip()
        conn.spreadsheet_url = data.get('spreadsheet_url', conn.spreadsheet_url).strip()
        conn.sheet_name = (data.get('sheet_name', conn.sheet_name) or 'Sheet1').strip()
        conn.header_row = int(data.get('header_row', conn.header_row) or 1)
        conn.sync_model = data.get('sync_model', conn.sync_model)
        conn.is_active = data.get('is_active', conn.is_active)
        conn.notes = data.get('notes', conn.notes)
        conn.save()
        return JsonResponse({'success': True, 'id': conn.id, 'name': conn.name})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def delete_sheet_connection(request, connection_id):
    """Delete a connection and its logs."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    conn.delete()
    return JsonResponse({'success': True})


@login_required
@require_GET
def get_connection_data(request, connection_id):
    """Return connection fields as JSON for the edit modal."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    return JsonResponse({
        'success': True,
        'connection': {
            'id': conn.id,
            'name': conn.name,
            'connection_type': conn.connection_type,
            'csv_url': conn.csv_url,
            'apps_script_url': conn.apps_script_url,
            'spreadsheet_url': conn.spreadsheet_url,
            'sheet_name': conn.sheet_name,
            'header_row': conn.header_row,
            'sync_model': conn.sync_model,
            'field_mapping': conn.field_mapping,
            'notes': conn.notes,
            'is_active': conn.is_active,
        }
    })


# ─── Test Connection ──────────────────────────────────────────────────────────

@login_required
@require_GET
def test_connection(request):
    """
    Quick URL validation — called before saving.
    Returns headers and row count on success.
    """
    connection_type = request.GET.get('connection_type', 'csv')
    url = request.GET.get('url', '').strip()

    if not url:
        return JsonResponse({'success': False, 'error': 'URL is required.'}, status=400)

    try:
        if connection_type == 'csv':
            # Must be a published CSV URL
            if '/edit' in url or ('/pub' not in url and 'output=csv' not in url and 'export' not in url):
                return JsonResponse({
                    'success': False,
                    'error': (
                        'That is a regular Google Sheet link. '
                        'Go to your sheet → File → Share → Publish to web → '
                        'choose "Comma-separated values (.csv)" → Publish → copy that link.'
                    )
                }, status=400)
            rows = services.preview_csv_url(url, num_rows=3)
            headers = rows[0] if rows else []
            return JsonResponse({
                'success': True,
                'headers': headers,
                'row_count': max(0, len(rows) - 1),
            })

        elif connection_type == 'apps_script':
            if 'script.google.com' not in url:
                return JsonResponse({
                    'success': False,
                    'error': (
                        'That is not an Apps Script URL. '
                        'It must start with https://script.google.com/macros/s/.../exec'
                    )
                }, status=400)

            import requests as _req
            resp = _req.get(url, timeout=15, allow_redirects=True)
            ct = resp.headers.get('content-type', '')

            if 'text/html' in ct or 'accounts.google.com' in resp.url:
                return JsonResponse({
                    'success': False,
                    'error': (
                        'Apps Script requires login. '
                        'Fix: In Apps Script → Deploy → Manage Deployments → Edit → '
                        'set "Who has access" to "Anyone" (not "Anyone with Google account") '
                        '→ Re-deploy and paste the new URL.'
                    )
                }, status=400)

            data = resp.json()
            if not data.get('success'):
                return JsonResponse({
                    'success': False,
                    'error': data.get('error', 'Apps Script returned an error.')
                }, status=400)

            headers = data.get('headers', [])
            rows = data.get('rows', [])
            return JsonResponse({
                'success': True,
                'headers': headers,
                'row_count': len(rows),
            })

        else:
            return JsonResponse({'success': False, 'error': 'Invalid connection type.'}, status=400)

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ─── Sync ─────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def trigger_sync(request, connection_id):
    """Run a sync operation. direction = to_sheet | from_sheet | both"""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    try:
        data = json.loads(request.body)
        direction = data.get('direction', 'from_sheet')
        if conn.connection_type == 'csv' and direction != 'from_sheet':
            return JsonResponse({'success': False, 'error': 'CSV connections are read-only.'}, status=400)
        result = services.run_sync(conn, direction=direction, user=request.user)
        return JsonResponse({
            'success': True,
            'result': result,
            'last_synced_ago': conn.get_last_sync_ago(),
        })
    except Exception as e:
        logger.error(f"trigger_sync error for connection {connection_id}: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ─── Data Preview Table ───────────────────────────────────────────────────────

@login_required
@require_GET
def get_sheet_data_table(request, connection_id):
    """Return paginated sheet data for the inline data table."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 50))
    try:
        if conn.connection_type == 'csv' and conn.csv_url:
            records = services.fetch_csv_from_url(conn.csv_url)
            headers = list(records[0].keys()) if records else []
            col_widths = []
            row_heights = []
        elif conn.connection_type == 'apps_script' and conn.apps_script_url:
            sheet_name = request.GET.get('sheet')
            apps_data = services.apps_script_read(conn.apps_script_url, sheet_name=sheet_name)
            records = apps_data['rows']
            headers = apps_data['headers']
            col_widths = apps_data.get('col_widths', [])
            row_heights = apps_data.get('row_heights', [])
        else:
            return JsonResponse({'success': False, 'error': 'No URL configured for this connection.'}, status=400)

        start = (page - 1) * per_page
        paginated = records[start:start + per_page]
        return JsonResponse({
            'success': True,
            'headers': headers,
            'rows': paginated,
            'col_widths': col_widths,
            'row_heights': row_heights,
            'total': len(records),
            'page': page,
            'per_page': per_page,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ─── Sync Logs ────────────────────────────────────────────────────────────────

@login_required
@require_GET
def sync_logs(request, connection_id):
    """Return recent sync logs for a connection."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    logs = conn.sync_logs.all()[:20]
    log_data = [{
        'id': log.id,
        'direction': log.get_direction_display(),
        'status': log.status,
        'rows_synced': log.rows_synced,
        'rows_failed': log.rows_failed,
        'rows_skipped': log.rows_skipped,
        'error_message': log.error_message,
        'synced_at': log.synced_at.strftime('%Y-%m-%d %H:%M:%S'),
        'triggered_by': log.triggered_by.get_full_name() if log.triggered_by else 'System',
    } for log in logs]
    return JsonResponse({'success': True, 'logs': log_data})


# ─── Full-Page Spreadsheet View ───────────────────────────────────────────────

@login_required
def view_connection_sheet(request, connection_id):
    """Renders the full-page editable spreadsheet interface."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    return render(request, 'google_sheets/sheet_view.html', {
        'conn': conn
    })

@login_required
@require_POST
def update_sheet_cell(request, connection_id):
    """AJAX endpoint to update a single cell in the Google Sheet."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    if conn.connection_type != 'apps_script' or not conn.apps_script_url:
        return JsonResponse({'success': False, 'error': 'Cell updates require an Apps Script connection.'}, status=400)
    
    try:
        data = json.loads(request.body)
        row = data.get('row')
        col = data.get('col')
        val = data.get('value')
        old_val = data.get('old_value', '')
        sheet_name = data.get('sheet')
        
        if row is None or col is None:
            return JsonResponse({'success': False, 'error': 'Row and col required.'}, status=400)
            
        # Call the existing service method
        payload = {'action': 'update_cell', 'row': int(row), 'col': int(col), 'value': val}
        if sheet_name:
            payload['sheet'] = sheet_name
            
        import requests
        resp = requests.post(conn.apps_script_url, json=payload, timeout=10)
        resp.raise_for_status()
        success = resp.json().get('success', False)
        
        return JsonResponse({'success': success})
    except Exception as e:
        logger.error(f"update_sheet_cell failed for connection {connection_id}: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def update_sheet_dimension(request, connection_id):
    """AJAX endpoint to update row height or column width in the Google Sheet."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    if conn.connection_type != 'apps_script' or not conn.apps_script_url:
        return JsonResponse({'success': False, 'error': 'Dimension updates require an Apps Script connection.'}, status=400)
    
    try:
        data = json.loads(request.body)
        dimension = data.get('dimension') # 'row' or 'column'
        index = data.get('index')
        size = data.get('size')

        services.apps_script_update_dimension(conn.apps_script_url, dimension, index, size)
        return JsonResponse({'success': True})
    except Exception as e:
        logger.error(f"update_sheet_dimension failed: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def update_sheet_structure(request, connection_id):
    """AJAX endpoint to insert/delete rows or columns in the Google Sheet."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    if conn.connection_type != 'apps_script' or not conn.apps_script_url:
        return JsonResponse({'success': False, 'error': 'Structure updates require an Apps Script connection.'}, status=400)
    
    try:
        data = json.loads(request.body)
        action = data.get('action') # 'insert_row', 'delete_row', 'insert_column', 'delete_column'
        index = data.get('index')

        services.apps_script_structure_action(conn.apps_script_url, action, index)
        return JsonResponse({'success': True})
    except Exception as e:
        logger.error(f"update_sheet_structure failed: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
