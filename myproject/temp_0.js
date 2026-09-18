
    const CSRF = document.querySelector('[name=csrfmiddlewaretoken]').value;
    const URLS = {
        create: '{% url "hrm:leave_application_create" %}',
        detail: (pk) => `{% url 'hrm:leave_application_detail' 0 %}`.replace('0', pk),
        update: (pk) => `{% url 'hrm:leave_application_update' 0 %}`.replace('0', pk),
        delete: (pk) => `{% url 'hrm:leave_application_delete' 0 %}`.replace('0', pk),
        status: (pk) => `{% url 'hrm:leave_application_update_status' 0 %}`.replace('0', pk),
    };
    let deletePk = null;

    function escapeHtml(unsafe) {
        if (unsafe === null || unsafe === undefined) return '';
        return unsafe.toString()
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // ── Toast ────────────────────────────────────────────────────────────────
    function showToast(msg, isError = false) {
        const el = document.getElementById('lmToast');
        const msgEl = document.getElementById('lmToastMsg');
        msgEl.textContent = msg;
        el.classList.toggle('error', isError);
        el.querySelector('.fa').className = isError ? 'fas fa-times-circle' : 'fas fa-check-circle';
        el.classList.add('show');
        setTimeout(() => el.classList.remove('show'), 4000);
    }

    // ── Per Page ─────────────────────────────────────────────────────────────
    function changePerPage(val) {
        const url = new URL(window.location.href);
        url.searchParams.set('per_page', val);
        url.searchParams.set('page', 1);
        window.location.href = url.toString();
    }

    // ── Days Preview ─────────────────────────────────────────────────────────
    function updateDaysPreview() {
        const s = document.getElementById('leaveStartDate').value;
        const e = document.getElementById('leaveEndDate').value;
        const el = document.getElementById('daysPreview');
        if (s && e) {
            const diff = Math.round((new Date(e) - new Date(s)) / 86400000) + 1;
            if (diff > 0) {
                el.textContent = diff + (diff === 1 ? ' day' : ' days');
                el.style.color = '#16a34a';
            } else {
                el.textContent = 'Invalid date range';
                el.style.color = '#ef4444';
            }
        } else {
            el.textContent = '— days';
        }
    }
    document.getElementById('leaveStartDate').addEventListener('change', updateDaysPreview);
    document.getElementById('leaveEndDate').addEventListener('change', updateDaysPreview);

    // ── File name display ────────────────────────────────────────────────────
    function updateFileName(input) {
        const display = document.getElementById('fileNameDisplay');
        display.textContent = input.files.length ? input.files[0].name : 'Select attachment file...';
    }

    // ── Open Add Modal ───────────────────────────────────────────────────────
    function openAddModal() {
        document.getElementById('leaveModalLabel').innerHTML = '<i class="fas fa-calendar-plus me-2"></i>Add New Leave Application';
        document.getElementById('leaveId').value = '';
        document.getElementById('leaveForm').reset();
        document.getElementById('daysPreview').textContent = '— days';
        document.getElementById('fileNameDisplay').textContent = 'Select attachment file...';
        new bootstrap.Modal(document.getElementById('leaveModal')).show();
    }

    // ── Edit ─────────────────────────────────────────────────────────────────
    function editApplication(pk) {
        fetch(URLS.detail(pk))
            .then(r => r.json())
            .then(data => {
                if (!data.success) { showToast(data.error, true); return; }
                const a = data.application;
                document.getElementById('leaveModalLabel').innerHTML = '<i class="fas fa-pen me-2"></i>Edit Leave Application';
                document.getElementById('leaveId').value = pk;
                document.getElementById('leaveEmployee').value = a.employee_id;
                document.getElementById('leaveType').value = a.leave_type_id;
                document.getElementById('leaveStartDate').value = a.start_date;
                document.getElementById('leaveEndDate').value = a.end_date;
                document.getElementById('leaveReason').value = a.reason;
                const statusSel = document.getElementById('leaveStatus');
                if (statusSel) statusSel.value = a.status;
                updateDaysPreview();
                new bootstrap.Modal(document.getElementById('leaveModal')).show();
            })
            .catch(() => showToast('Failed to load data.', true));
    }

    // ── Form Submit (Create / Update) ────────────────────────────────────────
    document.getElementById('leaveForm').addEventListener('submit', async function (e) {
        e.preventDefault();
        const pk = document.getElementById('leaveId').value.trim();
        const url = pk ? URLS.update(pk) : URLS.create;
        const formData = new FormData(this);

        const btn = document.getElementById('saveLeaveBtn');
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Saving...';

        try {
            const r = await fetch(url, { method: 'POST', headers: { 'X-CSRFToken': CSRF }, body: formData });
            const data = await r.json();

            if (data.success) {
                location.reload();
            } else {
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-save me-1"></i>Save';
                showToast(data.error || 'Failed to save.', true);
            }
        } catch (netErr) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-save me-1"></i>Save';
            showToast('Network error. Please try again.', true);
        }
    });

    // ── View ─────────────────────────────────────────────────────────────────
    function viewApplication(pk) {
        const body = document.getElementById('viewModalBody');
        body.innerHTML = '<div class="text-center py-4"><i class="fas fa-spinner fa-spin fa-2x" style="color:#22c55e;"></i></div>';
        new bootstrap.Modal(document.getElementById('viewModal')).show();

        fetch(URLS.detail(pk))
            .then(r => r.json())
            .then(data => {
                if (!data.success) { body.innerHTML = '<p class="text-danger text-center">Failed to load.</p>'; return; }
                const a = data.application;
                const statusClass = {
                    'approved': 'status-approved', 'pending': 'status-pending',
                    'rejected': 'status-rejected', 'cancelled': 'status-cancelled'
                }[a.status] || '';
                body.innerHTML = `
                    <div class="view-detail-row"><span class="view-detail-label">Employee</span><span class="view-detail-value"><strong>${escapeHtml(a.employee_name)}</strong> (${escapeHtml(a.employee_code)})</span></div>
                    <div class="view-detail-row"><span class="view-detail-label">Email</span><span class="view-detail-value">${escapeHtml(a.employee_email)}</span></div>
                    <div class="view-detail-row"><span class="view-detail-label">Leave Type</span><span class="view-detail-value">${escapeHtml(a.leave_type_name || '—')}</span></div>
                    <div class="view-detail-row"><span class="view-detail-label">Period</span><span class="view-detail-value">${escapeHtml(a.start_date)} → ${escapeHtml(a.end_date)} <strong>(${escapeHtml(a.days)} day${a.days !== 1 ? 's' : ''})</strong></span></div>
                    <div class="view-detail-row"><span class="view-detail-label">Status</span><span class="view-detail-value"><span class="status-badge ${escapeHtml(statusClass)}">${escapeHtml(a.status_display)}</span></span></div>
                    <div class="view-detail-row"><span class="view-detail-label">Reason</span><span class="view-detail-value">${escapeHtml(a.reason)}</span></div>
                    ${a.rejection_reason ? `<div class="view-detail-row"><span class="view-detail-label">Rejection</span><span class="view-detail-value text-danger">${escapeHtml(a.rejection_reason)}</span></div>` : ''}
                    <div class="view-detail-row"><span class="view-detail-label">Approved By</span><span class="view-detail-value">${escapeHtml(a.approved_by || '—')}</span></div>
                    <div class="view-detail-row"><span class="view-detail-label">Applied On</span><span class="view-detail-value">${escapeHtml(a.applied_on)}</span></div>
                    ${a.attachment_url ? `<div class="view-detail-row"><span class="view-detail-label">Attachment</span><span class="view-detail-value"><a href="${escapeHtml(a.attachment_url)}" target="_blank"><i class="fas fa-paperclip me-1"></i>View Attachment</a></span></div>` : ''}
                    <hr style="margin:.75rem 0;">
                    <div class="d-flex gap-2 flex-wrap mt-2">
                        ${a.status !== 'approved' ? `<button class="btn-save-modal btn-sm" style="font-size:.8rem;padding:.35rem .8rem;" onclick="updateStatus(${a.id},'approve')"><i class="fas fa-check me-1"></i>Approve</button>` : ''}
                        ${a.status !== 'rejected' ? `<button class="btn-save-modal btn-sm" style="font-size:.8rem;padding:.35rem .8rem;background:linear-gradient(135deg,#ef4444,#dc2626);" onclick="promptReject(${a.id})"><i class="fas fa-times me-1"></i>Reject</button>` : ''}
                        ${a.status !== 'cancelled' ? `<button class="btn-cancel-modal btn-sm" style="font-size:.8rem;padding:.35rem .8rem;" onclick="updateStatus(${a.id},'cancel')"><i class="fas fa-ban me-1"></i>Cancel</button>` : ''}
                    </div>
                `;
            })
            .catch(() => { body.innerHTML = '<p class="text-danger text-center">Network error.</p>'; });
    }

    function updateStatus(pk, action, rejectionReason = '') {
        const fd = new FormData();
        fd.append('action', action);
        if (rejectionReason) fd.append('rejection_reason', rejectionReason);
        fetch(URLS.status(pk), { method: 'POST', headers: { 'X-CSRFToken': CSRF }, body: fd })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    bootstrap.Modal.getInstance(document.getElementById('viewModal'))?.hide();
                    showToast(data.message);
                    setTimeout(() => location.reload(), 900);
                } else { showToast(data.error, true); }
            })
            .catch(() => showToast('Network error.', true));
    }

    function promptReject(pk) {
        const reason = prompt('Enter rejection reason:');
        if (reason === null) return;
        if (!reason.trim()) { showToast('Rejection reason is required.', true); return; }
        updateStatus(pk, 'reject', reason.trim());
    }

    // ── Delete ────────────────────────────────────────────────────────────────
    function deleteApplication(pk) {
        deletePk = pk;
        new bootstrap.Modal(document.getElementById('deleteModal')).show();
    }

    document.getElementById('confirmDeleteBtn').addEventListener('click', function () {
        if (!deletePk) return;
        fetch(URLS.delete(deletePk), { method: 'POST', headers: { 'X-CSRFToken': CSRF } })
            .then(r => r.json())
            .then(data => {
                bootstrap.Modal.getInstance(document.getElementById('deleteModal')).hide();
                if (data.success) {
                    showToast(data.message);
                    setTimeout(() => location.reload(), 900);
                } else { showToast(data.error, true); }
            })
            .catch(() => showToast('Network error.', true));
    });
