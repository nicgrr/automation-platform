"""adapters/ebay_trading.py: the call that sees a listing regardless of how
it was created.

The XML fixtures below are trimmed but structurally real -- copied down
from a live GetMyeBaySelling response against this account (two real
listings, one relisted through Seller Hub) after confirming by hand that
the Sell Inventory API could not see either of them.
"""

from decimal import Decimal

import pytest

from automation_control.adapters.ebay import EbayApiError
from automation_control.adapters.ebay_trading import EbayTradingReadAdapter, parse_active_listings

SUCCESS_ONE_PAGE = """<?xml version='1.0' encoding='UTF-8'?>
<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">
<Ack>Success</Ack>
<ActiveList><ItemArray>
<Item><ItemID>267785450003</ItemID><Title>Pokemon Pikachu TG05/TG30 Lost Origin</Title>
<Quantity>1</Quantity>
<SellingStatus><CurrentPrice currencyID="AUD">300.00</CurrentPrice><QuantitySold>0</QuantitySold></SellingStatus>
<ListingType>FixedPriceItem</ListingType>
<ListingDetails><StartTime>2026-09-14T10:00:00.000Z</StartTime>
<ViewItemURL>https://www.ebay.com.au/itm/267785450003</ViewItemURL></ListingDetails></Item>
<Item><ItemID>267785450167</ItemID><Title>EKWB Watercooling Bundle</Title>
<Quantity>1</Quantity>
<SellingStatus><CurrentPrice currencyID="AUD">299.00</CurrentPrice><QuantitySold>0</QuantitySold></SellingStatus>
<ListingType>FixedPriceItem</ListingType>
<ListingDetails><StartTime>2026-09-13T08:00:00.000Z</StartTime>
<ViewItemURL>https://www.ebay.com.au/itm/267785450167</ViewItemURL></ListingDetails></Item>
</ItemArray>
<PaginationResult><TotalNumberOfPages>1</TotalNumberOfPages><TotalNumberOfEntries>2</TotalNumberOfEntries></PaginationResult>
</ActiveList>
</GetMyeBaySellingResponse>"""

SUCCESS_NO_ACTIVE_LIST = """<?xml version='1.0' encoding='UTF-8'?>
<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">
<Ack>Success</Ack><Timestamp>2026-09-14T22:20:16Z</Timestamp><Version>1271</Version>
</GetMyeBaySellingResponse>"""

FAILURE = """<?xml version='1.0' encoding='UTF-8'?>
<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">
<Ack>Failure</Ack><Errors>
<ShortMessage>Invalid value for header "X-EBAY-API-SITEID".</ShortMessage>
<LongMessage>Header "X-EBAY-API-SITEID" with value "99999" is out of range.</LongMessage>
<ErrorCode>10012</ErrorCode><SeverityCode>Error</SeverityCode>
</Errors>
</GetMyeBaySellingResponse>"""


def _page(item_ids: list[str], page: int, total_pages: int) -> str:
    items = "".join(
        f'<Item><ItemID>{i}</ItemID><Title>Item {i}</Title><Quantity>1</Quantity>'
        f'<SellingStatus><CurrentPrice currencyID="AUD">10.00</CurrentPrice><QuantitySold>0</QuantitySold></SellingStatus>'
        f'<ListingType>FixedPriceItem</ListingType><ListingDetails><ViewItemURL>https://x/{i}</ViewItemURL></ListingDetails></Item>'
        for i in item_ids
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack>'
        f'<ActiveList><ItemArray>{items}</ItemArray>'
        f'<PaginationResult><TotalNumberOfPages>{total_pages}</TotalNumberOfPages></PaginationResult></ActiveList>'
        '</GetMyeBaySellingResponse>'
    )


