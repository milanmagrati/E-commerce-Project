/* ============================================================================
   Setup → Product Page Theme: the editing surface.

   Three fields on this screen are really lists of rows — videos, how-to steps
   and key ingredients — and they are stored as one `a | b | c` line each. That
   format is what the storefront parses and it is worth keeping: it survives a
   copy-paste, a per-product override and a database dump. What is not worth
   keeping is making a shop owner *type* it.

   So the textarea stays the single source of truth, hidden, and a repeater is
   drawn on top of it. Every edit in the repeater rewrites the textarea, which
   is what the plain form post then carries — no new endpoint, no new save
   path, and the raw text is one click away for anyone who wants it.

   Media works the same way: photos and clips are uploaded here and what lands
   in the field is the file's URL, so a pasted CDN link is still perfectly
   valid input.
   ========================================================================= */

(function () {
    'use strict';

    var CFG = window.PT_SETUP || {};

    function $(sel, root) { return (root || document).querySelector(sel); }
    function $$(sel, root) {
        return Array.prototype.slice.call((root || document).querySelectorAll(sel));
    }

    function el(tag, cls, text) {
        var node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    /* One row is `a | b | c`. Cells are padded on read so a trailing field can
       be left out, and trimmed on write so a row of empties is not stored as a
       row of pipes. */
    function splitRow(line, count) {
        var parts = String(line || '').split('|').map(function (p) { return p.trim(); });
        while (parts.length < count) parts.push('');
        return parts.slice(0, count);
    }

    function joinRow(values) {
        var out = values.slice();
        while (out.length && out[out.length - 1] === '') out.pop();
        return out.join(' | ');
    }

    /* ── the shared media picker ──────────────────────────────────────── */

    var picker = {
        node: null,
        grid: null,
        status: null,
        target: null,   // the input to write the chosen URL into
        kind: ''        // 'image' | 'video' | ''
    };

    function pickerBuild() {
        if (picker.node) return;

        var wrap = el('div', 'ptm');
        wrap.innerHTML =
            '<div class="ptm-veil" data-ptm-close></div>' +
            '<div class="ptm-panel">' +
            '  <div class="ptm-head">' +
            '    <h3 data-ptm-title>Choose a file</h3>' +
            '    <button type="button" class="pt-x" data-ptm-close aria-label="Close">&times;</button>' +
            '  </div>' +
            '  <div class="ptm-drop" data-ptm-drop>' +
            '    <strong>Drop a file here, or</strong>' +
            '    <label class="pt-btn is-primary">' +
            '      Choose from your computer' +
            '      <input type="file" data-ptm-file hidden>' +
            '    </label>' +
            '    <span class="ptm-limits" data-ptm-limits></span>' +
            '  </div>' +
            '  <p class="ptm-status" data-ptm-status hidden></p>' +
            '  <div class="ptm-grid" data-ptm-grid></div>' +
            '</div>';
        document.body.appendChild(wrap);

        picker.node = wrap;
        picker.grid = $('[data-ptm-grid]', wrap);
        picker.status = $('[data-ptm-status]', wrap);

        $$('[data-ptm-close]', wrap).forEach(function (n) {
            n.addEventListener('click', pickerClose);
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && wrap.classList.contains('is-open')) pickerClose();
        });

        var file = $('[data-ptm-file]', wrap);
        file.addEventListener('change', function () {
            if (file.files && file.files[0]) upload(file.files[0]);
            file.value = '';
        });

        var drop = $('[data-ptm-drop]', wrap);
        ['dragenter', 'dragover'].forEach(function (type) {
            drop.addEventListener(type, function (e) {
                e.preventDefault();
                drop.classList.add('is-over');
            });
        });
        ['dragleave', 'drop'].forEach(function (type) {
            drop.addEventListener(type, function (e) {
                e.preventDefault();
                drop.classList.remove('is-over');
            });
        });
        drop.addEventListener('drop', function (e) {
            var dropped = e.dataTransfer && e.dataTransfer.files;
            if (dropped && dropped[0]) upload(dropped[0]);
        });
    }

    function pickerStatus(message, isError) {
        if (!picker.status) return;
        picker.status.textContent = message || '';
        picker.status.hidden = !message;
        picker.status.classList.toggle('is-error', !!isError);
    }

    function upload(file) {
        pickerStatus('Uploading ' + file.name + '…', false);
        var body = new FormData();
        body.append('file', file);
        fetch(CFG.uploadUrl, {
            method: 'POST',
            headers: {'X-CSRFToken': CFG.csrf, 'X-Requested-With': 'XMLHttpRequest'},
            body: body,
            credentials: 'same-origin'
        }).then(function (res) {
            return res.json().catch(function () { return {}; });
        }).then(function (payload) {
            if (!payload.success) {
                pickerStatus(payload.message || 'That upload did not work.', true);
                return;
            }
            pickerStatus('Uploaded.', false);
            // Straight into the field that opened the picker: uploading a file
            // and then having to pick it again is a step that earns nothing.
            if (picker.target) {
                setMediaValue(picker.target, payload.media.url);
                pickerClose();
            }
            pickerLoad();
        }).catch(function () {
            pickerStatus('Could not reach the server.', true);
        });
    }

    function pickerLoad() {
        if (!picker.grid) return;
        var url = CFG.listUrl + (picker.kind ? '?kind=' + picker.kind : '');
        fetch(url, {credentials: 'same-origin'})
            .then(function (r) { return r.json(); })
            .then(function (data) { pickerRender(data.media || []); })
            .catch(function () { pickerRender([]); });
    }

    function pickerRender(rows) {
        picker.grid.innerHTML = '';
        if (!rows.length) {
            picker.grid.appendChild(el('p', 'ptm-empty', 'Nothing uploaded yet.'));
            return;
        }
        rows.forEach(function (row) {
            var card = el('div', 'ptm-card');
            var preview;
            if (row.kind === 'video') {
                preview = document.createElement('video');
                preview.src = row.url;
                preview.muted = true;
                preview.playsInline = true;
                preview.preload = 'metadata';
            } else {
                preview = document.createElement('img');
                preview.src = row.url;
                preview.loading = 'lazy';
                preview.alt = '';
            }
            card.appendChild(preview);

            var name = el('span', 'ptm-name', row.title);
            name.title = row.title + ' · ' + row.size;
            card.appendChild(name);

            card.addEventListener('click', function () {
                if (picker.target) setMediaValue(picker.target, row.url);
                pickerClose();
            });

            var remove = el('button', 'ptm-del', '×');
            remove.type = 'button';
            remove.title = 'Delete this file';
            remove.addEventListener('click', function (event) {
                event.stopPropagation();
                if (!window.confirm('Delete ' + row.title + '? Anything still pointing at it will lose its image.')) return;
                fetch(CFG.deleteUrlBase.replace(/0\/delete\/$/, row.id + '/delete/'), {
                    method: 'POST',
                    headers: {'X-CSRFToken': CFG.csrf, 'X-Requested-With': 'XMLHttpRequest'},
                    credentials: 'same-origin'
                }).then(pickerLoad);
            });
            card.appendChild(remove);

            picker.grid.appendChild(card);
        });
    }

    function pickerOpen(target, kind, title) {
        pickerBuild();
        picker.target = target;
        picker.kind = kind || '';
        $('[data-ptm-title]', picker.node).textContent = title || 'Choose a file';
        $('[data-ptm-limits]', picker.node).textContent =
            picker.kind === 'video'
                ? 'MP4, WebM or MOV, up to 64 MB. Vertical 9:16 looks best.'
                : 'JPG, PNG, WebP, GIF or AVIF, up to 8 MB.';
        $('[data-ptm-file]', picker.node).setAttribute(
            'accept', picker.kind === 'video' ? 'video/*' : 'image/*');
        pickerStatus('', false);
        picker.node.classList.add('is-open');
        pickerLoad();
    }

    function pickerClose() {
        if (picker.node) picker.node.classList.remove('is-open');
        picker.target = null;
    }

    /* ── a media field: a URL box, a preview, and two buttons ─────────── */

    function setMediaValue(input, url) {
        input.value = url;
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
    }

    function mediaField(kind, label, value, onChange) {
        var wrap = el('div', 'ptf');
        var thumb = el('div', 'ptf-thumb');
        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'ptf-url';
        input.placeholder = label + ' — upload, or paste a URL';
        input.value = value || '';

        function paint() {
            thumb.innerHTML = '';
            if (!input.value) {
                thumb.classList.add('is-empty');
                return;
            }
            thumb.classList.remove('is-empty');
            var node;
            if (kind === 'video') {
                node = document.createElement('video');
                node.src = input.value;
                node.muted = true;
                node.preload = 'metadata';
            } else {
                node = document.createElement('img');
                node.src = input.value;
                node.alt = '';
            }
            thumb.appendChild(node);
        }

        input.addEventListener('input', function () {
            paint();
            if (onChange) onChange();
        });

        var pick = el('button', 'pt-btn ptf-btn', 'Upload');
        pick.type = 'button';
        pick.addEventListener('click', function () {
            pickerOpen(input, kind, 'Choose a ' + (kind === 'video' ? 'clip' : 'photo'));
        });

        var clear = el('button', 'pt-btn ptf-btn', 'Clear');
        clear.type = 'button';
        clear.addEventListener('click', function () {
            input.value = '';
            paint();
            if (onChange) onChange();
        });

        wrap.appendChild(thumb);
        wrap.appendChild(input);
        wrap.appendChild(pick);
        wrap.appendChild(clear);
        paint();
        return wrap;
    }

    /* ── the repeater ─────────────────────────────────────────────────── */

    var SPECS = {
        videos: {
            title: 'Clips',
            addLabel: 'Add a clip',
            empty: 'No clips yet. The “See it in action” strip is hidden until you add one.',
            columns: [
                {key: 'video', type: 'media', kind: 'video', label: 'Clip'},
                {key: 'poster', type: 'media', kind: 'image', label: 'Poster (optional)'},
                {key: 'creator', type: 'flag', label: 'Show the CREATOR badge', value: 'creator'},
                // Trailing on purpose: every row written before these existed
                // still parses to exactly the card it always drew.
                {key: 'title', type: 'text', label: 'Title (optional)',
                 placeholder: 'How it looks on camera'},
                {key: 'note', type: 'text', label: 'Description (optional)',
                 placeholder: 'A line about what this clip shows'}
            ]
        },
        info_media: {
            title: 'Description gallery',
            addLabel: 'Add a photo',
            empty: 'No photos yet. These show inside the Product information block.',
            columns: [
                {key: 'image', type: 'media', kind: 'image', label: 'Photo'},
                {key: 'heading', type: 'text', label: 'Heading (optional)',
                 placeholder: 'Tried and trusted by'},
                {key: 'text', type: 'text', label: 'Description (optional)',
                 placeholder: 'Designed for thick hair growth'}
            ]
        },
        features: {
            title: 'Features',
            addLabel: 'Add a feature',
            empty: 'No features yet.',
            columns: [
                {key: 'title', type: 'text', label: 'Feature', placeholder: '84% natural ingredients'},
                {key: 'text', type: 'text', label: 'Description (optional)',
                 placeholder: 'Liquorice extract, fuller’s earth, rice powder'}
            ]
        },
        before_after: {
            title: 'Pairs',
            addLabel: 'Add a before / after pair',
            empty: 'No pairs yet. A pair needs both halves before it will render.',
            columns: [
                {key: 'before', type: 'media', kind: 'image', label: 'Before'},
                {key: 'after', type: 'media', kind: 'image', label: 'After'},
                {key: 'caption', type: 'text', label: 'Caption (optional)',
                 placeholder: 'Four weeks apart, same lighting'}
            ]
        },
        steps: {
            title: 'Steps',
            addLabel: 'Add a step',
            empty: 'No steps yet. Numbers are generated from the order of these rows.',
            columns: [
                {key: 'title', type: 'text', label: 'Title', placeholder: 'Mix'},
                {key: 'instruction', type: 'text', label: 'Instruction',
                 placeholder: 'Add water gradually to form a smooth, thick paste'},
                {key: 'image', type: 'media', kind: 'image', label: 'Image (optional)'}
            ]
        },
        ingredients: {
            title: 'Ingredients',
            addLabel: 'Add an ingredient',
            empty: 'No ingredients yet.',
            columns: [
                {key: 'name', type: 'text', label: 'Name', placeholder: 'Aloe vera'},
                {key: 'image', type: 'media', kind: 'image', label: 'Image'},
                {key: 'note', type: 'text', label: 'Short note', placeholder: 'Soothes and cools'}
            ]
        }
    };

    function buildRepeater(textarea, spec) {
        if (!textarea || textarea.dataset.ptReady === '1') return;
        textarea.dataset.ptReady = '1';

        var host = el('div', 'ptr');
        var list = el('div', 'ptr-rows');
        host.appendChild(list);

        var foot = el('div', 'ptr-foot');
        var add = el('button', 'pt-btn', '+ ' + spec.addLabel);
        add.type = 'button';
        foot.appendChild(add);

        var raw = el('button', 'pt-btn ptr-raw', 'Edit as text');
        raw.type = 'button';
        foot.appendChild(raw);
        host.appendChild(foot);

        textarea.parentNode.insertBefore(host, textarea);
        textarea.classList.add('ptr-source');
        textarea.hidden = true;

        function commit() {
            var out = [];
            $$('.ptr-row', list).forEach(function (row) {
                var values = spec.columns.map(function (col) {
                    var input = $('[data-col="' + col.key + '"]', row);
                    if (!input) return '';
                    if (col.type === 'flag') return input.checked ? col.value : '';
                    // A pipe or a newline inside a cell would silently split the
                    // row somewhere the author did not intend.
                    return input.value.replace(/[|\r\n]+/g, ' ').trim();
                });
                if (values.join('')) out.push(joinRow(values));
            });
            textarea.value = out.join('\n');
            var placeholder = $('.ptr-empty', list);
            if (placeholder) placeholder.remove();
            if (!$$('.ptr-row', list).length) list.appendChild(el('p', 'ptr-empty', spec.empty));
        }

        function addRow(values) {
            var placeholder = $('.ptr-empty', list);
            if (placeholder) placeholder.remove();

            var row = el('div', 'ptr-row');
            var handle = el('span', 'ptr-n', String($$('.ptr-row', list).length + 1));
            row.appendChild(handle);

            var body = el('div', 'ptr-body');
            spec.columns.forEach(function (col, i) {
                var cell = el('div', 'ptr-cell');
                if (col.type === 'media') {
                    var field = mediaField(col.kind, col.label, values[i], commit);
                    $('.ptf-url', field).setAttribute('data-col', col.key);
                    cell.appendChild(field);
                } else if (col.type === 'flag') {
                    var wrap = el('label', 'ptr-flag');
                    var box = document.createElement('input');
                    box.type = 'checkbox';
                    box.setAttribute('data-col', col.key);
                    box.checked = (values[i] || '').toLowerCase() === col.value;
                    box.addEventListener('change', commit);
                    wrap.appendChild(box);
                    wrap.appendChild(el('span', null, col.label));
                    cell.appendChild(wrap);
                } else {
                    var input = document.createElement('input');
                    input.type = 'text';
                    input.setAttribute('data-col', col.key);
                    input.placeholder = col.placeholder || col.label;
                    input.value = values[i] || '';
                    input.addEventListener('input', commit);
                    cell.appendChild(input);
                }
                body.appendChild(cell);
            });
            row.appendChild(body);

            var tools = el('div', 'ptr-tools');
            [['↑', -1], ['↓', 1]].forEach(function (pair) {
                var button = el('button', 'ptr-move', pair[0]);
                button.type = 'button';
                button.title = pair[1] < 0 ? 'Move up' : 'Move down';
                button.addEventListener('click', function () {
                    var sibling = pair[1] < 0 ? row.previousElementSibling : row.nextElementSibling;
                    if (!sibling || !sibling.classList.contains('ptr-row')) return;
                    if (pair[1] < 0) list.insertBefore(row, sibling);
                    else list.insertBefore(sibling, row);
                    renumber();
                    commit();
                });
                tools.appendChild(button);
            });
            var remove = el('button', 'ptr-move is-del', '×');
            remove.type = 'button';
            remove.title = 'Remove this row';
            remove.addEventListener('click', function () {
                row.remove();
                renumber();
                commit();
            });
            tools.appendChild(remove);
            row.appendChild(tools);

            list.appendChild(row);
            renumber();
        }

        function renumber() {
            $$('.ptr-row', list).forEach(function (row, i) {
                $('.ptr-n', row).textContent = String(i + 1);
            });
        }

        function load() {
            list.innerHTML = '';
            var lines = String(textarea.value || '')
                .split('\n')
                .map(function (l) { return l.trim(); })
                .filter(Boolean);
            lines.forEach(function (line) { addRow(splitRow(line, spec.columns.length)); });
            if (!lines.length) list.appendChild(el('p', 'ptr-empty', spec.empty));
        }

        add.addEventListener('click', function () {
            addRow(spec.columns.map(function () { return ''; }));
            commit();
        });

        raw.addEventListener('click', function () {
            var showing = textarea.hidden;
            textarea.hidden = !showing;
            host.classList.toggle('is-raw', showing);
            raw.textContent = showing ? 'Back to the editor' : 'Edit as text';
            if (!showing) load();   // coming back from raw text: re-read it
        });

        // Keep the repeater honest if something else writes the textarea (the
        // per-product drawer refills it on every open).
        textarea.addEventListener('pt:reload', load);
        load();
    }

    /* ── single media fields ──────────────────────────────────────────── */

    function decorateMediaInput(input) {
        if (!input || input.dataset.ptReady === '1') return;
        input.dataset.ptReady = '1';
        var kind = input.getAttribute('data-pt-media') || 'image';

        var row = el('div', 'ptf is-single');
        input.parentNode.insertBefore(row, input);

        var thumb = el('div', 'ptf-thumb');
        function paint() {
            thumb.innerHTML = '';
            if (!input.value) { thumb.classList.add('is-empty'); return; }
            thumb.classList.remove('is-empty');
            var node = kind === 'video' ? document.createElement('video')
                                        : document.createElement('img');
            node.src = input.value;
            if (kind === 'video') { node.muted = true; node.preload = 'metadata'; }
            thumb.appendChild(node);
        }
        input.addEventListener('input', paint);

        var pick = el('button', 'pt-btn ptf-btn', 'Upload');
        pick.type = 'button';
        pick.addEventListener('click', function () {
            pickerOpen(input, kind, 'Choose a ' + (kind === 'video' ? 'clip' : 'photo'));
        });
        var clear = el('button', 'pt-btn ptf-btn', 'Clear');
        clear.type = 'button';
        clear.addEventListener('click', function () {
            input.value = '';
            paint();
        });

        row.appendChild(thumb);
        row.appendChild(input);
        row.appendChild(pick);
        row.appendChild(clear);
        paint();
    }

    /* ── go ───────────────────────────────────────────────────────────── */

    function enhance(scope) {
        Object.keys(SPECS).forEach(function (key) {
            $$('[data-pt-repeater="' + key + '"]', scope).forEach(function (node) {
                buildRepeater(node, SPECS[key]);
            });
        });
        $$('[data-pt-media]', scope).forEach(decorateMediaInput);
    }

    // The drawer refills its inputs after a fetch, so it announces that rather
    // than having this file poll for it.
    window.ptRefresh = function (scope) {
        enhance(scope || document);
        $$('[data-pt-repeater]', scope || document).forEach(function (node) {
            node.dispatchEvent(new Event('pt:reload'));
        });
        $$('[data-pt-media]', scope || document).forEach(function (node) {
            node.dispatchEvent(new Event('input', {bubbles: false}));
        });
    };

    document.addEventListener('DOMContentLoaded', function () { enhance(document); });
})();
