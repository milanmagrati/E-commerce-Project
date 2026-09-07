# 20 — Reports & Analytics

Every reporting screen, what it counts, and which permission gates it.

---

## The dashboard home

**URL** `/` · **name** `dashboard` · **View** `home_view` → dispatches:

| Visitor | Renders |
|---|---|
| Anonymous | `landing.html` — the public marketing page, driven by `LandingPageSettings` |
| Authenticated | `dashboard.html` — KPI tiles and charts |

The dashboard view checks access **inline**:
`is_superuser or role == 'administrator' or can_view_dashboard`.

### Widget-level permissions

Each tile has its own flag, so a user can see the dashboard with only some panels:

| Flag | Panel |
|---|---|
| `can_view_dashboard` | Access at all |
| `can_view_dashboard_sales_overview` | Sales overview |
| `can_view_dashboard_orders_overview` | Orders overview |
| `can_view_dashboard_orders_by_source` | Orders by source |
| `can_view_low_stock_alerts` | Low-stock alerts |
| `can_view_dashboard_incomplete_attendance` | HRM incomplete-attendance alert |
| `can_view_total_revenue` | Revenue figures |

### Chart data endpoints

JSON, `@login_required`:

| URL | Feeds |
|---|---|
| `/api/chart-data/` | The main chart |
| `/api/order-sources-data/` | Orders-by-source donut |
| `/api/order-overview-data/` | Orders overview |
| `/api/bestselling-products/` | Best sellers |

Recent orders use `Order.objects.safe_recent()` (`dashboard/models.py:335`), which defers
ten decimal fields so one corrupted row cannot break the whole dashboard.

---

## The reports

| Report | URL | View | Template | Permission |
|---|---|---|---|---|
| Sales | `/reports/sales/` | `sales_report` | `sales_report.html` | `can_view_sales_reports` |
| Daily sales | `/reports/daily-sales/` | `daily_sales_report` | `daily_sales_report.html` | `can_view_daily_sales_reports` |
| Product sales | `/reports/product-sales/` | `product_sales_report` | `product_sales_report.html` | `can_view_product_sales_reports` |
| Purchase | `/reports/purchase/` | `purchase_report` | `purchase/purchase_report.html` | `@login_required` |
| Orders by source | `/reports/orders-by-source/` | `orders_by_source_report` | `orders_by_source_report.html` | `can_view_orders_by_source_report` |
| RTV | `/reports/rtv/` | `rtv_report` (`views.py:25821`) | `rtv_report.html` | inline `can_view_rtv_report` |
| Follow-ups | `/reports/followups/` | `follow_up_report` (`views.py:26600`) | `dashboard/followup_report.html` | inline `can_view_follow_up_report` |
| Financial | `/financial-report/` | `financial_report` | `financial_report.html` | `can_view_financial_reports` |
| Staff performance | `/staff-performance/` | `staff_performance_analytics` (`views.py:17703`) | `staff_performance.html` | inline `can_view_staff_performance` |
| Salary | `/hrm/reports/salary/` | `hrm/salary_report.py` | `hrm/salary_report.html` | — see [23](./23-hrm.md) |

### Drill-down and data endpoints

| URL | Serves |
|---|---|
| `/reports/product-sales/staff-orders/` | `api_product_staff_orders` — which staff sold what. XLSX export via `_staff_orders_log_export` |
| `/api/reports/orders-by-source/analytics/` | Chart data |
| `/api/reports/orders-by-source/table/` | Table data |
| `/financial-report/data/` | `financial_report_data` |
| `/api/reports/followups/<pk>/logs/`, `…/staff/<id>/logs/`, `…/status/logs/` | Follow-up drill-downs |

Several reports offer CSV or XLSX export — RTV exports CSV, the staff-orders drill-down
exports XLSX.

---

## Daily sales report

**URL** `/reports/daily-sales/` · **View** `daily_sales_report` (`dashboard/views.py:17223`)
**Permission** `can_view_daily_sales_reports`

One day's orders as a table, with breakdown cards beside it.