class _FakeClient:
    """Records every call and answers with canned pages in order, the way
    real pagination would arrive."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def post(self, url, content=None, headers=None):
        self.calls.append((url, headers, content))

        class _Response:
            def __init__(self, text, status_code=200):
                self.text = text
                self.status_code = status_code

        return _Response(self.responses.pop(0))


# --- parse_active_listings: pure, no network -------------------------------

def test_parses_two_real_listings():
    listings, pages = parse_active_listings(SUCCESS_ONE_PAGE)
    assert pages == 1
    assert [l.ebay_item_id for l in listings] == ["267785450003", "267785450167"]
    assert listings[0].price == Decimal("300.00")
    assert listings[0].currency == "AUD"
    assert listings[0].quantity == 1
    assert listings[0].listing_type == "FixedPriceItem"
    assert listings[0].view_item_url == "https://www.ebay.com.au/itm/267785450003"


def test_an_absent_activelist_container_means_zero_not_an_error():
    """eBay omits the whole <ActiveList> element, rather than returning it
    empty, when there is nothing in it -- confirmed live. This must not be
    mistaken for a request eBay silently ignored."""
    listings, pages = parse_active_listings(SUCCESS_NO_ACTIVE_LIST)
    assert listings == []
    assert pages == 1


def test_ack_failure_raises_with_ebays_own_message():
    """Trading API returns HTTP 200 on failure too -- the real status lives
    in <Ack>, confirmed live with a deliberately invalid site id."""
    with pytest.raises(EbayApiError, match="out of range"):
        parse_active_listings(FAILURE)


def test_unparseable_xml_raises_cleanly():
    with pytest.raises(EbayApiError):
        parse_active_listings("not xml at all <<<")


# --- EbayTradingReadAdapter: pagination and headers -------------------------

@pytest.mark.asyncio
async def test_fetches_a_single_page_when_that_is_all_there_is():
    client = _FakeClient([SUCCESS_ONE_PAGE])
    adapter = EbayTradingReadAdapter("tok", "app-id", environment="production", client=client)

    listings = await adapter.get_active_listings()

    assert len(listings) == 2
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_walks_every_page_until_total_pages_is_reached():
    client = _FakeClient([_page(["a"], 1, 3), _page(["b"], 2, 3), _page(["c"], 3, 3)])
    adapter = EbayTradingReadAdapter("tok", "app-id", environment="production", client=client)

    listings = await adapter.get_active_listings()

    assert [l.ebay_item_id for l in listings] == ["a", "b", "c"]
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_stops_if_a_page_comes_back_empty_even_if_more_pages_were_claimed():
    """A defensive stop, not just a total-pages check -- an inconsistent
    response must not spin the loop."""
    client = _FakeClient([_page(["a"], 1, 5), _page([], 2, 5)])
    adapter = EbayTradingReadAdapter("tok", "app-id", environment="production", client=client)

    listings = await adapter.get_active_listings()

    assert [l.ebay_item_id for l in listings] == ["a"]
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_the_iaf_token_bridges_the_oauth_token_into_trading_api():
    """Trading API predates OAuth; this header is what lets it accept the
    same Sell-scope access token as everything else in the app."""
    client = _FakeClient([SUCCESS_ONE_PAGE])
    adapter = EbayTradingReadAdapter("my-oauth-token", "my-app-id",
                                     environment="production", client=client)
    await adapter.get_active_listings()

    _, headers, _ = client.calls[0]
    assert headers["X-EBAY-API-IAF-TOKEN"] == "my-oauth-token"
    assert headers["X-EBAY-API-APP-NAME"] == "my-app-id"
    assert headers["X-EBAY-API-CALL-NAME"] == "GetMyeBaySelling"


@pytest.mark.asyncio
async def test_sold_and_unsold_history_are_explicitly_excluded():
    """GetMyeBaySelling can return real buyer order details alongside
    ActiveList; this call has no reason to fetch them, so the request body
    says so explicitly rather than relying on a default."""
    client = _FakeClient([SUCCESS_ONE_PAGE])
    adapter = EbayTradingReadAdapter("tok", "app-id", environment="production", client=client)
    await adapter.get_active_listings()

    _, _, body = client.calls[0]
    assert "<SoldList><Include>false</Include></SoldList>" in body
    assert "<UnsoldList><Include>false</Include></UnsoldList>" in body


@pytest.mark.asyncio
async def test_production_and_sandbox_target_different_hosts():
    prod = _FakeClient([SUCCESS_ONE_PAGE])
    await EbayTradingReadAdapter("tok", "app", environment="production", client=prod).get_active_listings()
    assert prod.calls[0][0] == "https://api.ebay.com/ws/api.dll"

    sandbox = _FakeClient([SUCCESS_ONE_PAGE])
    await EbayTradingReadAdapter("tok", "app", environment="sandbox", client=sandbox).get_active_listings()
    assert sandbox.calls[0][0] == "https://api.sandbox.ebay.com/ws/api.dll"


@pytest.mark.asyncio
async def test_an_unmapped_marketplace_falls_back_to_au_rather_than_crashing():
    client = _FakeClient([SUCCESS_ONE_PAGE])
    adapter = EbayTradingReadAdapter("tok", "app", environment="production",
                                     marketplace_id="EBAY_NOWHERE", client=client)
    await adapter.get_active_listings()
    assert client.calls[0][1]["X-EBAY-API-SITEID"] == "15"


@pytest.mark.asyncio
async def test_an_http_error_status_raises_ebayapierror():
    class _ErrorClient:
        calls = []
        async def post(self, *a, **k):
            class _R:
                status_code = 500
                text = "server error"
            return _R()

    with pytest.raises(EbayApiError):
        await EbayTradingReadAdapter("tok", "app", environment="production",
                                     client=_ErrorClient()).get_active_listings()
