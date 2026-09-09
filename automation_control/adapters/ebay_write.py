"""Mutating eBay Sell API client -- a deliberately separate class from
EbaySandboxReadAdapter (see that class's own docstring and
tests/test_ebay_adapter.py, which asserts the read adapter has neither
`create_listing` nor `update_listing`). Every method here makes a real,
possibly-billable, possibly-irreversible call against whichever
environment it's constructed for.

Nothing in this module is wired into any route or script yet -- see
automation_control/ebay_publish.py (Phase 3) and automation_control/
ebay_sync.py (Phase 4) for the call sites, both of which gate the
listing-creation path behind an Approval record per the existing
convention in automation_control/approvals.py.
"""

from typing import Any

import httpx

from .ebay import EbayApiError, EbayNotConfigured

_BASE_URLS = {"sandbox": "https://api.sandbox.ebay.com", "production": "https://api.ebay.com"}


class EbayWriteAdapter:
    def __init__(self, access_token: str | None = None, client: httpx.AsyncClient | None = None, *, environment: str = "sandbox"):
        self._access_token = access_token
        self.base_url = _BASE_URLS[environment]
        self.client = client or httpx.AsyncClient(timeout=15.0)

    def _headers(self) -> dict[str, str]:
        if not self._access_token:
            raise EbayNotConfigured("eBay access token is not configured")
        return {"Authorization": f"Bearer {self._access_token}", "Accept": "application/json", "Content-Type": "application/json"}

    async def _get(self, path: str, **kwargs: Any) -> dict:
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        response = await self.client.get(f"{self.base_url}{path}", headers=headers, **kwargs)
        if response.status_code >= 400:
            raise EbayApiError(f"eBay API GET {path} failed ({response.status_code}): {response.text}")
        return response.json() if response.content else {}

    async def _put(self, path: str, json: dict, **kwargs: Any) -> dict:
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        response = await self.client.put(f"{self.base_url}{path}", json=json, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise EbayApiError(f"eBay API PUT {path} failed ({response.status_code}): {response.text}")
        return response.json() if response.content else {}

    async def _post(self, path: str, json: dict | None = None, **kwargs: Any) -> dict:
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        response = await self.client.post(f"{self.base_url}{path}", json=json, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise EbayApiError(f"eBay API POST {path} failed ({response.status_code}): {response.text}")
        return response.json() if response.content else {}

    async def upsert_inventory_item(self, sku: str, payload: dict) -> None:
        """createOrReplaceInventoryItem -- a full replace, not a patch: any
        field omitted from `payload` is cleared on eBay's side, not left
        alone. Callers that only mean to change one field (see
        set_inventory_quantity below) must fetch the current item first."""
        await self._put(f"/sell/inventory/v1/inventory_item/{sku}", json=payload)

    async def get_inventory_item(self, sku: str) -> dict:
        return await self._get(f"/sell/inventory/v1/inventory_item/{sku}")

    async def set_inventory_quantity(self, sku: str, quantity: int) -> None:
        """Reduce/raise a SKU's eBay quantity without disturbing any other
        field -- fetches the current inventory item (upsert_inventory_item
        is a full replace, per its own docstring above) and PUTs it back
        with only availability.shipToLocationAvailability.quantity changed."""
        current = await self.get_inventory_item(sku)
        availability = current.setdefault("availability", {})
        ship_to = availability.setdefault("shipToLocationAvailability", {})
        ship_to["quantity"] = quantity
        await self.upsert_inventory_item(sku, current)

    async def create_offer(self, payload: dict) -> str:
        response = await self._post("/sell/inventory/v1/offer", json=payload)
        return response["offerId"]

    async def publish_offer(self, offer_id: str) -> str:
        response = await self._post(f"/sell/inventory/v1/offer/{offer_id}/publish")
        return response["listingId"]

    async def withdraw_offer(self, offer_id: str) -> None:
        await self._post(f"/sell/inventory/v1/offer/{offer_id}/withdraw")

    async def get_orders(self, *, since: str | None = None, limit: int = 50) -> Any:
        """`since` is an RFC 3339 timestamp string (eBay's `filter=lastmodifieddate` format
        expects `[<from>..<to>]`); the caller (scripts/import_ebay_orders.py) owns
        formatting it, this just passes the filter through untouched when given."""
        params: dict[str, Any] = {"limit": min(max(limit, 1), 200)}
        if since:
            params["filter"] = f"lastmodifieddate:[{since}..]"
        return await self._get("/sell/fulfillment/v1/order", params=params)
