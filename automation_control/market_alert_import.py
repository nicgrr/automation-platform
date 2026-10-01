"""Parse marketplace alert emails without logging into or scraping a market.

Gumtree and Japanese proxy services already send listing alerts.  EzBay can
consume an exported/forwarded RFC 822 message, normalize supported product
links and deduplicate the message and each listing.  It never follows a link;
the marketplace remains the authority for availability and final price.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import imaplib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import select

from .market_watch_service import (
    associate_listing,
    exchange_rate_to_aud,
    market_source_for_url,
    upsert_listing,
    watch_matches_title,
)
from .models import MarketAlertImport, MarketWatchTerm


TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
PRICE_PATTERNS = (
    ("JPY", re.compile(r"(?:JPY\s*|¥\s*)(\d[\d,]*(?:\.\d{1,2})?)", re.I)),
    ("AUD", re.compile(r"(?:AUD\s*|AU\s*\$\s*|A\$\s*|\$\s*)(\d[\d,]*(?:\.\d{1,2})?)", re.I)),
)


class _LinkTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self.all_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        cleaned = " ".join(data.split())
        if cleaned:
            self.all_text.append(cleaned)
            if self._href is not None:
                self._text.append(cleaned)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


@dataclass(frozen=True)
class AlertImportResult:
    status: str
    imported: int
    updated: int
    ignored: int
    detail: str
    message_ids: tuple[bytes, ...] = ()


def imap_configured(settings) -> bool:
    return bool(
        settings.market_alert_imap_host
        and settings.market_alert_imap_username
        and settings.market_alert_imap_password
    )


def _imap_connection(settings):
    if settings.market_alert_imap_use_ssl:
        client = imaplib.IMAP4_SSL(settings.market_alert_imap_host, settings.market_alert_imap_port)
    else:
        client = imaplib.IMAP4(settings.market_alert_imap_host, settings.market_alert_imap_port)
        client.starttls()
    client.login(settings.market_alert_imap_username, settings.market_alert_imap_password)
    return client


def _fetch_unseen(settings, limit: int) -> list[tuple[bytes, bytes]]:
    client = _imap_connection(settings)
    try:
        status, _ = client.select(settings.market_alert_imap_mailbox, readonly=True)
        if status != "OK":
            raise RuntimeError("Could not open the configured alert mailbox.")
        status, payload = client.search(None, "UNSEEN")
        if status != "OK":
            raise RuntimeError("Could not search the configured alert mailbox.")
        message_ids = (payload[0].split() if payload and payload[0] else [])[-limit:]
        messages = []
        for message_id in message_ids:
            status, rows = client.fetch(message_id, "(BODY.PEEK[])")
            if status != "OK":
                continue
            raw = next((row[1] for row in rows if isinstance(row, tuple) and isinstance(row[1], bytes)), None)
            if raw:
                messages.append((message_id, raw))
        return messages
    finally:
        try:
            client.logout()
        except imaplib.IMAP4.error:
            pass


def _mark_seen(settings, message_ids: list[bytes]) -> None:
    if not message_ids:
        return
    client = _imap_connection(settings)
    try:
        client.select(settings.market_alert_imap_mailbox, readonly=False)
        for message_id in message_ids:
            client.store(message_id, "+FLAGS", "\\Seen")
    finally:
        try:
            client.logout()
        except imaplib.IMAP4.error:
            pass


def _canonical_url(url: str) -> str:
    parsed = urlparse(html.unescape(url).rstrip(".,);]"))
    kept = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
    ]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", urlencode(kept), ""))


def _price(text: str) -> tuple[Decimal, str] | None:
    for currency, pattern in PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            return Decimal(match.group(1).replace(",", "")), currency
    return None


def _clean_title(text: str, fallback: str) -> str:
    text = re.sub(r"(?:JPY\s*|AUD\s*|AU\s*\$\s*|A\$\s*|[$¥]\s*)\d[\d,]*(?:\.\d{1,2})?", "", text, flags=re.I)
    text = " ".join(html.unescape(text).split()).strip(" -–—|:")
    generic = {"view", "view item", "shop now", "buy now", "see listing", "open listing"}
    return (fallback if not text or text.lower() in generic else text)[:512]


def _message_parts(raw_message: bytes) -> tuple[str, str | None, datetime | None, list[tuple[str, str]]]:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    subject = str(message.get("subject") or "Marketplace alert").strip()
    message_id = str(message.get("message-id") or "").strip() or None
    received_at = None
    if message.get("date"):
        try:
            received_at = parsedate_to_datetime(str(message["date"]))
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=UTC)
        except (TypeError, ValueError, OverflowError):
            pass

    bodies: list[tuple[str, str]] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_type() not in {"text/html", "text/plain"}:
            continue
        try:
            body = part.get_content()
        except (KeyError, LookupError, UnicodeError):
            continue
        bodies.append((part.get_content_type(), str(body)))
    links: list[tuple[str, str]] = []
    for content_type, body in bodies:
        if content_type == "text/html":
            parser = _LinkTextParser()
            parser.feed(body)
            links.extend(parser.links)
            links.extend((url, "") for url in URL_RE.findall(" ".join(parser.all_text)))
        else:
            links.extend((url, "") for url in URL_RE.findall(body))
    return subject, message_id, received_at, links


async def import_alert_email(session, raw_message: bytes, *, default_country: str = "AU") -> AlertImportResult:
    if not raw_message.strip():
        raise ValueError("The email file is empty.")
    if len(raw_message) > 5 * 1024 * 1024:
        raise ValueError("Email files must be 5 MB or smaller.")

    subject, message_id, received_at, candidates = _message_parts(raw_message)
    fingerprint = hashlib.sha256((message_id.encode() if message_id else raw_message)).hexdigest()
    existing = session.scalar(select(MarketAlertImport).where(MarketAlertImport.fingerprint == fingerprint))
    if existing:
        return AlertImportResult("duplicate", 0, 0, 0, "This alert email was already imported.")

    supported: dict[str, tuple[str, str, str, Decimal | None, str | None]] = {}
    ignored = 0
    for raw_url, anchor_text in candidates:
        url = _canonical_url(raw_url)
        info = market_source_for_url(url)
        if not info:
            ignored += 1
            continue
        source, fixed_country = info
        country = fixed_country or default_country
        title = _clean_title(anchor_text, subject)
        amount = _price(anchor_text)
        supported[url] = (source, country, title, amount[0] if amount else None, amount[1] if amount else None)

    watches = session.scalars(select(MarketWatchTerm).where(MarketWatchTerm.active.is_(True))).all()
    rates: dict[str, Decimal] = {"AUD": Decimal("1")}
    imported = updated = 0
    for url, (source, country, title, amount, currency) in supported.items():
        if country not in {"AU", "JP"}:
            ignored += 1
            continue
        currency = currency or ("JPY" if country == "JP" else "AUD")
        if currency not in rates:
            rates[currency] = await exchange_rate_to_aud(currency)
        raw = {
            "entry_method": "alert_email",
            "email_subject": subject,
            "price_available": amount is not None,
        }
        listing, created = upsert_listing(
            session,
            source=source,
            external_id=hashlib.sha256(url.encode()).hexdigest(),
            title=title,
            url=url,
            country=country,
            price=amount or Decimal("0"),
            shipping=Decimal("0"),
            currency=currency,
            exchange_rate=rates[currency],
            raw=raw,
        )
        for watch in watches:
            if country in (watch.countries or []) and watch_matches_title(title, watch):
                associate_listing(session, listing, watch, matched_by="alert_email")
        imported += int(created)
        updated += int(not created)

    status = "imported" if supported else "empty"
    detail = (
        f"{imported} new and {updated} existing listing(s) read from {len(supported)} supported link(s)."
        if supported else
        "No Gumtree, Buyee, ZenMarket, FROM JAPAN, Depop or Facebook listing links were found."
    )
    session.add(MarketAlertImport(
        fingerprint=fingerprint,
        source="email",
        subject=subject,
        imported_count=imported + updated,
        status=status,
        detail=detail,
        received_at=received_at,
    ))
    session.flush()
    return AlertImportResult(status, imported, updated, ignored, detail)


async def import_unseen_alerts(session, settings, *, limit: int = 25) -> AlertImportResult:
    """Fetch and import unseen messages from the dedicated alert mailbox."""
    if not imap_configured(settings):
        raise ValueError("The market-alert IMAP mailbox is not configured.")
    messages = await asyncio.to_thread(_fetch_unseen, settings, min(max(limit, 1), 100))
    imported = updated = ignored = 0
    completed_ids: list[bytes] = []
    for message_id, raw in messages:
        result = await import_alert_email(session, raw)
        imported += result.imported
        updated += result.updated
        ignored += result.ignored
        completed_ids.append(message_id)
    detail = (
        f"Checked {len(messages)} unread alert email(s): {imported} new and {updated} existing listing(s)."
    )
    return AlertImportResult("imported", imported, updated, ignored, detail, tuple(completed_ids))


async def mark_imported_alerts_seen(settings, result: AlertImportResult) -> None:
    """Mark mail read only after the caller has committed imported rows."""
    await asyncio.to_thread(_mark_seen, settings, list(result.message_ids))
