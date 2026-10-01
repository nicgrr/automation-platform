"""Marketplace-neutral normalization, landed-cost maths, and alerts."""

import re
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import select

from .models import MarketAlertDelivery, MarketListing, MarketListingMatch, MarketWatchTerm


DEFAULT_ALLOWED_COUNTRIES = ("AU", "JP")
COUNTRY_LABELS = {"AU": "Australia", "JP": "Japan"}
SOURCE_LABELS = {
    "ebay": "eBay",
    "facebook": "Facebook Marketplace",
    "depop": "Depop",
    "gumtree": "Gumtree",
    "buyee": "Buyee",
    "zenmarket": "ZenMarket",
    "fromjapan": "FROM JAPAN",
    "public_web": "Public web",
}
MARKET_SOURCE_DOMAINS = {
    "facebook": "facebook.com",
    "depop": "depop.com",
    "gumtree": "gumtree.com.au",
    "buyee": "buyee.jp",
    "zenmarket": "zenmarket.jp",
    "fromjapan": "fromjapan.co.jp",
}
MARKET_SOURCE_FIXED_COUNTRIES = {
    "gumtree": "AU",
    "buyee": "JP",
    "zenmarket": "JP",
    "fromjapan": "JP",
}


def allowed_country_codes(configured: str | None = None) -> tuple[str, ...]:
    """Validated location scope shared by collectors, pages and alerts."""
    raw = configured or ",".join(DEFAULT_ALLOWED_COUNTRIES)
    codes = tuple(dict.fromkeys(part.strip().upper() for part in raw.split(",") if part.strip()))
    valid = tuple(code for code in codes if len(code) == 2 and code.isalpha())
    return valid or DEFAULT_ALLOWED_COUNTRIES


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source.replace("_", " ").title())


def valid_market_listing_url(source: str, url: str) -> bool:
    """Validate both the marketplace domain and a product/listing path."""
    parsed = urlparse(url)
    expected = MARKET_SOURCE_DOMAINS.get(source)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not expected:
        return False
    host = parsed.hostname.lower()
    if not (host == expected or host.endswith("." + expected)):
        return False
    path = parsed.path.lower()
    if source == "facebook":
        return "/marketplace/item/" in path
    if source == "depop":
        return "/products/" in path
    if source == "gumtree":
        return "/s-ad/" in path
    if source == "buyee":
        return any(marker in path for marker in ("/item/", "/auction/", "/mercari/"))
    if source == "zenmarket":
        query = {key.lower(): value for key, value in parse_qs(parsed.query).items()}
        return "itemcode" in query or any(marker in path for marker in ("product", "auction", "mercari", "/item/"))
    if source == "fromjapan":
        return any(marker in path for marker in ("/auction/", "/item/", "/mercari/", "/product/"))
    return False


def market_source_for_url(url: str) -> tuple[str, str | None] | None:
    for source in MARKET_SOURCE_DOMAINS:
        if valid_market_listing_url(source, url):
            return source, MARKET_SOURCE_FIXED_COUNTRIES.get(source)
    return None


def listing_has_price(listing: MarketListing) -> bool:
    """Discovery feeds can supply a useful URL before they expose a price."""
    return bool((listing.raw or {}).get("price_available", True)) and listing.price > 0


def watch_matches_title(title: str, watch: MarketWatchTerm) -> bool:
    """Conservative all-token match used for manual and email discoveries."""
    haystack = title.casefold()
    words = [word for word in re.findall(r"[\w'-]+", watch.query.casefold()) if len(word) > 1]
    return bool(words) and all(word in haystack for word in words)


async def exchange_rate_to_aud(currency: str) -> Decimal:
    code = currency.strip().upper()
    if code == "AUD":
        return Decimal("1")
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get("https://api.frankfurter.dev/v1/latest", params={"from": code, "to": "AUD"})
        response.raise_for_status()
        return Decimal(str(response.json()["rates"]["AUD"]))


KIND_PATTERNS = (
    ("full_case", re.compile(r"\b(full (?:box|case)|display box|case of \d+|(?:6|12|24)\s*x|(?:6|12|24) boxes?)\b", re.I)),
    ("blind_box", re.compile(r"\b(blind box|sealed box|unopened box|random (?:1\s*)?(?:pc|pcs|piece|box))\b", re.I)),
    ("bundle", re.compile(r"\b(lot|bundle|set of|pair|\d+\s*x)\b", re.I)),
    ("individual", re.compile(r"\b(figure|figurine|secret|robby|opened)\b", re.I)),
)
NON_FIGURE_PATTERN = re.compile(r"\b(stickers?|decal|phone case|clothes|shirt|keychain|display shelf|stand only)\b", re.I)


