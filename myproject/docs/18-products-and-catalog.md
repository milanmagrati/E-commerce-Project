# 18 — Products & Catalog

Products, variations, bundles, batches, images and the shared media library.

---

## Product types

`Product.PRODUCT_TYPE` — `dashboard/models.py:50-54`:

| Type | Means |
|---|---|
| `simple` | One SKU, one price, one stock number |
| `variable` | Has `ProductVariation` rows (size, colour, …), each with its own SKU and stock |
| `bundle` | Composed of other products via `BundleComponent`. Reserving a bundle reserves each component |

`STOCK_STATUS` (`:56-60`): `in_stock` (default), `out_of_stock`, `low_stock`.
`COST_PRICE_TYPE` (`:62-65`): `fixed`, `variable`.

---

## `Product` — `dashboard/models.py:48-198`

| Group | Fields |
|---|---|
| Ownership | `user` FK, `related_name='products'` |
| Identity | name, SKU, slug, `category` FK, `description` |
| Pricing | selling price, cost price, offer price, `cost_price_type` |
| Stock | `stock`, `reserved_qty` (`:86`), `backordered_qty` (`:87`), `backorders_allowed` (`:88`), low-stock threshold |
| Type | `product_type`, `is_bundle` |
| Media | main image + `ProductImage` gallery |
| Soft delete | `is_deleted`, `deleted_at` |

**`available_stock` property** (`:190`) = `stock − reserved_qty`. Use this, not `stock`,
when asking "can I sell one?" See [17](./17-inventory-and-stock.md).

### Storefront helper properties

Added Sep 2026 when the storefront learned to sell variations. All read-only, no schema
change, so views, template tags and templates all answer the same question the same way:

| On `Product` | Line | Returns |
|---|---|---|
| `is_variable` | `:123` | `product_type == 'variable'` |
| `active_variations` | `:128` | The sellable `ProductVariation` rows |
| `has_variations` | `:140` | Variable **and** it actually has active rows |
| `variation_price_range` | `:146` | `(min, max)` across those rows, else `(price, price)` |
| `in_stock_variation_exists` | `:155` | Any option still buyable |
| `storefront_available` | `:162` | The one question a card should ask before offering a buy button |

| On `ProductVariation` | Line | Returns |
|---|---|---|
| `display_label` | `:726` | `variation_name` or the SKU |
| `is_in_stock` | `:732` | `stock > 0` |
| `committed_qty` | `:749` | Units already sitting on open dashboard `OrderItem` rows |
| `available_stock` | `:764` | `stock − committed_qty` — **derived, not a counter**, so it cannot drift |

> A variable product's own `price` is a parent value **nobody is charged**. Quote
> `variation_price_range` or the picked variation. See [24](./24-storefront.md).

---

## Related models

| Model | Lines | Purpose |
|---|---|---|
| `Category` | `:18-28` | Product categories |
| `ProductBatch` | `:201-246` | **FIFO lots.** `quantity` remaining, `initial_quantity`, `manufactured_date`, `expiry_date`, `cost_price`. Ordered `['expiry_date', 'created_at']` — oldest expiry first |
| `BundleComponent` | `:248-279` | `component_product` + `quantity_required` |
| `ProductPurchase` | `:281-300` | Weighted-average cost tracking |
| `ProductAttribute` | `:632-642` | e.g. "Size" |
| `ProductAttributeValue` | `:644-654` | e.g. "Large" |
| `ProductVariation` | `:656-681` | One sellable variant, own SKU/price/stock |
| `VariationAttributeValue` | `:683-693` | Links a variation to its attribute values |
| `ProductImage` | `:695-721` | Gallery images, orderable, one featured |
| `ProductVariantOption` | `:723-735` | Variant option storage |
| `MediaCategory` | `:2575-2599` | Folders in the media library |
| `MediaAsset` | `:2601-2616` | A reusable image, pickable from any product gallery |

### Expiry tracking

Because `ProductBatch` carries `expiry_date` and sorts by it, the system can warn about
stock going out of date. `dashboard/context_processors.py:283` `expiry_notifications` puts
that warning on every page.

---

## Pages

### Products

| Page | URL | View | Template | Permission |
|---|---|---|---|---|
| Product list | `/products/` | `products_view` | `products.html` | `can_view_products` |
| Add product | `/products/add/` | `product_add` | `product_form.html` | `can_create_products` |
| Product detail | `/products/<id>/` | `product_detail` | `product_detail.html` | `can_view_products` |
| Edit product | `/products/<id>/edit/` | `product_edit` | `product_form.html` | `can_edit_products` |
| Trash | `/products/trash/` | `products_trash` | `products_trash.html` | `can_delete_products` |

