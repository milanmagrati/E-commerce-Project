# EcommerceAdmin — System Documentation

This folder is the manual for the EcommerceAdmin Django project: what every page does,
where its data comes from, and how order statuses move — both internally and through the
NCM logistics API.

It is written **from the code as it currently stands**, not from older design notes.
Where an older document disagreed with the code, the code won and the difference is
recorded in [A4 — Known quirks & doc drift](./A4-appendix-known-quirks.md).

---

## How to read this

You do **not** need to read it in order. Pick your entry point:

| If you want to… | Start here |
|---|---|
| Understand the system in 5 minutes | [01 — System overview](./01-system-overview.md) |
| Know why a status changed | [07 — Order statuses](./07-order-statuses.md) ← **the important one** |
| Fix something on the orders screen | [05 — Orders list](./05-orders-list.md) |
| Fix something on one order | [06 — Order detail](./06-order-detail.md) |
| Understand NCM end-to-end | [09](./09-ncm-api-client.md) → [10](./10-ncm-sending.md) → [11](./11-ncm-webhook.md) → [12](./12-ncm-sync-and-scheduler.md) |
| Find a URL or view | [A1 — URL map](./A1-appendix-url-map.md) |
| Give someone the right access | [03 — Auth, roles & permissions](./03-auth-roles-permissions.md) |
| Debug a live problem | [30 — Ops & troubleshooting](./30-ops-and-troubleshooting.md) |
| Change how a product's page looks in the shop | [31 — Product page themes](./31-product-page-themes.md) |

---

## Chapters

### Foundations
- [01 — System overview](./01-system-overview.md)
- [02 — Architecture & conventions](./02-architecture-and-conventions.md)
- [03 — Auth, roles & permissions](./03-auth-roles-permissions.md)

### The order core *(deepest coverage)*
- [04 — Order data model](./04-order-data-model.md)
- [05 — Orders list page](./05-orders-list.md)
- [06 — Order detail page](./06-order-detail.md)
- [07 — Order statuses: the complete reference](./07-order-statuses.md)
- [08 — Creating, editing & bulk actions](./08-order-create-edit-bulk.md)

### Logistics *(deepest coverage)*
- [09 — NCM API client](./09-ncm-api-client.md)
- [10 — Sending orders to NCM](./10-ncm-sending.md)
- [11 — Receiving from NCM: the webhook](./11-ncm-webhook.md)
- [12 — NCM sync & the cron-less scheduler](./12-ncm-sync-and-scheduler.md)
- [13 — Pick and Drop](./13-pick-and-drop.md)
- [14 — RTV & order redirection](./14-rtv-and-redirection.md)

### Operations
- [15 — Returns (RMA)](./15-returns.md)
- [16 — Dispatch](./16-dispatch.md)
- [17 — Inventory & stock](./17-inventory-and-stock.md)
- [18 — Products & catalog](./18-products-and-catalog.md)
- [19 — Customers & follow-ups](./19-customers-and-followups.md)
- [20 — Reports & analytics](./20-reports-and-analytics.md)
- [21 — Purchases & suppliers](./21-purchases-and-suppliers.md)
- [22 — Settings & Setup Management](./22-settings-and-setup.md)

### Other apps
- [23 — HRM (HR, attendance, payroll)](./23-hrm.md)
- [24 — Storefront](./24-storefront.md)
- [25 — TrendyCRM](./25-trendycrm.md)
- [26 — Bill rewards (OCR + loyalty)](./26-bill-rewards.md)
- [27 — Google Sheets & WooCommerce](./27-sheets-and-woocommerce.md)
- [28 — Chat, Todo & Resources](./28-chat-todo-resources.md)
- [29 — Sentinel Vault (audit trail)](./29-sentinel-audit.md)
- [31 — Product page themes & the landing-page editor](./31-product-page-themes.md)

### Reference
- [30 — Ops & troubleshooting](./30-ops-and-troubleshooting.md)
- [A1 — Complete URL map](./A1-appendix-url-map.md)
- [A2 — Status quick reference](./A2-appendix-status-reference.md)
- [A3 — Permission flags](./A3-appendix-permission-flags.md)
- [A4 — Known quirks & doc drift](./A4-appendix-known-quirks.md)

---

## Conventions used in this manual

**Code pointers.** Facts carry a `file.py:line` pointer, e.g. `dashboard/views.py:3061`.
Line numbers drift when code is edited — if a pointer looks wrong, search for the function
name instead. Every chapter ends with a **Files that own this** list for exactly that reason.

**Page blocks.** Every page is documented with the same block, so you can skim:

> **Purpose** → **URL / name / view / template / permission** → **Where the data comes
> from** → **Filters** → **Actions** → **Live updates** → **Gotchas**

**Diagrams** are [Mermaid](https://mermaid.js.org/) inside ` ```mermaid ` fences. They
render on GitHub and in most markdown editors, and stay editable as plain text.

**Terminology.**

| Term | Means |
|---|---|
| **NCM** | Nepal Can Move — the primary courier, has a full two-way API |
| **PND** | Pick and Drop — the secondary courier, outbound only |
| **RTV** | Return To Vendor — a parcel travelling back to us through NCM |
| **Setup row** | A row in the `Setup` table; this is where status names actually live |
| **System status** | Our internal status value, e.g. `in_transit` |
| **Raw status** | The courier's own wording, e.g. `Sent for Delivery` |
| **Manual hold** | A staff-set status protected from being overwritten by the next sync |

---

## Editing this manual

These are plain markdown files — edit them freely. Two habits keep them useful:

1. When you change code, update the chapter that owns it (each chapter names its files).
2. Keep the page block structure. It is what makes the docs skimmable rather than a wall
   of text.
