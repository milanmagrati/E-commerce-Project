import re

file_path = r'c:\Users\milan\OneDrive\Desktop\E-commerce-Project\myproject\templates\dashboard\follow_ups.html'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Merge Headers
content = content.replace('<th style="min-width: 135px;">Followup 1</th>\n                            <th style="min-width: 135px;">Followup 2</th>', '<th style="min-width: 135px;">Follow up</th>')

# 2. Merge Inputs
content = content.replace('<td><input type="text" class="form-control form-control-sm" id="newFollowup1" placeholder="Followup 1"></td>\n                            <td><input type="text" class="form-control form-control-sm" id="newFollowup2" placeholder="Followup 2"></td>', '<td><input type="text" class="form-control form-control-sm" id="newFollowupNote" placeholder="Follow up note"></td>')

# 3. Merge Table Cells
old_cells = '''                            <!-- Followup 1 cell: shows latest log + a + button to add new note -->
                            <td class="fu-cell position-relative p-1" style="min-width:135px; vertical-align:top; background-color:#f8f9fa;">
                                <div class="fu-content">
                                    {% with log=item.latest_f1_log %}
                                    {% if log %}
                                    <div class="mb-1">
                                        <div class="d-inline-block border rounded bg-light px-2 py-1 mb-1 text-center shadow-sm" style="font-size:0.65rem;width:100%;">
                                            <div class="text-muted" style="font-size:0.6rem;">{{ log.timestamp|date:"M d, Y h:i A" }}</div>
                                            <div class="text-muted" style="font-size:0.6rem;">by {{ log.user.username|default:"System" }}</div>
                                        </div>
                                        <div class="text-dark text-break" style="font-size:0.75rem;">{{ log.new_value }}</div>
                                    </div>
                                    {% elif item.followup_1 %}
                                    <div class="text-dark text-break" style="font-size:0.75rem;">{{ item.followup_1 }}</div>
                                    {% else %}
                                    <span class="text-muted" style="font-size:0.75rem;">-</span>
                                    {% endif %}
                                    {% endwith %}
                                </div>
                                <button class="fu-add-btn" onclick="openAddNoteModal({{ item.id }}, \'followup_1\')" title="Add Followup 1 Note">
                                    <i class="fas fa-plus"></i>
                                </button>
                            </td>
                            <!-- Followup 2 cell -->
                            <td class="fu-cell position-relative p-1" style="min-width:135px; vertical-align:top; background-color:#f8f9fa;">
                                <div class="fu-content">
                                    {% with log=item.latest_f2_log %}
                                    {% if log %}
                                    <div class="mb-1">
                                        <div class="d-inline-block border rounded bg-light px-2 py-1 mb-1 text-center shadow-sm" style="font-size:0.65rem;width:100%;">
                                            <div class="text-muted" style="font-size:0.6rem;">{{ log.timestamp|date:"M d, Y h:i A" }}</div>
                                            <div class="text-muted" style="font-size:0.6rem;">by {{ log.user.username|default:"System" }}</div>
                                        </div>
                                        <div class="text-dark text-break" style="font-size:0.75rem;">{{ log.new_value }}</div>
                                    </div>
                                    {% elif item.followup_2 %}
                                    <div class="text-dark text-break" style="font-size:0.75rem;">{{ item.followup_2 }}</div>
                                    {% else %}
                                    <span class="text-muted" style="font-size:0.75rem;">-</span>
                                    {% endif %}
                                    {% endwith %}
                                </div>
                                <button class="fu-add-btn" onclick="openAddNoteModal({{ item.id }}, \'followup_2\')" title="Add Followup 2 Note">
                                    <i class="fas fa-plus"></i>
                                </button>
                            </td>'''

new_cell = '''                            <!-- Follow up cell -->
                            <td class="fu-cell position-relative p-1" style="min-width:135px; vertical-align:top; background-color:#f8f9fa;">
                                <div class="fu-content">
                                    {% with log=item.latest_followup_log %}
                                    {% if log %}
                                    <div class="mb-1">
                                        <div class="d-inline-block border rounded bg-light px-2 py-1 mb-1 text-center shadow-sm" style="font-size:0.65rem;width:100%;">
                                            <div class="text-muted" style="font-size:0.6rem;">{{ log.timestamp|date:"M d, Y h:i A" }}</div>
                                            <div class="text-muted" style="font-size:0.6rem;">by {{ log.user.username|default:"System" }}</div>
                                        </div>
                                        <div class="text-dark text-break" style="font-size:0.75rem;">{{ log.new_value }}</div>
                                    </div>
                                    {% else %}
                                    <span class="text-muted" style="font-size:0.75rem;">-</span>
                                    {% endif %}
                                    {% endwith %}
                                </div>
                                <button class="fu-add-btn" onclick="openAddNoteModal({{ item.id }})" title="Add Follow up Note">
                                    <i class="fas fa-plus"></i>
                                </button>
                            </td>'''