def classify_item_kind(title: str) -> str:
    for kind, pattern in KIND_PATTERNS:
        if pattern.search(title):
            return kind
    # A canonical figure name without packaging language is an individual.
    # Import lazily to keep this normalization module usable on its own.
    from . import vocabulary
    found = vocabulary.match(title, vocabulary.SONNY_ANGEL)
    if found and found.kind == vocabulary.CHARACTER:
        return "individual"
    if "sonny angel" in title.lower() and not NON_FIGURE_PATTERN.search(title):
        return "individual"
    return "unknown"


def landed_cost_aud(price, shipping, exchange_rate, tax_rate=Decimal("0")) -> tuple[Decimal, Decimal]:
    subtotal = (Decimal(str(price)) + Decimal(str(shipping or 0))) * Decimal(str(exchange_rate))
    tax = subtotal * Decimal(str(tax_rate))
    cents = Decimal("0.01")
    return tax.quantize(cents, rounding=ROUND_HALF_UP), (subtotal + tax).quantize(cents, rounding=ROUND_HALF_UP)


def upsert_listing(session, *, source: str, external_id: str, title: str, url: str,
                   price, currency: str, shipping=0, exchange_rate=1, tax_rate=0,
                   image_url=None, seller=None, condition=None, country=None,
                   catalog_item_id=None, raw=None) -> tuple[MarketListing, bool]:
    tax, landed = landed_cost_aud(price, shipping, exchange_rate, tax_rate)
    row = session.scalar(select(MarketListing).where(
        MarketListing.source == source, MarketListing.external_id == external_id,
    ))
    created = row is None
    if row is None:
        row = MarketListing(source=source, external_id=external_id, first_seen_at=datetime.now(UTC))
        session.add(row)
    row.title, row.url, row.price, row.currency = title, url, Decimal(str(price)), currency
    row.shipping, row.exchange_rate_aud = Decimal(str(shipping or 0)), Decimal(str(exchange_rate))
    row.estimated_tax_aud, row.landed_aud = tax, landed
    row.image_url, row.seller, row.condition, row.country = image_url, seller, condition, country
    row.catalog_item_id, row.item_kind, row.raw = catalog_item_id, classify_item_kind(title), raw or {}
    row.active, row.last_seen_at = True, datetime.now(UTC)
    session.flush()
    return row, created


def associate_listing(session, listing: MarketListing, watch: MarketWatchTerm,
                      *, matched_by: str = "query") -> MarketListingMatch:
    """Idempotently record every watch that led to a normalized listing."""
    match = session.get(MarketListingMatch, (watch.id, listing.id))
    if match is None:
        match = MarketListingMatch(
            watch_term_id=watch.id,
            market_listing_id=listing.id,
            matched_by=matched_by,
        )
        session.add(match)
    else:
        match.matched_by = matched_by
        match.matched_at = datetime.now(UTC)
    session.flush()
    return match


async def send_alerts(session, listing: MarketListing, settings) -> list[str]:
    """Send each configured channel once per listing; failures are recorded."""
    message = (f"Sonny Angel find: {listing.title}\n{source_label(listing.source)} · "
               f"{listing.currency} {listing.price} + {listing.shipping} shipping · "
               f"landed A${listing.landed_aud}\n{listing.url}")
    destinations = []
    if settings.telegram_bot_token and settings.telegram_chat_id:
        destinations.append(("telegram", f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                             {"chat_id": settings.telegram_chat_id, "text": message}))
    if settings.discord_webhook_url:
        destinations.append(("discord", settings.discord_webhook_url, {"content": message}))
    outcomes = []
    async with httpx.AsyncClient(timeout=15) as client:
        for channel, url, payload in destinations:
            if session.scalar(select(MarketAlertDelivery).where(
                MarketAlertDelivery.market_listing_id == listing.id,
                MarketAlertDelivery.channel == channel,
            )):
                continue
            status, detail = "sent", None
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                status, detail = "failed", str(exc)[:500]
            session.add(MarketAlertDelivery(market_listing_id=listing.id, channel=channel,
                                            status=status, detail=detail))
            outcomes.append(f"{channel}:{status}")
    return outcomes
