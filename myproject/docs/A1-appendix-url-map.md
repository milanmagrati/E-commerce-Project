# A1 — Complete URL Map

**685 named URL patterns across 14 mounted apps.** This appendix is the index; each chapter
carries the detail.

---

## Top-level mounting — `myproject/urls.py`

| Prefix | Include | Patterns | Chapter |
|---|---|---|---|
| `admin/` | `django.contrib.admin` | — | — |
| `` (root) | `dashboard.urls` | **253** | [05](./05-orders-list.md)–[22](./22-settings-and-setup.md) |
| `accounts/` | `accounts.urls` | 25 | [03](./03-auth-roles-permissions.md) |
| `ncm/` | `ncm.urls` | 17 | [09](./09-ncm-api-client.md)–[12](./12-ncm-sync-and-scheduler.md) |
| `pnd/` | `pick_and_drop.urls` | 5 | [13](./13-pick-and-drop.md) |
| `chat/` | `chat.urls` | 16 | [28](./28-chat-todo-resources.md) |
| `hrm/` | `hrm.urls` | **218** | [23](./23-hrm.md) |
| `todo/` | `todo.urls` | 6 | [28](./28-chat-todo-resources.md) |
| `store/` | `store.urls` | 23 | [24](./24-storefront.md) |
| `api/integrations/` | `integrations.urls` | 1 | [27](./27-sheets-and-woocommerce.md) |
| `bill-rewards/` | `bill_rewards.urls` | 16 | [26](./26-bill-rewards.md) |
| `imports/` | `google_sheets.urls` | 13 | [27](./27-sheets-and-woocommerce.md) |
| `trendy-crm/` | `trendycrm.urls` | 62 | [25](./25-trendycrm.md) |
| `resources/` | `resources.urls` | 10 | [28](./28-chat-todo-resources.md) |
| `sentinel/` | `sentinel.urls` | 20 | [29](./29-sentinel-audit.md) |
| `iclock/cdata`, `iclock/getrequest`, `iclock/devicecmd` | `hrm.views`, `@csrf_exempt` | 3 | [23](./23-hrm.md) |

Two apps have **no URLs**: `inventory` (pure service layer) and `services/` (not a Django
app).

`handler500 = dashboard.views.server_error_500` → renders `500.html`.

---

## Orders

| URL | Name | Permission | Chapter |
|---|---|---|---|
| `/orders/` | `orders_list` | `can_view_orders_list` | [05](./05-orders-list.md) |
| `/orders/create/` | `order_create` | `can_create_orders` | [08](./08-order-create-edit-bulk.md) |
| `/orders/<id>/` | `order_detail` | `can_view_orders` | [06](./06-order-detail.md) |
| `/orders/<id>/edit/` | `order_edit` | `can_edit_orders` | [08](./08-order-create-edit-bulk.md) |
| `/orders/<id>/delete/` | `order_delete` | `can_delete_orders` | |
| `/orders/<id>/invoice/` | `order_invoice` | `can_view_orders` | |
| `/orders/bulk-action/` | `orders_bulk_action` | login | [08](./08-order-create-edit-bulk.md) |
| `/orders/bulk-ncm-send/` | `orders_bulk_ncm_send` | login | [10](./10-ncm-sending.md) |
| `/orders/bulk-pnd-send/` | `orders_bulk_pnd_send` | ⚠️ **none** | [13](./13-pick-and-drop.md) |
| `/orders/trash/` | `orders_trash` | `can_view_orders` | |
| `/orders/<id>/restore\|move-to-trash\|permanent-delete/` | `order_restore`, `order_move_to_trash`, `order_permanent_delete` | `can_delete_orders` | |
| `/orders/trash/bulk-action/`, `/orders/trash/empty/` | `orders_trash_bulk_action`, `empty_orders_trash` | `can_delete_orders` | |
| `/orders/on-hold/` | `on_hold_orders_list` | `can_view_on_hold_orders` | [19](./19-customers-and-followups.md) |
| `/orders/returns/` | `return_orders_list` | `can_view_orders` | [15](./15-returns.md) |
| `/orders/woocommerce/`, `/orders/woocommerce/sync/` | `woocommerce_orders`, `woocommerce_orders_sync` | login | [27](./27-sheets-and-woocommerce.md) |
| `/orders/export/selected/` | `export_selected_orders_excel` | login | |
| `/orders/<id>/export/` | `export_order_details` | `can_export_data` | |
| `/orders/import/excel/` | `import_orders_excel` | login | [08](./08-order-create-edit-bulk.md) |
| `/orders/<id>/exchange/` | `create_exchange_order` | login | [10](./10-ncm-sending.md) |
| `/api/search-orders/`, `/api/check-duplicate-order/`, `/api/order-by-barcode/` | | login | |

