# A2 — Status Quick Reference

A one-page cheat sheet. Full explanations in [07 — Order statuses](./07-order-statuses.md).

---

## The five fields

| Field | Holds | Read by |
|---|---|---|
| `Order.status` | System status string | Sync paths, bulk-sync eligibility |
| `Order.order_status` | **Duplicate** of the above | Orders list, on-hold, returns list, KPI tiles |
| `Order.status_setup` | FK → `Setup` row | Order-detail header badge and dropdown |
| `Order.ncm_status` | NCM's raw wording | NCM panel, logistics list, badge colours |
| `Order.payment_status` (+ `payment_status_setup`) | Payment state | Revenue KPI |

**Never write these directly.** Use `apply_manual_status()` (staff) or
`sync_order_status_fields()` (courier).

---

## System statuses

| Value | Meaning |
|---|---|
| `processing` | New order; also the "unrecognised NCM status" fallback |
| `pending` | Awaiting confirmation |
| `confirmed` | Confirmed by staff |
| `Pickup Created` / `pickup_created` | Handed to a courier |
| `in_transit` | Moving |
| `dispatched` | Dispatched locally by barcode scan |
| `shipped` | Legacy bulk action only |
| `packed` | Business-specific |
| `delivered` | Delivered to the customer |
| `cancelled` | **Protected — no sync overwrites it** |
| `return_processing` | In the return-to-vendor pipeline |
| `return` | Confirmed back with the vendor |
| `returned` | Physically scanned back in |
| `redirected` | Redirected to a different customer |
| `on_hold`, `inquiry` | Business-specific holding states |

Payment: `pending`, `paid`, `partial`, `cod_pending`

---

## NCM raw → system status

`services/ncm_service.py:653-671`

| NCM raw status | System status |
|---|---|
| `Pickup Order Created` | `Pickup Created` |
| `Drop off Order Created` | `Pickup Created` |
| `Pickup Complete` | `in_transit` |
| `Drop off Order Collected` | `in_transit` |
| `Dispatched` | `in_transit` |
| `In Transit` | `in_transit` |
| `Arrived` | `in_transit` |
| `Sent for Delivery` | `in_transit` |
| `Out for Delivery` | `in_transit` |
| `Delivered` | `delivered` |
| `Confirmed` | `delivered` |
| `Returned` | `returned` |
| `Return Initiated` | `return_processing` |
| `Return Approved` | `return_processing` |
| `Order Marked Return` | `return_processing` |
| `Sent to Vendor` | `return_processing` |
| `Returned to Warehouse` | `return` |
| *contains `return` / `rtv` / `sent to vendor`* | `return_processing` |
| *anything else* | `processing` |

## Payment mapping

| NCM | Internal |
|---|---|
| `COD Collected` | `paid` |
| `Payment Collected` | `paid` |
| `Pending` | `pending` |
| `COD Pending` | `cod_pending` |

## Webhook event → status

`ncm/webhook_handler.py:43-50` — used only when `status` is blank.

| Event | Status |
|---|---|
| `pickup_completed` | `Pickup Complete` |
| `sent_for_delivery` | `Sent for Delivery` |
| `order_dispatched` | `Dispatched` |
| `order_arrived` | `Arrived` |
| `delivery_completed` | `Delivered` |
| `order_marked_rtv` | `Order Marked Return` |

---

## The `Delivered` ambiguity

`resolve_delivered_status()` — `services/ncm_service.py:694`

| `vendor_return` | Status text | Result |
|---|---|---|
| false | `Delivered` | `('delivered', 'paid')` |
| false | anything else | ordinary mapping |
| **true** | reads as completed | `('return', None)` |
| **true** | anything else | `('return_processing', None)` |

"Reads as completed" = starts with `delivered`, `confirmed`, `returned`,
`returned to warehouse`, or `return completed`.

- ✅ `Returned to Warehouse (TINKUNE)` → completed
- ❌ `Arrived at RETURN (TINKUNE)` → still travelling

`vendor_return` arrives as the **string** `'True'` / `'False'`.

---

## Protected & terminal

| Set | Values | Effect |
|---|---|---|
| `PROTECTED_STATUSES` (`ncm/bulk_sync.py:52`) | `cancelled` | **Never** overwritten by any sync, regardless of settings |
| `DEFAULT_TERMINAL_STATUSES` (`:40-43`) | `cancelled`, `delivered`, `return`, `returned`, `return_initiated`, `return_approved` | Skipped by the background sync. **Overridable** via `APISettings.bulk_sync_included_statuses` |
| `COMPLETED_RETURN_SYSTEM_STATUSES` (`ncm_service.py:619`) | `return`, `returned` | Never downgraded to `return_processing` |

