import asyncio
from decimal import Decimal

import httpx
import pytest

from automation_control.adapters.ebay import EbayApiError
from automation_control.adapters.ebay_trading_write import (
    EbayTradingListingAdapter,
    EbayTradingQuantityAdapter,
    InventoryRevision,
    parse_end_fixed_price_item,
    parse_revise_fixed_price_item,
    parse_revise_inventory_status,
)


@pytest.mark.asyncio
async def test_revise_inventory_status_sends_absolute_quantity_and_escapes_values():
    seen = {}

    async def handler(request):
        seen["headers"] = request.headers
        seen["body"] = request.content.decode()
        return httpx.Response(200, text=(
            '<ReviseInventoryStatusResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<Ack>Success</Ack></ReviseInventoryStatusResponse>'
        ))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayTradingQuantityAdapter("token", "app", environment="production", client=client)
    await adapter.revise_inventory_status([
        InventoryRevision("123", "sku&one", 4, price=Decimal("7.95"))
    ])

    assert seen["headers"]["x-ebay-api-call-name"] == "ReviseInventoryStatus"
    assert "<ItemID>123</ItemID>" in seen["body"]
    assert "<SKU>sku&amp;one</SKU>" in seen["body"]
    assert "<Quantity>4</Quantity>" in seen["body"]
    assert '<StartPrice currencyID="AUD">7.95</StartPrice>' in seen["body"]
    await client.aclose()


@pytest.mark.asyncio
async def test_revise_inventory_status_rejects_more_than_four_changes():
    adapter = EbayTradingQuantityAdapter("token", "app")
    with pytest.raises(ValueError, match="1-4"):
        await adapter.revise_inventory_status([InventoryRevision(str(i), str(i), 1) for i in range(5)])
    await adapter.client.aclose()


def test_revise_inventory_status_surfaces_ebay_failure_message():
    with pytest.raises(EbayApiError, match="variation SKU was not found"):
        parse_revise_inventory_status(
            '<ReviseInventoryStatusResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<Ack>Failure</Ack><Errors><LongMessage>variation SKU was not found</LongMessage></Errors>'
            '</ReviseInventoryStatusResponse>'
        )


@pytest.mark.asyncio
async def test_revise_fixed_price_item_sends_copy_and_complete_https_photo_set():
    seen = {}

    async def handler(request):
        seen["headers"] = request.headers
        seen["body"] = request.content.decode()
        return httpx.Response(200, text=(
            '<ReviseFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<Ack>Success</Ack><ItemID>123</ItemID></ReviseFixedPriceItemResponse>'
        ))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayTradingListingAdapter("token", "app", environment="production", client=client)
    await adapter.revise_copy_and_photos(
        "123", title="Luffy & Crew", description="Actual card < pictured",
        picture_urls=["https://images.example/front.jpg", "https://images.example/back.jpg"],
        price=Decimal("12.50"), condition_descriptor="400016",
    )

    assert seen["headers"]["x-ebay-api-call-name"] == "ReviseFixedPriceItem"
    assert "<ItemID>123</ItemID>" in seen["body"]
    assert "<Title>Luffy &amp; Crew</Title>" in seen["body"]
    assert "<Description>Actual card &lt; pictured</Description>" in seen["body"]
    assert seen["body"].count("<PictureURL>") == 2
    assert "<PictureSource>Vendor</PictureSource>" in seen["body"]
    assert '<StartPrice currencyID="AUD">12.50</StartPrice>' in seen["body"]
    assert "<ConditionID>4000</ConditionID>" in seen["body"]
    assert "<Name>40001</Name>" in seen["body"]
    assert "<Value>400016</Value>" in seen["body"]
    await client.aclose()


def test_revise_fixed_price_item_rejects_unknown_card_condition_descriptor():
    adapter = EbayTradingListingAdapter("token", "app")
    with pytest.raises(ValueError, match="unsupported eBay ungraded card condition"):
        asyncio.run(adapter.revise_copy_and_photos(
            "123", title="A title", description="A description",
            picture_urls=["https://images.example/front.jpg"],
            price=Decimal("12.50"), condition_descriptor="999999",
        ))
    asyncio.run(adapter.client.aclose())


def test_revise_fixed_price_item_requires_https_photos():
    adapter = EbayTradingListingAdapter("token", "app")
    with pytest.raises(ValueError, match="HTTPS"):
        asyncio.run(adapter.revise_copy_and_photos(
            "123", title="A title", description="A description",
            picture_urls=["http://images.example/front.jpg"],
            price=Decimal("12.50"),
        ))
    asyncio.run(adapter.client.aclose())


def test_revise_fixed_price_item_surfaces_ebay_failure_message():
    with pytest.raises(EbayApiError, match="title cannot be revised"):
        parse_revise_fixed_price_item(
            '<ReviseFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<Ack>Failure</Ack><Errors><LongMessage>title cannot be revised after a sale</LongMessage></Errors>'
            '</ReviseFixedPriceItemResponse>'
        )


