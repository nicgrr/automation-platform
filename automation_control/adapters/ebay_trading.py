"""Read-only client for eBay's legacy Trading API -- specifically
GetMyeBaySelling, which is the one call that sees a listing regardless of
how it was created.

Why this exists alongside adapters/ebay.py's Sell Inventory client: the
Inventory API (GET /sell/inventory/v1/inventory_item + offer) only shows
listings that were themselves *created* through the Inventory API. A
listing made any other way -- by hand on eBay.com, through Seller Hub's
Relist button, via bulk File Exchange -- is invisible to it, permanently,
regardless of how often it's polled. Confirmed on this account directly:
the Inventory API reported 0 listings while two real, live ones existed,
found only once this call was tried. GetMyeBaySelling is the call Seller
Hub itself is built on, so it is the one place "what's actually live"
means the same thing here as it does on eBay's own page.

Trading API is XML-over-HTTP and predates OAuth, but accepts a Sell-scope
OAuth access token as an "IAF" bridge token via a header, which is what
lets this reuse the same connected credential as everything else --
nothing new to configure or reconnect.

Failure is not an HTTP status. Trading API returns HTTP 200 on almost
everything, success or failure, and puts the real result in <Ack> --
confirmed live by deliberately sending an invalid site id and getting
back a 200 with Ack=Failure and a real error message in the body.

Read-only like its sibling: no method here can create, edit, or end a
listing.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

import httpx

from .ebay import EbayApiError

TRADING_API_URL = {
    "sandbox": "https://api.sandbox.ebay.com/ws/api.dll",
    "production": "https://api.ebay.com/ws/api.dll",
}

# eBay's numeric site ids for the marketplaces this app actually lists on
# or is likely to. Trading API takes a site id, not the EBAY_XX string the
# rest of this app uses everywhere else, so this is the one place that
# translation has to happen.
SITE_IDS = {
    "EBAY_AU": "15", "EBAY_US": "0", "EBAY_GB": "3", "EBAY_DE": "77",
    "EBAY_CA": "2", "EBAY_FR": "71", "EBAY_IT": "101", "EBAY_ES": "186",
}

_NS = {"eb": "urn:ebay:apis:eBLBaseComponents"}
_COMPATIBILITY_LEVEL = "1193"  # matches the version pinned in ebay-category-listing-template.csv
MAX_PAGES = 20  # a hard stop against an unbounded loop, not a realistic cap for this account


@dataclass(frozen=True)
class ActiveListing:
    """One row of GetMyeBaySelling's ActiveList, normalised down to what
    the rest of the app actually uses. Everything eBay returns beyond this
    is kept in the caller's raw payload rather than added here piecemeal."""

    ebay_item_id: str
    title: str
    price: Decimal | None
    currency: str | None
    quantity: int | None
    quantity_sold: int
    listing_type: str | None
    view_item_url: str | None
    start_time: datetime | None


def _text(el: ET.Element | None, path: str) -> str | None:
    found = el.find(path, _NS) if el is not None else None
    return found.text if found is not None and found.text else None