**Order follow-ups** (`can_view_on_hold_orders`): `/orders/<id>/followup/add/`,
`/orders/<id>/followups/`, `/orders/<id>/next-followup/`

---

## RTV & redirection — [14](./14-rtv-and-redirection.md)

| URL | Name | Permission |
|---|---|---|
| `/orders/rtvs/` | `ncm_rtvs_list` | `can_view_orders` |
| `/api/ncm-rtv/sync/` | `ncm_rtvs_sync` | `can_view_orders` |
| `/api/ncm-rtv/<id>/comment\|comments\|detail/` | | `can_view_orders` |
| `/api/rtv/<id>/followup/add/`, `/api/rtv/<id>/followups/` | | `can_view_orders` |
| `/orders/possible-redirection/` | `possible_redirection_list` | `can_view_orders` |
| `/api/possible-redirection/refresh-status/` | | `can_view_orders` |
| `/orders/redirect-orders/` | `redirect_orders_list` | `can_view_orders` |
| `/api/orders/<id>/redirect-get\|get-redirect-details\|redirect-save/` | | `can_view_orders` |
| `/api/rtv/<ncm_id>/redirect-get\|redirect-save/` | | `can_view_orders` |
| `/setup/rtv-status/` + add/edit/delete/get | `rtv_status_list` | login |
| `/api/rtv/<id>/set-status/` | | login |

---

## Returns — [15](./15-returns.md)

`/returns/` `returns_dashboard` · `/returns/list/` `returns_list` · `/returns/create/`
`return_create` · `/returns/bulk-create/` · `/returns/<id>/` `return_detail` ·
`/returns/<id>/trash/` · `/returns/trash/` · `/returns/<id>/restore/` ·
`/returns/<id>/permanent-delete/` **(admin)** · `/returns/empty-trash/` **(admin)** ·
`returns_bulk_action` · `returns_batch_bulk_action` · `returns_trash_bulk_action`

---

## Logistics — [10](./10-ncm-sending.md)–[13](./13-pick-and-drop.md)

### Shared

| URL | Name | Permission |
|---|---|---|
| `/logistics/orders/` | `logistics_orders_list` (`?provider=ncm\|pnd`) | `can_view_ncm_orders` |
| `/logistics/orders/export/` | `logistics_orders_export` | `can_view_ncm_orders` |
| `/logistics/bulk-logs/` | `logistics_bulk_logs_list` | `can_view_ncm_bulk_logs` |
| `/logistics/bulk-logs/trash/` + restore/permanent-delete/bulk-action/empty | | `can_manage_ncm_bulk_logs` |
| `/logistics/bulk-logs/progress/` | JSON poller | |
| `/logistics/bulk-logs/<provider>/<log_id>/terminate\|resume/` | Batch control | |
| `/logistics/branches/` | `logistics_branches` | `can_view_ncm_branches` |

> Route order matters: `/logistics/bulk-logs/trash/…` is declared **before**
> `/logistics/bulk-logs/<str:provider>/…` so `"trash"` can't be captured as a provider
> (comment at `dashboard/urls.py:334`).

### NCM (`/ncm/`)

| URL | Name | Permission |
|---|---|---|
| `orders/<id>/create/` | `ncm:create_shipment` | `can_create_ncm_orders` |
| `orders/<id>/sync/` | `ncm:sync_status` | `can_sync_ncm_orders` |
| `orders/<id>/track/` | `ncm:track_order` | `can_view_ncm_orders` |
| `bulk-sync/`, `bulk-sync/start/` | `ncm:bulk_sync`, `ncm:bulk_sync_json` | `can_sync_ncm_orders` |
| `branches/`, `branches/json/` | `ncm:branches`, `ncm:branches_json` | `can_view_ncm_branches` |
| **`webhook/`** | `ncm:webhook` | ⚠️ **unauthenticated** |
| `api/order/<id>/status\|sync\|activity\|comments\|comments/add/` | | login |
| `api/orders/batch-status/` | `ncm:api_batch_status` | login (inline) |
| **`api/heartbeat/`** | `ncm:api_sync_heartbeat` | login **or** `?token=` |
| `orders/<id>/clear-ncm-id/`, `orders/<id>/verify-ncm/` | | login |

