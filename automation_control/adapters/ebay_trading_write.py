"""Narrow, explicitly-invoked eBay Trading API write clients.

The quantity adapter serves the confirmed bulk-order workflow. The listing
adapter revises title, description, price and the complete photo set for one
already-live fixed-price listing after Listing Studio's explicit approval. It
can also end one exact item when a caller supplies an explicit item id; it
cannot create or relist anything.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import httpx

from .ebay import EbayApiError
from .ebay_trading import SITE_IDS, TRADING_API_URL, _COMPATIBILITY_LEVEL, _NS, _text

MAX_REVISIONS_PER_REQUEST = 4


@dataclass(frozen=True)
class InventoryRevision:
    item_id: str
    sku: str
    quantity: int
    price: Decimal | None = None


def parse_revise_inventory_status(xml_text: str) -> None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise EbayApiError(f"eBay Trading API returned unparseable XML: {exc}") from exc
    ack = _text(root, "eb:Ack")
    if ack not in ("Success", "Warning"):
        message = _text(root, "eb:Errors/eb:LongMessage") or _text(root, "eb:Errors/eb:ShortMessage") or ack
        raise EbayApiError(f"eBay quantity update failed: {message}")


def parse_revise_fixed_price_item(xml_text: str) -> None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise EbayApiError(f"eBay Trading API returned unparseable XML: {exc}") from exc
    ack = _text(root, "eb:Ack")
    if ack not in ("Success", "Warning"):
        message = _text(root, "eb:Errors/eb:LongMessage") or _text(root, "eb:Errors/eb:ShortMessage") or ack
        raise EbayApiError(f"eBay listing revision failed: {message}")


def parse_end_fixed_price_item(xml_text: str) -> None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise EbayApiError(f"eBay Trading API returned unparseable XML: {exc}") from exc
    ack = _text(root, "eb:Ack")
    if ack not in ("Success", "Warning"):
        message = _text(root, "eb:Errors/eb:LongMessage") or _text(root, "eb:Errors/eb:ShortMessage") or ack
        raise EbayApiError(f"eBay listing end failed: {message}")


class EbayTradingQuantityAdapter:
    """Can only revise the absolute quantity of existing ItemID/SKU pairs."""

    def __init__(self, access_token: str, app_id: str, *, environment: str = "sandbox",
                 marketplace_id: str = "EBAY_AU", client: httpx.AsyncClient | None = None):
        self._access_token = access_token
        self._app_id = app_id
        self._url = TRADING_API_URL[environment]
        self._site_id = SITE_IDS.get(marketplace_id, SITE_IDS["EBAY_AU"])
        self.client = client or httpx.AsyncClient(timeout=20.0)

    def _headers(self) -> dict[str, str]:
        return {
            "X-EBAY-API-COMPATIBILITY-LEVEL": _COMPATIBILITY_LEVEL,
            "X-EBAY-API-CALL-NAME": "ReviseInventoryStatus",
            "X-EBAY-API-SITEID": self._site_id,
            "X-EBAY-API-IAF-TOKEN": self._access_token,
            "X-EBAY-API-APP-NAME": self._app_id,
            "Content-Type": "text/xml",
        }

    async def revise_inventory_status(self, revisions: list[InventoryRevision]) -> None:
        if not revisions or len(revisions) > MAX_REVISIONS_PER_REQUEST:
            raise ValueError(f"each eBay quantity request must contain 1-{MAX_REVISIONS_PER_REQUEST} revisions")
        blocks = []
        for revision in revisions:
            if revision.quantity < 0:
                raise ValueError("eBay quantity cannot be negative")
            price_block = ""
            if revision.price is not None:
                try:
                    clean_price = Decimal(str(revision.price)).quantize(Decimal("0.01"))
                except (InvalidOperation, ValueError) as exc:
                    raise ValueError("eBay price must be a valid amount") from exc
                if clean_price < Decimal("1.00"):
                    raise ValueError("eBay fixed-price listings must be at least AUD $1.00")
                price_block = f'<StartPrice currencyID="AUD">{clean_price:.2f}</StartPrice>'
            blocks.append(
                "<InventoryStatus>"
                f"<ItemID>{escape(revision.item_id)}</ItemID>"
                f"<SKU>{escape(revision.sku)}</SKU>"
                f"<Quantity>{revision.quantity}</Quantity>"
                f"{price_block}"
                "</InventoryStatus>"
            )
        body = (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<ReviseInventoryStatusRequest xmlns=\"urn:ebay:apis:eBLBaseComponents\">"
            + "".join(blocks)
            + "</ReviseInventoryStatusRequest>"
        )
        response = await self.client.post(self._url, content=body, headers=self._headers())
        if response.status_code >= 400:
            raise EbayApiError(f"eBay quantity update failed ({response.status_code})")
        parse_revise_inventory_status(response.text)


class EbayTradingListingAdapter:
    """Can revise or explicitly end one existing fixed-price listing."""

    def __init__(self, access_token: str, app_id: str, *, environment: str = "sandbox",
                 marketplace_id: str = "EBAY_AU", client: httpx.AsyncClient | None = None):
        self._access_token = access_token
        self._app_id = app_id
        self._url = TRADING_API_URL[environment]
        self._site_id = SITE_IDS.get(marketplace_id, SITE_IDS["EBAY_AU"])
        self.client = client or httpx.AsyncClient(timeout=30.0)

    def _headers(self) -> dict[str, str]:
        return {
            "X-EBAY-API-COMPATIBILITY-LEVEL": _COMPATIBILITY_LEVEL,
            "X-EBAY-API-CALL-NAME": "ReviseFixedPriceItem",
            "X-EBAY-API-SITEID": self._site_id,
            "X-EBAY-API-IAF-TOKEN": self._access_token,
            "X-EBAY-API-APP-NAME": self._app_id,
            "Content-Type": "text/xml",
        }

    async def revise_photos(self, item_id: str, *, picture_urls: list[str]) -> None:
        """Replace only the gallery of an exact existing fixed-price item."""
        if not item_id.strip() or not 1 <= len(picture_urls) <= 24:
            raise ValueError("item ID and 1-24 photos are required")
        if any(not url.startswith("https://") for url in picture_urls):
            raise ValueError("every eBay photo URL must use HTTPS")
        if sum(map(len, picture_urls)) > 3975:
            raise ValueError("combined eBay photo URLs exceed 3975 characters")
        pictures = "".join(f"<PictureURL>{escape(url)}</PictureURL>" for url in picture_urls)
        body = (
            '<ReviseFixedPriceItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
            f'<Item><ItemID>{escape(item_id.strip())}</ItemID>'
            '<PictureDetails><PictureSource>Vendor</PictureSource>'
            f'{pictures}</PictureDetails></Item></ReviseFixedPriceItemRequest>'
        )
        response = await self.client.post(self._url, content=body, headers=self._headers())
        if response.status_code >= 400:
            raise EbayApiError(f"eBay photo revision failed ({response.status_code})")
        parse_revise_fixed_price_item(response.text)

    async def revise_copy_and_photos(
        self, item_id: str, *, title: str, description: str, picture_urls: list[str],
        price: Decimal, condition_descriptor: str | None = None,
    ) -> None:
        clean_title = title.strip()
        clean_description = description.strip()
        if not item_id.strip():
            raise ValueError("eBay item ID is required")
        if not clean_title or len(clean_title) > 80:
            raise ValueError("eBay title must contain 1-80 characters")
        if not clean_description:
            raise ValueError("eBay description is required")
        if not picture_urls or len(picture_urls) > 24:
            raise ValueError("eBay requires 1-24 photos")
        if any(not url.startswith("https://") for url in picture_urls):
            raise ValueError("every eBay photo URL must use HTTPS")
        if sum(len(url) for url in picture_urls) > 3975:
            raise ValueError("combined eBay photo URLs exceed 3975 characters")
        try:
            clean_price = Decimal(str(price)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("eBay price must be a valid amount") from exc
        if clean_price < Decimal("1.00"):
            raise ValueError("eBay fixed-price listings must be at least AUD $1.00")
        condition_block = ""
        if condition_descriptor is not None:
            clean_descriptor = condition_descriptor.strip()
            if clean_descriptor not in {"400010", "400015", "400016", "400017"}:
                raise ValueError("unsupported eBay ungraded card condition descriptor")
            condition_block = (
                "<ConditionID>4000</ConditionID>"
                "<ConditionDescriptors><ConditionDescriptor>"
                "<Name>40001</Name>"
                f"<Value>{clean_descriptor}</Value>"
                "</ConditionDescriptor></ConditionDescriptors>"
            )
        pictures = "".join(f"<PictureURL>{escape(url)}</PictureURL>" for url in picture_urls)
        body = (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<ReviseFixedPriceItemRequest xmlns=\"urn:ebay:apis:eBLBaseComponents\">"
            "<Item>"
            f"<ItemID>{escape(item_id.strip())}</ItemID>"
            f"<Title>{escape(clean_title)}</Title>"
            f"<Description>{escape(clean_description)}</Description>"
            f"<StartPrice currencyID=\"AUD\">{clean_price:.2f}</StartPrice>"
            f"{condition_block}"
            "<PictureDetails><PictureSource>Vendor</PictureSource>"
            f"{pictures}</PictureDetails>"
            "</Item></ReviseFixedPriceItemRequest>"
        )
        response = await self.client.post(self._url, content=body, headers=self._headers())
        if response.status_code >= 400:
            raise EbayApiError(f"eBay listing revision failed ({response.status_code})")
        parse_revise_fixed_price_item(response.text)

    async def revise_title(self, item_id: str, *, title: str) -> None:
        """Change only the parent title, preserving variations and their photos."""
        clean_item_id = item_id.strip()
        clean_title = title.strip()
        if not clean_item_id or not clean_item_id.isdigit():
            raise ValueError("eBay item ID must contain digits only")
        if not clean_title or len(clean_title) > 80:
            raise ValueError("eBay title must contain 1-80 characters")
        body = (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<ReviseFixedPriceItemRequest xmlns=\"urn:ebay:apis:eBLBaseComponents\">"
            "<Item>"
            f"<ItemID>{escape(clean_item_id)}</ItemID>"
            f"<Title>{escape(clean_title)}</Title>"
            "</Item></ReviseFixedPriceItemRequest>"
        )
        response = await self.client.post(self._url, content=body, headers=self._headers())
        if response.status_code >= 400:
            raise EbayApiError(f"eBay listing revision failed ({response.status_code})")
        parse_revise_fixed_price_item(response.text)

    async def revise_price(self, item_id: str, *, price: Decimal) -> None:
        """Change only the price of one single-item listing; title, copy,
        photos and quantity stay as they are live."""
        clean_item_id = item_id.strip()
        if not clean_item_id or not clean_item_id.isdigit():
            raise ValueError("eBay item ID must contain digits only")
        try:
            clean_price = Decimal(str(price)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("eBay price must be a valid amount") from exc
        if clean_price < Decimal("1.00"):
            raise ValueError("eBay fixed-price listings must be at least AUD $1.00")
        body = (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<ReviseFixedPriceItemRequest xmlns=\"urn:ebay:apis:eBLBaseComponents\">"
            "<Item>"
            f"<ItemID>{escape(clean_item_id)}</ItemID>"
            f"<StartPrice currencyID=\"AUD\">{clean_price:.2f}</StartPrice>"
            "</Item></ReviseFixedPriceItemRequest>"
        )
        response = await self.client.post(self._url, content=body, headers=self._headers())
        if response.status_code >= 400:
            raise EbayApiError(f"eBay listing revision failed ({response.status_code})")
        parse_revise_fixed_price_item(response.text)

    async def delete_variations(self, item_id: str, *, skus: list[str]) -> None:
        """Delete exact SKU-addressed variations from one live listing.

        The caller must resolve and confirm the parent item and every SKU
        before invoking this method.  eBay requires all variation changes for
        one listing to be sent together, so this intentionally accepts one
        item ID and a finite, explicit list rather than a query or pattern.
        """
        clean_item_id = item_id.strip()
        clean_skus = [sku.strip() for sku in skus]
        if not clean_item_id or not clean_item_id.isdigit():
            raise ValueError("eBay item ID must contain digits only")
        if not clean_skus or any(not sku for sku in clean_skus):
            raise ValueError("at least one non-empty variation SKU is required")
        if len(clean_skus) != len(set(clean_skus)):
            raise ValueError("variation SKUs must be unique")
        variations = "".join(
            "<Variation>"
            f"<SKU>{escape(sku)}</SKU>"
            "<Delete>true</Delete>"
            "</Variation>"
            for sku in clean_skus
        )
        body = (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<ReviseFixedPriceItemRequest xmlns=\"urn:ebay:apis:eBLBaseComponents\">"
            "<Item>"
            f"<ItemID>{escape(clean_item_id)}</ItemID>"
            f"<Variations>{variations}</Variations>"
            "</Item></ReviseFixedPriceItemRequest>"
        )
        response = await self.client.post(self._url, content=body, headers=self._headers())
        if response.status_code >= 400:
            raise EbayApiError(f"eBay variation deletion failed ({response.status_code})")
        parse_revise_fixed_price_item(response.text)

    async def end_item(self, item_id: str, *, reason: str = "NotAvailable") -> None:
        """End exactly one known item; never accepts a SKU, query, or wildcard."""
        clean_item_id = item_id.strip()
        if not clean_item_id or not clean_item_id.isdigit():
            raise ValueError("eBay item ID must contain digits only")
        allowed_reasons = {"NotAvailable", "Incorrect", "LostOrBroken", "OtherListingError"}
        if reason not in allowed_reasons:
            raise ValueError("unsupported eBay ending reason")
        body = (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<EndFixedPriceItemRequest xmlns=\"urn:ebay:apis:eBLBaseComponents\">"
            f"<ItemID>{escape(clean_item_id)}</ItemID>"
            f"<EndingReason>{escape(reason)}</EndingReason>"
            "</EndFixedPriceItemRequest>"
        )
        headers = self._headers()
        headers["X-EBAY-API-CALL-NAME"] = "EndFixedPriceItem"
        response = await self.client.post(self._url, content=body, headers=headers)
        if response.status_code >= 400:
            raise EbayApiError(f"eBay listing end failed ({response.status_code})")
        parse_end_fixed_price_item(response.text)
