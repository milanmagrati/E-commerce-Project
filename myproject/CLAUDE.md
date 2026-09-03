# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**EcommerceAdmin** is a Django 5.2 monolith for a Nepali e-commerce operation. It handles order management, inventory/backorders, logistics webhook sync (NCM, Pick and Drop), HR/payroll (`hrm`), internal chat/todo, bill OCR + loyalty rewards, Google Sheets imports, and a customer-facing storefront (`store`) — all in one Django project under `myproject/`.

## Commands

```bash
# Run dev server
python manage.py runserver

# Migrations (apps live at repo root, not in a src/ layout)
python manage.py makemigrations <app_name>
python manage.py migrate

# Create admin user
python manage.py createsuperuser

# Celery worker (bill_rewards OCR tasks, etc.) — requires Redis broker
celery -A myproject worker -l info
```

There is no configured pytest/unittest suite — every app's `tests.py` is the empty Django boilerplate. Verification instead happens via **standalone root-level scripts** (`test_*.py`, `debug_*.py`) that call `django.setup()` manually and exercise real models/DB, e.g.:

```bash
python test_order_redirection.py
python check_roles.py
```

When asked to "add a test" for a bug fix, follow this repo's existing convention (a standalone script under root that sets up Django and asserts behavior) rather than assuming a pytest/Django TestCase harness exists — unless the user asks you to introduce one.

## Architecture

### App map (`myproject/settings.py` → `EXTERNAL_APPS`)

| App | Responsibility |
|---|---|
| `dashboard` | The core monolith — Products, Orders, Customers, Returns, Dispatches, StaffPerformance, Payroll-adjacent Setup/CompanySetup, RTV (Return to Vendor), FollowUps. `dashboard/views.py` is ~23k lines; `dashboard/models.py` is ~2k lines with ~45 models. When editing, grep for the specific view/model rather than reading the file wholesale. **The printable order invoice is data-driven** — `order_invoice.html` renders only what `dashboard/invoice_config.build_invoice_context()` hands it, from the `InvoiceTemplate` singleton + `InvoiceElement` rows edited at Setup → Invoice Customizer (`dashboard/invoice_customizer_views.py`, admin-only). Add a field to the whitelist in `invoice_config.TOKENS` rather than hardcoding it into the template. |
| `accounts` | `CustomUser` (custom auth model, `AUTH_USER_MODEL`) + `Role`. RBAC is **not** Django's permission framework — it's boolean attributes on `CustomUser` (e.g. `can_create_orders`) checked via decorators in `accounts/decorators.py` (`permission_required`, `admin_or_permission_required`, `admin_only`). `user.role == 'administrator'` or `is_superuser` always bypasses checks. |
| `ncm` | Nepal Can Move logistics integration: webhook receiver (`webhook_handler.py`), realtime status polling (`realtime_api.py`), order recovery (`order_recovery.py`). |
| `pick_and_drop` | Second logistics provider (PND), parallel structure to `ncm`. |
| `hrm` | Full HR/payroll suite: Employee, Attendance (incl. ZKTeco biometric device sync via `iclock` endpoints wired at the project root in `myproject/urls.py`), Leave, Payroll, Payslip, Advance/Bonus. Has stray `views.py.backup` / `views.py.git_orig` files — do not treat these as active code. |
| `store` | Customer-facing storefront (separate from the admin `dashboard`), own `context_processors.py` and templates. **Shopper accounts are `store.StoreCustomer`, NOT `AUTH_USER_MODEL`** — they are authenticated purely through `store/customer_auth.py` (session key `store_customer_id`), so `request.user` stays the staff user and no RBAC decorator ever sees a shopper. **Signing in is optional everywhere**: cart, wishlist, checkout, reviews and order tracking all still work for a guest keyed by `session_key`, and `adopt_guest_data()` merges that guest state into the account at sign-in. Orders are placed through the `partials/order_form.html` panel (Confirm Order / Inquiry Only) with district + NCM courier-branch selects fed by `store/services.py`; shoppers can also return via the phone + order-number lookup at `/store/track-order/`. Delivery pricing/times/zones are data-driven — `store.DeliverySetting` (site-wide defaults) + `store.DeliveryCharge` (per-district, optionally per-branch rules), administered from Setup → Delivery Charge Setup (`dashboard/delivery_charge_views.py`, admin-only); a matched rule is authoritative for its district. |
| `bill_rewards` | OCR-based receipt matching (`ocr_service.py`, `matching_service.py`) → loyalty rewards (`rewards_service.py`), async work via `tasks.py` (Celery). |
| `trendycrm` | Customer messaging CRM with a multi-model AI router (`ai_router.py`): Gemini Flash for cheap intent classification, GPT-4o for complex text/image OCR, dynamic prompts injected from `CRMPageProfile` data. Also `meta_sync.py` (Facebook/Instagram) and `crypto_utils.py`. |
| `google_sheets` | Import pipeline from Google Sheets (`gspread`) into store data. |
| `chat`, `todo` | Internal staff messaging and ticketing. |
| `integrations` | Generic external API integration surface (webhooks, serializers) under `/api/integrations/`. |
| `services/` | Cross-app shared logic, not a Django app: `sms_service.py` (provider-agnostic SMS — see below), `ncm_service.py`, `pick_and_drop_service.py`. |
| `inventory` | Atomic stock allocation/backorder services (`services.py`) and demand `forecasting.py`; no `models.py` — it operates on `dashboard` models. |

