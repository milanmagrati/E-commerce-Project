/* ═══════════════════════════════════════════════════════════════════
   ORDER-FORM.JS — behaviour for the guest Confirm Order / Inquiry panel.

   Progressive enhancement: the markup posts and validates server-side on
   its own. This file adds the searchable district/branch comboboxes, live
   delivery + discount pricing, inline validation and AJAX submission.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
    'use strict';

    var ENDPOINTS = {
        locations: '/store/api/locations/',
        quote: '/store/api/quote/',
        discount: '/store/api/discount/'
    };

    var VALLEY = ['KATHMANDU', 'LALITPUR', 'BHAKTAPUR'];
    var NEPALI_MOBILE = /^9[678]\d{8}$/;

    // The catalogue is ~630 branches. Fetch it once per page, lazily, and
    // hand the same promise to every panel that asks.
    var locationsPromise = null;
    function loadLocations() {
        if (!locationsPromise) {
            locationsPromise = fetch(ENDPOINTS.locations, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (r) { return r.json(); })
                .catch(function () { return { districts: [] }; });
        }
        return locationsPromise;
    }

    function csrf() {
        return (window.CSRF_TOKEN) || (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
    }

    function money(value) {
        var n = Math.round(parseFloat(value) || 0);
        return 'Rs. ' + n.toLocaleString('en-IN');
    }

    function digitsOnly(raw) {
        var d = (raw || '').replace(/\D/g, '');
        if (d.length === 13 && d.indexOf('977') === 0) d = d.slice(3);
        else if (d.length === 11 && d.charAt(0) === '0') d = d.slice(1);
        return d;
    }

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function setText(el, text) {
        if (!el || el.textContent === text) return;
        el.textContent = text;
        el.classList.remove('of-value-changed');
        // Force a reflow so the animation restarts on every change.
        void el.offsetWidth;
        el.classList.add('of-value-changed');
    }

    /* ── Searchable combobox ─────────────────────────────────────── */

    // Every combo whose list is currently open. Scrolling the page or
    // rotating the phone changes how much room is left below the field, so
    // open lists are re-placed rather than left hanging off-screen.
    var openCombos = [];
    var placeQueued = false;
    function replaceOpenCombos() {
        if (placeQueued || !openCombos.length) return;
        placeQueued = true;
        requestAnimationFrame(function () {
            placeQueued = false;
            openCombos.forEach(function (combo) { combo.place(); });
        });
    }
    window.addEventListener('scroll', replaceOpenCombos, { passive: true });
    window.addEventListener('resize', replaceOpenCombos, { passive: true });

    function Combo(root, opts) {
        this.root = root;
        this.input = root.querySelector('.of-combo-input');
        this.list = root.querySelector('.of-combo-list');
        this.options = [];
        this.filtered = [];
        this.activeIndex = -1;
        this.selected = null;
        this.onSelect = opts.onSelect || function () {};
        this.emptyText = opts.emptyText || 'No matches';
        this.bind();
    }

    Combo.prototype.bind = function () {
        var self = this;

        this.input.addEventListener('focus', function () {
            self.input.select();
            self.open('');
        });
        this.input.addEventListener('input', function () {
            self.selected = null;
            self.open(self.input.value);
        });
        this.input.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                if (!self.root.classList.contains('is-open')) { self.open(''); return; }
                self.move(e.key === 'ArrowDown' ? 1 : -1);
            } else if (e.key === 'Enter') {
                if (self.root.classList.contains('is-open') && self.activeIndex >= 0) {
                    e.preventDefault();
                    self.choose(self.filtered[self.activeIndex]);
                }
            } else if (e.key === 'Escape') {
                self.close();
            }
        });
        this.input.addEventListener('blur', function () {
            // Let a click on an option land before the list disappears. A
            // half-typed query with nothing chosen is cleared so the box never
            // shows a district that was not actually selected — unless the
            // combo is in free-text mode (courier catalogue unavailable).
            setTimeout(function () {
                if (!self.selected && !self.freeText) self.input.value = '';
                self.close();
            }, 140);
        });
        this.list.addEventListener('mousedown', function (e) {
            var node = e.target.closest('[data-index]');
            if (!node) return;
            e.preventDefault();
            self.choose(self.filtered[parseInt(node.dataset.index, 10)]);
        });
        this.root.querySelector('.of-combo-caret').addEventListener('mousedown', function (e) {
            e.preventDefault();
            if (self.input.disabled) return;
            if (self.root.classList.contains('is-open')) self.close();
            else self.input.focus();
        });
    };

    Combo.prototype.setOptions = function (options, keepValue) {
        this.options = options || [];
        this.selected = null;
        if (!keepValue) this.input.value = '';
        this.input.disabled = this.options.length === 0;
        this.root.classList.toggle('is-disabled', this.options.length === 0);
    };

    /** Pick the option whose label matches `label`, case-insensitively. */
    Combo.prototype.selectByLabel = function (label) {
        var wanted = (label || '').trim().toLowerCase();
        if (!wanted) return false;
        for (var i = 0; i < this.options.length; i++) {
            if (this.options[i].label.toLowerCase() === wanted) {
                this.choose(this.options[i]);
                return true;
            }
        }
        return false;
    };

    /** Open downwards unless the space below is cramped and above is better.
        Called on open and whenever the page moves under an open list. */
    Combo.prototype.place = function () {
        var rect = this.input.getBoundingClientRect();
        var below = window.innerHeight - rect.bottom;
        var above = rect.top;
        this.root.classList.toggle('is-up', below < 220 && above > below);
    };

    Combo.prototype.open = function (query) {
        if (this.input.disabled) return;
        this.place();
        // open() fires on every keystroke, so guard against duplicates.
        if (openCombos.indexOf(this) === -1) openCombos.push(this);
        var q = (query || '').trim().toLowerCase();
        this.filtered = q
            ? this.options.filter(function (o) { return o.search.indexOf(q) !== -1; })
            : this.options.slice();
        this.activeIndex = this.filtered.length ? 0 : -1;
        this.render();
        this.root.classList.add('is-open');
        this.input.setAttribute('aria-expanded', 'true');
    };

    Combo.prototype.close = function () {
        var at = openCombos.indexOf(this);
        if (at !== -1) openCombos.splice(at, 1);
        this.root.classList.remove('is-open');
        this.input.setAttribute('aria-expanded', 'false');
        this.activeIndex = -1;
    };

    Combo.prototype.render = function () {
        if (!this.filtered.length) {
            this.list.innerHTML = '<div class="of-combo-empty">' + this.emptyText + '</div>';
            return;
        }
        var self = this;
        // Cap the DOM at 80 rows — 630 branches would make the list janky.
        var shown = this.filtered.slice(0, 80);
        this.list.innerHTML = shown.map(function (o, i) {
            return '<div class="of-combo-option' + (i === self.activeIndex ? ' is-active' : '') +
                '" role="option" data-index="' + i + '"' +
                (self.selected === o ? ' aria-selected="true"' : '') + '>' +
                '<div class="of-combo-option-title">' + esc(o.label) + '</div>' +
                (o.sub ? '<div class="of-combo-option-sub">' + esc(o.sub) + '</div>' : '') +
                '</div>';
        }).join('') + (this.filtered.length > shown.length
            ? '<div class="of-combo-empty">Keep typing to narrow ' + (this.filtered.length - shown.length) + ' more…</div>'
            : '');
    };

    Combo.prototype.move = function (delta) {
        if (!this.filtered.length) return;
        this.activeIndex = (this.activeIndex + delta + this.filtered.length) % this.filtered.length;
        this.render();
        var node = this.list.querySelector('[data-index="' + this.activeIndex + '"]');
        if (node) node.scrollIntoView({ block: 'nearest' });
    };

    Combo.prototype.choose = function (option) {
        if (!option) return;
        this.selected = option;
        this.input.value = option.label;
        this.close();
        this.onSelect(option);
    };

    /* ── Panel ───────────────────────────────────────────────────── */

    function OrderForm(panel) {
        this.panel = panel;
        panel.orderForm = this;
        this.form = panel.querySelector('[data-order-form-body]');
        if (!this.form) return;

        this.config = JSON.parse(panel.dataset.config || '{}');
        this.unitPrice = parseFloat(this.config.unitPrice || 0);
        this.subtotal = parseFloat(this.config.subtotal || 0);
        this.maxQty = parseInt(this.config.maxQty || 99, 10);
        this.qtyEditable = !!this.config.qtyEditable;

        this.district = '';
        this.discountCode = '';
        this.discountAmount = 0;
        this.freeDelivery = false;

        this.initQuantity();
        this.initCombos();
        this.initDiscount();
        this.initValidation();
        this.initSubmit();
        this.recalculate();
    }

    OrderForm.prototype.q = function (sel) { return this.panel.querySelector(sel); };

    /* Quantity */
    OrderForm.prototype.initQuantity = function () {
        var input = this.q('[data-qty-input]');
        if (!input) return;
        var self = this;
        var minus = this.q('[data-qty-minus]');
        var plus = this.q('[data-qty-plus]');

        function apply(value) {
            var v = Math.max(1, Math.min(self.maxQty, parseInt(value, 10) || 1));
            input.value = v;
            if (minus) minus.disabled = v <= 1;
            if (plus) plus.disabled = v >= self.maxQty;
            var badge = self.q('[data-item-qty]');
            if (badge) badge.textContent = v;
            var price = self.q('[data-item-price]');
            if (price) price.textContent = money(self.unitPrice * v);
            self.subtotal = self.unitPrice * v;
            self.recalculate();
        }

        if (minus) minus.addEventListener('click', function () { apply(parseInt(input.value, 10) - 1); });
        if (plus) plus.addEventListener('click', function () { apply(parseInt(input.value, 10) + 1); });
        input.addEventListener('change', function () { apply(input.value); });
        this.applyQuantity = apply;
        apply(input.value);
    };

    OrderForm.prototype.setQuantity = function (value) {
        if (this.applyQuantity) this.applyQuantity(value);
    };

    /* District + branch */
    OrderForm.prototype.initCombos = function () {
        var self = this;
        var districtRoot = this.q('[data-combo="district"]');
        var branchRoot = this.q('[data-combo="branch"]');
        if (!districtRoot || !branchRoot) return;

        this.branchCombo = new Combo(branchRoot, {
            emptyText: 'No branch matches',
            onSelect: function (option) {
                var codeInput = self.q('[data-branch-code]');
                if (codeInput) codeInput.value = option.value;
                self.clearError('courier_branch');
            }
        });

        this.districtCombo = new Combo(districtRoot, {
            emptyText: 'No district matches',
            onSelect: function (option) {
                self.district = option.value;
                self.clearError('district');
                var codeInput = self.q('[data-branch-code]');
                if (codeInput) codeInput.value = '';
                self.branchCombo.setOptions((option.branches || []).map(function (b) {
                    return {
                        label: b.name,
                        value: b.code,
                        sub: b.areas || b.address || '',
                        search: (b.name + ' ' + (b.areas || '') + ' ' + (b.address || '')).toLowerCase()
                    };
                }));
                self.branchCombo.input.placeholder = self.branchCombo.options.length
                    ? 'Select courier branch' : 'No branches listed';
                self.recalculate();
            }
        });

        // A failed no-JS POST comes back with these already filled in; keep
        // them and re-select them once the catalogue lands.
        var presetDistrict = this.districtCombo.input.value.trim();
        var presetBranch = this.branchCombo.input.value.trim();

        // Nothing to pick until the catalogue arrives.
        this.districtCombo.setOptions([], true);
        this.districtCombo.input.placeholder = 'Loading districts…';

        loadLocations().then(function (data) {
            var districts = (data && data.districts) || [];
            self.districtCombo.setOptions(districts.map(function (d) {
                var count = (d.branches || []).length;
                return {
                    label: d.name,
                    value: d.name,
                    sub: d.province + (count ? ' · ' + count + ' branch' + (count > 1 ? 'es' : '') : ''),
                    branches: d.branches,
                    search: (d.name + ' ' + d.province).toLowerCase()
                };
            }), true);
            self.districtCombo.input.placeholder = districts.length
                ? 'Type to search district' : 'Type your district';

            if (!districts.length) {
                // Courier catalogue unreachable — let the shopper type their
                // district freely rather than blocking the order behind a
                // dropdown that can never be populated.
                self.districtCombo.freeText = true;
                self.districtCombo.input.disabled = false;
                districtRoot.classList.remove('is-disabled');
                self.district = presetDistrict;
                self.districtCombo.input.addEventListener('input', function () {
                    self.district = self.districtCombo.input.value.trim();
                    self.recalculate();
                });
                self.recalculate();
                return;
            }

            if (presetDistrict && self.districtCombo.selectByLabel(presetDistrict)) {
                if (presetBranch) self.branchCombo.selectByLabel(presetBranch);
            } else {
                self.districtCombo.input.value = '';
            }
        });
    };

    /* Discount */
    OrderForm.prototype.initDiscount = function () {
        var self = this;
        var input = this.q('[data-discount-input]');
        var button = this.q('[data-discount-apply]');
        var msg = this.q('[data-discount-msg]');
        if (!input || !button) return;

        function showMessage(text, tone) {
            if (!msg) return;
            msg.textContent = text;
            msg.dataset.tone = tone;
            msg.classList.toggle('is-visible', !!text);
        }

        function apply() {
            var code = input.value.trim().toUpperCase();
            if (!code) { showMessage('Enter a discount code first.', 'bad'); return; }
            button.disabled = true;
            button.textContent = '…';
            fetch(ENDPOINTS.discount, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrf(),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({ code: code, subtotal: self.subtotal, district: self.district })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        self.discountCode = data.code;
                        showMessage(data.message, 'ok');
                    } else {
                        self.discountCode = '';
                        showMessage(data.message || 'That code is not valid.', 'bad');
                    }
                    self.recalculate();
                })
                .catch(function () { showMessage('Could not check that code. Try again.', 'bad'); })
                .finally(function () {
                    button.disabled = false;
                    button.textContent = 'Apply';
                });
        }

        button.addEventListener('click', apply);
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); apply(); }
        });
        input.addEventListener('input', function () {
            if (self.discountCode && input.value.trim().toUpperCase() !== self.discountCode) {
                self.discountCode = '';
                showMessage('', 'bad');
                self.recalculate();
            }
        });
    };

    /* Pricing — asks the server so the shopper never sees a total the
       backend would disagree with. */
    OrderForm.prototype.recalculate = function () {
        var self = this;
        var params = new URLSearchParams({
            subtotal: this.subtotal,
            district: this.district || '',
            code: this.discountCode || ''
        });
        clearTimeout(this._quoteTimer);
        this._quoteTimer = setTimeout(function () {
            fetch(ENDPOINTS.quote + '?' + params.toString(), {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(function (r) { return r.json(); })
                .then(function (data) { self.paintTotals(data); })
                .catch(function () { /* keep the last good figures */ });
        }, 90);
    };

    OrderForm.prototype.paintTotals = function (data) {
        var t = data.totals || {};
        var known = data.district_known !== false;
        var delivery = parseFloat(t.delivery || 0);
        var discount = parseFloat(t.discount || 0);
        var subtotal = parseFloat(t.subtotal || 0);

        setText(this.q('[data-total="subtotal"]'), money(subtotal));

        // Before a district is picked the delivery fee is genuinely unknown —
        // showing a guess here and correcting it later reads as a price change.
        var deliveryRow = this.q('[data-delivery-row]');
        setText(this.q('[data-total="delivery"]'),
            !known ? 'Select district' : (delivery > 0 ? money(delivery) : 'FREE'));
        if (deliveryRow) {
            deliveryRow.classList.toggle('is-free', known && delivery === 0);
            deliveryRow.classList.toggle('is-pending', !known);
        }

        var discountRow = this.q('[data-discount-row]');
        if (discountRow) {
            discountRow.hidden = discount <= 0;
            setText(this.q('[data-total="discount"]'), '− ' + money(discount));
        }

        setText(this.q('[data-total="total"]'),
            money(known ? t.total : Math.max(subtotal - discount, 0)));
        this.paintNotice(data.inside_valley, delivery);
    };

    OrderForm.prototype.paintNotice = function (insideValley, delivery) {
        var notice = this.q('[data-delivery-notice]');
        var text = this.q('[data-delivery-text]');
        if (!notice || !text) return;

        if (!this.district) {
            notice.dataset.tone = 'free';
            text.innerHTML = '<strong>FREE delivery</strong> inside Kathmandu Valley · काठमाडौँ उपत्यका भित्र नि:शुल्क';
        } else if (delivery === 0) {
            notice.dataset.tone = 'free';
            text.innerHTML = '<strong>FREE delivery</strong> to ' + esc(this.district) +
                (insideValley ? ' · काठमाडौँ उपत्यका भित्र' : '');
        } else {
            notice.dataset.tone = 'paid';
            text.innerHTML = 'Delivery to <strong>' + esc(this.district) + '</strong> — ' + money(delivery) +
                ', paid on arrival with the goods.';
        }
    };

    /* Inline validation */
    OrderForm.prototype.field = function (name) {
        return this.panel.querySelector('[data-field="' + name + '"]');
    };

    OrderForm.prototype.showError = function (name, message) {
        var field = this.field(name);
        if (!field) return;
        field.classList.add('has-error');
        var slot = field.querySelector('[data-error]');
        if (slot) slot.textContent = message;
    };

    OrderForm.prototype.clearError = function (name) {
        var field = this.field(name);
        if (field) field.classList.remove('has-error');
    };

    OrderForm.prototype.initValidation = function () {
        var self = this;
        var phone = this.panel.querySelector('[name="phone"]');
        if (phone) {
            // Keep the box to digits and the usual +977 prefix as they type.
            phone.addEventListener('input', function () {
                phone.value = phone.value.replace(/[^\d+\s-]/g, '');
                self.clearError('phone');
            });
        }
        ['full_name', 'address', 'email'].forEach(function (name) {
            var input = self.panel.querySelector('[name="' + name + '"]');
            if (input) input.addEventListener('input', function () { self.clearError(name); });
        });
    };

    OrderForm.prototype.validate = function () {
        var ok = true;
        var name = (this.panel.querySelector('[name="full_name"]').value || '').trim();
        if (name.length < 2) { this.showError('full_name', 'Please enter your name.'); ok = false; }

        var phone = digitsOnly(this.panel.querySelector('[name="phone"]').value);
        if (!NEPALI_MOBILE.test(phone)) {
            this.showError('phone', 'Enter a valid 10-digit number starting 98, 97 or 96.');
            ok = false;
        }

        if (!this.district) { this.showError('district', 'Please select your district.'); ok = false; }

        var address = (this.panel.querySelector('[name="address"]').value || '').trim();
        if (address.length < 4) { this.showError('address', 'Please enter your delivery address.'); ok = false; }

        if (!ok) {
            var firstBad = this.panel.querySelector('.of-field.has-error');
            if (firstBad) {
                firstBad.scrollIntoView({ behavior: 'smooth', block: 'center' });
                var input = firstBad.querySelector('input');
                if (input && !input.disabled) input.focus({ preventScroll: true });
            }
        }
        return ok;
    };

    /* Submission */
    OrderForm.prototype.initSubmit = function () {
        var self = this;
        var buttons = this.panel.querySelectorAll('[data-submit]');
        var typeInput = this.q('[data-order-type]');

        Array.prototype.forEach.call(buttons, function (button) {
            button.addEventListener('click', function (e) {
                e.preventDefault();
                if (typeInput) typeInput.value = button.dataset.submit;
                self.setMode(button.dataset.submit);
                self.submit(button);
            });
        });
    };

    OrderForm.prototype.setMode = function (mode) {
        this.panel.dataset.mode = mode;
    };

    OrderForm.prototype.setFormError = function (message) {
        var box = this.q('[data-form-error]');
        if (!box) return;
        box.textContent = message || '';
        box.classList.toggle('is-visible', !!message);
    };

    OrderForm.prototype.submit = function (button) {
        var self = this;
        this.setFormError('');
        if (!this.validate()) return;

        button.classList.add('is-busy');
        button.disabled = true;

        var payload = new FormData(this.form);
        payload.set('phone', digitsOnly(payload.get('phone')));
        payload.set('district', this.district);
        payload.set('discount_code', this.discountCode || '');

        fetch(this.form.action, {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrf() },
            body: payload
        })
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
            .then(function (res) {
                if (res.ok && res.data.success) {
                    self.showSuccess(res.data);
                    return;
                }
                var errors = res.data.errors || {};
                Object.keys(errors).forEach(function (name) {
                    self.showError(name, errors[name][0]);
                });
                self.setFormError(res.data.message || 'Something went wrong. Please try again.');
            })
            .catch(function () {
                self.setFormError('Could not reach the server. Check your connection and try again.');
            })
            .finally(function () {
                button.classList.remove('is-busy');
                button.disabled = false;
            });
    };

    OrderForm.prototype.showSuccess = function (data) {
        var box = this.q('[data-success]');
        var inquiry = data.order_type === 'inquiry';
        if (box) box.dataset.mode = data.order_type;

        setText(this.q('[data-success-title]'), inquiry ? 'Inquiry received' : 'Order confirmed');
        var msg = this.q('[data-success-msg]');
        if (msg) {
            msg.textContent = inquiry
                ? 'Thank you! Our team will call you shortly to answer your questions — no obligation to buy.'
                : 'Thank you! Our team will call you shortly to confirm delivery. Pay cash when the parcel arrives.';
        }
        setText(this.q('[data-success-ref]'), data.order_number || '—');

        var link = this.q('[data-success-link]');
        if (link && data.redirect) link.href = data.redirect;

        var copy = this.q('[data-copy-ref]');
        if (copy) {
            copy.onclick = function () {
                navigator.clipboard && navigator.clipboard.writeText(data.order_number || '');
                if (window.showToast) window.showToast('Reference copied');
            };
        }

        this.panel.classList.add('is-done');
        this.panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
    };

    /* ── Boot ────────────────────────────────────────────────────── */

    function init(scope) {
        var panels = (scope || document).querySelectorAll('[data-order-form]:not([data-of-ready])');
        Array.prototype.forEach.call(panels, function (panel) {
            panel.setAttribute('data-of-ready', '1');
            new OrderForm(panel);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { init(); });
    } else {
        init();
    }

    window.StoreOrderForm = { init: init };
})();
