# -*- coding: utf-8 -*-
import re

with open('dashboard/templates/imports.html', 'r', encoding='utf-8') as f:
    content = f.read()

js_to_add = '''
// Add Apps Script Code Modal to body
document.body.insertAdjacentHTML('beforeend', 
<div class="modal fade" id="appsScriptModal" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <div class="modal-content">
      <div class="modal-header bg-dark text-white">
        <h5 class="modal-title">Apps Script Code</h5>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <pre><code style="font-size:0.8rem;">{{ apps_script_code|safe }}</code></pre>
      </div>
    </div>
  </div>
</div>
);

function showAppsScriptCode(e) {
  e.preventDefault();
  new bootstrap.Modal(document.getElementById('appsScriptModal')).show();
}

function toggleConnType() {
  const t = document.getElementById('wConnType');
  if (!t) return;
  const val = t.value;
  const wrapCsv = document.getElementById('wrapCsvUrl');
  const wrapApps = document.getElementById('wrapAppsScriptUrl');
  if (wrapCsv) wrapCsv.style.display = val === 'csv' ? 'block' : 'none';
  if (wrapApps) wrapApps.style.display = val === 'apps_script' ? 'block' : 'none';
}

function toggleEditConnType() {
  const t = document.getElementById('editConnType');
  if (!t) return;
  const val = t.value;
  const wrapCsv = document.getElementById('editWrapCsvUrl');
  const wrapApps = document.getElementById('editWrapAppsScriptUrl');
  if (wrapCsv) wrapCsv.style.display = val === 'csv' ? 'block' : 'none';
  if (wrapApps) wrapApps.style.display = val === 'apps_script' ? 'block' : 'none';
}
'''

if 'function toggleConnType()' not in content:
    content = content.replace('function buildReviewSummary()', js_to_add + '\nfunction buildReviewSummary()')

# Rewrite saveConnection
save_conn_target = re.search(r'async function saveConnection\(\) \{.*?\n\}', content, flags=re.DOTALL)
if save_conn_target:
    new_save_conn = '''async function saveConnection() {
  collectFieldMapping();
  const payload = {
    name: document.getElementById('wConnName').value.trim(),
    connection_type: document.getElementById('wConnType').value,
    csv_url: document.getElementById('wCsvUrl').value.trim(),
    apps_script_url: document.getElementById('wAppsScriptUrl').value.trim(),
    spreadsheet_url: document.getElementById('wSheetUrl').value.trim(),
    sync_model: document.getElementById('wSyncModel').value,
    field_mapping: fieldMapping,
    notes: document.getElementById('wNotes').value.trim(),
  };

  if (!payload.name) { showToast('Please enter a name.', 'warning'); return; }

  try {
    const resp = await fetch('/imports/connections/add/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
      body: JSON.stringify(payload)
    });
    const data = await resp.json();
    if (data.success) {
      bootstrap.Modal.getInstance(document.getElementById('connectionWizardModal')).hide();
      showToast('? Connection saved! Reloading...');
      setTimeout(() => location.reload(), 1200);
    } else {
      showToast(data.error || 'Failed to save.', 'error');
    }
  } catch (e) {
    showToast('Network error.', 'error');
  }
}'''
    content = content.replace(save_conn_target.group(0), new_save_conn)

# Rewrite fetchSheetPreview
fetch_preview_target = re.search(r'async function fetchSheetPreview\(\) \{.*?\} catch \(e\) \{.*?\}', content, flags=re.DOTALL)
if fetch_preview_target:
    new_fetch_preview = '''async function fetchSheetPreview() {
  const type = document.getElementById('wConnType').value;
  const url = type === 'csv' ? document.getElementById('wCsvUrl').value.trim() : document.getElementById('wAppsScriptUrl').value.trim();
  
  const loading = document.getElementById('previewLoading');
  const errorEl = document.getElementById('previewError');
  const emptyEl = document.getElementById('previewEmpty');

  loading.style.display = 'block';
  errorEl.style.display = 'none';
  emptyEl.style.display = 'none';

  try {
    const resp = await fetch(/imports/preview-by-url/?connection_type=&url=);
    const data = await resp.json();
    loading.style.display = 'none';

    if (!data.success) {
      errorEl.textContent = '? ' + (data.error || 'Failed to fetch preview.');
      errorEl.style.display = 'block';
      return;
    }

    previewData = { rows: data.rows || [], headers: data.rows[0] || [] };
    buildPreviewTable(previewData.rows);
  } catch (e) {
    loading.style.display = 'none';
    errorEl.textContent = '? Network error.';
    errorEl.style.display = 'block';
  }'''
    content = content.replace(fetch_preview_target.group(0), new_fetch_preview)