Dashboard-side NCM: `/ncm-orders/<id>/` `ncm_order_detail` · `/ncm-orders/track/<id>/` ·
`/ncm-orders/sync-all/` `ncm_sync_all_statuses` · `/ncm-orders/trash/` + actions ·
`/api/ncm-branches/` · `/ncm-bulk-logs/<id>/` + trash + bulk-action

### PND (`/pnd/`) — all login-only

`orders/<id>/create/` `create_shipment` · `orders/<id>/sync/` **(placeholder)** ·
`orders/<id>/track/` · `orders/<id>/cancel/` · `bulk-sync/` **(placeholder)**

Dashboard-side: `/pnd-bulk-logs/<id>/` + trash + bulk-action

---

## Products & catalog — [18](./18-products-and-catalog.md)

`/products/` · `/products/add/` · `/products/<id>/` · `/products/<id>/edit/` ·
`/products/trash/` + move-to-trash/restore/permanent-delete/bulk/empty ·
`/products/bulk-action/` · `/products/export-excel/`
`/categories/` **(admin)** + edit/delete · `/add-category/` (login)
`/products/<id>/variations/` + variation create/update/delete
Image endpoints: `delete_main_product_image`, `upload_product_images`,
`delete_product_image`, `set_featured_image`, `reorder_product_images`
`/media/` `media_library` · `/api/media/`
APIs: `/api/search-products/` · `/api/product/<id>/` · `/api/product/<id>/update-price/` ·
`/api/product/<id>/variations/` · `/api/bestselling-products/` ·
`/api/create-custom-product/` · `/api/product/<id>/stock-in/`

---

## Customers & follow-ups — [19](./19-customers-and-followups.md)

`/customers/` · `/customers/add/` · `/customers/<id>/` · `/customers/<id>/edit/` ·
`/customers/<id>/delete/` · `/customers/bulk-action/` ·
`/api/customer/<id>/` · `/api/search-customer-by-phone/`
`/orders/follow-ups/` `follow_ups_list` · `/orders/follow-ups/trash/` ·
`/api/orders/follow-ups/add|<pk>/edit|<pk>/logs|<pk>/delete|<pk>/restore|<pk>/hard-delete/` ·
`/api/orders/follow-ups/sync/` · `/api/orders/follow-ups/presence/`
`/setup/followup-status/` + add/edit/update-color/delete/toggle-default

---

## Dispatch — [16](./16-dispatch.md)

`/dispatch/` `dispatch_management` · `/dispatch/list/` · `/dispatch/<pk>/` ·
`/dispatch/trash/` · `/dispatch/<pk>/trash|restore|permanent-delete/` ·
`/dispatch/bulk-action/` · `/dispatch/trash/bulk-action/` · `/dispatch/trash/empty/`

## Inventory — [17](./17-inventory-and-stock.md)

`/inventory-dashboard/` · `/inventory/stock-in/create/` · `/inventory/stock-in/<id>/` ·
`/inventory/low-stock-settings/` · `/inventory/low-stock-alerts/` ·
`/inventory/backorders/` `backorder_management`

## Purchases & suppliers — [21](./21-purchases-and-suppliers.md)

`/purchases/dashboard/` · `/purchases/create/` · `/purchases/<id>/` ·
`/purchases/<id>/edit/` · `/suppliers/` · `/suppliers/add/` · `/suppliers/<id>/` ·
`/suppliers/<id>/edit/` · `/suppliers/payment/add/` ·
`/products/<id>/purchase-history/` · `/api/supplier/<id>/purchases/`

---

## Reports — [20](./20-reports-and-analytics.md)

| URL | Name | Permission |
|---|---|---|
| `/reports/sales/` | `sales_report` | `can_view_sales_reports` |
| `/reports/daily-sales/` | `daily_sales_report` | `can_view_daily_sales_reports` |
| `/reports/product-sales/` | `product_sales_report` | `can_view_product_sales_reports` |
| `/reports/product-sales/staff-orders/` | `api_product_staff_orders` | login |
| `/reports/purchase/` | `purchase_report` | login |
| `/reports/orders-by-source/` (+ `/analytics/`, `/table/`) | `orders_by_source_report` | `can_view_orders_by_source_report` |
| `/reports/rtv/` | `rtv_report` | inline `can_view_rtv_report` |
| `/reports/followups/` (+ 3 log APIs) | `follow_up_report` | inline `can_view_follow_up_report` |
| `/financial-report/` (+ `/data/`) | `financial_report` | `can_view_financial_reports` |
| `/staff-performance/` | `staff_performance_analytics` | inline `can_view_staff_performance` |
| `/api/chart-data/`, `/api/order-sources-data/`, `/api/order-overview-data/` | | login |