**The Items column names what sold** (Sep 2026), one product per line — quantity, name,
variation, and a type badge for anything that is not a plain `simple` product — plus a
"N products · N pcs" footer on orders carrying more than one. It used to read
"1 item (1 pcs)", which said nothing about what the day actually sold.

**The Payment column paid for that width and is gone.** Nothing is lost: method and status
both moved into the expanded row detail, next to the amount breakdown, and the Payment Status
/ Payment Method breakdown cards on the right are untouched.

> **The CSV export builds from `window.ordersData`, not from the table's cells.** It used to
> read `cells[6]`, `cells[7]` … by index, which the removed column would have shifted by one
> in silence. Building from the payload also gained it the per-product item list and a
> product-count column, and it can no longer drift from the markup.

`product_type` falls back to `'simple'` for a line whose `Product` row has since been deleted.

---

## Orders by source

**URL** `/reports/orders-by-source/` · **Permission** `can_view_orders_by_source_report`

Groups orders by `Order.order_from`, whose vocabulary comes from
`Setup(setup_type='order_source')`. Both an analytics view (charts) and a table view, each
with its own JSON endpoint so the page can refresh without a reload.

### The date range — `_orders_by_source_date_range` (`views.py:16880`)

A preset is `days` ∈ `{1, 7, 14, 30, 60, 90}`; anything else falls back to `default_days`. A
custom range posts `custom_from` / `custom_to`, and an unparseable pair falls back to the
preset window. Every window also gets an equal-length preceding window, which is what the
growth figures compare against.

- **`days=1` is "Today"** (Sep 2026), on the same code path as every other preset — so the
  single-day window still gets its own equal-length comparison period, yesterday. Before
  this, the one range a shop watches all day could only be reached by opening Custom Range
  and setting both dates by hand.
