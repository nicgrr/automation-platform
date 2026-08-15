from datetime import UTC, datetime
from typing import Any

import httpx


class EbayNotConfigured(Exception):
    pass


class EbayApiError(Exception):
    pass


class EbaySandboxReadAdapter:
    """Read-only eBay Sandbox client. No mutation methods exist."""

    base_url = "https://api.sandbox.ebay.com"

    def __init__(self, access_token: str | None = None, client: httpx.AsyncClient | None = None):
        self._access_token = access_token
        self.client = client or httpx.AsyncClient(timeout=15.0)

    def _headers(self) -> dict[str, str]:
        if not self._access_token:
            raise EbayNotConfigured("eBay sandbox access token is not configured")
        return {"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"}

    async def _get(self, path: str, **kwargs: Any) -> dict:
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        response = await self.client.get(f"{self.base_url}{path}", headers=headers, **kwargs)
        if response.status_code >= 400:
            raise EbayApiError(f"eBay Sandbox API request failed ({response.status_code})")
        return response.json()

    async def get_listings(self, *, limit: int = 50, offset: int = 0) -> Any:
        params = {"limit": min(max(limit, 1), 200), "offset": max(offset, 0)}
        inventory = await self._get("/sell/inventory/v1/inventory_item", params=params)
        offers = await self._get("/sell/inventory/v1/offer", params=params)
        inventory["offers"] = offers.get("offers", [])
        return inventory

    async def get_seller(self) -> Any:
        return await self._get("/commerce/identity/v1/user/")

    async def search_market(self, query: str, *, limit: int = 20, marketplace: str = "EBAY_AU") -> Any:
        return await self._get("/buy/browse/v1/item_summary/search", params={"q": query, "limit": min(max(limit, 1), 200)}, headers={**self._headers(), "X-EBAY-C-MARKETPLACE-ID": marketplace})


def normalize_inventory(payload: dict, *, retrieved_at: datetime | None = None) -> list[dict]:
    retrieved_at = retrieved_at or datetime.now(UTC)
    offers_by_sku = {offer.get("sku"): offer for offer in payload.get("offers", [])}
    normalized = []
    for item in payload.get("inventoryItems", []):
        product = item.get("product") or {}
        availability = item.get("availability") or {}
        ship = availability.get("shipToLocationAvailability") or {}
        price = item.get("price") or {}
        offer = offers_by_sku.get(item.get("sku"), {})
        price = offer.get("pricingSummary", {}).get("price") or price
        normalized.append({
            "ebay_item_id": offer.get("listing", {}).get("listingId") or offer.get("offerId") or item.get("sku"),
            "sku": item.get("sku"),
            "title": product.get("title") or item.get("sku") or "Untitled",
            "price": price.get("value"),
            "currency": price.get("currency"),
            "quantity": ship.get("quantity"),
            "listing_status": offer.get("status") or "INVENTORY_ITEM",
            "marketplace": offer.get("marketplaceId"),
            "retrieved_at": retrieved_at,
            "raw": {"inventory_item": item, "offer": offer},
        })
    return normalized


def normalize_market(payload: dict, *, retrieved_at: datetime | None = None) -> list[dict]:
    retrieved_at = retrieved_at or datetime.now(UTC)
    return [{"ebay_item_id": item.get("itemId"), "title": item.get("title"), "price": (item.get("price") or {}).get("value"), "currency": (item.get("price") or {}).get("currency"), "marketplace": item.get("itemLocation", {}).get("country"), "retrieved_at": retrieved_at} for item in payload.get("itemSummaries", [])]
