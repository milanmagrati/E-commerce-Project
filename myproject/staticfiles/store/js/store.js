/* ═══════════════════════════════════════════════
   STORE.JS — Interactions & AJAX
   ═══════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', function () {

    // ─── Toast Notifications ───
    window.showToast = function (message, type) {
        type = type || 'success';
        const container = document.getElementById('toast-container');
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = 'toast-notification';
        toast.style.borderLeftColor = type === 'error' ? '#E62929' : '#00B14F';
        toast.innerHTML = `
            <span class="toast-icon">${type === 'error' ? '!' : '&#10003;'}</span>
            <span class="toast-msg">${message}</span>
            <button class="toast-close-btn" onclick="this.parentElement.remove()">&times;</button>
        `;
        container.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(100px)';
            toast.style.transition = 'all 0.3s';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    };

    // ─── Hero Slider ───
    const sliderTrack = document.getElementById('sliderTrack');
    const dotsContainer = document.getElementById('sliderDots');
    if (sliderTrack) {
        const slides = sliderTrack.querySelectorAll('.slide');
        let current = 0;
        const total = slides.length;

        // Create dots
        for (let i = 0; i < total; i++) {
            const dot = document.createElement('span');
            dot.className = 'slider-dot' + (i === 0 ? ' active' : '');
            dot.addEventListener('click', () => goToSlide(i));
            dotsContainer.appendChild(dot);
        }

        function goToSlide(index) {
            current = index;
            sliderTrack.style.transform = `translateX(-${current * 100}%)`;
            document.querySelectorAll('.slider-dot').forEach((d, i) => {
                d.classList.toggle('active', i === current);
            });
        }

        document.getElementById('sliderPrev')?.addEventListener('click', () => {
            goToSlide((current - 1 + total) % total);
        });
        document.getElementById('sliderNext')?.addEventListener('click', () => {
            goToSlide((current + 1) % total);
        });

        // Auto rotate
        setInterval(() => goToSlide((current + 1) % total), 4000);
    }

    // ─── Flash Sale Countdown ───
    const cdHours = document.getElementById('cdHours');
    const cdMinutes = document.getElementById('cdMinutes');
    const cdSeconds = document.getElementById('cdSeconds');
    if (cdHours) {
        function updateCountdown() {
            const now = new Date();
            const end = new Date(now);
            end.setHours(23, 59, 59, 999);
            const diff = end - now;
            const h = Math.floor(diff / 3600000);
            const m = Math.floor((diff % 3600000) / 60000);
            const s = Math.floor((diff % 60000) / 1000);
            cdHours.textContent = String(h).padStart(2, '0');
            cdMinutes.textContent = String(m).padStart(2, '0');
            cdSeconds.textContent = String(s).padStart(2, '0');
        }
        updateCountdown();
        setInterval(updateCountdown, 1000);
    }

    // ─── Search Autocomplete ───
    const searchInput = document.getElementById('searchInput');
    const dropdown = document.getElementById('autocompleteDropdown');
    let debounceTimer;

    if (searchInput && dropdown) {
        searchInput.addEventListener('input', function () {
            clearTimeout(debounceTimer);
            const q = this.value.trim();
            if (q.length < 2) {
                dropdown.classList.remove('show');
                dropdown.innerHTML = '';
                return;
            }
            debounceTimer = setTimeout(() => {
                fetch(STORE_URLS.searchAutocomplete + '?q=' + encodeURIComponent(q))
                    .then(r => r.json())
                    .then(data => {
                        if (data.results.length) {
                            dropdown.innerHTML = data.results.map(r =>
                                `<div class="autocomplete-item" data-slug="${r.slug}">${r.name}</div>`
                            ).join('');
                            dropdown.classList.add('show');
                        } else {
                            dropdown.classList.remove('show');
                        }
                    });
            }, 300);
        });

        dropdown.addEventListener('click', function (e) {
            const item = e.target.closest('.autocomplete-item');
            if (item) {
                window.location.href = '/store/products/' + item.dataset.slug + '/';
            }
        });

        document.addEventListener('click', function (e) {
            if (!e.target.closest('.search-input-wrap')) {
                dropdown.classList.remove('show');
            }
        });
    }

    // ─── Add to Cart (AJAX for product cards — uses event delegation) ───
    document.addEventListener('submit', function (e) {
        const form = e.target.closest('.add-to-cart-form');
        if (!form) return;
        e.preventDefault();
        const formData = new FormData(form);
        fetch(form.action, {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': CSRF_TOKEN },
            body: formData,
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast(data.message);
                const badge = document.getElementById('cartBadge');
                if (badge) badge.textContent = data.count;
                const cartIcon = document.getElementById('cartIconLink');
                if (cartIcon) {
                    cartIcon.classList.add('shake');
                    setTimeout(() => cartIcon.classList.remove('shake'), 500);
                }
            } else if (data.message) {
                showToast(data.message, 'error');
            }
        })
        .catch(() => {
            form.submit();
        });
    });

    // ─── Wishlist Hearts (product cards — uses event delegation) ───
    document.addEventListener('click', function (e) {
        const btn = e.target.closest('.wishlist-heart');
        if (!btn) return;
        e.preventDefault();
        e.stopPropagation();
        const pid = btn.dataset.productId;
        if (!IS_AUTHENTICATED) {
            window.location = '/store/login/?next=' + window.location.pathname;
            return;
        }
        fetch('/store/wishlist/toggle/' + pid + '/', {
            method: 'POST',
            headers: { 'X-CSRFToken': CSRF_TOKEN, 'X-Requested-With': 'XMLHttpRequest' },
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                btn.classList.toggle('active', data.added);
                const svg = btn.querySelector('svg');
                svg.setAttribute('fill', data.added ? '#F85606' : 'none');
                svg.setAttribute('stroke', data.added ? '#F85606' : '#999');
                showToast(data.message);
                btn.style.transform = 'scale(1.3)';
                setTimeout(() => { btn.style.transform = ''; }, 200);
            }
        });
    });

    // ─── Load More (Just For You) ───
    const loadMoreBtn = document.getElementById('loadMoreBtn');
    const justForYouGrid = document.getElementById('justForYouGrid');
    if (loadMoreBtn && justForYouGrid) {
        loadMoreBtn.addEventListener('click', function () {
            const offset = parseInt(this.dataset.offset);
            this.textContent = 'Loading...';
            this.disabled = true;

            fetch(STORE_URLS.loadMore + '?offset=' + offset)
                .then(r => r.json())
                .then(data => {
                    if (data.products.length) {
                        data.products.forEach(p => {
                            const card = document.createElement('div');
                            card.className = 'product-card scroll-reveal';
                            card.dataset.productId = p.id;
                            const imgUrl = p.image || '';
                            const imgHtml = imgUrl
                                ? `<img src="${imgUrl}" alt="${p.name}" loading="lazy">`
                                : `<div class="product-card-placeholder"><svg width="48" height="48" viewBox="0 0 24 24" fill="#ccc"><rect x="2" y="2" width="20" height="20" rx="2" fill="#f0f0f0"/></svg></div>`;

                            // Star rating
                            let stars = '';
                            for (let i = 1; i <= 5; i++) {
                                stars += `<span class="star ${i <= Math.round(p.average_rating) ? 'filled' : 'empty'}">&#9733;</span>`;
                            }

                            card.innerHTML = `
                                <a href="/store/products/${p.slug}/" class="product-card-link">
                                    <div class="product-card-img">${imgHtml}</div>
                                </a>
                                <button class="wishlist-heart" data-product-id="${p.id}" title="Add to wishlist">
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#999" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
                                </button>
                                <a href="/store/products/${p.slug}/" class="product-card-info">
                                    <h3 class="product-card-title">${p.name}</h3>
                                    <div class="product-card-rating">${stars}<span class="review-count">(${p.review_count})</span></div>
                                    <div class="product-card-price"><span class="price-current">Rs. ${p.price}</span></div>
                                </a>
                                <form method="POST" action="/store/cart/add/${p.id}/" class="add-to-cart-form">
                                    <input type="hidden" name="csrfmiddlewaretoken" value="${CSRF_TOKEN}">
                                    <input type="hidden" name="quantity" value="1">
                                    <button type="submit" class="product-card-cart-btn">
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg>
                                        Add to Cart
                                    </button>
                                </form>
                            `;
                            justForYouGrid.appendChild(card);
                            requestAnimationFrame(() => card.classList.add('visible'));
                        });
                        loadMoreBtn.dataset.offset = offset + data.products.length;
                    }
                    if (!data.has_more) {
                        loadMoreBtn.style.display = 'none';
                    } else {
                        loadMoreBtn.textContent = 'Load More';
                        loadMoreBtn.disabled = false;
                    }
                });
        });
    }

    // ─── Hamburger Menu ───
    const hamburger = document.getElementById('hamburgerBtn');
    const mobileMenu = document.getElementById('mobileMenu');
    if (hamburger && mobileMenu) {
        hamburger.addEventListener('click', () => {
            mobileMenu.classList.toggle('open');
        });
    }

    // ─── Scroll Reveal (IntersectionObserver) ───
    const revealElements = document.querySelectorAll('.section, .product-card, .order-card');
    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('scroll-reveal', 'visible');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1 });
        revealElements.forEach(el => observer.observe(el));
    }
});