**Targets:** `/targets/manage/` · `/targets/my-targets/` · `/targets/set/` ·
`/targets/<id>/edit|delete/` · `/api/targets/<id>/`

---

## Settings & setup — [22](./22-settings-and-setup.md)

`/settings/` `settings_hub` · `/settings/company/` · `/settings/landing-page/`
`/setup/` `setup_management` + add/edit/delete/toggle-default/reorder
`/cities/` + edit/delete + `/api/cities/quick_add|bulk_add|` + get-valley-status
`/api-integration/` + add/edit/delete/toggle/get
`/pages/` + add/edit/delete (CMS pages)
Maintenance & notices: `/api/maintenance/toggle|logs/` · `/api/active-notice/` ·
`/api/create-notice/` · `/api/update-notice/<id>/` · `/api/notice-history/` ·
`/api/notice/<id>/stop/`

## Content & staff reports — [20](./20-reports-and-analytics.md)

`/content-management/` + `/trash/` + APIs (`can_view_content_management`)
`/staff-reports/` + add/edit/delete (login)

---

## Auth & landing — [03](./03-auth-roles-permissions.md)

`/` `dashboard` · `/welcome/` `landing` · `/login/` · `/logout/`
`/accounts/roles/` + create/permissions/delete **(admin)**
`/accounts/users/` + create/edit/soft-delete/toggle/trash/restore/hard-delete **(admin)**
`/accounts/password-reset/…` (4 URLs, public)
`/accounts/profile/` + update + change-password (login)

---

## Other apps

| App | Prefix | Key pages | Chapter |
|---|---|---|---|
| HRM | `/hrm/` | ~218 patterns — see the chapter | [23](./23-hrm.md) |
| Storefront | `/store/` | landing, products, cart, checkout, orders, profile, `p/<slug>/` | [24](./24-storefront.md) |
| TrendyCRM | `/trendy-crm/` | conversations, chatbots, integrations, contacts, tickets, social | [25](./25-trendycrm.md) |
| Bill rewards | `/bill-rewards/` | dashboard, upload, bills, aliases, rewards, trending | [26](./26-bill-rewards.md) |
| Google Sheets | `/imports/` | imports, sheet view | [27](./27-sheets-and-woocommerce.md) |
| WooCommerce | `/api/integrations/` | `woocommerce/orders/` (HMAC) | [27](./27-sheets-and-woocommerce.md) |
| Chat | `/chat/` | inbox, thread + 13 APIs | [28](./28-chat-todo-resources.md) |
| Todo | `/todo/` | board, task CRUD | [28](./28-chat-todo-resources.md) |
| Resources | `/resources/` | list, detail, form, topics | [28](./28-chat-todo-resources.md) |
| Sentinel | `/sentinel/` | command centre, stream, sessions, alerts, people, settings | [29](./29-sentinel-audit.md) |

---

## Unauthenticated endpoints (by design)

| URL | Why |
|---|---|
| `/ncm/webhook/` | NCM pushes here |
| `/api/integrations/woocommerce/orders/` | WooCommerce pushes here (**real HMAC**) |
| `/trendy-crm/integrations/meta/webhook/` | Meta pushes here |
| `/iclock/cdata`, `/iclock/getrequest`, `/iclock/devicecmd` | ZKTeco devices push here |
| `/store/` public browse pages | Customer-facing |
| `/`, `/welcome/`, `/login/` | Landing and login |
| `/accounts/password-reset/…` | Password recovery |

---

## Route-ordering dependencies

Two places where declaration order is load-bearing:

1. `dashboard/urls.py:334` — `/logistics/bulk-logs/trash/…` **before**
   `/logistics/bulk-logs/<str:provider>/…`, so `"trash"` isn't captured as a provider.
2. `trendycrm/urls.py` — the named provider connect routes **before**
   `integrations/<str:channel_key>/connect/`, so `facebook`/`instagram`/`whatsapp`/`tiktok`
   reach their specific views.