---

## The manual hold

| Function | File | Does |
|---|---|---|
| `apply_manual_status(order, name, setup)` | `services/status_override.py:64` | Writes all three status fields + stamps the hold |
| `manual_override_holds(order, incoming, event_at)` | `:110` | `True` while the hold wins |
| `clear_manual_status_override(order)` | `:55` | Drops it |

**The hold releases when** NCM reports a **different** raw status, **or** NCM's `event_at`
is **later** than the manual change, **or** there was never a hold.

**The hold does not protect `ncm_status`** — the raw string is always recorded.

Consulted by: webhook · per-order sync · bulk sync · sync-all · order recovery ·
`repair_return_stage`.

---

## Badge colours

`dashboard/logistics_status.py` — applied to the **raw** courier status.

| Bucket | Class |
|---|---|
| `Delivered`, `Confirmed` | `bg-success` |
| `In Transit`, `Dispatched`, `Arrived`, `Sent for Delivery`, `Out for Delivery` | `bg-primary` |
| `Returned`, `Return Initiated`, `Return Approved`, `Order Marked Return`, `Sent to Vendor`, `Returned to Warehouse` | `bg-danger` |
| `Cancelled` | `bg-danger` |
| `Order Created`, `Pickup Order Created`, `Drop off Order Created`, `Pickup Complete`, `Drop off Order Collected` | `bg-warning text-dark` |
| anything else | `bg-secondary` |

Blank → `Pickup Order Created` (NCM) or `Order Created` (PND).

---

## Redirection gates

`dashboard/views.py:5401-5446`

| Gate | Rule |
|---|---|
| **Non-redirectable** | RTV `last_status` in `returned` / `delivered` / `sent to vendor`, **or** the order's `ncm_status` matches `delivered\|returned\|sent to vendor`, **or** the order is `delivered` |
| **Redirect-eligible** | Status starts with `arrived`, `pickup complete`, or `returned to warehouse` (NCM's own rule) |

---

## WooCommerce → `store.Order`

`integrations/services.py` — `WOO_STATUS_MAP`

| Woo | Internal |
|---|---|
| `pending`, `on-hold` | `pending` |
| `processing` | `confirmed` |
| `completed`, `delivered` | `delivered` |
| `shipped` | `shipped` |
| `cancelled`, `refunded`, `failed` | `cancelled` |

`delivered` and `shipped` are **custom statuses** this store registers on top of
WooCommerce's seven core ones — they arrive over the API with the `wc-` prefix already
stripped, exactly like the core ones. `delivered` is not a rare edge case: it is the single
largest bucket in the live store (~2,875 of 4,954 orders). Anything unrecognised still falls
back to `pending`, so a new plugin status silently lands there — check `WOO_STATUS_MAP`
before assuming an order is genuinely awaiting payment.

---

## Other status vocabularies

| Model | Values | File |
|---|---|---|
| `ReturnRequest.status` | `pending` (default), `approved`, `rejected`, `received`, `inspecting`, `approved_refund`, `approved_exchange`, `refunded`, `exchanged`, `cancelled` | `dashboard/models.py:915-926` |
| `Dispatch.status` | `pending`, `processing`, `confirmed`, `packed`, `shipped`, `delivered`, `cancelled`, `dispatched` (default) | `:1124-1133` |
| `DispatchItem.dispatch_status` | `success`, `failed`, `not_found` (default) | `:1342-1346` |
| `DispatchItem.failure_code` | `already_dispatched`, `not_found`, `status_reverted`, `error` | `:1350-1355` |
| `NCMBulkLog.status` | `processing`, `completed`, `partial`, `failed`, `cancelled` | `ncm/models.py:10-16` |
| `WebhookLog.status` | `pending`, `processing`, `completed`, `failed` | `ncm/models.py:156-161` |
| `Purchase.payment_status` | `paid`, `partial`, `unpaid` (default) | `dashboard/models.py:1739-1743` |
| `Order.exchange_status` | `''`, `pending`, `created`, `failed` | `:529-534` |
| `RTVOrder.rtv_marked_at_source` | `''`, `order_created`, `status_timeline`, `ncm_staff_comment`, `comment`, `webhook`, `manual` | `:2201-2209` |
| `OrderActivityLog.action_type` | `created`, `status_changed`, `payment_changed`, `tracking_added`, `tracking_updated`, `notes_added`, `notes_updated`, `updated`, `redirected` (+ undeclared `deleted`, `restored`, `trashed`, `city_detected`) | `:739-749` |
| `sentinel.EventType` | 17 values — see [29](./29-sentinel-audit.md) | `sentinel/models.py:28-45` |