### Cross-cutting conventions

- **Timezone**: `TIME_ZONE = 'Asia/Kathmandu'`, `USE_TZ = True`. Always convert/display via `dashboard/timezone_utils.py` (`get_nepali_now`, `convert_to_nepali`, `format_nepali_datetime`) rather than raw `datetime.now()` — naive-vs-aware and UTC-vs-local mismatches here have been a recurring source of bugs (see recent commit history on attendance/biometric delete and payroll runs).
- **Decimal safety**: Use `dashboard/decimal_utils.py`'s `safe_decimal()` when parsing/storing money or quantity fields; it clamps corrupted/oversized values instead of raising `decimal.InvalidOperation`. There are also management commands (`diagnose_decimals`, `fix_decimal_corruption`, `cleanup_decimals`) for auditing existing corrupted rows.
- **Webhook security** (`ncm/webhook_handler.py`): HMAC-SHA256 verification via `X-NCM-Signature` against `NCM_WEBHOOK_SECRET`, idempotency via `WebhookLog`, and `@transaction.atomic()` wrapping. This is CSRF-exempt by necessity — don't add CSRF protection back to this endpoint.
- **SMS**: `services/sms_service.py` abstracts over `SMS_PROVIDER` (`console` / `twilio` / `sparrow` / `atuha`); provider selection and `SMS_ENABLED` are env-driven, not hardcoded.
- **Logging**: Per-subsystem rotating file handlers under `logs/` configured in `settings.py` `LOGGING` — `ncm`, `webhook`, `sms`, `hrm.adms` (ZKTeco), `integrations` loggers each write to their own file plus console. Use `logging.getLogger(<that name>)` in matching modules so entries land in the right log.
- **Custom session middleware**: `myproject/middleware.py`'s `GracefulSessionInterruptionMiddleware` handles concurrent session deletion gracefully (JSON 401 for `/api/`-ish paths, redirect-to-login otherwise) — needed because `SESSION_SAVE_EVERY_REQUEST = True`.
- **Frontend**: server-rendered Django templates (no SPA build step) with Alertify.js + AJAX polling for near-real-time updates (order status polling every 60s, batch sync on list views). `REDIS_URL` in `.env.example` is for future/optional real-time features — there is no Django Channels/ASGI routing configured (`asgi.py` is the stock passthrough).
- **Root-level clutter**: many one-off `debug_*.py`, `test_*.py`, `fix_*.py`, `patch_*.py` scripts sit at repo root from prior debugging sessions. They're historical artifacts, not part of the app — don't assume they're wired into any pipeline, but do follow their pattern (manual `django.setup()`) if asked to write a new one-off verification script.

### Storefront product page: Theme 1 and Theme 2

The storefront ships **two selectable product-page designs**, chosen at
**Setup → Product Page Theme** (`dashboard/product_theme_views.py`, admin-only):

