# A3 — Permission Flags

Every `can_*` field on `accounts.CustomUser` (`accounts/models.py:28`), grouped as in the
model. See [03 — Auth, roles & permissions](./03-auth-roles-permissions.md) for how they are
enforced.

**Admins bypass everything:** `is_superuser` **or** `role == 'administrator'`
(`accounts/decorators.py:6`).

Defaults shown as ✅ (on) / ❌ (off).

---

## Orders — `accounts/models.py:55-63`

| Flag | Default | Grants |
|---|---|---|
| `can_view_orders` | ✅ | Order detail, invoice |
| `can_view_orders_list` | ❌ | **The orders list page** |
| `can_create_orders` | ✅ | Creating orders, Setup add/edit |
| `can_edit_orders` | ❌ | Editing orders |
| `can_delete_orders` | ❌ | Trash, restore, permanent delete, Setup delete |
| `can_cancel_orders` | ❌ | Cancelling |
| `can_view_on_hold_orders` | ❌ | On-hold page, order follow-up endpoints |
| `can_export_orders` | ❌ | Excel export |
| `can_access_offer_price` | ❌ | Offer price field |

## Products — `:66-69`

`can_view_products` ✅ · `can_create_products` ❌ · `can_edit_products` ❌ ·
`can_delete_products` ❌

## Customers — `:72-75`

`can_view_customers` ✅ · `can_create_customers` ✅ · `can_edit_customers` ✅ ·
`can_delete_customers` ❌

## Dispatch — `:78-81`

`can_view_dispatch` ❌ · `can_manage_dispatch` ❌ · `can_delete_dispatch` ❌ ·
`can_scan_barcodes` ❌

## Inventory — `:84-90`

| Flag | Default |
|---|---|
| `can_view_inventory` | ✅ |
| `can_manage_inventory` | ❌ |
| `can_adjust_stock` | ❌ |
| `can_view_inventory_cost` | ❌ |
| `can_toggle_product_price` | ❌ |
| `can_view_selling_unit_price` | ❌ |
| `can_view_cost_unit_price` | ❌ |

## Stock valuation — `:93-95`

`can_view_valuation_selling` ❌ · `can_view_valuation_cost` ❌ ·
`can_toggle_stock_valuation` ❌

## Reports — `:98-106`

| Flag | Default | Report |
|---|---|---|
| `can_view_reports` | ❌ | General |
| `can_view_sales_reports` | ❌ | `/reports/sales/` |
| `can_view_daily_sales_reports` | ❌ | `/reports/daily-sales/` |
| `can_view_product_sales_reports` | ❌ | `/reports/product-sales/` |
| `can_view_financial_reports` | ❌ | `/financial-report/` |
| `can_view_orders_by_source_report` | ❌ | `/reports/orders-by-source/` |
| `can_view_rtv_report` | ❌ | `/reports/rtv/` (checked inline) |
| `can_view_total_revenue` | ❌ | Revenue figures |
| `can_export_data` | ❌ | Data export |

## Pricing — `:109-111`

`can_view_cost_price` ❌ · `can_edit_prices` ❌ · `can_give_discounts` ✅
**`max_discount_percent`** — a `Decimal`, the only non-boolean permission

## Returns — `:115-120`

`can_view_returns` ❌ · `can_create_returns` ❌ · `can_edit_returns` ❌ ·
`can_delete_returns` ❌ · `can_approve_returns` ❌ · `can_process_refunds` ❌

> Permanent delete and empty trash for returns are **admin-only**, not covered by any flag.

## Targets — `:123-127`

`can_view_targets` ❌ · `can_set_targets` ❌ · `can_edit_targets` ❌ ·
`can_delete_targets` ❌ · `can_view_own_targets` ✅

## Purchases — `:130-133`

`can_view_purchases` ❌ · `can_create_purchases` ❌ · `can_manage_suppliers` ❌ ·
`can_make_supplier_payments` ❌

## Staff — `:136`

`can_view_staff_performance` ❌

## Cities — `:139-142`

`can_view_cities` ❌ · `can_add_cities` ❌ · `can_edit_cities` ❌ · `can_delete_cities` ❌

## Content — `:145`

`can_view_content_management` ❌

## Dashboard widgets — `:148-153`

| Flag | Default | Panel |
|---|---|---|
| `can_view_dashboard` | ❌ | Access at all |
| `can_view_low_stock_alerts` | ❌ | Low-stock alerts |
| `can_view_dashboard_incomplete_attendance` | ❌ | Incomplete-attendance alert |
| `can_view_dashboard_sales_overview` | ❌ | Sales overview |
| `can_view_dashboard_orders_overview` | ❌ | Orders overview |
| `can_view_dashboard_orders_by_source` | ❌ | Orders by source |

