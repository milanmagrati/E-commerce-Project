/* ============================================================================
   product.js — behaviour for the product page, plus the two bits of header
   chrome that go with it (the account menu and the "item added" notification).

   Everything here is an enhancement: with JavaScript off, the gallery still
   shows every photo in a swipeable row, the accordions are open, the add-to-
   cart form posts normally and the order form submits server-side.
   ========================================================================= */
(function () {
    'use strict';

    function $(sel, root) { return (root || document).querySelector(sel); }
    function $$(sel, root) {
        return Array.prototype.slice.call((root || document).querySelectorAll(sel));
    }

    function csrf() {
        return (typeof CSRF_TOKEN !== 'undefined' && CSRF_TOKEN) ||
            (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
    }

    function money(value) {
        var n = Number(value || 0);
        return 'Rs. ' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
    }

    /* ====================================================================
       Account menu in the header
       ==================================================================== */

    function initAccountMenu() {
        var wrap = $('[data-account-menu]');
        if (!wrap) return;
        var button = $('[data-account-toggle]', wrap);
        if (!button) return;

        function close() {
            wrap.classList.remove('is-open');
            button.setAttribute('aria-expanded', 'false');
        }

        button.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            var open = wrap.classList.toggle('is-open');
            button.setAttribute('aria-expanded', open ? 'true' : 'false');
        });

        document.addEventListener('click', function (e) {
            if (!wrap.contains(e.target)) close();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') close();
        });
    }

    /* ====================================================================
       "Item added to your cart" notification

       The reference storefront answers an add-to-cart with a small card
       offering View cart / Check out / Continue shopping rather than a full
       page reload, so the shopper keeps their place on the product page.
       ==================================================================== */

    var noteTimer = null;

    function showCartNote(payload) {
        var note = $('[data-cart-note]');
        if (!note) return;

        var img = $('[data-cart-note-img]', note);
        var name = $('[data-cart-note-name]', note);
        var meta = $('[data-cart-note-meta]', note);

        if (img) {
            if (payload.image) {
                img.src = payload.image;
                img.hidden = false;
            } else {
                img.hidden = true;
            }
        }
        if (name) name.textContent = payload.name || '';
        if (meta) {
            meta.textContent = [
                payload.variant,
                'Qty ' + (payload.quantity || 1),
                payload.price ? money(payload.price) : ''
            ].filter(Boolean).join(' · ');
        }

        note.classList.add('is-open');
        window.clearTimeout(noteTimer);
        // Long enough to read and act on, short enough not to sit over the
        // page; hovering the card holds it open.
        noteTimer = window.setTimeout(hideCartNote, 7000);
    }

    function hideCartNote() {
        var note = $('[data-cart-note]');
        if (note) note.classList.remove('is-open');
    }

    function initCartNote() {
        var note = $('[data-cart-note]');
        if (!note) return;
        $$('[data-cart-note-close]', note).forEach(function (btn) {
            btn.addEventListener('click', hideCartNote);
        });
        note.addEventListener('mouseenter', function () { window.clearTimeout(noteTimer); });
        note.addEventListener('mouseleave', function () {
            noteTimer = window.setTimeout(hideCartNote, 2500);
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') hideCartNote();
        });
    }

    function setCartBadge(count) {
        $$('#cartBadge, .cart-badge').forEach(function (badge) {
            badge.textContent = count;
            badge.hidden = false;
        });
    }

    /* ====================================================================
       Media gallery
       ==================================================================== */

    function Gallery(root) {
        this.root = root;
        this.track = $('[data-media-track]', root);
        this.slides = $$('[data-slide]', root);
        this.thumbs = $$('[data-thumb]', root);
        this.counter = $('[data-media-counter]', root);
        this.prev = $('[data-media-prev]', root);
        this.next = $('[data-media-next]', root);
        this.index = 0;

        if (!this.track || this.slides.length === 0) return;
        this.bind();
        this.sync();
    }

    Gallery.prototype.bind = function () {
        var self = this;

        this.thumbs.forEach(function (thumb, i) {
            thumb.addEventListener('click', function () { self.go(i); });
        });

        if (this.prev) this.prev.addEventListener('click', function () { self.go(self.index - 1); });
        if (this.next) this.next.addEventListener('click', function () { self.go(self.index + 1); });

        // The track is a real scroller, so a swipe must update the counter and
        // the active thumbnail too — read the position back rather than
        // assuming clicks are the only way it moves.
        var settle = null;
        this.track.addEventListener('scroll', function () {
            window.clearTimeout(settle);
            settle = window.setTimeout(function () {
                var width = self.track.clientWidth || 1;
                var at = Math.round(self.track.scrollLeft / width);
                if (at !== self.index) {
                    self.index = Math.max(0, Math.min(self.slides.length - 1, at));
                    self.sync();
                }
            }, 90);
        }, { passive: true });

        this.root.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowRight') { self.go(self.index + 1); }
            else if (e.key === 'ArrowLeft') { self.go(self.index - 1); }
        });
    };

    Gallery.prototype.go = function (index) {
        if (index < 0 || index >= this.slides.length) return;
        this.index = index;
        this.track.scrollTo({ left: this.track.clientWidth * index, behavior: 'smooth' });
        this.sync();
    };

    Gallery.prototype.sync = function () {
        var total = this.slides.length;
        if (this.counter) this.counter.textContent = (this.index + 1) + ' / ' + total;
        this.thumbs.forEach(function (thumb, i) {
            thumb.classList.toggle('is-active', i === this.index);
            thumb.setAttribute('aria-current', i === this.index ? 'true' : 'false');
        }, this);
        if (this.prev) this.prev.disabled = this.index === 0;
        if (this.next) this.next.disabled = this.index >= total - 1;
    };

    Gallery.prototype.currentSrc = function () {
        var img = this.slides[this.index] ? this.slides[this.index].querySelector('img') : null;
        return img ? (img.dataset.full || img.src) : '';
    };

    /* ====================================================================
       Lightbox — "Open media N in modal"
       ==================================================================== */

    function initLightbox(gallery) {
        var modal = $('[data-media-modal]');
        if (!modal || !gallery || !gallery.slides.length) return;

        var image = $('[data-modal-img]', modal);
        var count = $('[data-modal-count]', modal);
        var at = 0;
        var lastFocus = null;

        function render() {
            var slide = gallery.slides[at];
            var img = slide ? slide.querySelector('img') : null;
            if (img && image) {
                image.src = img.dataset.full || img.src;
                image.alt = img.alt || '';
            }
            if (count) count.textContent = (at + 1) + ' / ' + gallery.slides.length;
        }

        function open(index) {
            at = index;
            lastFocus = document.activeElement;
            render();
            modal.classList.add('is-open');
            // Stop the page behind from scrolling while the modal is up.
            document.body.style.overflow = 'hidden';
            var closeBtn = $('[data-modal-close]', modal);
            if (closeBtn) closeBtn.focus();
        }

        function close() {
            modal.classList.remove('is-open');
            document.body.style.overflow = '';
            if (lastFocus && lastFocus.focus) lastFocus.focus();
        }

        function step(delta) {
            at = (at + delta + gallery.slides.length) % gallery.slides.length;
            render();
        }

        var main = $('[data-media-main]', gallery.root);
        main.addEventListener('click', function (e) {
            // Let the arrows do their own job rather than opening the modal.
            if (e.target.closest('[data-media-prev], [data-media-next]')) return;
            open(gallery.index);
        });
        // The photo advertises itself as a button, so it has to answer the
        // keyboard like one.
        main.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                open(gallery.index);
            }
        });

        $$('[data-modal-close]', modal).forEach(function (b) { b.addEventListener('click', close); });
        var prev = $('[data-modal-prev]', modal);
        var next = $('[data-modal-next]', modal);
        if (prev) prev.addEventListener('click', function () { step(-1); });
        if (next) next.addEventListener('click', function () { step(1); });

        modal.addEventListener('click', function (e) {
            if (e.target === modal) close();
        });

        document.addEventListener('keydown', function (e) {
            if (!modal.classList.contains('is-open')) return;
            if (e.key === 'Escape') close();
            else if (e.key === 'ArrowRight') step(1);
            else if (e.key === 'ArrowLeft') step(-1);
        });

        if (gallery.slides.length < 2) {
            if (prev) prev.hidden = true;
            if (next) next.hidden = true;
        }
    }

    /* ====================================================================
       Accordions
       ==================================================================== */

    function initAccordions() {
        $$('[data-acc]').forEach(function (acc) {
            var head = $('[data-acc-head]', acc);
            if (!head) return;
            head.addEventListener('click', function () {
                var open = acc.classList.toggle('is-open');
                head.setAttribute('aria-expanded', open ? 'true' : 'false');
            });
        });
    }

    /* ====================================================================
       Share / copy link
       ==================================================================== */

    function initShare() {
        var share = $('[data-share]');
        if (!share) return;
        var toggle = $('[data-share-toggle]', share);
        var input = $('[data-share-input]', share);
        var copy = $('[data-share-copy]', share);

        if (input && !input.value) input.value = window.location.href;

        if (toggle) {
            toggle.addEventListener('click', function () {
                // Hand off to the OS sheet where there is one — that is what a
                // phone user expects from a share button.
                if (navigator.share) {
                    navigator.share({
                        title: document.title,
                        url: window.location.href
                    }).catch(function () { /* dismissed */ });
                    return;
                }
                share.classList.toggle('is-open');
                if (share.classList.contains('is-open') && input) {
                    input.focus();
                    input.select();
                }
            });
        }

        if (copy) {
            copy.addEventListener('click', function () {
                var value = input ? input.value : window.location.href;
                var done = function () {
                    copy.textContent = 'Copied';
                    window.setTimeout(function () { copy.textContent = 'Copy link'; }, 1800);
                };
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(value).then(done, done);
                } else if (input) {
                    // execCommand is deprecated but is the only path on
                    // non-secure origins, where the Clipboard API is absent.
                    input.select();
                    try { document.execCommand('copy'); } catch (err) { /* ignore */ }
                    done();
                }
            });
        }
    }

    /* ====================================================================
       Star rating input on the review form
       ==================================================================== */

    function initStarInput() {
        var wrap = $('[data-star-input]');
        if (!wrap) return;
        var stars = $$('span', wrap);
        var field = $('[data-star-value]', wrap.parentNode) || $('[data-star-value]');

        function paint(upTo) {
            stars.forEach(function (star, i) {
                star.classList.toggle('on', i < upTo);
            });
        }

        stars.forEach(function (star, i) {
            star.setAttribute('role', 'radio');
            star.setAttribute('tabindex', '0');
            star.addEventListener('click', function () {
                if (field) field.value = i + 1;
                wrap.dataset.value = String(i + 1);
                paint(i + 1);
            });
            star.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    star.click();
                }
            });
            star.addEventListener('mouseenter', function () { paint(i + 1); });
        });

        wrap.addEventListener('mouseleave', function () {
            paint(parseInt(wrap.dataset.value || (field && field.value) || 5, 10));
        });

        paint(parseInt((field && field.value) || 5, 10));
    }

    /* ====================================================================
       The buy column: quantity, variants, add to cart, order panel
       ==================================================================== */

    function initBuyColumn() {
        var root = $('[data-buy]');
        if (!root) return;

        var maxStock = parseInt(root.dataset.maxStock || '0', 10);
        var unitPrice = parseFloat(root.dataset.price || '0');
        var qtyInput = $('[data-qty]', root);
        var minus = $('[data-qty-minus]', root);
        var plus = $('[data-qty-plus]', root);
        var cartQty = $('[data-cart-qty]', root);
        var cartVariant = $('[data-cart-variant]', root);
        var stickyBar = $('[data-sticky-bar]');

        function clamp(value) {
            var n = parseInt(value, 10);
            if (isNaN(n) || n < 1) n = 1;
            // Backorderable products have no ceiling worth enforcing here; the
            // server is the one that decides what can actually be committed.
            if (maxStock > 0 && n > maxStock) n = maxStock;
            return n;
        }

        function quantity() {
            return qtyInput ? clamp(qtyInput.value) : 1;
        }

        function syncQuantity() {
            var n = quantity();
            if (qtyInput) qtyInput.value = n;
            if (cartQty) cartQty.value = n;
            if (minus) minus.disabled = n <= 1;
            if (plus) plus.disabled = maxStock > 0 && n >= maxStock;
            syncOrderPanel();
        }

        if (minus) minus.addEventListener('click', function () {
            if (qtyInput) qtyInput.value = quantity() - 1;
            syncQuantity();
        });
        if (plus) plus.addEventListener('click', function () {
            if (qtyInput) qtyInput.value = quantity() + 1;
            syncQuantity();
        });
        if (qtyInput) {
            qtyInput.addEventListener('change', syncQuantity);
            qtyInput.addEventListener('blur', syncQuantity);
        }

        /* ── variants ── */

        var chosen = {};

        function variantString() {
            return Object.keys(chosen).map(function (key) {
                return key + ': ' + chosen[key];
            }).join(', ');
        }

        $$('[data-pill]', root).forEach(function (pill) {
            pill.addEventListener('click', function () {
                var group = pill.dataset.option;
                $$('[data-pill][data-option="' + CSS.escape(group) + '"]', root).forEach(function (other) {
                    other.classList.remove('is-selected');
                    other.setAttribute('aria-pressed', 'false');
                });
                pill.classList.add('is-selected');
                pill.setAttribute('aria-pressed', 'true');
                chosen[group] = pill.dataset.value;

                var label = $('[data-option-value="' + CSS.escape(group) + '"]', root);
                if (label) label.textContent = pill.dataset.value;
                if (cartVariant) cartVariant.value = variantString();
                syncOrderPanel();
            });
        });

        // Start every group on its first value, so the form is never posted
        // with a half-chosen variant.
        $$('[data-option-group]', root).forEach(function (group) {
            var first = $('[data-pill]', group);
            if (first) first.click();
        });

        /* ── the inline order panel ── */

        var slot = $('[data-order-slot]');
        var panel = slot ? $('[data-order-form]', slot) : null;

        function syncOrderPanel() {
            if (!panel || !panel.orderForm) return;
            panel.orderForm.setQuantity(quantity());
            var variant = cartVariant ? cartVariant.value : '';
            var hidden = panel.querySelector('[data-variant-input]');
            var label = panel.querySelector('[data-variant-label]');
            if (hidden) hidden.value = variant;
            if (label) {
                label.textContent = variant;
                label.hidden = !variant;
            }
        }

        function openOrderPanel(mode) {
            if (!slot || !panel) return;
            slot.classList.add('is-open');
            // Only once the height transition has finished may the slot stop
            // clipping, or the district dropdown is cut off at its edge.
            window.setTimeout(function () { slot.classList.add('is-expanded'); }, 460);

            $$('[data-open-order]').forEach(function (btn) {
                btn.setAttribute('aria-expanded', btn.dataset.openOrder === mode ? 'true' : 'false');
            });

            if (panel.orderForm) panel.orderForm.setMode(mode);
            var typeInput = panel.querySelector('[data-order-type]');
            if (typeInput) typeInput.value = mode;
            syncOrderPanel();

            window.setTimeout(function () {
                slot.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                var first = panel.querySelector('[name="full_name"]');
                if (first && !first.value) first.focus({ preventScroll: true });
            }, 380);
        }

        $$('[data-open-order]').forEach(function (btn) {
            btn.addEventListener('click', function () { openOrderPanel(btn.dataset.openOrder); });
        });

        /* ── add to cart, without losing the shopper's place ── */

        var form = $('[data-add-to-cart]', root);
        if (form) {
            form.addEventListener('submit', function (e) {
                if (!window.fetch) return;  // let the plain POST happen
                e.preventDefault();

                var button = form.querySelector('button[type="submit"]');
                var label = button ? button.innerHTML : '';
                if (button) {
                    button.disabled = true;
                    button.innerHTML = 'Adding…';
                }

                fetch(form.action, {
                    method: 'POST',
                    body: new FormData(form),
                    headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' }
                }).then(function (res) {
                    return res.json().catch(function () { return {}; });
                }).then(function (data) {
                    if (data && data.success === false) {
                        if (typeof showToast === 'function') {
                            showToast(data.message || 'Could not add to cart.', 'error');
                        }
                        return;
                    }
                    if (data && typeof data.count !== 'undefined') setCartBadge(data.count);
                    // Trust the server's figure: a stock cap can mean fewer
                    // units went in than were asked for.
                    if (data && typeof data.item_quantity !== 'undefined') {
                        setInCart(data.item_quantity);
                    } else {
                        setInCart(inCart + quantity());
                    }
                    showCartNote({
                        name: (data && data.name) || root.dataset.name || '',
                        image: (data && data.image) || root.dataset.image || '',
                        variant: (data && data.variant) || (cartVariant ? cartVariant.value : ''),
                        quantity: quantity(),
                        price: (data && data.line_total) || unitPrice * quantity()
                    });
                }).catch(function () {
                    // Network trouble: fall back to the ordinary form post so
                    // the shopper is never left with a dead button.
                    form.submit();
                }).then(function () {
                    if (button) {
                        button.disabled = false;
                        button.innerHTML = label;
                    }
                });
            });
        }

        /* ── "(N in cart)" ── */

        var inCartLabel = $('[data-in-cart]', root);
        var inCart = parseInt(root.dataset.inCart || '0', 10);

        function setInCart(total) {
            inCart = total;
            if (inCartLabel) {
                inCartLabel.textContent = '(' + inCart + ' in cart)';
                inCartLabel.hidden = inCart <= 0;
            }
        }

        /* ── wishlist ── */

        var save = $('[data-save]', root);
        if (save) {
            save.addEventListener('click', function () {
                var id = save.dataset.save;
                fetch('/store/wishlist/toggle/' + id + '/', {
                    method: 'POST',
                    headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' }
                }).then(function (r) { return r.json(); }).then(function (data) {
                    if (!data.success) return;
                    save.classList.toggle('is-active', data.added);
                    var text = $('[data-save-text]', save);
                    if (text) text.textContent = data.added ? 'Saved' : 'Save for later';
                    var icon = save.querySelector('svg');
                    if (icon) icon.setAttribute('fill', data.added ? 'currentColor' : 'none');
                    if (typeof showToast === 'function') showToast(data.message);
                }).catch(function () { /* leave the button as it was */ });
            });
        }

        /* ── sticky buy bar on small screens ── */

        if (stickyBar && form) {
            var anchor = $('[data-buttons]', root);
            if (anchor && 'IntersectionObserver' in window) {
                new IntersectionObserver(function (entries) {
                    // Show the bar exactly when the real buttons are off-screen.
                    stickyBar.classList.toggle('is-visible', !entries[0].isIntersecting);
                }, { rootMargin: '0px 0px -60px 0px' }).observe(anchor);
            }

            $$('[data-sticky-add]', stickyBar).forEach(function (btn) {
                btn.addEventListener('click', function () {
                    if (typeof form.requestSubmit === 'function') form.requestSubmit();
                    else form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
                });
            });
        }

        syncQuantity();
    }

    /* ==================================================================== */

    function init() {
        initAccountMenu();
        initCartNote();
        initAccordions();
        initShare();
        initStarInput();

        var galleryRoot = $('[data-gallery]');
        if (galleryRoot) {
            var gallery = new Gallery(galleryRoot);
            initLightbox(gallery);
        }

        initBuyColumn();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