Trash actions (all `can_delete_products`): `product_move_to_trash`, `product_restore`,
`product_permanent_delete`, `products_trash_bulk_action`, `empty_trash`.
Bulk: `/products/bulk-action/`. Export: `/products/export-excel/`.

### Categories

| Page | URL | Template | Permission |
|---|---|---|---|
| Category list | `/categories/` | `category_list.html` | **`@admin_only`** |
| Edit | `/categories/<id>/edit/` | `category_edit.html` | `@admin_only` |
| Delete | `/categories/<id>/delete/` | — | `@admin_only` |
| Quick add (AJAX) | `/add-category/` | — | `@login_required` |

### Variations

| Page | URL | Template | Permission |
|---|---|---|---|
| Variations | `/products/<id>/variations/` | `product_variations.html` | `can_edit_products` |

Plus `variation_create` (`can_create_products`), `variation_update` (`can_edit_products`),
`variation_delete`.

### Images

All JSON, all `can_edit_products`:

`delete_main_product_image` · `upload_product_images` (also aliased `product_gallery_upload`)
· `delete_product_image` · `set_featured_image` · `reorder_product_images`

### Media library

| Page | URL | Template | Permission |
|---|---|---|---|
| Media library | `/media/` | `media_library.html` | `@admin_or_permission_required('can_edit_products', 'can_create_products')` |
| Media list API | `/api/media/` | — | same |

A shared pool of images so the same photo can be reused across products without
re-uploading. `MediaAsset.image` uploads to `media_library/%Y/%m/`.

`backfill_media_library.py` at the repo root is a historical one-off that populated this
from existing product images.

---

## Product APIs

All JSON, all `@login_required`:

| URL | Purpose |
|---|---|
| `/api/search-products/` | Autocomplete in the order form |
| `/api/product/<id>/` | Product details |
| `/api/product/<id>/update-price/` | Inline price edit |
| `/api/product/<id>/variations/` | Variation picker |
| `/api/bestselling-products/` | Dashboard widget |
| `/api/create-custom-product/` | Create a product inline while writing an order |
| `/api/product/<id>/stock-in/` | Receive stock |

---

## Price visibility

Pricing is gated more finely than most things, because staff should not always see cost:

| Flag | Controls |
|---|---|
| `can_view_cost_price` | Cost price at all |
| `can_edit_prices` | Changing prices |
| `can_give_discounts` + `max_discount_percent` | Discounting, with a per-user ceiling |
| `can_access_offer_price` | Offer price field |
| `can_view_selling_unit_price` / `can_view_cost_unit_price` | Per-unit columns |
| `can_toggle_product_price` | Switch the displayed basis |
| `can_view_valuation_selling` / `can_view_valuation_cost` / `can_toggle_stock_valuation` | Stock valuation reports |

`max_discount_percent` is the only **non-boolean** permission on `CustomUser`.

---

## Gotchas

- **`available_stock`, not `stock`.** Reserved units are still physically present.
- **A `ProductVariation` is a sellable thing on the storefront now**, not just admin metadata.
  Deleting or deactivating one removes a shopper's option (and cascades away any
  `store.BulkDiscount` scoped to it) — `store.OrderItem.variation` is `SET_NULL`, so placed
  orders keep their `selected_variant` text.
- **`ProductVariantOption` is not `ProductVariation`.** The former is loose comma-separated
  display metadata; only the latter has a SKU, a price and stock, and only the latter can be
  bought.
- **Bundles reserve components, not themselves.** A bundle's own `stock` number is not what
  gates a sale.
- **Batches sort by expiry, then creation** — so FIFO here means "oldest expiry first", not
  strictly "oldest received first".
- Category management is **admin-only**, but the AJAX quick-add is available to any logged-in
  user.
- `delete_product_image` and `variation_delete` are each **defined twice** in
  `dashboard/views.py` (`:2135`/`:9087` and `:2907`/`:9178`); the later definition wins. See
  [A4](./A4-appendix-known-quirks.md).
- Product deletion is soft by default; permanent delete is a separate action.

---

## Files that own this

- `dashboard/models.py:18-280` — `Category`, `Product`, `ProductBatch`, `BundleComponent`, `ProductPurchase`
- `dashboard/models.py:632-735` — attributes, variations, images
- `dashboard/models.py:2575-2616` — media library
- `dashboard/views.py` — the product, category, variation and media views
- `dashboard/urls.py:15-58` — the routes
- `dashboard/templates/product*.html`, `media_library.html`
