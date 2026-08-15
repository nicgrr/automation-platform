import httpx
import pytest

from automation_control.adapters.grocery import GroceryReadAdapter


@pytest.mark.asyncio
async def test_grocery_adapter_allows_only_known_gets():
    async def handler(request: httpx.Request):
        assert request.method == "GET"
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GroceryReadAdapter("http://grocery", client)
    assert await adapter.get("status") == {"ok": True}
    with pytest.raises(ValueError):
        await adapter.get("write")
    await client.aclose()

