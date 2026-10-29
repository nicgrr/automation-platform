import httpx
import pytest

from automation_control.adapters.ebay import EbayApiError, EbayNotConfigured, EbaySandboxReadAdapter


@pytest.mark.asyncio
async def test_ebay_defaults_to_sandbox_and_fails_closed_without_token():
    adapter = EbaySandboxReadAdapter()
    assert adapter.base_url == "https://api.sandbox.ebay.com"
    with pytest.raises(EbayNotConfigured):
        await adapter.get_listings()
    assert not hasattr(adapter, "update_listing")


def test_production_environment_targets_the_production_host():
    adapter = EbaySandboxReadAdapter(environment="production")
    assert adapter.base_url == "https://api.ebay.com"
    # still read-only regardless of environment
    assert not hasattr(adapter, "update_listing")
    assert not hasattr(adapter, "create_listing")



class _FakeClient:
    """Records every call and answers by URL path, the way a real eBay
    response would -- inventory_item once, offer once per SKU."""
    def __init__(self, offers_by_sku=None):
        self.calls = []
        self.offers_by_sku = offers_by_sku or {}

    async def get(self, url, headers=None, params=None):
        self.calls.append((url, params))

        class _Response:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
            def json(self):
                return self._payload

        if url.endswith("/sell/inventory/v1/inventory_item"):
            return _Response(200, {"inventoryItems": [{"sku": sku} for sku in self.offers_by_sku]})
        if url.endswith("/sell/inventory/v1/offer"):
            sku = params.get("sku") if params else None
            if not sku:
                # The bug this test locks in: eBay's real Inventory API has
                # no "list every offer" call. GET .../offer with no sku
                # 400s (errorId 25707, "invalid value for a SKU") because a
                # missing sku is treated as an empty one.
                return _Response(400, {"errors": [{"errorId": 25707}]})
            return _Response(200, {"offers": self.offers_by_sku.get(sku, [])})
        raise AssertionError(f"unexpected call: {url}")


@pytest.mark.asyncio
async def test_get_listings_fetches_offers_per_sku_not_in_one_bulk_call():
    """The endpoint this used to hit -- GET .../offer with no sku -- 400s on
    every real eBay account regardless of environment, because getOffers is
    per-SKU and there is no bulk list. Confirmed live against production:
    an account with inventory items and zero offers is the ordinary case
    (items created but never published), not an error."""
    client = _FakeClient({"SKU-A": [{"offerId": "o1"}], "SKU-B": [{"offerId": "o2"}]})
    adapter = EbaySandboxReadAdapter("tok", client=client, environment="production")

    payload = await adapter.get_listings()

    offer_calls = [(url, params) for url, params in client.calls if url.endswith("/offer")]
    assert {p["sku"] for _, p in offer_calls} == {"SKU-A", "SKU-B"}
    assert {o["offerId"] for o in payload["offers"]} == {"o1", "o2"}


@pytest.mark.asyncio
async def test_inventory_items_with_no_offers_is_not_an_error():
    """Items exist but were never published -- the state confirmed live on
    this account: inventory_item returns total:0, so no offer call is made
    and get_listings returns cleanly rather than raising."""
    client = _FakeClient({})
    adapter = EbaySandboxReadAdapter("tok", client=client, environment="production")

    payload = await adapter.get_listings()

    assert payload["offers"] == []
    assert not any(url.endswith("/offer") for url, _ in client.calls)


@pytest.mark.asyncio
async def test_error_messages_name_the_real_environment_not_hardcoded_sandbox():
    """Both messages used to say "sandbox" unconditionally, so a production
    failure read as a sandbox problem -- found while auditing other pages
    for the same class of bug adapters/ebay_trading.py was built to avoid.
    """
    adapter = EbaySandboxReadAdapter(access_token=None, environment="production")
    with pytest.raises(EbayNotConfigured, match="production"):
        adapter._headers()

    class _FailingClient:
        async def get(self, *a, **k):
            class _R:
                status_code = 500
            return _R()

    prod = EbaySandboxReadAdapter("tok", client=_FailingClient(), environment="production")
    with pytest.raises(EbayApiError, match="production") as exc:
        await prod._get("/x")
    assert "sandbox" not in str(exc.value).lower()

    sandbox = EbaySandboxReadAdapter("tok", client=_FailingClient(), environment="sandbox")
    with pytest.raises(EbayApiError, match="sandbox"):
        await sandbox._get("/x")


@pytest.mark.asyncio
async def test_transient_ebay_timeout_is_retried(monkeypatch):
    """One Browse API timeout must not abort the entire nightly price sweep."""
    class _FlakyClient:
        def __init__(self):
            self.calls = 0

        async def get(self, *args, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise httpx.ReadTimeout("temporary eBay timeout")

            class _Response:
                status_code = 200

                @staticmethod
                def json():
                    return {"itemSummaries": []}

            return _Response()

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("automation_control.adapters.ebay.asyncio.sleep", no_wait)
    client = _FlakyClient()
    adapter = EbaySandboxReadAdapter("tok", client=client, environment="production")

    payload = await adapter.search_market("Pikachu")

    assert payload == {"itemSummaries": []}
    assert client.calls == 3