## NCM — `:156-165`

| Flag | Default | Grants |
|---|---|---|
| `can_view_ncm_orders` | ❌ | NCM order pages, logistics list, tracking |
| `can_create_ncm_orders` | ❌ | **Sending an order to NCM** |
| `can_edit_ncm_orders` | ❌ | Editing NCM details |
| `can_delete_ncm_orders` | ❌ | Deleting |
| `can_view_ncm_bulk_logs` | ❌ | Bulk send logs |
| `can_manage_ncm_bulk_logs` | ❌ | Bulk log trash / bulk actions |
| `can_view_ncm_trash` | ❌ | NCM order trash |
| `can_sync_ncm_orders` | ❌ | **Manual and bulk sync** |
| `can_view_ncm_branches` | ❌ | Branch list |
| `can_manage_ncm_branches` | ❌ | Branch management |

## HRM — `:168-174` — ⚠️ **sidebar only**

| Flag | Default | Sidebar section |
|---|---|---|
| `can_view_hrm` | ❌ | HRM at all |
| `can_view_hrm_hr_management` | ❌ | HR management |
| `can_view_hrm_asset_management` | ❌ | Assets |
| `can_view_hrm_attendance` | ❌ | Attendance |
| `can_view_hrm_payroll` | ❌ | Payroll |
| `can_view_hrm_incomplete_attendance` | ❌ | The alert |
| `can_fix_hrm_incomplete_attendance` | ❌ | Fixing it — **one of the few enforced in-view** |

> **HRM views carry only `@login_required`.** These flags hide sidebar links; they do not
> block access by URL. See [23](./23-hrm.md).

## Todo — `:177`

`can_access_todo` ❌

> Access also leaks through **task assignment** — `todo_access_required`
> (`todo/views.py:17`) allows any user with a task assigned to or created by them.

## Follow-ups — `:180-183`

`can_access_follow_ups` ❌ · `can_view_follow_up_report` ❌ ·
`can_setup_follow_up_status` ❌

## Resources — `:186-187`

`can_view_resources` ❌ · `can_create_resources` ❌ (create/edit/delete)

## Sentinel Vault — `:190-194`

| Flag | Default | Grants |
|---|---|---|
| `can_view_audit_trail` | ❌ | Access to the vault |
| `can_view_all_users_activity` | ❌ | Everyone's activity, not just your own |
| `can_export_audit_logs` | ❌ | Export |
| `can_manage_sessions` | ❌ | Revoke sessions, action alerts |
| `can_configure_audit` | ❌ | Vault settings, purge |

---

## Apps with no permission flag at all

| App | Guard |
|---|---|
| **TrendyCRM** | `@login_required` only |
| **Bill rewards** | `@login_required` only |
| **Google Sheets** | `@login_required` only |
| **Chat** | `@login_required` only |
| **Storefront** | Public browsing; `@login_required(login_url='/store/login/')` for account pages |

---

## Enforcement helpers

| Helper | File | Rule |
|---|---|---|
| `is_admin(user)` | `accounts/decorators.py:6` | The single bypass definition |
| `has_any_permission(user, *perms)` | `:15` | Admin **or ANY** — for JSON endpoints |
| `@permission_required(*perms)` | `:30` | Admin **or ALL** listed flags |
| `@admin_or_permission_required(*perms)` | `:56` | Admin **or ANY** one flag |
| `@admin_only` | `:74` | Admin only |
| `ncm_permission_required(field)` | `ncm/views.py:43` | Admin or the named NCM flag |
| `pnd_permission_required(field)` | `pick_and_drop/views.py:26` | **Defined, never applied** |
| `todo_access_required` | `todo/views.py:17` | Admin, flag, **or** has a task |
| `resources_access_required` | `resources/views.py:16` | Admin or either resources flag |
| `vault_access(*perms)` | `sentinel/access.py:32` | Defaults to `can_view_audit_trail` |
| `administrator_required` | `accounts/views.py:28` | `user_passes_test`, admin login URL |

---

## Setting defaults for a role

`CustomUser.set_default_permissions_by_role()`:

- `role == 'administrator'` → a **hardcoded grant-all list**
- anything else → `Role.default_permissions['permissions']` from the matching `Role` row

Edit the matrix at `/accounts/roles/<id>/permissions/`. **Changing a role does not
retroactively update existing users** — it only supplies defaults at creation or role change.
