# 30 — Ops & Troubleshooting

The runbook. Start here when something is wrong in production.

---

## Running the project

```bash
# Dev server
python manage.py runserver

# Migrations (apps live at the repo root, not in a src/ layout)
python manage.py makemigrations <app_name>
python manage.py migrate

# Admin user
python manage.py createsuperuser

# Celery worker — only needed for bill_rewards OCR. Requires Redis.
celery -A myproject worker -l info
```

There is **no test suite**. Verification is done with standalone root-level scripts that
call `django.setup()` themselves:

```bash
python test_order_redirection.py
python test_ncm_status_history.py
python check_roles.py
```

---

## Log files

All under `logs/`, rotating (10 MB × 5, except SMS at 5 MB × 3).

| File | Contains | Logger name |
|---|---|---|
| `django_errors.log` | **Every 500 with a full traceback** | `django`, `django.request`, `django.db.backends` |
| `ncm_integration.log` | NCM client, sync, scheduler | `ncm` |
| `ncm_webhooks.log` | Inbound NCM webhooks — full payloads | `webhook` |
| `ncm_sms.log` | Customer SMS | `sms` |
| `adms.log` | ZKTeco biometric device traffic | `hrm.adms` |
| `integrations.log` | WooCommerce receiver | `integrations` |
| `trendycrm.log` | CRM inbox, Meta sync, AI replies | `trendycrm` |
| `sentinel.log` | Sentinel's **own failures** (the trail is in the DB) | `sentinel` |

> **Pick and Drop has no configured logger.** Its entries fall through to root. Add one to
> `settings.LOGGING` if you need to debug PND.

---

## Management commands

| Command | App | Purpose |
|---|---|---|
| `sync_all_ncm_orders [--force]` | `ncm` | Run the NCM bulk status sync. Safe to cron every minute — the due-check is in the DB |
| `setup_ncm_webhook --domain URL [--test]` | `dashboard` | Register the webhook with NCM |
| `fetch_ncm_delivery_charges [--force] [--limit N]` | `dashboard` | Backfill `delivery_charge` from NCM |
| `repair_return_stage` | `dashboard` | Fix orders marked `return` while still in transit |
| `repair_rtv_marked_at` | `dashboard` | Re-verify RTV dates from untrusted sources |
| `diagnose_decimals` | `dashboard` | Report corrupted decimal rows |
| `fix_decimal_corruption` | `dashboard` | Repair them |
| `cleanup_decimals` | `dashboard` | Tidy up |
| `reset_stock_counters` | `inventory` | Recalculate `reserved_qty` / `backordered_qty` from order data |
| `sync_woocommerce_orders --since-hours N` | `integrations` | Poll WooCommerce for recently-modified orders (webhook fallback) |
| `sync_woocommerce_orders --full` | `integrations` | Crawl the **whole** Woo order history. Run once per store — neither the webhook nor the windowed poll ever backfills, and an un-backfilled dashboard looks stuck in a single status with no error. Takes minutes on a ~5k-order store |
| `sentinel_prune` | `sentinel` | Apply audit retention |
| `crm_poll_meta` | `trendycrm` | Poll Meta for messages |
| `reclassify_crm_intents` | `trendycrm` | Re-run intent classification |
| `seed_crm_chatbot` | `trendycrm` | Seed a starter chatbot |
| `seed_data` | `store` | Seed storefront demo data |

---

## Troubleshooting by symptom

### "The order status is wrong / reverted"

1. Was it changed with a **legacy bulk action** (`mark_delivered`, `mark_cancelled`, …)?
   Those write only `order_status` and set no manual hold — the next sync reverts them.
   See [07 §5](./07-order-statuses.md).
2. Is there a **manual hold**? Check `manual_status_override_at` and
   `manual_status_override_ncm_status`.
3. Is the order **cancelled**? Every sync path refuses cancelled orders.
4. Is it stuck on `return`? The "finished return never reopens" guard is doing its job —
   use `manage.py repair_return_stage`.
5. Read the order's `OrderActivityLog`, sorted by `Coalesce(event_at, created_at)`.

### "Nothing has synced since last night"

The background sync is driven by a **browser heartbeat**, not cron. With no tab open,
nothing runs.

**Fix:** set `NCM_HEARTBEAT_TOKEN` in `.env` and point an external uptime pinger at

```
https://<site>/ncm/api/heartbeat/?token=<value>
```

See [12](./12-ncm-sync-and-scheduler.md).

### "Sync seems stuck"

Check `APISettings` (`pk=1`):

| Column | Meaning |
|---|---|
| `bulk_sync_running_since` | Non-null while a run holds the lock. **Older than 300s = stale**, and will be taken over automatically |
| `last_bulk_sync_started_at` | The schedule clock |
| `last_bulk_sync_summary` | `duration_seconds`, `updated_count`, `errors` |

Remember the **adaptive floor**: if the last run took D seconds, the next won't start for at
least 2 × D (capped at 1800s).

### "One order never syncs"

| Check | Where |
|---|---|
| Does it have an `ncm_order_id`? | Order detail, NCM panel |
| Is its status in `DEFAULT_TERMINAL_STATUSES`? | `cancelled`, `delivered`, `return`, `returned`, `return_initiated`, `return_approved` |
| Is it `cancelled`? | Protected — never synced |
| Is `api_config` correct? | NCM 404s an order queried on the wrong account. `fetch_order_status_raw` sweeps accounts and writes the right one back |
| Manual hold? | See above |

Recovery: `/ncm/orders/<id>/verify-ncm/`, or `/ncm/orders/<id>/clear-ncm-id/` to allow a
re-send.

### "An order didn't reach NCM"