def _decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_active_listings(xml_text: str) -> tuple[list[ActiveListing], int]:
    """Parses one page of GetMyeBaySelling's response.

    Returns (listings on this page, total page count) so the caller can
    decide whether to fetch another page. Raises EbayApiError on
    Ack=Failure, using eBay's own error text -- Trading API returns HTTP
    200 either way, so the body is the only place failure shows up.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise EbayApiError(f"eBay Trading API returned unparseable XML: {exc}") from exc

    ack = _text(root, "eb:Ack")
    if ack not in ("Success", "Warning"):
        message = _text(root, "eb:Errors/eb:LongMessage") or _text(root, "eb:Errors/eb:ShortMessage") or ack
        raise EbayApiError(f"eBay Trading API request failed: {message}")

    active_list = root.find("eb:ActiveList", _NS)
    if active_list is None:
        # eBay omits the whole <ActiveList> container, rather than
        # returning it empty, when there is nothing in it -- confirmed
        # live rather than assumed, since an absent section and a section
        # eBay declined to honour look identical from the outside without
        # checking Ack first (done above).
        return [], 1

    total_pages = _int(_text(active_list, "eb:PaginationResult/eb:TotalNumberOfPages")) or 1
    listings = []
    for item in active_list.findall("eb:ItemArray/eb:Item", _NS):
        listings.append(ActiveListing(
            ebay_item_id=_text(item, "eb:ItemID") or "",
            title=_text(item, "eb:Title") or "Untitled",
            price=_decimal(_text(item, "eb:SellingStatus/eb:CurrentPrice")
                          or _text(item, "eb:StartPrice") or _text(item, "eb:BuyItNowPrice")),
            currency=(item.find("eb:SellingStatus/eb:CurrentPrice", _NS).get("currencyID")
                     if item.find("eb:SellingStatus/eb:CurrentPrice", _NS) is not None else None),
            quantity=_int(_text(item, "eb:Quantity")),
            quantity_sold=_int(_text(item, "eb:SellingStatus/eb:QuantitySold")) or 0,
            listing_type=_text(item, "eb:ListingType"),
            view_item_url=_text(item, "eb:ListingDetails/eb:ViewItemURL"),
            start_time=_datetime(_text(item, "eb:ListingDetails/eb:StartTime")),
        ))
    return listings, total_pages


class EbayTradingReadAdapter:
    """Read-only. Deliberately has no create/revise/end methods, the same
    constraint adapters/ebay.py's Sell API client holds itself to -- the
    actual write path lives in listing_pipeline/publish.py behind its own
    Approval gate, and nothing here should be able to bypass that."""

    def __init__(self, access_token: str, app_id: str, *, environment: str = "sandbox",
                marketplace_id: str = "EBAY_AU", client: httpx.AsyncClient | None = None):
        self._access_token = access_token
        self._app_id = app_id
        self._url = TRADING_API_URL[environment]
        self._site_id = SITE_IDS.get(marketplace_id, SITE_IDS["EBAY_AU"])
        self.client = client or httpx.AsyncClient(timeout=20.0)

    def _headers(self, call_name: str) -> dict[str, str]:
        return {
            "X-EBAY-API-COMPATIBILITY-LEVEL": _COMPATIBILITY_LEVEL,
            "X-EBAY-API-CALL-NAME": call_name,
            "X-EBAY-API-SITEID": self._site_id,
            "X-EBAY-API-IAF-TOKEN": self._access_token,
            "X-EBAY-API-APP-NAME": self._app_id,
            "Content-Type": "text/xml",
        }

    async def get_active_listings(self, *, entries_per_page: int = 100) -> list[ActiveListing]:
        listings: list[ActiveListing] = []
        page = 1
        while True:
            body = (
                "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
                "<GetMyeBaySellingRequest xmlns=\"urn:ebay:apis:eBLBaseComponents\">"
                "<DetailLevel>ReturnAll</DetailLevel>"
                "<ActiveList><Include>true</Include>"
                f"<Pagination><EntriesPerPage>{entries_per_page}</EntriesPerPage>"
                f"<PageNumber>{page}</PageNumber></Pagination></ActiveList>"
                # Explicitly excluded, not just left to default: GetMyeBaySelling
                # can return SoldList/UnsoldList history alongside ActiveList,
                # and that history carries real buyer order details this call
                # has no reason to fetch or store.
                "<SoldList><Include>false</Include></SoldList>"
                "<UnsoldList><Include>false</Include></UnsoldList>"
                "</GetMyeBaySellingRequest>"
            )
            response = await self.client.post(self._url, content=body,
                                               headers=self._headers("GetMyeBaySelling"))
            if response.status_code >= 400:
                raise EbayApiError(f"eBay Trading API request failed ({response.status_code})")
            page_listings, total_pages = parse_active_listings(response.text)
            listings.extend(page_listings)
            if page >= total_pages or page >= MAX_PAGES or not page_listings:
                break
            page += 1
        return listings