content = content.replace(old_cells, new_cell)

# 4. Modal Label
content = content.replace('<span class="badge bg-secondary" id="addNoteFieldLabel">Followup 1</span>', '<span class="badge bg-secondary" id="addNoteFieldLabel">Follow up</span>')

# 5. JS Add Modal fields
content = content.replace("function openAddNoteModal(id, field) {\n        currentNoteId = id;\n        currentNoteField = field; // 'followup_1' or 'followup_2'\n        document.getElementById('addNoteText').value = '';\n        \n        document.getElementById('addNoteFieldLabel').textContent = field === 'followup_1' ? 'Followup 1' : 'Followup 2';\n        addNoteModal.show();\n    }", "function openAddNoteModal(id) {\n        currentNoteId = id;\n        document.getElementById('addNoteText').value = '';\n        addNoteModal.show();\n    }")

# 6. JS saveNote() update
content = content.replace("followup_1: currentNoteField === 'followup_1' ? noteText : (row.getAttribute('data-f1') || ''),\n                followup_2: currentNoteField === 'followup_2' ? noteText : (row.getAttribute('data-f2') || ''),", "new_followup_note: noteText,")

content = content.replace("row.setAttribute('data-f1', result.data.followup_1 || '');\n                row.setAttribute('data-f2', result.data.followup_2 || '');\n                row.setAttribute('data-version', result.data.version || 1);\n\n                // Update the Followup cells (index 5 & 6) and Status cell (7)\n                const cells = row.querySelectorAll('td');\n                cells[5].innerHTML = buildFuCellHtml(result.data.f1_logs, result.data.followup_1, currentNoteId, 'followup_1');\n                cells[6].innerHTML = buildFuCellHtml(result.data.f2_logs, result.data.followup_2, currentNoteId, 'followup_2');\n                cells[7].innerHTML = `<span class=\"badge status-badge rounded-pill px-2 py-1 ${getStatusBadgeClass(result.data.status)}\" data-status-val=\"${(result.data.status||'').toLowerCase()}\">${result.data.status || '-'}</span>`;", "row.setAttribute('data-version', result.data.version || 1);\n\n                // Update the Followup cell (index 5) and Status cell (6)\n                const cells = row.querySelectorAll('td');\n                cells[5].innerHTML = buildFuCellHtml(result.data.all_logs, currentNoteId);\n                cells[6].innerHTML = `<span class=\"badge status-badge rounded-pill px-2 py-1 ${getStatusBadgeClass(result.data.status)}\" data-status-val=\"${(result.data.status||'').toLowerCase()}\">${result.data.status || '-'}</span>`;")

# 7. Sync loop data fetching
content = content.replace("row.setAttribute('data-f1', d.followup_1 || '');\n                            row.setAttribute('data-f2', d.followup_2 || '');\n                            row.setAttribute('data-remarks', d.remarks || '');\n\n                            const cells = row.querySelectorAll('td');\n                            if (cells.length > 7) {\n                                cells[1].textContent = d.name || '-';\n                                cells[2].textContent = d.phone || '';\n                                cells[3].textContent = d.lead_source || '-';\n                                cells[4].innerHTML = d.products?.length > 0 ? '<div class=\"d-flex flex-wrap\">' + d.products.map(p => `<span class=\"chip-badge me-1\">${escapeHtml(p.name)}</span>`).join('') + '</div>' : '<span class=\"text-muted\">-</span>';\n                                cells[5].innerHTML = buildFuCellHtml(d.f1_logs, d.followup_1, d.id, 'followup_1');\n                                cells[6].innerHTML = buildFuCellHtml(d.f2_logs, d.followup_2, d.id, 'followup_2');\n                                cells[7].innerHTML = `<span class=\"badge status-badge rounded-pill px-2 py-1 ${getStatusBadgeClass(d.status)}\" data-status-val=\"${(d.status||'').toLowerCase()}\">${d.status || '-'}</span>`;\n                            }", "row.setAttribute('data-remarks', d.remarks || '');\n\n                            const cells = row.querySelectorAll('td');\n                            if (cells.length > 6) {\n                                cells[1].textContent = d.name || '-';\n                                cells[2].textContent = d.phone || '';\n                                cells[3].textContent = d.lead_source || '-';\n                                cells[4].innerHTML = d.products?.length > 0 ? '<div class=\"d-flex flex-wrap\">' + d.products.map(p => `<span class=\"chip-badge me-1\">${escapeHtml(p.name)}</span>`).join('') + '</div>' : '<span class=\"text-muted\">-</span>';\n                                cells[5].innerHTML = buildFuCellHtml(d.all_logs, d.id);\n                                cells[6].innerHTML = `<span class=\"badge status-badge rounded-pill px-2 py-1 ${getStatusBadgeClass(d.status)}\" data-status-val=\"${(d.status||'').toLowerCase()}\">${d.status || '-'}</span>`;\n                            }")

