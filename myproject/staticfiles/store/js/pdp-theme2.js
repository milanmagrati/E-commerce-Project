/* ============================================================================
   Theme 2 — the conversion product landing page.

   One IIFE, one DOMContentLoaded, one initialiser per surface. Every one of
   them must no-op cleanly when its markup is absent: blocks that hide
   themselves when the administrator has filled nothing in mean half of them
   usually are.

   Two rules worth stating up front:

   * The bundle ladder and the price row are re-priced from `data-p2-bulk-map`,
     which the server already resolved per variation. Nothing here recomputes a
     discount from a percentage, so the page and the order cannot disagree.
   * The checkout modal's figures come from the quote endpoint every time. The
     browser never decides money.
   ========================================================================= */

(function () {
    'use strict';

    var CFG = window.LX_P2 || {};

    function $(sel, root) { return (root || document).querySelector(sel); }
    function $$(sel, root) {
        return Array.prototype.slice.call((root || document).querySelectorAll(sel));
    }

    /* The single fetch wrapper: POSTs form-encoded with the CSRF token, and
       resolves to the parsed body whatever the status, so callers can read the
       server's own message instead of inventing one. */
    function post(url, data) {
        var body = new URLSearchParams();
        Object.keys(data || {}).forEach(function (k) {
            if (data[k] !== undefined && data[k] !== null) body.append(k, data[k]);
        });
        return fetch(url, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': CFG.csrf || '',
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: body.toString(),
            credentials: 'same-origin'
        }).then(function (res) {
            return res.json().catch(function () { return {}; });
        });
    }

    function toast(message) {
        var box = document.getElementById('lx-p2-toast');
        if (!box) return;
        box.textContent = message;
        box.hidden = false;
        clearTimeout(box._t);
        box._t = setTimeout(function () { box.hidden = true; }, 2600);
    }

    /* ── money ─────────────────────────────────────────────────────────── */

    function parseMoney(raw) {
        var n = parseFloat(String(raw === undefined ? '' : raw).replace(/[^0-9.\-]/g, ''));
        return isNaN(n) ? 0 : n;
    }

    function formatPlain(value) {
        var n = Number(value) || 0;
        var whole = Math.abs(n - Math.round(n)) < 0.005;
        return n.toLocaleString('en-US', {
            minimumFractionDigits: whole ? 0 : 2,
            maximumFractionDigits: whole ? 0 : 2
        });
    }

    function money(value) { return 'Rs. ' + formatPlain(value); }

    /* ── shared page state ─────────────────────────────────────────────── */

    var root = $('[data-p2-root]');
    if (!root) return;

    var bulkMap = {};
    try {
        var mapNode = $('[data-p2-bulk-map]', root);
        bulkMap = mapNode ? JSON.parse(mapNode.textContent || '{}') : {};
    } catch (e) { bulkMap = {}; }

    var state = {
        productId: root.getAttribute('data-product-id') || '',
        variationId: '',
        variantLabel: '',
        qty: 1,
        basePrice: parseMoney(root.getAttribute('data-price')),
        maxStock: parseInt(root.getAttribute('data-max-stock'), 10) || 0,
        hasVariations: root.getAttribute('data-has-variations') === '1'
    };

    function currentTiers() {
        return bulkMap[state.variationId || '0'] || [];
    }

    function currentBase() { return state.basePrice; }

    /* The rung in force is the *cheapest* applicable one, not simply the
       highest minQty — exactly the rule bulk_discounts.price_for uses, so a
       mis-ordered ladder can never quote more for taking more. */
    function activeTier(qty) {
        var best = null;
        currentTiers().forEach(function (tier) {
            if (qty >= tier.minQty && (best === null || tier.unitPrice < best.unitPrice)) best = tier;
        });
        return best;
    }

    function unitFor(qty) {
        var tier = activeTier(qty);
        return tier ? tier.unitPrice : currentBase();
    }

    /* ── price row ─────────────────────────────────────────────────────── */

    function updatePrice() {
        var base = currentBase();
        var unit = unitFor(state.qty);
        var priceEl = $('[data-p2-price]', root);
        var badgeEl = $('[data-p2-badge]', root);
        var wasEl = $('[data-p2-was]', root);
        var stickyEl = $('[data-p2-sticky-price]');

        if (priceEl) priceEl.textContent = formatPlain(unit);
        if (stickyEl) stickyEl.textContent = formatPlain(unit);

        var off = base > 0 ? Math.round((base - unit) / base * 100) : 0;
        if (badgeEl) {
            badgeEl.hidden = off <= 0;
            badgeEl.innerHTML = '🏷 ' + off + '%';
        }
        if (wasEl) {
            wasEl.hidden = off <= 0;
            wasEl.textContent = money(base);
        }
    }

    /* ── bundle ladder ─────────────────────────────────────────────────── */

    function tierThumb() {
        var img = $('[data-p2-slide].is-active img', root) || $('[data-p2-slide] img', root);
        return img ? img.getAttribute('src') : '';
    }

    /* Admin-authored strings (a rung's label, an image URL) go through this
       before they are concatenated into markup. split/join rather than regex
       literals so the quote case is spelled out rather than hidden inside a
       pattern. */
    function escapeHtml(value) {
        return String(value === undefined || value === null ? '' : value)
            .split('&').join('&amp;')
            .split('<').join('&lt;')
            .split('>').join('&gt;')
            .split('"').join('&quot;');
    }

    /* The rungs the buy box draws, in the shape store/theme2.py::ladder builds
       them server-side: a "Buy 1" rung first so the block is a chooser rather
       than a column of upsells, and every rung quoting the *line total* for its
       own quantity, which is the number that will actually be charged. */
    function ladderRows() {
        var tiers = currentTiers();
        if (!tiers.length) return [];
        var base = currentBase();
        if (base <= 0) return [];

        var deepest = tiers.length > 1
            ? tiers.reduce(function (a, b) { return b.saveEach > a.saveEach ? b : a; })
            : null;

        var rows = [{
            qty: 1, name: 'Buy 1 piece', save: '',
            total: base, was: base, popular: false, discounted: false
        }];
        tiers.forEach(function (tier) {
            rows.push({
                qty: tier.minQty,
                name: tier.label || ('Buy ' + tier.minQty + ' pieces'),
                save: 'You save ' + money(tier.saveEach * tier.minQty),
                // Priced off the option's own base, so a variant switch
                // re-prices the whole ladder rather than the headline alone.
                total: tier.unitPrice * tier.minQty,
                was: base * tier.minQty,
                popular: deepest ? tier === deepest : false,
                discounted: true
            });
        });
        return rows;
    }

    function renderTierTotals() {
        var box = $('[data-p2-tiers]', root);
        var wrap = $('[data-p2-bundle]', root);
        if (!box || !wrap) return;

        var awaiting = $('[data-p2-bundle-await]', root);
        if (awaiting) awaiting.hidden = !(state.hasVariations && !state.variationId);

        var rows = ladderRows();
        if (!rows.length) {
            box.innerHTML = '';
            wrap.hidden = !(state.hasVariations && !state.variationId);
            return;
        }
        wrap.hidden = false;

        // The rung in force is the deepest one this quantity has reached.
        var active = rows[0];
        rows.forEach(function (row) { if (state.qty >= row.qty) active = row; });

        var thumb = tierThumb();
        box.innerHTML = rows.map(function (row) {
            var selected = row === active ? ' is-selected' : '';
            var popular = row.popular ? ' is-popular' : '';
            return '<button type="button" class="lx-p2-tier' + selected + popular + '"' +
                ' data-p2-tier data-min-qty="' + row.qty + '"' +
                ' aria-pressed="' + (selected ? 'true' : 'false') + '">' +
                '<span class="lx-p2-tier-thumb">' +
                (thumb ? '<img src="' + escapeHtml(thumb) + '" alt="" loading="lazy">' : '') +
                '<span class="lx-p2-tier-qty">' + row.qty + '×</span></span>' +
                '<span class="lx-p2-tier-text">' +
                '<span class="lx-p2-tier-name">' + escapeHtml(row.name) + '</span>' +
                (row.save ? '<span class="lx-p2-tier-save">' + escapeHtml(row.save) + '</span>' : '') +
                '</span>' +
                '<span class="lx-p2-tier-price"><span class="now">' + money(row.total) + '</span>' +
                (row.discounted ? '<span class="was">' + money(row.was) + '</span>' : '') +
                '</span></button>';
        }).join('');
    }

    function initTiers() {
        var box = $('[data-p2-tiers]', root);
        if (!box) return;
        box.addEventListener('click', function (event) {
            var card = event.target.closest('[data-p2-tier]');
            if (!card) return;
            setQty(parseInt(card.getAttribute('data-min-qty'), 10) || 1);
        });
        renderTierTotals();
    }

    /* ── quantity ──────────────────────────────────────────────────────── */

    function qtyInput() { return $('[data-p2-qty]', root); }

    function setQty(next) {
        var input = qtyInput();
        var ceiling = state.maxStock > 0 ? state.maxStock : 999;
        state.qty = Math.max(1, Math.min(parseInt(next, 10) || 1, ceiling));
        if (input) input.value = state.qty;
        updatePrice();
        renderTierTotals();
    }

    function initQty() {
        var input = qtyInput();
        if (!input) return;
        state.qty = parseInt(input.value, 10) || 1;
        var minus = $('[data-p2-minus]', root);
        var plus = $('[data-p2-plus]', root);
        if (minus) minus.addEventListener('click', function () { setQty(state.qty - 1); });
        if (plus) plus.addEventListener('click', function () { setQty(state.qty + 1); });
        input.addEventListener('change', function () { setQty(input.value); });
    }

    /* ── gallery ───────────────────────────────────────────────────────── */

    var gallery = { slides: [], thumbs: [], index: 0 };

    function showSlide(index) {
        if (!gallery.slides.length) return;
        gallery.index = (index + gallery.slides.length) % gallery.slides.length;
        gallery.slides.forEach(function (slide, i) {
            slide.classList.toggle('is-active', i === gallery.index);
        });
        gallery.thumbs.forEach(function (thumb, i) {
            thumb.classList.toggle('is-active', i === gallery.index);
        });
        var counter = $('[data-p2-count]', root);
        if (counter) counter.textContent = (gallery.index + 1) + ' / ' + gallery.slides.length;
    }

    function initGallery() {
        var box = $('[data-p2-gallery]', root);
        if (!box) return;
        gallery.slides = $$('[data-p2-slide]', box);
        gallery.thumbs = $$('[data-p2-thumb]', box);
        if (!gallery.slides.length) return;

        gallery.thumbs.forEach(function (thumb, i) {
            thumb.addEventListener('click', function () { showSlide(i); });
        });
        var prev = $('[data-p2-prev]', box);
        var next = $('[data-p2-next]', box);
        if (prev) prev.addEventListener('click', function () { showSlide(gallery.index - 1); });
        if (next) next.addEventListener('click', function () { showSlide(gallery.index + 1); });

        box.setAttribute('tabindex', '-1');
        box.addEventListener('keydown', function (event) {
            if (event.key === 'ArrowLeft') { showSlide(gallery.index - 1); }
            else if (event.key === 'ArrowRight') { showSlide(gallery.index + 1); }
        });

        var startX = null;
        var stage = $('[data-p2-stage]', box);
        if (stage) {
            stage.addEventListener('touchstart', function (e) {
                startX = e.touches[0].clientX;
            }, { passive: true });
            stage.addEventListener('touchend', function (e) {
                if (startX === null) return;
                var dx = e.changedTouches[0].clientX - startX;
                if (Math.abs(dx) > 40) showSlide(gallery.index + (dx < 0 ? 1 : -1));
                startX = null;
            });
        }

        showSlide(0);
    }

    /* Hover magnifier. `background-size` is set in PIXELS, computed from the
       image's own rendered box — a percentage is a share of the *panel*, so
       `250%` only magnifies honestly when the panel's aspect ratio happens to
       match the photo's, and stretches every other one. */
    function initZoom() {
        var panel = $('[data-p2-zoom]', root);
        var stage = $('[data-p2-stage]', root);
        if (!panel || !stage) return;
        if (!window.matchMedia || !window.matchMedia('(hover: hover)').matches) return;
        if (window.innerWidth < 1100) return;

        var inner = $('[data-p2-zoom-inner]', panel);
        var ZOOM = 2.6;

        stage.addEventListener('mouseenter', function () {
            var img = $('[data-p2-slide].is-active img', root);
            if (!img) return;
            var box = img.getBoundingClientRect();
            if (!box.width || !box.height) return;
            inner.style.backgroundImage = 'url("' + img.currentSrc + '")';
            inner.style.backgroundSize = (box.width * ZOOM) + 'px ' + (box.height * ZOOM) + 'px';
            panel.style.height = stage.offsetHeight + 'px';
            panel.classList.add('is-on');
        });

        stage.addEventListener('mousemove', function (event) {
            var img = $('[data-p2-slide].is-active img', root);
            if (!img || !panel.classList.contains('is-on')) return;
            var box = img.getBoundingClientRect();
            var x = Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1);
            var y = Math.min(Math.max((event.clientY - box.top) / box.height, 0), 1);
            var over = {
                w: box.width * ZOOM - panel.offsetWidth,
                h: box.height * ZOOM - panel.offsetHeight
            };
            inner.style.backgroundPosition =
                (-Math.max(over.w, 0) * x) + 'px ' + (-Math.max(over.h, 0) * y) + 'px';
        });

        stage.addEventListener('mouseleave', function () { panel.classList.remove('is-on'); });
    }

    /* ── videos ────────────────────────────────────────────────────────── */

    function initVideos() {
        var cards = $$('[data-p2-video]', root);
        if (!cards.length) return;

        cards.forEach(function (card) {
            var video = $('video', card);
            var play = $('[data-p2-play]', card);
            var mute = $('[data-p2-mute]', card);
            var expand = $('[data-p2-expand]', card);
            if (!video) return;

            function toggle() {
                if (video.paused) {
                    // Playing one pauses the rest: two soundtracks at once is
                    // nobody's idea of a product demo.
                    cards.forEach(function (other) {
                        if (other === card) return;
                        var v = $('video', other);
                        if (v && !v.paused) { v.pause(); other.classList.remove('is-playing'); }
                    });
                    video.play().catch(function () { /* autoplay policy — ignore */ });
                    card.classList.add('is-playing');
                } else {
                    video.pause();
                    card.classList.remove('is-playing');
                }
            }

            if (play) play.addEventListener('click', toggle);
            video.addEventListener('click', toggle);
            video.addEventListener('ended', function () { card.classList.remove('is-playing'); });

            if (mute) {
                mute.addEventListener('click', function (event) {
                    event.stopPropagation();
                    video.muted = !video.muted;
                    mute.setAttribute('aria-label', video.muted ? 'Unmute video' : 'Mute video');
                    mute.classList.toggle('is-on', !video.muted);
                });
            }
            if (expand) {
                expand.addEventListener('click', function (event) {
                    event.stopPropagation();
                    var go = video.requestFullscreen || video.webkitEnterFullscreen
                        || video.webkitRequestFullscreen;
                    if (go) go.call(video);
                });
            }
        });
    }

    /* ── variants ──────────────────────────────────────────────────────── */

    function bringVariantImageForward(url) {
        if (!url) return;
        var match = gallery.slides.filter(function (slide) {
            return slide.getAttribute('data-full') === url;
        })[0];
        if (match) { showSlide(gallery.slides.indexOf(match)); return; }

        // No gallery slide holds this shade's photo, so one is kept for the
        // purpose and reused rather than appending a new slide per click.
        var slidesBox = $('[data-p2-slides]', root);
        if (!slidesBox) return;
        var slot = $('[data-p2-variant-slide]', slidesBox);
        if (!slot) {
            slot = document.createElement('figure');
            slot.className = 'lx-p2-slide';
            slot.setAttribute('data-p2-slide', '');
            slot.setAttribute('data-p2-variant-slide', '');
            slot.innerHTML = '<img alt="">';
            slidesBox.appendChild(slot);
            gallery.slides = $$('[data-p2-slide]', root);
        }
        slot.setAttribute('data-full', url);
        $('img', slot).setAttribute('src', url);
        showSlide(gallery.slides.indexOf(slot));
    }

    function selectVariant(card) {
        if (!card || card.disabled) return;
        $$('[data-p2-variant]', root).forEach(function (other) {
            other.classList.toggle('is-selected', other === card);
            other.setAttribute('aria-pressed', other === card ? 'true' : 'false');
        });

        state.variationId = card.getAttribute('data-variation-id') || '';
        state.variantLabel = card.getAttribute('data-label') || '';
        state.basePrice = parseMoney(card.getAttribute('data-price'));
        state.maxStock = parseInt(card.getAttribute('data-stock'), 10) || 0;

        var label = $('[data-p2-variant-label]', root);
        if (label) label.textContent = state.variantLabel || 'not chosen yet';

        var stock = $('[data-p2-stock]', root);
        if (stock) {
            stock.textContent = state.maxStock > 0
                ? 'In stock — ' + state.maxStock + ' available'
                : 'Sold out';
        }

        var qty = qtyInput();
        if (qty && state.maxStock > 0) qty.setAttribute('max', state.maxStock);

        bringVariantImageForward(card.getAttribute('data-image'));

        // Both, not just the price: the ladder is priced off the chosen
        // option, so updating one without the other leaves the bundle cards
        // quoting the previous shade.
        setQty(state.qty);
    }

    function initVariants() {
        var cards = $$('[data-p2-variant]', root);
        if (!cards.length) return;
        cards.forEach(function (card) {
            card.addEventListener('click', function () { selectVariant(card); });
        });
        var opening = cards.filter(function (c) { return c.hasAttribute('data-preselect') && !c.disabled; })[0]
            || cards.filter(function (c) { return c.classList.contains('is-selected') && !c.disabled; })[0]
            || cards.filter(function (c) { return !c.disabled; })[0];
        if (opening) selectVariant(opening);
    }

    /* ── buy ───────────────────────────────────────────────────────────── */

    function buyHint(message) {
        var hint = $('[data-p2-buyhint]', root);
        if (!hint) { if (message) toast(message); return; }
        hint.textContent = message || '';
        hint.hidden = !message;
    }

    function addToCartAndCheckout() {
        var body = new URLSearchParams();
        body.append('quantity', state.qty);
        body.append('selected_variant', state.variantLabel || '');
        body.append('selected_variation', state.variationId || '');
        fetch(CFG.addToCartUrl, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': CFG.csrf || '',
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: body.toString(),
            credentials: 'same-origin'
        }).then(function () {
            window.location.href = CFG.checkoutUrl;
        }).catch(function () {
            window.location.href = CFG.checkoutUrl;
        });
    }

    function initBuy() {
        // Every match, so the buy box and the sticky bar share one code path.
        $$('[data-p2-buy]').forEach(function (button) {
            button.addEventListener('click', function () {
                if (state.hasVariations && !state.variationId) {
                    buyHint('Please choose an option first.');
                    return;
                }
                buyHint('');
                // openCheckout returns false when the page has no modal — that
                // is the whole fallback switch, no config flag needed.
                if (!openCheckout()) addToCartAndCheckout();
            });
        });
    }

    /* ── sticky bar ────────────────────────────────────────────────────── */

    function initSticky() {
        var bar = $('[data-p2-sticky]');
        var box = $('[data-p2-buybox]', root);
        if (!bar || !box) return;
        if (!('IntersectionObserver' in window)) { bar.hidden = false; return; }

        new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                bar.hidden = entry.isIntersecting || entry.boundingClientRect.top > 0;
            });
        }, { threshold: 0 }).observe(box);
    }

    /* ── checkout modal ────────────────────────────────────────────────── */

    var modal = $('[data-p2-modal]');
    var branchesByDistrict = null;

    function coFail(message) {
        var box = $('[data-p2-co-error]', modal);
        if (!box) return;
        box.textContent = message || '';
        box.hidden = !message;
    }

    function coRender(quote) {
        if (!modal || !quote) return;
        var set = function (sel, value) {
            var node = $(sel, modal);
            if (node) node.textContent = value;
        };
        set('[data-p2-co-subtotal]', money(quote.subtotal));
        set('[data-p2-co-shipping]', quote.shippingIsFree ? 'Free' : money(quote.shipping));
        set('[data-p2-co-total]', money(quote.total));

        var qtyField = $('[data-p2-co-qty]', modal);
        var qtyHidden = $('[data-p2-co-qty-field]', modal);
        if (qtyField) { qtyField.value = quote.qty; qtyField.setAttribute('max', quote.ceiling); }
        if (qtyHidden) qtyHidden.value = quote.qty;

        var variant = $('[data-p2-co-variant]', modal);
        if (variant) {
            variant.textContent = quote.variantLabel || '';
            variant.hidden = !quote.variantLabel;
        }

        var nudge = $('[data-p2-co-nudge]', modal);
        if (nudge) {
            nudge.hidden = !quote.nudge;
            if (quote.nudge) {
                set('[data-p2-co-nudge-text]',
                    'Add ' + quote.nudge.need + ' more to save ' + money(quote.nudge.saveTotal));
                set('[data-p2-co-nudge-count]', quote.qty + ' of ' + quote.nudge.minQty);
                var bar = $('[data-p2-co-nudge-bar]', modal);
                if (bar) bar.style.width = quote.nudge.progress + '%';
            }
        }
    }

    function coQuote(qty) {
        if (!modal) return Promise.resolve(null);
        var district = $('[data-p2-co-district]', modal);
        var code = $('[data-p2-co-branch-code]', modal);
        return post(CFG.quoteUrl, {
            variation: state.variationId,
            quantity: qty === undefined ? state.qty : qty,
            district: district ? district.value : '',
            courier_branch_code: code ? code.value : ''
        }).then(function (payload) {
            if (!payload.success) { coFail(payload.message || 'Could not price this order.'); return null; }
            coFail('');
            coRender(payload.quote);
            return payload.quote;
        }).catch(function () {
            coFail('Could not reach the server. Please try again.');
            return null;
        });
    }

    function openCheckout() {
        if (!modal) return false;
        var variation = $('[data-p2-co-variation]', modal);
        if (variation) variation.value = state.variationId || '';
        var image = $('[data-p2-co-image]', modal);
        var current = $('[data-p2-slide].is-active img', root);
        if (image && current) image.setAttribute('src', current.getAttribute('src'));

        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        loadBranches();
        coQuote(state.qty);
        var first = $('input[name="full_name"]', modal);
        if (first) setTimeout(function () { first.focus(); }, 40);
        return true;
    }

    function closeCheckout() {
        if (!modal) return;
        modal.hidden = true;
        document.body.style.overflow = '';
    }

    function loadBranches() {
        if (branchesByDistrict !== null || !modal) return;
        branchesByDistrict = {};
        fetch(CFG.locationsUrl, { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                (data.districts || []).forEach(function (entry) {
                    branchesByDistrict[String(entry.name).toUpperCase()] = entry.branches || [];
                });
                fillBranches();
            })
            .catch(function () { /* the courier API is down — the order still posts */ });
    }

    function fillBranches() {
        if (!modal || !branchesByDistrict) return;
        var district = $('[data-p2-co-district]', modal);
        var select = $('[data-p2-co-branch]', modal);
        var code = $('[data-p2-co-branch-code]', modal);
        if (!district || !select) return;

        var list = branchesByDistrict[String(district.value).toUpperCase()] || [];
        select.innerHTML = '<option value="">Nearest branch</option>' + list.map(function (b) {
            return '<option value="' + b.name + '" data-code="' + b.code + '">' + b.name + '</option>';
        }).join('');
        if (code) code.value = '';
    }

    function initCheckout() {
        if (!modal) return;

        $$('[data-p2-modal-close]', modal).forEach(function (node) {
            node.addEventListener('click', closeCheckout);
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && !modal.hidden) closeCheckout();
        });

        var minus = $('[data-p2-co-minus]', modal);
        var plus = $('[data-p2-co-plus]', modal);
        var qty = $('[data-p2-co-qty]', modal);
        var district = $('[data-p2-co-district]', modal);
        var branch = $('[data-p2-co-branch]', modal);
        var code = $('[data-p2-co-branch-code]', modal);

        function reprice(next) {
            var wanted = Math.max(1, parseInt(next, 10) || 1);
            coQuote(wanted).then(function (quote) {
                if (quote) { state.qty = quote.qty; setQty(quote.qty); }
            });
        }

        if (minus) minus.addEventListener('click', function () { reprice((parseInt(qty.value, 10) || 1) - 1); });
        if (plus) plus.addEventListener('click', function () { reprice((parseInt(qty.value, 10) || 1) + 1); });
        if (qty) qty.addEventListener('change', function () { reprice(qty.value); });

        if (district) {
            district.addEventListener('change', function () {
                fillBranches();
                coQuote();
            });
        }
        if (branch) {
            branch.addEventListener('change', function () {
                var picked = branch.options[branch.selectedIndex];
                if (code) code.value = picked ? (picked.getAttribute('data-code') || '') : '';
                coQuote();
            });
        }

        var form = $('[data-p2-co-form]', modal);
        var submit = $('[data-p2-co-submit]', modal);
        if (!form) return;

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            coFail('');
            var data = {};
            new FormData(form).forEach(function (value, key) { data[key] = value; });
            // The quantity and option are read from page state, and price is
            // never posted at all — the server re-quotes both.
            data.variation = state.variationId || '';
            data.quantity = qty ? qty.value : state.qty;

            var label = submit ? submit.textContent : '';
            if (submit) { submit.disabled = true; submit.textContent = 'Placing order…'; }

            post(form.getAttribute('action'), data).then(function (payload) {
                if (payload.success && payload.redirect) {
                    window.location.href = payload.redirect;
                    return;
                }
                if (submit) { submit.disabled = false; submit.textContent = label; }
                var message = payload.message || 'Could not place the order.';
                if (payload.errors) {
                    var first = Object.keys(payload.errors)[0];
                    if (first && payload.errors[first] && payload.errors[first][0]) {
                        message = payload.errors[first][0];
                    }
                }
                coFail(message);
            }).catch(function () {
                if (submit) { submit.disabled = false; submit.textContent = label; }
                coFail('Could not reach the server. Please try again.');
            });
        });
    }

    /* ── reviews ───────────────────────────────────────────────────────── */

    function initReviews() {
        var picker = $('[data-p2-starpick]', root);
        var value = $('[data-p2-starvalue]', root);
        if (picker && value) {
            var stars = $$('span', picker);
            var paint = function (n) {
                stars.forEach(function (star, i) { star.classList.toggle('on', i < n); });
            };
            stars.forEach(function (star, i) {
                star.addEventListener('click', function () {
                    value.value = i + 1;
                    paint(i + 1);
                });
            });
            paint(parseInt(value.value, 10) || 5);
        }

        var form = $('[data-p2-review-form]', root);
        if (form) {
            form.addEventListener('submit', function (event) {
                event.preventDefault();
                var data = {};
                new FormData(form).forEach(function (v, k) { data[k] = v; });
                var box = $('[data-p2-review-msg]', form);
                post(form.getAttribute('action'), data).then(function (payload) {
                    if (box) {
                        box.textContent = payload.message || '';
                        box.hidden = !payload.message;
                        box.classList.toggle('is-error', !payload.success);
                    }
                    if (payload.success) {
                        form.reset();
                        setTimeout(function () { window.location.reload(); }, 900);
                    }
                });
            });
        }

        $$('[data-p2-vote]', root).forEach(function (button) {
            button.addEventListener('click', function () {
                var card = button.closest('[data-p2-review]');
                if (!card) return;
                var id = card.getAttribute('data-p2-review');
                post(CFG.voteUrlBase + id + '/vote/', { value: button.getAttribute('data-p2-vote') })
                    .then(function (payload) {
                        if (!payload.success) { toast(payload.message || 'Could not record that vote.'); return; }
                        $$('[data-p2-vote]', card).forEach(function (b) { b.disabled = true; });
                        var up = $('[data-p2-vote="up"] [data-p2-vote-count]', card);
                        var down = $('[data-p2-vote="down"] [data-p2-vote-count]', card);
                        if (up) up.textContent = payload.up;
                        if (down) down.textContent = payload.down;
                    });
            });
        });
    }

    /* ── back in stock ─────────────────────────────────────────────────── */

    function initRestock() {
        var form = $('[data-p2-restock]', root);
        if (!form) return;
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var data = {};
            new FormData(form).forEach(function (v, k) { data[k] = v; });
            var box = $('[data-p2-restock-msg]', form);
            post(form.getAttribute('action'), data).then(function (payload) {
                if (!box) return;
                box.textContent = payload.message || 'We will let you know.';
                box.hidden = false;
            });
        });
    }

    /* ── go ────────────────────────────────────────────────────────────── */

    document.addEventListener('DOMContentLoaded', function () {
        initGallery();
        initZoom();
        initVideos();
        initQty();
        initTiers();
        initVariants();
        updatePrice();
        initBuy();
        initSticky();
        initCheckout();
        initReviews();
        initRestock();
    });
})();
