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