# Edit Modal JS
# Need to replace openEditModal and saveEditConnection
edit_js = '''
async function openEditModal(connId) {
  try {
    const resp = await fetch(/imports/connections//get/);
    const data = await resp.json();
    if (!data.success) { showToast('Failed to load connection data.', 'error'); return; }
    const c = data.connection;
    document.getElementById('editConnId').value = c.id;
    document.getElementById('editConnName').value = c.name;
    document.getElementById('editConnType').value = c.connection_type;
    document.getElementById('editCsvUrl').value = c.csv_url || '';
    document.getElementById('editAppsScriptUrl').value = c.apps_script_url || '';
    document.getElementById('editSheetUrl').value = c.spreadsheet_url || '';
    document.getElementById('editSyncModel').value = c.sync_model;
    
    toggleEditConnType();
    new bootstrap.Modal(document.getElementById('editConnectionModal')).show();
  } catch (e) {
    showToast('Failed to open edit modal.', 'error');
  }
}

async function saveEditConnection() {
  const connId = document.getElementById('editConnId').value;
  const payload = {
    name: document.getElementById('editConnName').value.trim(),
    connection_type: document.getElementById('editConnType').value,
    csv_url: document.getElementById('editCsvUrl').value.trim(),
    apps_script_url: document.getElementById('editAppsScriptUrl').value.trim(),
    spreadsheet_url: document.getElementById('editSheetUrl').value.trim(),
    sync_model: document.getElementById('editSyncModel').value,
  };
  try {
    const resp = await fetch(/imports/connections//edit/, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
      body: JSON.stringify(payload)
    });
    const data = await resp.json();
    if (data.success) {
      bootstrap.Modal.getInstance(document.getElementById('editConnectionModal')).hide();
      showToast('Connection updated! Refreshing...');
      setTimeout(() => location.reload(), 1200);
    } else {
      showToast(data.error || 'Update failed.', 'error');
    }
  } catch (e) {
    showToast('Network error.', 'error');
  }
}
'''
if 'async function openEditModal' not in content:
    content = content.replace('// --- Delete Modal ---', edit_js + '\n// --- Delete Modal ---')
else:
    # Replace existing
    content = re.sub(r'async function openEditModal\(connId\) \{.*?\n\}', '', content, flags=re.DOTALL)
    content = re.sub(r'async function saveEditConnection\(\) \{.*?\n\}', '', content, flags=re.DOTALL)
    content = content.replace('// --- Delete Modal ---', edit_js + '\n// --- Delete Modal ---')

# Reset wizard
wizard_target = re.search(r"document\.getElementById\('connectionWizardModal'\)\.addEventListener\('show\.bs\.modal', \(\) => \{.*?\n\}\);", content, flags=re.DOTALL)
if wizard_target:
    new_wizard = '''document.getElementById('connectionWizardModal').addEventListener('show.bs.modal', () => {
  currentStep = 1;
  previewData = { rows: [], headers: [], spreadsheetId: '' };
  fieldMapping = {};
  updateWizardUI();
  ['wConnName','wCsvUrl','wAppsScriptUrl','wSheetUrl','wNotes'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  document.getElementById('wConnType').value = 'csv';
  toggleConnType();
  document.getElementById('wSyncModel').value = 'orders';
  document.getElementById('previewHead').innerHTML = '';
  document.getElementById('previewBody').innerHTML = '';
  document.getElementById('previewError').style.display = 'none';
  document.getElementById('previewEmpty').style.display = 'none';
});'''
    content = content.replace(wizard_target.group(0), new_wizard)

# Make sure edit modal HTML has wrapping for toggle
content = content.replace('id="editCsvUrl"', 'id="editCsvUrl"')
# The earlier python script already replaced the edit modal HTML, let's wrap it here if it's missing
content = re.sub(r'<div class="col-12">\s*<label class="form-label fw-semibold">CSV URL</label>', '<div class="col-12" id="editWrapCsvUrl">\n            <label class="form-label fw-semibold">CSV URL</label>', content)
content = re.sub(r'<div class="col-12">\s*<label class="form-label fw-semibold">Apps Script Web App URL</label>', '<div class="col-12" id="editWrapAppsScriptUrl" style="display:none;">\n            <label class="form-label fw-semibold">Apps Script Web App URL</label>', content)
content = content.replace('id="editConnType"', 'id="editConnType" onchange="toggleEditConnType()"')


with open('dashboard/templates/imports.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("JS FIXED!")