- ⚠️ **The presets anchor on the Kathmandu date, not on `timezone.now().date()`.** The window
  bounds are built with `make_aware()` in local time, so a UTC anchor is a mismatch that is
  invisible on a 90-day window and fatal on a one-day one: every night between local midnight
  and 05:45, "Today" showed yesterday. It now reads `get_nepali_now().date()` — see
  [02](./02-architecture-and-conventions.md#timezone--always-convert-never-use-datetimenow).

### The Order Details table

`orders_by_source_table_data` (`views.py:17113`) prefetches `items__product` and returns
`items`, `items_count` and `total_qty` per row. **The Payment column was replaced by Items**,
drawn exactly as the daily sales report draws it. Payment status is not lost — it stays in
the payload and in the CSV, which also gained the item list and the product/piece counts.

Verified by `test_orders_by_source_items.py`.

---

## RTV report

**URL** `/reports/rtv/` · **View** `dashboard/views.py:25821`

Covered in [14 — RTV & redirection](./14-rtv-and-redirection.md). Two details:

- Badge colours come from `NCM_STATUS_COLOURS` (`dashboard/views.py:25761-25771`), with
  `NCM_STATUS_FALLBACK = '#94a3b8'` for anything unrecognised.
- **The KPI tiles count over *all* RTV orders, not just the listed page** — filtering the
  list does not change the totals.

---

## Staff performance

**URL** `/staff-performance/` · **View** `dashboard/views.py:17703`
**Permission** inline `can_view_staff_performance`

### `StaffPerformance` — `dashboard/models.py:1612-1673`

`OneToOneField` to a user, `related_name='staff_performance'`.

| Metric | Notes |
|---|---|
| `total_orders` | Orders where `created_by` is this user, `is_deleted=False` |
| `successful_orders` | Those where `order_status='delivered'` **OR** `status='delivered'` |
| `return_count` | |
| `total_revenue` | |
| `success_rate` | Percentage 0–100 |
| `period_start`, `period_end` | Period tracking |

`calculate_metrics()` (`:1631`) recomputes from live order data.

> Note it checks **both** `order_status` and `status` with a `Q(...) | Q(...)` — a
> defensive acknowledgement of the duplicate-field problem described in
> [07](./07-order-statuses.md).

---

## Staff targets

**Pages**

| Page | URL | View | Template | Permission |
|---|---|---|---|---|
| Manage targets | `/targets/manage/` | `manage_targets` (`views.py:19865`) | `staff_targets.html` | inline `can_view_targets` |
| My targets | `/targets/my-targets/` | `my_targets` | `staff_targets.html` | `can_view_own_targets` |

Actions: `/targets/set/`, `/targets/<id>/edit/`, `/targets/<id>/delete/`, plus
`/api/targets/<id>/`.

### `StaffTarget` — `dashboard/models.py:1675-1706`

| Field | Values |
|---|---|
| `target_type` | `sales` / `warehouse` |
| `period` | `weekly` / `monthly` (default `monthly`) |
| `target_value` | Decimal |
| `start_date`, `end_date` | The window |
| `set_by` | Who assigned it |
| `note` | |

Permissions: `can_view_targets` (all), `can_set_targets`, `can_edit_targets`,
`can_delete_targets`, `can_view_own_targets`.

---

## Staff reports & content management

Two small internal-tracking features.

### Staff reports — `dashboard/models.py:2518-2542`

A free-form daily report row per staff member: `platform`, `no_of_posts`, `views`, `likes`,
`comments`, `follower_growth`, `punctuality`, `behaviour`, `leave_and_wfh`,
`notes_remarks`. Almost every field is a `TextField` — it is a spreadsheet in a table.

| Page | URL | Template | Guard |
|---|---|---|---|
| Staff reports | `/staff-reports/` | `dashboard/staff_reports.html` | `@login_required` |

Plus add / edit / delete JSON endpoints.

### Content management — `ContentAccount` `dashboard/models.py:2497-2516`

Tracks social/content accounts (`account_id`, `user_name`, `gmail`, …).

| Page | URL | Template | Permission |
|---|---|---|---|
| Content accounts | `/content-management/` | `dashboard/content_accounts.html` | `can_view_content_management` |
| Trash | `/content-management/trash/` | `dashboard/content_accounts_trash.html` | `can_view_content_management` |

APIs: add, update-order, edit, delete, restore, hard-delete.

---

## Gotchas

- **Several reports check permissions inline, not with a decorator** — `rtv_report`,
  `follow_up_report`, `staff_performance_analytics`, `manage_targets`, `purchase_report`.
  Easy to miss when auditing access.
- **`financial_report` carries `@login_required` three times** (`views.py:17043`). Harmless,
  but noted in [A4](./A4-appendix-known-quirks.md).
- **Date filtering in reports uses explicit localized bounds**, never `__date` — see
  [02](./02-architecture-and-conventions.md#timezone--always-convert-never-use-datetimenow).
- **A preset window must anchor on the local date too.** Bounds made local and an anchor read
  from `timezone.now().date()` disagree for the first 5h45m of every Kathmandu day — harmless
  on a long window, wrong all night on a one-day one.
- **A report's CSV export should build from the payload, not from the rendered table.** Two
  exports read cells by index; removing or reordering a column shifted every later field with
  no error anywhere. Both now build from the same data the table renders.
- **Revenue counts `payment_status='paid'`**, not delivered orders. An order delivered but
  unpaid contributes nothing to revenue KPIs.
- `StaffPerformance` is a stored snapshot, not a live query — call `calculate_metrics()` to
  refresh it.

---

## Files that own this

- `dashboard/views.py:16880` — `_orders_by_source_date_range` (the preset windows)
- `dashboard/views.py:16971-17222` — orders-by-source report, analytics and table endpoints
- `dashboard/views.py:17223-…` — daily sales report
- `dashboard/views.py:17043-…` — financial report
- `dashboard/views.py:17703` — staff performance
- `dashboard/views.py:19865` — targets
- `dashboard/views.py:25761-25821` — RTV report and its colour table
- `dashboard/views.py:26600` — follow-up report
- `dashboard/models.py:1612-1706` — `StaffPerformance`, `StaffTarget`
- `dashboard/models.py:2497-2542` — `ContentAccount`, `StaffReport`
- `hrm/salary_report.py` — the salary report ([23](./23-hrm.md))
