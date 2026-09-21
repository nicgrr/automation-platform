"""Identify a collectible from a marketplace link -- "is this a good buy?"

Paste the URL of a Sonny Angel listing and this fetches the page, works
out which figure it is, matches it against the catalogue, and puts the
asking price next to what that figure actually goes for.

The verdict is the point. Knowing a listing is "Fruit Series secret" is
only half an answer; the useful half is that Fruit secrets ask around
$421 and this one wants $90. Sonny Angel pricing turns almost entirely on
one bit -- secret or not, a 7x difference -- so a listing that doesn't say
gets flagged rather than assumed.

Nothing here writes to the catalogue. It reads a page and reports.
"""

from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .card_recognition import RecognitionError, extract_collectible_listing
from .config import get_settings
from .database import get_session
from .link_fetch import FetchFailed, UnsafeUrl, fetch_text
from .models import CardPrice, CatalogItem, CollectibleProduct
from .ui import brand_header, page, pill

router = APIRouter(tags=["collectibles"])

EBAY_MEDIAN = "ebay_active_median"
EBAY_LOW = "ebay_active_low"

# How far under the market median a listing has to sit before it's called
# a deal. Postage and the risk of a wrong identification both eat into a
# thin margin, so "slightly cheaper" is not worth a drive.
DEAL_DISCOUNT = Decimal("0.75")


def _strip_html(raw: str) -> str:
    """Page text, minus the markup, script and style content.

    A regex rather than a parser dependency: the output is going to a
    language model that reads through noise perfectly well, so the bar is
    "drop the tags", not "produce a correct DOM".
    """
    import re

    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _match_catalogue(session: Session, extracted) -> tuple[CatalogItem, CollectibleProduct] | None:
    """Find the catalogue row for an extracted listing.

    Matched on (series, is_secret) rather than the figure's name: the
    catalogue is seeded per series, and "Fruit Series secret" is the row
    that carries a meaningful price whatever the individual figure is.
    """
    series = (extracted.series or "").strip()
    if not series:
        return None
    rows = session.execute(
        select(CatalogItem, CollectibleProduct)
        .join(CollectibleProduct, CollectibleProduct.catalog_item_id == CatalogItem.id)
        .where(CollectibleProduct.is_secret.is_(bool(extracted.is_secret)))
    ).all()
    needle = series.lower()
    for item, product in rows:
        candidate = (product.series or "").lower()
        if candidate and (candidate == needle or needle in candidate or candidate in needle):
            return item, product
    return None


def _latest(session: Session, item_id: str, source: str) -> Decimal | None:
    row = session.scalars(
        select(CardPrice)
        .where(CardPrice.card_id == item_id, CardPrice.source == source)
        .order_by(CardPrice.fetched_at.desc()).limit(1)
    ).first()
    return Decimal(str(row.price)) if row else None


def _money(value) -> str:
    return "&mdash;" if value is None else f"${Decimal(str(value)):,.2f}"


def _form(url: str = "", note: str = "") -> str:
    return (
        "<div class='panel'><h2>Paste a listing link</h2>"
        "<p class='muted'>An eBay, Marketplace or shop page for a Sonny Angel. "
        "The page is read and identified &mdash; nothing is bought, listed or saved.</p>"
        "<form method='post' action='/collectibles/identify' class='calc-form'>"
        f"<label>Listing URL<input name='url' type='url' value='{escape(url)}' "
        "placeholder='https://www.ebay.com.au/itm/...' required autofocus></label>"
        "<button type='submit'>Identify</button></form>"
        + (f"<p class='linknote'>{note}</p>" if note else "")
        + "</div>"
    )


@router.get("/collectibles/identify", response_class=HTMLResponse)
def identify_form(user: str = Depends(require_dashboard_user)) -> HTMLResponse:
    body = (
        brand_header("Identify from a link")
        + _form() + _LINK_STYLE
    )
    return HTMLResponse(page("EzBay — Identify from a link", body))