@pytest.mark.asyncio
async def test_revise_title_changes_only_exact_parent_title():
    seen = {}

    async def handler(request):
        seen["body"] = request.content.decode()
        return httpx.Response(200, text=(
            '<ReviseFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<Ack>Success</Ack><ItemID>123</ItemID></ReviseFixedPriceItemResponse>'
        ))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayTradingListingAdapter("token", "app", environment="production", client=client)
    await adapter.revise_title("123", title="Dragon Ball & Friends - Complete Your Set")

    assert "<ItemID>123</ItemID>" in seen["body"]
    assert "<Title>Dragon Ball &amp; Friends - Complete Your Set</Title>" in seen["body"]
    assert "<Variations>" not in seen["body"]
    assert "<PictureDetails>" not in seen["body"]
    await client.aclose()


@pytest.mark.asyncio
async def test_revise_price_changes_only_the_price():
    seen = {}

    async def handler(request):
        seen["body"] = request.content.decode()
        return httpx.Response(200, text=(
            '<ReviseFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<Ack>Success</Ack><ItemID>123</ItemID></ReviseFixedPriceItemResponse>'
        ))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayTradingListingAdapter("token", "app", environment="production", client=client)
    await adapter.revise_price("123", price=Decimal("88"))

    assert "<ItemID>123</ItemID>" in seen["body"]
    assert '<StartPrice currencyID="AUD">88.00</StartPrice>' in seen["body"]
    for untouched in ("<Title>", "<Description>", "<PictureDetails>", "<Quantity>"):
        assert untouched not in seen["body"]
    await client.aclose()


def test_revise_price_rejects_prices_under_a_dollar():
    adapter = EbayTradingListingAdapter("token", "app")
    with pytest.raises(ValueError, match="at least AUD"):
        asyncio.run(adapter.revise_price("123", price=Decimal("0.50")))
    asyncio.run(adapter.client.aclose())


@pytest.mark.asyncio
async def test_delete_variations_addresses_one_parent_and_exact_skus():
    seen = {}

    async def handler(request):
        seen["body"] = request.content.decode()
        return httpx.Response(200, text=(
            '<ReviseFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<Ack>Success</Ack><ItemID>123</ItemID></ReviseFixedPriceItemResponse>'
        ))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayTradingListingAdapter("token", "app", environment="production", client=client)
    await adapter.delete_variations("123", skus=["sku&one", "sku-two"])

    assert "<ItemID>123</ItemID>" in seen["body"]
    assert seen["body"].count("<Variation>") == 2
    assert "<SKU>sku&amp;one</SKU><Delete>true</Delete>" in seen["body"]
    assert "<SKU>sku-two</SKU><Delete>true</Delete>" in seen["body"]
    await client.aclose()


@pytest.mark.asyncio
async def test_delete_variations_rejects_broad_or_duplicate_targets():
    adapter = EbayTradingListingAdapter("token", "app")
    with pytest.raises(ValueError, match="digits only"):
        await adapter.delete_variations("123,456", skus=["sku-one"])
    with pytest.raises(ValueError, match="at least one"):
        await adapter.delete_variations("123", skus=[])
    with pytest.raises(ValueError, match="unique"):
        await adapter.delete_variations("123", skus=["sku-one", "sku-one"])
    await adapter.client.aclose()


@pytest.mark.asyncio
async def test_end_fixed_price_item_requires_exact_numeric_id_and_sends_reason():
    seen = {}

    async def handler(request):
        seen["headers"] = request.headers
        seen["body"] = request.content.decode()
        return httpx.Response(200, text=(
            '<EndFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<Ack>Success</Ack><ItemID>267796015368</ItemID></EndFixedPriceItemResponse>'
        ))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EbayTradingListingAdapter("token", "app", environment="production", client=client)
    await adapter.end_item("267796015368", reason="NotAvailable")

    assert seen["headers"]["x-ebay-api-call-name"] == "EndFixedPriceItem"
    assert "<ItemID>267796015368</ItemID>" in seen["body"]
    assert "<EndingReason>NotAvailable</EndingReason>" in seen["body"]
    with pytest.raises(ValueError, match="digits only"):
        await adapter.end_item("267796015368,267")
    await client.aclose()


def test_end_fixed_price_item_surfaces_ebay_failure_message():
    with pytest.raises(EbayApiError, match="item cannot be ended"):
        parse_end_fixed_price_item(
            '<EndFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<Ack>Failure</Ack><Errors><LongMessage>item cannot be ended</LongMessage></Errors>'
            '</EndFixedPriceItemResponse>'
        )

@pytest.mark.asyncio
async def test_photo_only_revision_preserves_other_fields():
    async def handler(request):
        from xml.etree import ElementTree as ET
        root = ET.fromstring(request.content)
        ns = {'e': 'urn:ebay:apis:eBLBaseComponents'}
        item = root.find('e:Item', ns)
        assert {x.tag.split('}')[-1] for x in item} == {'ItemID', 'PictureDetails'}
        assert request.content.startswith(b'<?xml version="1.0" encoding="utf-8"?>')
        assert item.find('e:ItemID', ns).text == '123'
        assert item.find('e:PictureDetails/e:PictureURL', ns).text == 'https://example.com/front.jpg'
        return httpx.Response(200, text='<ReviseFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack></ReviseFixedPriceItemResponse>')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = EbayTradingListingAdapter('token', 'app', client=client)
        await adapter.revise_photos('123', picture_urls=['https://example.com/front.jpg'])
