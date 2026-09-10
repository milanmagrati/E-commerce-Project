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

    // Badge text on a quantity break is typed by an administrator, so it is
    // escaped before going anywhere near innerHTML.
    function esc(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
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

    function initBuyColumn(gallery) {
        var root = $('[data-buy]');
        if (!root) return;

        var maxStock = parseInt(root.dataset.maxStock || '0', 10);
        var unitPrice = parseFloat(root.dataset.price || '0');
        var hasVariations = root.dataset.hasVariations === '1';
        var qtyInput = $('[data-qty]', root);
        var minus = $('[data-qty-minus]', root);
        var plus = $('[data-qty-plus]', root);
        var cartQty = $('[data-cart-qty]', root);
        var cartVariant = $('[data-cart-variant]', root);
        var cartVariationId = $('[data-cart-variation-id]', root);
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
            // Re-price before the order panel is told the new quantity, so the
            // panel reads the rate this quantity has just earned.
            applyBulk(n);
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

        /* ── variations ──
           Each tile is one real ProductVariation. Picking one drives the
           price, the stock line, the quantity ceiling, the gallery image and
           the "(N in cart)" figure, and unlocks the buy buttons; nothing can
           be added or ordered until one is chosen. Sold-out tiles are rendered
           disabled, so anything that reaches selectVariation() is buyable. */

        var selectedVariation = null;   // {id, label, price, stock, image}
        var needsBtns = $$('[data-needs-variation]');
        var variationHint = $('[data-variation-hint]');
        var priceDisplay = $('[data-price-display]', root);
        var stockLine = $('[data-stock-line]', root);
        var variationValue = $('[data-variation-value]', root);
        var variationClear = $('[data-variation-clear]', root);
        var variationGroup = $('[data-variation-group]', root);
        var variationTrack = $('[data-variation-track]', root);
        var variationPrev = $('[data-variation-prev]', root);
        var variationNext = $('[data-variation-next]', root);
        var qtyBlock = $('[data-qty-block]', root);
        var tiles = $$('[data-variation]', root);
        var selectable = tiles.filter(function (t) { return !t.disabled; });

        // The unchosen state, captured before any pick so "Clear" can put the
        // price row, stock line, image and buttons back exactly as the page
        // first rendered them.
        var basePrice = unitPrice;
        var baseMaxStock = maxStock;
        var basePriceLabel = priceDisplay ? priceDisplay.textContent.trim() : '';
        var baseStockClass = stockLine ? stockLine.className : '';
        var baseStockText = stockLine ? stockLine.textContent.trim() : '';
        var baseInCart = parseInt(root.dataset.inCart || '0', 10);

        // What the cart already holds of each variation, so the label beside
        // the stepper follows whichever option is on screen rather than
        // reporting the first line found for this product.
        var inCartMap = readJSON('[data-in-cart-map]', {});

        function readJSON(sel, fallback) {
            var node = $(sel, root);
            if (!node) return fallback;
            try { return JSON.parse(node.textContent || '') || fallback; }
            catch (err) { return fallback; }
        }

        function setHint(message) {
            if (!variationHint) return;
            variationHint.textContent = message || '';
            variationHint.hidden = !message;
        }

        // The product's own first photo, kept so that moving from a variation
        // that has its own image to one that does not puts the product shot
        // back rather than leaving the previous variation's photo on screen.
        var baseSlideSrc = '';
        if (gallery && gallery.slides.length) {
            var firstImg = gallery.slides[0].querySelector('img');
            baseSlideSrc = firstImg ? firstImg.src : '';
        }

        function showVariationImage(url) {
            if (!gallery || !gallery.slides.length) return;
            var next = url || baseSlideSrc;
            if (!next) return;
            var img = gallery.slides[0].querySelector('img');
            if (img && img.src !== next) img.src = next;
            var thumb = gallery.thumbs[0] && gallery.thumbs[0].querySelector('img');
            if (thumb && thumb.src !== next) thumb.src = next;
            if (url) gallery.go(0);
        }

        function paintTiles(activeTile) {
            tiles.forEach(function (t) {
                var on = t === activeTile;
                t.classList.toggle('is-selected', on);
                t.setAttribute('aria-pressed', on ? 'true' : 'false');
                // Each enabled option is its own tab stop: the group is a set of
                // toggles the shopper can switch on and back off, not a radio
                // group that traps the choice once it is made.
                t.tabIndex = t.disabled ? -1 : 0;
            });
        }

        function selectVariation(tile, moveFocus) {
            if (!tile || tile.disabled) return;

            // A second activation of the chosen option clears it.
            if (selectedVariation && selectedVariation.id === tile.dataset.variationId) {
                clearVariation(tile);
                return;
            }

            paintTiles(tile);
            revealCard(tile);
            if (moveFocus) tile.focus();

            selectedVariation = {
                id: tile.dataset.variationId,
                label: tile.dataset.label || '',
                price: parseFloat(tile.dataset.price || '0'),
                stock: parseInt(tile.dataset.stock || '0', 10),
                image: tile.dataset.image || ''
            };

            unitPrice = selectedVariation.price;
            maxStock = selectedVariation.stock;

            if (variationValue) {
                variationValue.textContent = selectedVariation.label;
                variationValue.classList.add('is-set');
            }
            if (variationClear) variationClear.hidden = false;
            // The option's own list price and its own quantity-break ladder.
            // applyBulk(), at the end of syncQuantity(), paints the price row
            // from these — either the rung's rate or this label.
            currentPriceLabel = tile.dataset.priceLabel || basePriceLabel;
            bulkTiers = bulkMap[selectedVariation.id] || [];
            renderTiers();
            if (stockLine) {
                stockLine.className = 'pdp-stock in';
                stockLine.textContent = 'In stock — ' + selectedVariation.stock + ' available';
            }
            if (cartVariant) cartVariant.value = selectedVariation.label;
            if (cartVariationId) cartVariationId.value = selectedVariation.id;

            needsBtns.forEach(function (btn) {
                btn.classList.remove('is-awaiting');
                btn.removeAttribute('aria-disabled');
            });
            if (qtyBlock) qtyBlock.removeAttribute('data-locked');
            setHint('');
            setInCart(parseInt(inCartMap[selectedVariation.id] || 0, 10));

            showVariationImage(selectedVariation.image);
            if (qtyInput) {
                if (maxStock > 0) qtyInput.max = maxStock;
                qtyInput.value = 1;
            }
            if (panel && panel.orderForm) {
                panel.orderForm.setUnitPrice(unitPrice);
                panel.orderForm.setMaxQty(maxStock || 99);
            }
            syncQuantity();
        }

        // Back out of a choice: everything selectVariation() touched goes back
        // to the state captured at load, and the buy buttons re-lock.
        function clearVariation(focusTile) {
            if (!selectedVariation) return;
            selectedVariation = null;
            paintTiles(null);

            unitPrice = basePrice;
            maxStock = baseMaxStock;

            if (variationValue) {
                variationValue.textContent = 'Not chosen yet';
                variationValue.classList.remove('is-set');
            }
            if (variationClear) variationClear.hidden = true;
            currentPriceLabel = basePriceLabel;
            bulkTiers = bulkMap['0'] || [];
            renderTiers();
            if (stockLine) {
                stockLine.className = baseStockClass;
                stockLine.textContent = baseStockText;
            }
            if (cartVariant) cartVariant.value = '';
            if (cartVariationId) cartVariationId.value = '';

            needsBtns.forEach(function (btn) {
                btn.classList.add('is-awaiting');
                btn.setAttribute('aria-disabled', 'true');
            });
            if (qtyBlock) qtyBlock.setAttribute('data-locked', '');
            setHint('');
            setInCart(baseInCart);

            showVariationImage('');
            if (qtyInput) {
                if (baseMaxStock > 0) qtyInput.max = baseMaxStock;
                else qtyInput.removeAttribute('max');
                qtyInput.value = 1;
            }
            if (panel && panel.orderForm) {
                panel.orderForm.setUnitPrice(basePrice);
                panel.orderForm.setMaxQty(baseMaxStock || 99);
            }
            syncQuantity();

            var target = (focusTile && !focusTile.disabled) ? focusTile : selectable[0];
            if (target) target.focus();
        }

        tiles.forEach(function (tile) {
            tile.tabIndex = tile.disabled ? -1 : 0;
            if (tile.disabled) return;
            tile.addEventListener('click', function () { selectVariation(tile); });
            tile.addEventListener('keydown', function (e) {
                var step = 0;
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') step = 1;
                else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') step = -1;
                else return;
                e.preventDefault();
                var at = selectable.indexOf(tile);
                var next = selectable[(at + step + selectable.length) % selectable.length];
                if (next) next.focus();
            });
        });

        if (variationClear) {
            variationClear.addEventListener('click', function () { clearVariation(); });
        }
        // Escape anywhere in the option group drops the current choice.
        if (variationGroup) {
            variationGroup.addEventListener('keydown', function (e) {
                if (e.key === 'Escape' && selectedVariation) {
                    e.preventDefault();
                    clearVariation();
                }
            });
        }

        /* ── the option rail ──
           Prev / next stay hidden until the track actually overflows, and each
           end-stops itself when the track is resting against that edge. The
           step is a little under one viewport so a card is never sliced in
           half at the fold. */
        if (variationTrack) {
            var railStep = function () {
                var card = variationTrack.querySelector('[data-variation]');
                var cardW = card ? card.offsetWidth + 10 : 160;   // 10 = track gap
                var byView = variationTrack.clientWidth * 0.8;
                return Math.max(cardW, byView);
            };
            var scrollRail = function (dir) {
                variationTrack.scrollBy({ left: dir * railStep(), behavior: 'smooth' });
            };
            var updateNav = function () {
                var overflow = variationTrack.scrollWidth - variationTrack.clientWidth;
                var scrollable = overflow > 2;
                if (variationPrev) variationPrev.hidden = !scrollable;
                if (variationNext) variationNext.hidden = !scrollable;
                if (!scrollable) return;
                var x = variationTrack.scrollLeft;
                if (variationPrev) variationPrev.disabled = x <= 1;
                if (variationNext) variationNext.disabled = x >= overflow - 1;
            };
            if (variationPrev) {
                variationPrev.addEventListener('click', function () { scrollRail(-1); });
            }
            if (variationNext) {
                variationNext.addEventListener('click', function () { scrollRail(1); });
            }
            var navTick = false;
            variationTrack.addEventListener('scroll', function () {
                if (navTick) return;
                navTick = true;
                window.requestAnimationFrame(function () { navTick = false; updateNav(); });
            });
            window.addEventListener('resize', updateNav);
            window.addEventListener('load', updateNav);
            updateNav();
        }

        // Bring a card fully into view when it is picked (matters for the
        // preselected / deep-linked option, which may start off-screen).
        // Assigned only when the rail exists; selectVariation guards on it.
        function revealCard(tile) {
            if (!variationTrack || !tile) return;
            var t = tile.offsetLeft;
            var r = t + tile.offsetWidth;
            var vl = variationTrack.scrollLeft;
            var vr = vl + variationTrack.clientWidth;
            if (t < vl) variationTrack.scrollTo({ left: t - 8, behavior: 'smooth' });
            else if (r > vr) variationTrack.scrollTo({ left: r - variationTrack.clientWidth + 8, behavior: 'smooth' });
        }

        /* ── quantity breaks ──
           One ladder per line: the '0' key for a plain product, one entry per
           variation id for a variable one, so switching options re-prices
           without another request. Each rung is also a shortcut — pressing it
           sets the stepper to that quantity. */

        var bulkBlock = $('[data-bulk]', root);
        var bulkTierWrap = $('[data-bulk-tiers]', root);
        var bulkSaved = $('[data-bulk-saved]', root);
        var bulkNudge = $('[data-bulk-nudge]', root);
        var bulkFor = $('[data-bulk-for]', root);
        var bulkAwait = $('[data-bulk-await]', root);
        // The sticky bar lives outside the buy column and is all a shopper can
        // see once the price row scrolls away, so it tracks the same figure.
        var stickyPrice = stickyBar ? $('[data-sticky-price]', stickyBar) : null;
        var priceWas = $('[data-price-was]', root);
        var bulkMap = readJSON('[data-bulk-map]', {});
        // A variable product ships no '0' ladder: with no option picked there
        // is no price to discount, so the block stays hidden until there is.
        var bulkTiers = bulkMap['0'] || [];
        // What the price row reads when no rung is in force. Follows the
        // chosen option, so clearing a choice puts the range back.
        var currentPriceLabel = basePriceLabel;

        function tierFor(qty) {
            // The cheapest applicable rung, not simply the highest min_qty:
            // a ladder typed out of order can then never charge more for more.
            var best = null;
            bulkTiers.forEach(function (t) {
                if (qty < t.minQty) return;
                if (!best || t.unitPrice < best.unitPrice) best = t;
            });
            return best;
        }

        function nextTierFor(qty) {
            var next = null;
            bulkTiers.forEach(function (t) {
                if (t.minQty <= qty) return;
                if (!next || t.minQty < next.minQty) next = t;
            });
            return next;
        }

        // Handed to the order panel so its own stepper re-prices the same way.
        function effectiveUnit(qty) {
            var tier = tierFor(qty);
            return tier ? tier.unitPrice : unitPrice;
        }

        function renderTiers() {
            if (!bulkTierWrap) return;

            // Name the option these rungs price, so the summary pill on the
            // card and the ladder here read as one thing rather than two.
            if (bulkFor) {
                var who = (selectedVariation && bulkTiers.length) ? selectedVariation.label : '';
                bulkFor.textContent = who;
                bulkFor.hidden = !who;
            }

            if (!bulkTiers.length) {
                bulkTierWrap.innerHTML = '';
                // Nothing picked yet on a variable product: hold the block open
                // and say what to do. Anything else — an option that simply has
                // no break, or a plain product with none — has nothing to say,
                // so the block goes away rather than showing an empty promise.
                var awaiting = !!bulkAwait && !selectedVariation;
                if (bulkAwait) bulkAwait.hidden = !awaiting;
                if (bulkBlock) bulkBlock.hidden = !awaiting;
                if (bulkSaved) bulkSaved.hidden = true;
                if (bulkNudge) bulkNudge.hidden = true;
                return;
            }
            if (bulkAwait) bulkAwait.hidden = true;
            bulkTierWrap.innerHTML = bulkTiers.map(function (t) {
                return '<button type="button" class="pdp-bulk-tier" data-bulk-tier'
                    + ' data-min-qty="' + t.minQty + '" aria-pressed="false">'
                    + '<span class="pdp-bulk-tier-qty">' + t.minQty + 'pcs</span>'
                    + '<span class="pdp-bulk-tier-save">' + esc(t.badge) + '</span>'
                    + '<span class="pdp-bulk-tier-each">' + money(t.unitPrice) + ' each</span>'
                    + '</button>';
            }).join('');
            if (bulkBlock) bulkBlock.hidden = false;
        }

        function applyBulk(qty) {
            var tier = bulkTiers.length ? tierFor(qty) : null;

            $$('[data-bulk-tier]', root).forEach(function (chip) {
                var on = !!tier && parseInt(chip.dataset.minQty, 10) === tier.minQty;
                chip.classList.toggle('is-active', on);
                chip.setAttribute('aria-pressed', on ? 'true' : 'false');
            });

            if (priceDisplay) {
                priceDisplay.textContent = tier ? money(tier.unitPrice) : currentPriceLabel;
                if (stickyPrice) stickyPrice.textContent = priceDisplay.textContent;
            }
            if (priceWas) {
                priceWas.textContent = tier ? currentPriceLabel : '';
                priceWas.hidden = !tier;
            }
            if (bulkSaved) {
                var saved = tier ? tier.saveEach * qty : 0;
                bulkSaved.textContent = saved > 0 ? 'You save ' + money(saved) : '';
                bulkSaved.hidden = saved <= 0;
            }
            if (bulkNudge) {
                var next = bulkTiers.length ? nextTierFor(qty) : null;
                if (next) {
                    var more = next.minQty - qty;
                    var reward = next.type === 'percent'
                        ? 'save ' + next.savePercent + '%'
                        : 'pay ' + money(next.unitPrice) + ' each';
                    bulkNudge.textContent = 'Add ' + more + ' more to ' + reward + '.';
                    bulkNudge.hidden = false;
                } else {
                    bulkNudge.textContent = '';
                    bulkNudge.hidden = true;
                }
            }
        }

        // Delegated: the rungs are re-rendered whenever the option changes.
        if (bulkTierWrap) {
            bulkTierWrap.addEventListener('click', function (e) {
                var chip = e.target.closest && e.target.closest('[data-bulk-tier]');
                if (!chip) return;
                if (!variationChosen()) return;
                if (qtyInput) qtyInput.value = chip.dataset.minQty;
                syncQuantity();
                if (qtyInput) qtyInput.focus();
            });
        }

        function variationChosen() {
            if (!hasVariations || (selectedVariation && selectedVariation.id)) return true;
            // Say what to do, next to the buttons that were blocked, and put
            // the shopper on the first option rather than firing a toast at
            // the far corner of the screen.
            setHint('Choose an option to continue.');
            if (selectable[0]) selectable[0].focus();
            return false;
        }

        /* ── the inline order panel ── */

        var slot = $('[data-order-slot]');
        var panel = slot ? $('[data-order-form]', slot) : null;

        function syncOrderPanel() {
            if (!panel || !panel.orderForm) return;
            panel.orderForm.setQuantity(quantity());
            var variant = cartVariant ? cartVariant.value : '';
            var hidden = panel.querySelector('[data-variant-input]');
            var varIdInput = panel.querySelector('[data-variation-id-input]');
            var label = panel.querySelector('[data-variant-label]');
            if (hidden) hidden.value = variant;
            if (varIdInput) varIdInput.value = (selectedVariation && selectedVariation.id) || '';
            if (label) {
                label.textContent = variant;
                label.hidden = !variant;
            }
        }

        function openOrderPanel(mode) {
            if (!slot || !panel) return;
            if (!variationChosen()) return;
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
                if (hasVariations && !variationChosen()) {
                    e.preventDefault();
                    return;
                }
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
                    // Remember it against the variation, so switching options
                    // and switching back still reports the right figure.
                    if (selectedVariation) {
                        inCartMap[selectedVariation.id] = inCart;
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
                    // Both wordings are set by Setup → Store Button Labels and
                    // ride on the button itself; the literals are only what the
                    // page shipped with before that screen existed.
                    if (text) {
                        text.textContent = data.added
                            ? (save.dataset.savedLabel || 'Saved')
                            : (save.dataset.saveLabel || 'Save for later');
                    }
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

        // The order panel has its own stepper, so it needs to re-price the
        // same way the buy column does rather than holding one fixed rate.
        if (panel && panel.orderForm && panel.orderForm.setPriceResolver) {
            panel.orderForm.setPriceResolver(effectiveUnit);
        }

        syncQuantity();

        // Open on a specific option when the URL named one (a shared link, or
        // "edit" from the cart), otherwise auto-pick when there's only one
        // buyable option — a choice with one answer is not a choice. Runs last,
        // once the order panel and in-cart figure are wired up.
        var preselectTile = tiles.filter(function (t) {
            return t.dataset.preselect === '1' && !t.disabled;
        })[0];
        if (preselectTile) {
            selectVariation(preselectTile);
        } else if (hasVariations && selectable.length === 1) {
            selectVariation(selectable[0]);
        }
    }

    /* ====================================================================
       Back-in-stock: "tell me when it's back" on a sold-out product
       ==================================================================== */

    function initRestockForm() {
        var form = $('[data-restock-form]');
        if (!form) return;
        var msg = $('[data-restock-msg]', form);
        var email = $('[data-restock-email]', form);
        var button = form.querySelector('button[type="submit"]');

        function say(text, ok) {
            if (!msg) return;
            msg.textContent = text;
            msg.hidden = !text;
            form.classList.toggle('is-done', !!ok);
        }

        form.addEventListener('submit', function (e) {
            if (!window.fetch) return;  // let the plain POST through
            e.preventDefault();
            if (email && !email.value.trim()) { say('Enter your email first.', false); email.focus(); return; }

            var label = button ? button.textContent : '';
            if (button) { button.disabled = true; button.textContent = 'Saving…'; }

            fetch(form.action, {
                method: 'POST',
                body: new FormData(form),
                headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' }
            }).then(function (r) {
                return r.json().catch(function () { return {}; });
            }).then(function (data) {
                if (data && data.success) {
                    say(data.message || "You're on the list.", true);
                    if (email) email.disabled = true;
                    if (button) button.textContent = 'Done';
                    return;
                }
                say((data && data.message) || 'Could not save that — try again.', false);
                if (button) { button.disabled = false; button.textContent = label; }
            }).catch(function () {
                say('Network trouble — try again.', false);
                if (button) { button.disabled = false; button.textContent = label; }
            });
        });
    }

    /* ==================================================================== */

    function init() {
        initAccountMenu();
        initCartNote();
        initAccordions();
        initShare();
        initStarInput();
        initRestockForm();

        var gallery = null;
        var galleryRoot = $('[data-gallery]');
        if (galleryRoot) {
            gallery = new Gallery(galleryRoot);
            initLightbox(gallery);
        }

        initBuyColumn(gallery);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