@router.post("/collectibles/identify", response_class=HTMLResponse)
def identify_submit(
    url: str = Form(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    settings = get_settings()

    def fail(note: str) -> HTMLResponse:
        body = brand_header("Identify from a link") + _form(url, note) + _LINK_STYLE
        return HTMLResponse(page("EzBay — Identify from a link", body))

    try:
        html = fetch_text(url)
    except UnsafeUrl as exc:
        return fail(str(exc))
    except FetchFailed as exc:
        return fail(str(exc))

    if not settings.anthropic_api_key:
        return fail("No Anthropic API key is configured, so the page can't be identified.")

    try:
        found = extract_collectible_listing(_strip_html(html), api_key=settings.anthropic_api_key)
    except RecognitionError:
        return fail("The page was fetched but couldn't be read as a listing.")
    except Exception:
        return fail("Identification failed. The page may be a login wall or a captcha.")

    if not found:
        return fail("That page doesn't look like a listing for a collectible figure.")

    cards = "".join(_result_card(session, item) for item in found)
    body = (
        brand_header("Identify from a link")
        + f"<p class='subtitle'>{len(found)} figure(s) found in "
          f"<a href='{escape(url)}' target='_blank' rel='noopener noreferrer'>that listing</a>.</p>"
        + cards + _form() + _LINK_STYLE
    )
    return HTMLResponse(page("EzBay — Identify from a link", body))


def _result_card(session: Session, extracted) -> str:
    unread = set(extracted.unreadable_fields or [])
    matched = _match_catalogue(session, extracted)

    try:
        asking = Decimal(str(extracted.asking_price or 0))
    except (InvalidOperation, ValueError):
        asking = Decimal("0")
    quantity = max(1, int(extracted.quantity or 1))
    per_unit = asking / quantity if asking and quantity else asking

    median = low = None
    catalogue_name = "not in the catalogue"
    if matched:
        item, _product = matched
        catalogue_name = item.name
        median = _latest(session, item.id, EBAY_MEDIAN)
        low = _latest(session, item.id, EBAY_LOW)

    # The verdict, and the honesty about when there isn't one.
    if "is_secret" in unread:
        verdict = pill("Secret unclear", "warn")
        reason = ("The listing doesn't say whether this is the secret. That's the "
                  "difference between roughly $40 and roughly $400 — worth asking before buying.")
    elif asking <= 0:
        verdict = pill("No price", "neutral")
        reason = "No asking price on the page, so there's nothing to compare."
    elif median is None:
        verdict = pill("No comparison", "neutral")
        reason = f"No eBay price recorded for {escape(catalogue_name)} yet, so this can't be judged."
    elif per_unit <= median * DEAL_DISCOUNT:
        verdict = pill("Worth a look", "ok")
        reason = (f"{_money(per_unit)} each against a {_money(median)} median — "
                  f"about {(1 - per_unit / median) * 100:.0f}% under.")
    elif per_unit <= median:
        verdict = pill("Around market", "warn")
        reason = f"{_money(per_unit)} each against a {_money(median)} median. Not a bargain."
    else:
        verdict = pill("Over market", "bad")
        reason = (f"{_money(per_unit)} each is above the {_money(median)} median. "
                  "Asking prices already skew high, so this is dear.")

    facts = [
        ("Brand", escape(extracted.brand or "—")),
        ("Series", escape(extracted.series or "—")),
        ("Character", escape(extracted.character or "—")),
        ("Secret", "unclear" if "is_secret" in unread else ("yes" if extracted.is_secret else "no")),
        ("Sealed box", "yes" if extracted.is_sealed else "no"),
        ("Quantity", str(quantity)),
        ("Asking", f"{_money(asking)} {escape(extracted.currency or '')}".strip()),
        ("Per figure", _money(per_unit) if quantity > 1 else "—"),
        ("Catalogue match", escape(catalogue_name)),
        ("eBay low / median", f"{_money(low)} / {_money(median)}"),
    ]
    rows = "".join(f"<li><span>{label}</span><span>{value}</span></li>" for label, value in facts)

    return (
        "<div class='panel linkresult'>"
        f"<div class='linkhead'><h2>{escape(extracted.character or extracted.series or 'Unidentified figure')}</h2>{verdict}</div>"
        f"<p class='linkreason'>{reason}</p>"
        f"<ul class='spec-list'>{rows}</ul>"
        "<p class='muted' style='font-size:12.5px;margin:12px 0 0'>eBay figures are active-listing "
        "asking prices, not completed sales.</p>"
        "</div>"
    )


_LINK_STYLE = """<style>
.linkhead{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.linkhead h2{margin:0}
.linkreason{margin:0 0 14px;color:var(--text-dim)}
.linknote{margin:12px 0 0;padding:11px 13px;border-radius:8px;
  background:var(--warning-bg);color:var(--warning);font-size:13.5px}
.spec-list{list-style:none;padding:0;margin:0;display:grid;gap:1px;
  background:var(--panel-border);border:1px solid var(--panel-border);border-radius:9px;overflow:hidden}
.spec-list li{display:flex;justify-content:space-between;gap:14px;
  background:var(--panel);padding:9px 13px;font-size:13.5px}
.spec-list li span:first-child{color:var(--text-dim)}
.spec-list li span:last-child{font-weight:600;text-align:right}
</style>"""