# 8. buildFuCellHtml function update
content = content.replace("function buildFuCellHtml(logs, fallbackText, fuId, field) {\n        let contentHtml;\n        if (logs && logs.length > 0) {\n            const log = logs[0];\n            contentHtml = `\n                <div class=\"mb-1\">\n                    <div class=\"d-inline-block border rounded bg-light px-2 py-1 mb-1 text-center shadow-sm\" style=\"font-size:0.65rem;width:100%;\">\n                        <div class=\"text-muted\" style=\"font-size:0.6rem;\">${log.timestamp}</div>\n                        <div class=\"text-muted\" style=\"font-size:0.6rem;\">by ${escapeHtml(log.user)}</div>\n                    </div>\n                    <div class=\"text-dark text-break\" style=\"font-size:0.75rem;\">${escapeHtml(log.new_value)}</div>\n                </div>`;\n        } else if (fallbackText) {\n            contentHtml = `<div class=\"text-dark text-break\" style=\"font-size:0.75rem;\">${escapeHtml(fallbackText)}</div>`;\n        } else {\n            contentHtml = `<span class=\"text-muted\" style=\"font-size:0.75rem;\">-</span>`;\n        }\n        return `<div class=\"fu-content\">${contentHtml}</div>\n                <button class=\"fu-add-btn\" onclick=\"openAddNoteModal(${fuId}, '${field}')\" title=\"Add ${field === 'followup_1' ? 'Followup 1' : 'Followup 2'} Note\">\n                    <i class=\"fas fa-plus\"></i>\n                </button>`;\n    }", "function buildFuCellHtml(logs, fuId) {\n        let contentHtml;\n        if (logs && logs.length > 0) {\n            const log = logs[0];\n            contentHtml = `\n                <div class=\"mb-1\">\n                    <div class=\"d-inline-block border rounded bg-light px-2 py-1 mb-1 text-center shadow-sm\" style=\"font-size:0.65rem;width:100%;\">\n                        <div class=\"text-muted\" style=\"font-size:0.6rem;\">${log.timestamp}</div>\n                        <div class=\"text-muted\" style=\"font-size:0.6rem;\">by ${escapeHtml(log.user)}</div>\n                    </div>\n                    <div class=\"text-dark text-break\" style=\"font-size:0.75rem;\">${escapeHtml(log.new_value)}</div>\n                </div>`;\n        } else {\n            contentHtml = `<span class=\"text-muted\" style=\"font-size:0.75rem;\">-</span>`;\n        }\n        return `<div class=\"fu-content\">${contentHtml}</div>\n                <button class=\"fu-add-btn\" onclick=\"openAddNoteModal(${fuId})\" title=\"Add Follow up Note\">\n                    <i class=\"fas fa-plus\"></i>\n                </button>`;\n    }")

# 9. saveFollowUp logic (new entry row)
content = content.replace("followup_1: document.getElementById('newFollowup1').value.trim(),\n            followup_2: document.getElementById('newFollowup2').value.trim(),", "new_followup_note: document.getElementById('newFollowupNote').value.trim(),")
content = content.replace("['newName','newPhone','newFollowup1','newFollowup2','newRemarks'].forEach(id => document.getElementById(id).value = '');", "['newName','newPhone','newFollowupNote','newRemarks'].forEach(id => document.getElementById(id).value = '');")

content = content.replace("${buildFuCellHtml(data.f1_logs, data.followup_1, data.id, 'followup_1')}\n                ${buildFuCellHtml(data.f2_logs, data.followup_2, data.id, 'followup_2')}", "${buildFuCellHtml(data.all_logs, data.id)}")

# 10. remove extra currentNoteField definition
content = content.replace("let currentNoteField = 'followup_1'; // 'followup_1' or 'followup_2'\n", "")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('Success')
