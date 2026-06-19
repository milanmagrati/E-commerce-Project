# -*- coding: utf-8 -*-
import re

with open('dashboard/templates/imports.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove the setup banner
content = re.sub(r'<!-- -- Credentials Warning Banner -- -->.*?{% endif %}', '', content, flags=re.DOTALL)

# Update the buttons
buttons_target = '''        <!-- Sync buttons -->
        <button class="btn-sync sync-to" onclick="triggerSync({{ conn.id }}, 'to_sheet')" title="Push Django ? Sheet">
          <span class="syncing-spinner" id="spinner-to-{{ conn.id }}"></span>
          <i class="fas fa-upload"></i> To Sheet
        </button>
        <button class="btn-sync sync-from" onclick="triggerSync({{ conn.id }}, 'from_sheet')" title="Pull Sheet ? Django">
          <span class="syncing-spinner" id="spinner-from-{{ conn.id }}"></span>
          <i class="fas fa-download"></i> From Sheet
        </button>
        <button class="btn-sync sync-both" onclick="triggerSync({{ conn.id }}, 'both')" title="Two-way sync">
          <span class="syncing-spinner" id="spinner-both-{{ conn.id }}"></span>
          <i class="fas fa-sync-alt"></i> Sync Both
        </button>'''

buttons_replacement = '''        <!-- Sync buttons -->
        {% if conn.connection_type == 'csv' %}
        <button class="btn-sync sync-from" onclick="triggerSync({{ conn.id }}, 'from_sheet')" title="Pull Sheet -> Django">
          <span class="syncing-spinner" id="spinner-from-{{ conn.id }}"></span>
          <i class="fas fa-download"></i> Pull Data
        </button>
        {% else %}
        <button class="btn-sync sync-to" onclick="triggerSync({{ conn.id }}, 'to_sheet')" title="Push Django -> Sheet">
          <span class="syncing-spinner" id="spinner-to-{{ conn.id }}"></span>
          <i class="fas fa-upload"></i> To Sheet
        </button>
        <button class="btn-sync sync-from" onclick="triggerSync({{ conn.id }}, 'from_sheet')" title="Pull Sheet -> Django">
          <span class="syncing-spinner" id="spinner-from-{{ conn.id }}"></span>
          <i class="fas fa-download"></i> From Sheet
        </button>
        <button class="btn-sync sync-both" onclick="triggerSync({{ conn.id }}, 'both')" title="Two-way sync">
          <span class="syncing-spinner" id="spinner-both-{{ conn.id }}"></span>
          <i class="fas fa-sync-alt"></i> Sync Both
        </button>
        {% endif %}'''
content = content.replace(buttons_target, buttons_replacement)

# Replace Step 1 wizard
wizard_target = '''        <!-- Step 1: URL Input -->
        <div class="wizard-panel active" id="wizardStep1">
          <div class="mb-3">
            <label class="form-label fw-600">Connection Name <span class="text-danger">*</span></label>
            <input type="text" class="form-control" id="wConnName" placeholder="e.g. Followup Sheet, Orders Export...">
          </div>
          <div class="mb-3">
            <label class="form-label fw-600">Google Sheet URL <span class="text-danger">*</span></label>
            <div class="input-group">
              <span class="input-group-text" style="background:#f0fdf4;border-color:#bbf7d0;">
                <i class="fas fa-link" style="color:#34a853;"></i>
              </span>
              <input type="url" class="form-control" id="wSheetUrl"
                placeholder="https://docs.google.com/spreadsheets/d/...">
            </div>
            <div class="form-text">Paste the full Google Sheet URL. Make sure you have shared it with the service account email.</div>
          </div>
          <div class="row g-3">
            <div class="col-md-6">
              <label class="form-label fw-600">Sheet / Tab Name</label>
              <input type="text" class="form-control" id="wSheetName" placeholder="Sheet1" value="Sheet1">
              <div id="sheetTabsSuggestions" class="d-flex flex-wrap gap-1 mt-1"></div>
            </div>
            <div class="col-md-6">
              <label class="form-label fw-600">Header Row</label>
              <input type="number" class="form-control" id="wHeaderRow" value="1" min="1">
            </div>
          </div>
          <div class="mt-3">
            <label class="form-label fw-600">Data Model to Sync</label>
            <select class="form-select" id="wSyncModel">
              <option value="orders">Orders</option>
              <option value="products">Products</option>
              <option value="customers">Customers</option>
              <option value="custom">Custom</option>
            </select>
          </div>
        </div>'''

wizard_replacement = '''        <!-- Step 1: URL Input -->
        <div class="wizard-panel active" id="wizardStep1">
          <div class="mb-3">
            <label class="form-label fw-600">Connection Name <span class="text-danger">*</span></label>
            <input type="text" class="form-control" id="wConnName" placeholder="e.g. Followup Sheet, Orders Export...">
          </div>
          <div class="mb-3">
            <label class="form-label fw-600">Connection Type <span class="text-danger">*</span></label>
            <select class="form-select" id="wConnType" onchange="toggleConnType()">
              <option value="csv">Published CSV URL (Read Only)</option>
              <option value="apps_script">Apps Script Web App (Two-Way Sync)</option>
            </select>
          </div>
          <div class="mb-3" id="wrapCsvUrl">
            <label class="form-label fw-600">Published CSV URL <span class="text-danger">*</span></label>
            <input type="url" class="form-control" id="wCsvUrl" placeholder="https://docs.google.com/spreadsheets/d/.../export?format=csv&amp;gid=0">
            <div class="form-text">File -> Share -> Publish to web -> Select CSV.</div>
          </div>
          <div class="mb-3" id="wrapAppsScriptUrl" style="display:none;">
            <label class="form-label fw-600">Apps Script Web App URL <span class="text-danger">*</span></label>
            <input type="url" class="form-control" id="wAppsScriptUrl" placeholder="https://script.google.com/macros/s/.../exec">
            <div class="form-text">Paste the Apps Script code in your sheet and deploy as Web App. <a href="#" onclick="showAppsScriptCode(event)">Show Code</a></div>
          </div>
          <div class="mb-3">
            <label class="form-label fw-600">Sheet URL (optional)</label>
            <input type="url" class="form-control" id="wSheetUrl" placeholder="Link to view the sheet">
          </div>
          <div class="mt-3">
            <label class="form-label fw-600">Data Model to Sync</label>
            <select class="form-select" id="wSyncModel">
              <option value="orders">Orders</option>
              <option value="products">Products</option>
              <option value="customers">Customers</option>
              <option value="custom">Custom</option>
            </select>
          </div>
        </div>'''
content = content.replace(wizard_target, wizard_replacement)

# Edit Connection Modal
edit_modal_target = '''        <div class="row g-3">
          <div class="col-md-6">
            <label class="form-label fw-semibold">Connection Name</label>
            <input type="text" class="form-control" id="editConnName">
          </div>
          <div class="col-md-6">
            <label class="form-label fw-semibold">Sheet / Tab Name</label>
            <input type="text" class="form-control" id="editSheetName">
          </div>
          <div class="col-12">
            <label class="form-label fw-semibold">Google Sheet URL</label>
            <input type="url" class="form-control" id="editSheetUrl">
          </div>
          <div class="col-md-6">
            <label class="form-label fw-semibold">Sync Model</label>
            <select class="form-select" id="editSyncModel">
              <option value="orders">Orders</option>
              <option value="products">Products</option>
              <option value="customers">Customers</option>
              <option value="custom">Custom</option>
            </select>
          </div>
          <div class="col-md-6">
            <label class="form-label fw-semibold">Header Row</label>
            <input type="number" class="form-control" id="editHeaderRow" min="1">
          </div>'''

edit_modal_replacement = '''        <div class="row g-3">
          <div class="col-12">
            <label class="form-label fw-semibold">Connection Name</label>
            <input type="text" class="form-control" id="editConnName">
          </div>
          <div class="col-12">
            <label class="form-label fw-semibold">Connection Type</label>
            <select class="form-select" id="editConnType">
              <option value="csv">Published CSV URL (Read Only)</option>
              <option value="apps_script">Apps Script Web App (Two-Way Sync)</option>
            </select>
          </div>
          <div class="col-12">
            <label class="form-label fw-semibold">CSV URL</label>
            <input type="url" class="form-control" id="editCsvUrl">
          </div>
          <div class="col-12">
            <label class="form-label fw-semibold">Apps Script Web App URL</label>
            <input type="url" class="form-control" id="editAppsScriptUrl">
          </div>
          <div class="col-12">
            <label class="form-label fw-semibold">Google Sheet URL</label>
            <input type="url" class="form-control" id="editSheetUrl">
          </div>
          <div class="col-md-12">
            <label class="form-label fw-semibold">Sync Model</label>
            <select class="form-select" id="editSyncModel">
              <option value="orders">Orders</option>
              <option value="products">Products</option>
              <option value="customers">Customers</option>
              <option value="custom">Custom</option>
            </select>
          </div>'''
content = content.replace(edit_modal_target, edit_modal_replacement)

js_target = '''// Reset wizard on modal open
document.getElementById('connectionWizardModal').addEventListener('show.bs.modal', () => {
  currentStep = 1;
  previewData = { rows: [], headers: [], spreadsheetId: '' };
  fieldMapping = {};
  updateWizardUI();
  ['wConnName','wSheetUrl','wSheetName','wNotes'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = el.placeholder === 'Sheet1' ? 'Sheet1' : '';
  });
  document.getElementById('wHeaderRow').value = '1';
  document.getElementById('wSyncModel').value = 'orders';
  document.getElementById('previewHead').innerHTML = '';
  document.getElementById('previewBody').innerHTML = '';
  document.getElementById('previewError').style.display = 'none';
  document.getElementById('previewEmpty').style.display = 'none';
  document.getElementById('sheetTabsSuggestions').innerHTML = '';
});'''

js_replacement = '''
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
  const t = document.getElementById('wConnType').value;
  document.getElementById('wrapCsvUrl').style.display = t === 'csv' ? 'block' : 'none';
  document.getElementById('wrapAppsScriptUrl').style.display = t === 'apps_script' ? 'block' : 'none';
}

// Rewritten Wizard Preview
async function fetchSheetPreview() {
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
  }
}

// Rewritten save Connection
async function saveConnection() {
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
}

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

document.getElementById('connectionWizardModal').addEventListener('show.bs.modal', () => {
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
});
'''

# We also need to fix buildReviewSummary which references wSheetName and wSheetUrl
# Let's replace the whole function using regex to be safe
content = re.sub(r'function buildReviewSummary\(\) \{.*?\n\}', '''function buildReviewSummary() {
  const name = document.getElementById('wConnName').value;
  const type = document.getElementById('wConnType').value;
  const model = document.getElementById('wSyncModel').value;
  const mapped = Object.keys(fieldMapping).length;

  document.getElementById('reviewSummary').innerHTML = 
    <div class="row g-2">
      <div class="col-6"><strong>Name:</strong> </div>
      <div class="col-6"><strong>Model:</strong> </div>
      <div class="col-6"><strong>Type:</strong> </div>
      <div class="col-6"><strong>Fields Mapped:</strong> </div>
    </div>
  ;
}''', content, flags=re.DOTALL)

with open('dashboard/templates/imports.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated template successfully.")
