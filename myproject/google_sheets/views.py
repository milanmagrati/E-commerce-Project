import json
import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.utils import timezone

from .models import GoogleSheetConnection, GoogleSheetSyncLog
from . import services

logger = logging.getLogger(__name__)

# ─── Main Imports Page ────────────────────────────────────────────────────────

@login_required
def google_imports_page(request):
    """Main Imports page: list all Google Sheet connections."""
    connections = GoogleSheetConnection.objects.all()
    creds_ok = services.credentials_exist()

    # Annotate with last log
    for conn in connections:
        conn.last_log = conn.sync_logs.first()

    return render(request, 'imports.html', {
        'connections': connections,
        'creds_ok': creds_ok,
        'model_field_defs': services.MODEL_FIELD_DEFS,
        'apps_script_code': services.APPS_SCRIPT_CODE,
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
            name=data.get('name', 'My Sheet'),
            connection_type=data.get('connection_type', 'csv'),
            csv_url=data.get('csv_url', '').strip(),
            apps_script_url=data.get('apps_script_url', '').strip(),
            spreadsheet_url=data.get('spreadsheet_url', '').strip(),
            sync_model=data.get('sync_model', 'orders'),
            field_mapping=data.get('field_mapping', {}),
            notes=data.get('notes', ''),
            created_by=request.user,
        )
        return JsonResponse({'success': True, 'id': conn.id, 'name': conn.name})
    except Exception as e:
        logger.error(f"add_sheet_connection error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def edit_sheet_connection(request, connection_id):
    """Update an existing Google Sheet connection."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    try:
        data = json.loads(request.body)
        conn.name = data.get('name', conn.name)
        conn.connection_type = data.get('connection_type', conn.connection_type)
        conn.csv_url = data.get('csv_url', conn.csv_url)
        conn.apps_script_url = data.get('apps_script_url', conn.apps_script_url)
        conn.spreadsheet_url = data.get('spreadsheet_url', conn.spreadsheet_url)
        conn.sync_model = data.get('sync_model', conn.sync_model)
        conn.field_mapping = data.get('field_mapping', conn.field_mapping)
        conn.notes = data.get('notes', conn.notes)
        conn.save()
        return JsonResponse({'success': True, 'id': conn.id, 'name': conn.name})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def delete_sheet_connection(request, connection_id):
    """Delete a Google Sheet connection."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    conn.delete()
    return JsonResponse({'success': True})


# ─── Sync Endpoints ───────────────────────────────────────────────────────────

@login_required
@require_POST
def trigger_sync(request, connection_id):
    """Run a sync operation for a connection. direction = to_sheet|from_sheet|both"""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    try:
        data = json.loads(request.body)
        direction = data.get('direction', 'from_sheet')
        
        # If it's a CSV connection, it can only pull from sheet
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


# ─── Preview & Data Endpoints ─────────────────────────────────────────────────

@login_required
@require_GET
def preview_sheet_data(request, connection_id):
    """Preview rows of the sheet for column mapping."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    try:
        if conn.connection_type == 'csv' and conn.csv_url:
            rows = services.preview_csv_url(conn.csv_url, num_rows=6)
        elif conn.connection_type == 'apps_script' and conn.apps_script_url:
            raw_rows, headers = services.apps_script_read(conn.apps_script_url)
            # Reformat rows into list of lists for preview
            rows = [headers]
            for r in raw_rows[:5]:
                rows.append([r.get(h, '') for h in headers])
        else:
            return JsonResponse({'success': False, 'error': 'No URL configured'}, status=400)
            
        return JsonResponse({'success': True, 'rows': rows})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_GET
def preview_sheet_by_url(request):
    """Preview sheet data from a URL (used during connection wizard)."""
    connection_type = request.GET.get('connection_type', 'csv')
    url = request.GET.get('url', '')
    
    if not url:
        return JsonResponse({'success': False, 'error': 'URL required'}, status=400)
        
    try:
        if connection_type == 'csv':
            rows = services.preview_csv_url(url, num_rows=6)
        elif connection_type == 'apps_script':
            raw_rows, headers = services.apps_script_read(url)
            rows = [headers]
            for r in raw_rows[:5]:
                rows.append([r.get(h, '') for h in headers])
        else:
            return JsonResponse({'success': False, 'error': 'Invalid connection type'}, status=400)
            
        return JsonResponse({'success': True, 'rows': rows})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_GET
def get_sheet_data_table(request, connection_id):
    """Return paginated sheet data for the inline edit table."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 50))
    try:
        if conn.connection_type == 'csv' and conn.csv_url:
            records = services.fetch_csv_from_url(conn.csv_url)
            headers = list(records[0].keys()) if records else []
        elif conn.connection_type == 'apps_script' and conn.apps_script_url:
            records, headers = services.apps_script_read(conn.apps_script_url)
        else:
            return JsonResponse({'success': False, 'error': 'No URL configured'}, status=400)
            
        start = (page - 1) * per_page
        paginated = records[start:start + per_page]
        return JsonResponse({
            'success': True,
            'headers': headers,
            'rows': paginated,
            'total': len(records),
            'page': page,
            'field_mapping': conn.field_mapping,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def update_cell(request, connection_id):
    """Update a single cell in the sheet and optionally in Django DB."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    
    if conn.connection_type == 'csv':
        return JsonResponse({'success': False, 'error': 'CSV connections are read-only.'}, status=400)
        
    try:
        data = json.loads(request.body)
        row_number = int(data.get('row', 2))  # 1-indexed, +1 for header
        col_number = int(data.get('col', 1))
        value = data.get('value', '')
        update_db = data.get('update_db', False)
        unique_id = data.get('unique_id', None)
        field_name = data.get('field_name', None)

        # Update Google Sheet via Apps Script
        if conn.apps_script_url:
            services.apps_script_update_cell(conn.apps_script_url, row_number, col_number, value)

        # Optionally update Django DB
        db_updated = False
        if update_db and unique_id and field_name and conn.sync_model in ('orders', 'products', 'customers'):
            from dashboard.models import Order, Product, Customer
            MODEL_MAP = {'orders': Order, 'products': Product, 'customers': Customer}
            UNIQUE_FIELD = {'orders': 'order_number', 'products': 'name', 'customers': 'phone'}
            model_cls = MODEL_MAP[conn.sync_model]
            unique_key = UNIQUE_FIELD[conn.sync_model]
            try:
                obj = model_cls.objects.get(**{unique_key: unique_id})
                if hasattr(obj, field_name):
                    setattr(obj, field_name, value)
                    obj.save(update_fields=[field_name])
                    db_updated = True
            except model_cls.DoesNotExist:
                pass

        return JsonResponse({'success': True, 'db_updated': db_updated})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ─── Sync Logs ────────────────────────────────────────────────────────────────

@login_required
@require_GET
def sync_logs(request, connection_id):
    """Return recent sync logs for a connection."""
    conn = get_object_or_404(GoogleSheetConnection, id=connection_id)
    logs = conn.sync_logs.all()[:20]
    log_data = []
    for log in logs:
        log_data.append({
            'id': log.id,
            'direction': log.get_direction_display(),
            'status': log.status,
            'rows_synced': log.rows_synced,
            'rows_failed': log.rows_failed,
            'rows_skipped': log.rows_skipped,
            'error_message': log.error_message,
            'synced_at': log.synced_at.strftime('%Y-%m-%d %H:%M:%S'),
            'triggered_by': log.triggered_by.get_full_name() if log.triggered_by else 'System',
        })
    return JsonResponse({'success': True, 'logs': log_data})


@login_required
@require_GET
def get_connection_data(request, connection_id):
    """Return connection details as JSON (for edit modal)."""
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
            'sync_model': conn.sync_model,
            'field_mapping': conn.field_mapping,
            'notes': conn.notes,
        }
    })
