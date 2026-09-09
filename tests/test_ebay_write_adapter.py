import httpx
import pytest

from automation_control.adapters.ebay import EbayApiError, EbayNotConfigured
from automation_control.adapters.ebay_write import EbayWriteAdapter


@pytest.mark.asyncio
async def test_defaults_to_sandbox_and_fails_closed_without_token():
    adapter = EbayWriteAdapter()
    assert adapter.base_url == "https://api.sandbox.ebay.com"
    with pytest.raises(EbayNotConfigured):
        await adapter.create_offer({})


def test_production_environment_targets_the_production_host():
    adapter = EbayWriteAdapter(environment="production")
    assert adapter.base_url == "https://api.ebay.com"


@pytest.mark.asyncio
async def test_upsert_inventory_item_puts_the_full_payload_to_the_sku_path():
    async def handler(request: httpx.Request):
        assert request.method == "PUT"
        assert request.url.path == "/sell/inventory/v1/inventory_item/listing-1"
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayWriteAdapter("tok", client)
    await adapter.upsert_inventory_item("listing-1", {"condition": "USED_EXCELLENT"})
    await client.aclose()


@pytest.mark.asyncio
async def test_set_inventory_quantity_fetches_then_replaces_only_the_quantity():
    calls = []

    async def handler(request: httpx.Request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"sku": "listing-1", "condition": "USED_EXCELLENT", "availability": {"shipToLocationAvailability": {"quantity": 3}}})
        assert request.method == "PUT"
        import json
        body = json.loads(request.content)
        assert body["condition"] == "USED_EXCELLENT"  # untouched fields survive the round trip
        assert body["availability"]["shipToLocationAvailability"]["quantity"] == 0
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayWriteAdapter("tok", client)
    await adapter.set_inventory_quantity("listing-1", 0)
    assert calls == ["GET", "PUT"]
    await client.aclose()


@pytest.mark.asyncio
async def test_create_offer_returns_the_new_offer_id():
    async def handler(request: httpx.Request):
        assert request.method == "POST"
        assert request.url.path == "/sell/inventory/v1/offer"
        return httpx.Response(201, json={"offerId": "offer-123"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayWriteAdapter("tok", client)
    assert await adapter.create_offer({"sku": "listing-1"}) == "offer-123"
    await client.aclose()


@pytest.mark.asyncio
async def test_publish_offer_returns_the_new_ebay_listing_id():
    async def handler(request: httpx.Request):
        assert request.url.path == "/sell/inventory/v1/offer/offer-123/publish"
        return httpx.Response(200, json={"listingId": "ebay-999"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayWriteAdapter("tok", client)
    assert await adapter.publish_offer("offer-123") == "ebay-999"
    await client.aclose()


@pytest.mark.asyncio
async def test_withdraw_offer_posts_to_the_withdraw_path():
    async def handler(request: httpx.Request):
        assert request.url.path == "/sell/inventory/v1/offer/offer-123/withdraw"
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayWriteAdapter("tok", client)
    await adapter.withdraw_offer("offer-123")
    await client.aclose()


@pytest.mark.asyncio
async def test_get_orders_applies_the_lastmodifieddate_filter_when_since_given():
    async def handler(request: httpx.Request):
        assert request.url.path == "/sell/fulfillment/v1/order"
        assert "filter=lastmodifieddate" in str(request.url)
        return httpx.Response(200, json={"orders": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayWriteAdapter("tok", client)
    result = await adapter.get_orders(since="2026-09-01T00:00:00.000Z")
    assert result == {"orders": []}
    await client.aclose()


@pytest.mark.asyncio
async def test_a_4xx_response_raises_ebay_api_error():
    async def handler(request: httpx.Request):
        return httpx.Response(400, text="bad category id")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayWriteAdapter("tok", client)
    with pytest.raises(EbayApiError):
        await adapter.create_offer({})
    await client.aclose()