| | Theme 1 | Theme 2 |
|---|---|---|
| Template | `store/product_detail.html` (unchanged) | `store/product_detail_conversion.html` |
| Shell | `store/base.html` (navbar, search, cart drawer) | `store/base_pdp.html` — centred logo, dark 3-column footer, nothing else |
| Assets | `store.css` / `product.css` / `product.js` | `store/css/pdp-theme2.css`, `store/js/pdp-theme2.js` only |
| Shape | media + sticky buy column, accordions below | three sticky columns: media & content, buy box, info rail |

**Theme 1 is byte-identical when selected.** `product_detail.html`, `product.js`
and `product.css` are untouched; the switch is four lines at the end of
`store/views.py::product_detail`, and everything above it builds the context both
designs share. If a change makes the two pages disagree about price, stock or
what is in the cart, the change is in the wrong place — it belongs above the
router.

**Resolution lives in `store/theme2.py`, and only there.**
`ProductPageTheme` is the singleton global; `ProductThemeOverride` is one row per
product where **blank means inherit** — that is the whole precedence rule
(`override → global → theme2.DEFAULTS`). `layout_for()` / `is_theme2()` read one
cached snapshot; the setup page calls `theme2.invalidate_cache()` after every
write, and the cache is LocMemCache, so another worker keeps its snapshot until
the 60-second TTL expires.

**Money has one authority: `store/theme2_checkout.py::quote()`.** It is called
once to render the modal and again inside the place-order handler, and *nothing*
about price is ever read from the request — a forged total changes nothing. It
refuses a variation that belongs to another product, refuses a Theme 1 product
outright, and returns the quantity **clamped**, so callers must use the returned
figure rather than the one they asked for.

Traps that are already paid for, and must stay paid for:

- **The bundle-ladder skip condition is not `has_variations`.** Theme 2 draws its
  own option cards and its own ladder from `bulk_tiers_json`; skipping the ladder
  for variable products would silently delete every variable product's quantity
  break. The ladder renders whenever the product is sellable.
- **`.lx-p2-tier` restates `flex-direction: row`** along with `align-items`,
  `justify-content` and `text-align`. A tier card that inherits `column` from a
  compact chip looks like a specificity problem and is a missing-property one.
- **The hover magnifier sets `background-size` in pixels**, computed from the
  image's own rendered box. A percentage is a share of the *panel*, so it only
  magnifies honestly when the panel's aspect ratio matches the photo's.
- **`.lx-p2-buybtn` is the button; `.lx-p2-buy` is the sticky grid column.**
  Sharing the class makes the button sticky.
- **The sticky bar and the checkout modal live outside `.lx-p2-grid`** — one is
  fixed and the other covers the viewport, and nesting either inside a
  `position: sticky` column traps it in that column's stacking context.
- **`ship_fee` is parsed by matching the first number, never by stripping
  non-digits.** `preg_replace`-style stripping turns `Rs. 100` into `.100`, which
  is ten paisa. Commas are dropped first so `Rs. 1,000` is not cut to `1`.
- **A blank `ship_fee` is the recommended setting**: delivery then comes from
  `store/services.quote_delivery()` (Setup → Delivery Charge Setup), which knows
  the district and the courier branch. A flat fee reaches the written order
  through `price_order(..., delivery_override=)` → `_place_order(...,
  delivery_override=)`; without that the modal would show one figure and the
  order would be written at another.
- **The modal never touches the cart.** It builds the order straight from the
  product, like `quick_order`, so abandoning it costs the shopper nothing and a
  pre-existing cart survives.
- **`ReviewVote` deduplicates on `voter_key`**, not on a pair of partial unique
  constraints: production is MySQL, which silently declines to create conditional
  constraints, leaving "one vote per visitor" a promise nothing keeps.

Load-bearing markup contracts (renaming these breaks the script silently): every
`data-p2-*` attribute in `product_detail_conversion.html`, and the
`data-p2-bulk-map` JSON, whose keys are `'0'` for a plain product and the
variation id otherwise — the same shape `product.js` consumes for Theme 1.

Verification: `python test_product_page_theme.py` (65 checks — fee parsing, the
precedence rules, `quote()` refusals and clamping, the router, and an end-to-end
order whose total, delivery line, tier price, stock movement and untouched cart
are all asserted).