1. `logs/ncm_integration.log` — the full payload and URL are logged on failure.
2. If it was a **bulk** send: open the `NCMBulkLog` batch, then its
   `NCMBulkLogDetail` rows — they carry the raw `response_data`.
3. Bulk send derives the destination from `order.branch_city.upper()`, defaulting to
   `KATHMANDU`. A blank or misspelled city silently goes to the wrong branch.
4. Bulk send detects success by the string `'Order Successfully Created'`. If NCM reworded
   it, every send records as failed while actually succeeding.

### "A bulk send batch is stuck at 'processing'"

Check `worker_heartbeat_at` on the `NCMBulkLog`. A stale or null value means the worker
died. Use the **resume** endpoint —
`/logistics/bulk-logs/<provider>/<log_id>/resume/` — which is why `selected_order_ids` and
`send_options` are stored. `terminate/` sets `cancel_requested`.

### "The webhook isn't updating orders"

1. `logs/ncm_webhooks.log` — every request logs IP, User-Agent, query string and payload.
2. A **duplicate** returns 200 without touching anything — check `WebhookLog` for the
   derived `webhook_id`.
3. `failed_orders` in the response names the reason: cancelled, manual hold, or not found.
4. **Auth reminder:** the token check only applies when `NCM_WEBHOOK_SECRET` is set **and**
   the registered URL carries `?token=`. There is no HMAC — see [11](./11-ncm-webhook.md).

### "The page shows a 500"

`logs/django_errors.log` has the full traceback. `handler500` is
`dashboard.views.server_error_500`, rendering `500.html`.

Common causes:

| Cause | Fix |
|---|---|
| `decimal.InvalidOperation` | `manage.py diagnose_decimals` then `fix_decimal_corruption` |
| "MySQL server has gone away" | `CONN_MAX_AGE` + `CONN_HEALTH_CHECKS` should handle this; check DB load |
| Missing `Setup` row | Reopening the order auto-creates it |

### "A template change isn't showing"

With `DEBUG=False`, templates are **cached**. HTML-only edits need a dev server restart —
autoreload only watches `.py` files.

### "Stock numbers look wrong"

1. Remember: **dashboard orders don't reserve stock.** Only the storefront does.
2. Stock is deducted at the **dispatch scan**, not at order creation.
3. `restore_order_stock` refuses unless a successful `DispatchItem` exists.
4. Counters drifted? `python manage.py reset_stock_counters`.

See [17](./17-inventory-and-stock.md).

### "Attendance is missing punches"

1. Biometric punches sync **opportunistically on page visits** — nothing runs in the
   background.
2. Check `logs/adms.log` for device traffic.
3. Inconsistent PIN padding from the device splits one employee's punches; `_normalize_pin`
   in `hrm/views.py` handles it. Verify with `test_biometric_pin_normalization.py`.

### "The CRM stopped replying"

1. `logs/trendycrm.log` records **why** a reply was skipped.
2. The free-tier Gemini key allows only ~20 calls/day before every model 429s.
3. A **503** from Gemini is momentary overload, not a quota cap — it retries the same model.
4. Auto-replies run on background threads, so failures never surface in the triggering
   request.

### "Bill OCR produces nonsense"

If no provider is configured, `get_extractor()` falls back to **`MockExtractor`** and logs
*"Using MockExtractor – configure BILL_OCR_PROVIDER for production"*. Check
`BILL_OCR_PROVIDER` and the corresponding credentials.

---

## Health checklist

Quick things to verify on a fresh deploy or after an incident:

| Check | How |
|---|---|
| `DEBUG=False` | `.env` |
| `ALLOWED_HOSTS` set | `.env` — do not leave the default in production |
| `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` = `True` | `.env` |
| NCM webhook registered | `manage.py setup_ncm_webhook --domain … --test` |
| `NCM_WEBHOOK_SECRET` set **and** the registered URL carries `?token=` | Otherwise the endpoint is effectively open |
| `NCM_HEARTBEAT_TOKEN` set + external pinger | Otherwise no overnight sync |
| `WOOCOMMERCE_WEBHOOK_SECRET` set | The Woo receiver 401s without it — which is correct |
| Celery worker running | Only if bill OCR is in use |
| `logs/` writable | Handlers are created at startup |
| Static files collected | `python manage.py collectstatic` (WhiteNoise serves them) |
| Media served by Apache/Nginx | Not by Django, in production |
| `sentinel_prune` scheduled or run periodically | Or the audit table grows unbounded |

---

## Deploying

Commit and push to `main`. There is no separate deploy pipeline documented in the repo.

---

## Where things live

```
myproject/
  myproject/       settings, urls, middleware, celery, wsgi
  dashboard/       the core app (views.py ~27.5k lines)
  accounts/        users, roles, permissions
  ncm/             NCM integration
  pick_and_drop/   PND integration
  inventory/       stock services (no models, no urls)
  hrm/             HR, attendance, payroll (views.py ~11.2k lines)
  store/           storefront
  trendycrm/       CRM + AI
  bill_rewards/    OCR + loyalty (the only Celery user)
  google_sheets/   sheet sync
  integrations/    WooCommerce receiver
  chat/ todo/      internal tools
  resources/       knowledge base
  sentinel/        audit trail
  services/        shared logic (NOT a Django app)
  templates/       project-level templates + base.html
  static/          source static files
  staticfiles/     collectstatic output
  media/           uploads
  logs/            rotating log files
  docs/            this manual
  test_*.py        ~90 standalone verification scripts (root level)
  debug_*.py       historical one-offs — not part of any pipeline
```

---

## Files that own this

- `myproject/settings.py` — logging, database, security settings
- `logs/` — the log files themselves
- Every app's `management/commands/` directory
- `dashboard/views.py` — `server_error_500`
