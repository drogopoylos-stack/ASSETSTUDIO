---
name: wolt-drive-api
description: Use when integrating Wolt Drive (on-demand last-mile courier API) — auth, base URLs, the shipment-promise→delivery flow, cancel, and status webhooks.
disable-model-invocation: true
metadata:
  category: integrations
  created: 2026-06-20
  updated: 2026-06-20
  confidence: verified
  source: developer.wolt.com/docs/wolt-drive (fetched 2026-06; re-verify endpoints/fields, API still evolving)
---

# Wolt Drive API (on-demand delivery)

Dispatch a Wolt courier to deliver an order from your store to a customer. Distinct from Wolt Marketplace/POS (those bring you *orders*; Drive is *logistics*).

## Auth & base URLs
- Header: `Authorization: Bearer <token>` (token + a `venue_id` come from your Wolt contact/account).
- Production: `https://daas-public-api.wolt.com`
- Sandbox/dev: `https://daas-public-api.development.dev.woltapi.com`
- Money amounts are returned in **minor units** (e.g. cents).

## Venueful flow (recommended — fixed pickup = your venue)
Two calls: price-quote first, then create with the quote id.

1. **Shipment promise (quote):**
   `POST /v1/venues/{venue_id}/shipment-promises`
   Body: `street`, `city`, `post_code`, `lat?`, `lon?`, `min_preparation_time_minutes?` (how long until ready), `scheduled_dropoff_time?` (ISO-8601), `parcels?`.
   Returns: `id` (the promise id), `valid_until`, `price{amount,currency}`, and a `dropoff` block with **Wolt-geocoded coordinates + ETA**.

2. **Create delivery:**
   `POST /v1/venues/{venue_id}/deliveries`
   Body: `shipment_promise_id` (from step 1), `dropoff{location:{coordinates:{lat,lon}}, comment, options}`, `recipient{name, phone_number, email}`, `parcels[]` (dimensions/weight/contents), plus optional `order_number`, `merchant_order_reference_id`, `tips`, `cash`.
   Returns: `id`, `status`, `tracking{id,url}`, `price`, `pickup.eta`, `dropoff.eta`, `wolt_order_reference_id`.

**Gotcha:** `deliveries` needs dropoff **coordinates**. If your customer record has only a text address, take the `lat/lon` Wolt resolved in the *shipment-promise* response and pass those into `deliveries` — don't geocode yourself.

## Venueless flow (dynamic pickup each call)
`POST /merchants/{merchant_id}/delivery-fee` → then `POST /merchants/{merchant_id}/delivery-order`. You must include pickup location + contact in each call (no stored venue).

## Other endpoints
- Cancel: `PATCH /order/{wolt_order_reference_id}/status/cancel` body `{ "reason": "…" }`.
- Handshake PIN (proof of delivery): `GET /order/{wolt_order_reference_id}/handshake-delivery`.
- Coverage check: `GET /merchants/{merchant_id}/delivery-areas`.

## Status tracking
There is **no polling endpoint** for live status — use the customer **tracking URL** returned on create, and register a **webhook** to receive events (`order.*` lifecycle: created → picked_up → delivered, plus ETA updates). Store the raw webhook payload; map fields from a real sample.

## Minimal Python client
```python
import httpx
BASE = {"production": "https://daas-public-api.wolt.com",
        "development": "https://daas-public-api.development.dev.woltapi.com"}

class WoltDrive:
    def __init__(self, token, venue_id, env="production"):
        self.h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self.base, self.venue = BASE[env], venue_id

    def quote(self, street, city, post_code, prep_min=30, parcels=None):
        r = httpx.post(f"{self.base}/v1/venues/{self.venue}/shipment-promises",
                       headers=self.h, json={"street": street, "city": city, "post_code": post_code,
                       "min_preparation_time_minutes": prep_min, "parcels": parcels or []})
        r.raise_for_status(); return r.json()

    def create(self, promise, recipient, parcels, order_number=None):
        body = {"shipment_promise_id": promise["id"],
                "dropoff": {"location": {"coordinates": promise["dropoff"]["location"]["coordinates"]}},
                "recipient": recipient, "parcels": parcels}
        if order_number: body["order_number"] = order_number[:5]
        r = httpx.post(f"{self.base}/v1/venues/{self.venue}/deliveries", headers=self.h, json=body)
        r.raise_for_status(); return r.json()  # -> tracking.url, wolt_order_reference_id
```
