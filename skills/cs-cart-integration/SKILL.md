---
name: cs-cart-integration
description: Use when scraping or integrating a CS-Cart storefront (product/category/price/stock/description sync) — page structure, REST API, add-ons, and incremental crawling.
disable-model-invocation: true
metadata:
  category: integrations
  created: 2026-06-20
  updated: 2026-06-20
  confidence: verified
  source: session research (live-tested against a CS-Cart store, 2026-06)
---

# CS-Cart Integration (scrape + REST API + add-on)

How to pull a CS-Cart catalogue (products, categories, prices, stock, descriptions) and push back.

## Identify CS-Cart (it is NOT WordPress)
URLs use a dispatcher: `index.php?dispatch=products.view&product_id=…`, `dispatch=categories.view`, `dispatch=products.newest`. Storefront categories are **single-segment pretty URLs** (`/e-liquids/`). If you see `dispatch=`, treat it as CS-Cart and use the recipes below, not WP REST.

## Scraping the storefront (no credentials)
- **Product data → prefer JSON-LD.** Each product page has `<script type="application/ld+json">` with an `@type: Product` node: `name`, `sku` (the product/EAN code), `brand.name`, `image[]`, and `offers[].price` + `priceCurrency` + `availability` (`InStock`/`OutOfStock`). A separate `@type: BreadcrumbList` node gives the category path — drop the leading "Home/Αρχική" and the trailing item (== product name) to get the category.
- **Full description → `#content_description`.** The long description lives in `<div id="content_description" class="ty-wysiwyg-content content-description">`. JSON-LD `description` is usually **empty**; `og:description` is just a short snippet. Extraction order that works: `#content_description` → `[itemprop="description"]` → JSON-LD `description` → `<meta og:description>`.
  - **Decode entities twice.** CS-Cart's WYSIWYG often stores double-encoded entities, so `get_text()` still returns literal `&nbsp;`/`&amp;`. Run `html.unescape()` on the extracted text, then collapse whitespace (`re.sub(r"\s+", " ", s)`). Cap length (~3000 chars) for spreadsheet cells.
- **Listing + pagination.** Product links on any listing are `<a class="product-title" href>`. Paginate any category or dispatch with `?items_per_page=96&page=N`; stop when a page yields no new product links.
- **True "newest" order.** `index.php?dispatch=products.newest&items_per_page=96&page=N` returns products in the store's real newest-first order. Crawl it and stamp a `newest_rank` per product — public pages don't expose date-added otherwise, so sorting by your own first-seen timestamp will NOT match the site.
- **Stock.** Public pages expose availability **status only** (in/out of stock), never the quantity. For quantities you need the REST API.

## REST API 2.0 (admin credentials)
- Base `{store}/api/2.0/…`; auth = **HTTP Basic** with `admin_email : api_key` (enable under the admin user's profile → API access).
- `GET /api/2.0/products?page=&items_per_page=250` → `product_id`, `product` (name), `product_code`, `price`, **`amount` (stock qty)**, `timestamp` (created, unix), `full_description`, `main_category`.
  - **`tracking` field:** `"B"`/`"O"` = track inventory by `amount`; **`"D"` = do not track** → treat as always in stock regardless of `amount`.
- `GET /api/2.0/categories` → `category_id`, `category`.
- `PUT /api/2.0/products/{id}` with `{"price":…, "amount":…}` to push price/stock back.

## Extending the site (add-on, PHP)
CS-Cart is PHP. Add server-side behavior via `app/addons/<id>/`:
- `addon.xml` (manifest + settings schema), `init.php` calling `fn_register_hooks('change_order_status', …)`, `func.php` with `fn_<addon>_<hook>(...)`.
- Read settings with `Tygh\Registry::get('addons.<id>.<key>')`.
- `change_order_status($order_id, $status_to, $status_from, …)` is the hook to trigger external actions (e.g. dispatch a courier) when an order reaches a chosen status.

## Incremental crawl pattern (scan all, fetch only new)
The cheap part is listing the catalogue; the expensive part is fetching each product page. So:
1. Every run, discover the full catalogue (category crawl → all product URLs).
2. Diff against URLs already stored; **fetch only the new ones**. A catalogue with thousands of products then re-syncs in ~1–2 min (listing + newest crawl only) instead of re-downloading every page.
3. Keep a separate **"Full re-sync"** that refetches everything — prices/descriptions of *existing* products only refresh on a full pass.
Note the floor cost: the listing/newest crawls are sequential page GETs (~1 s each on a live store), so even a "0 new" scan takes ~tens of seconds. Parallelize listing fetches if you need it faster.

## Scraper hygiene
Set a real `User-Agent` + `Accept-Language`, add a small per-request delay, fetch product pages concurrently (thread pool ~8) but keep listing crawls polite. `robots.txt` on CS-Cart typically blocks only `/app/`.
