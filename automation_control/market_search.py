"""eBay market search -- Module 21's "scan the market for deals" feature,
built in place of scraping Facebook Marketplace. Facebook has no public
API for this, scraping Marketplace violates its Terms of Service, and
doing it for real would mean this app holding the operator's personal
Facebook login session -- a real account-ban/security risk with no safe
mitigation. eBay's Browse API is exactly the legitimate equivalent: a
public, read-only search over what's actually listed, no login session of
the operator's own needed (it runs on an application access token, not a
user token), and it was already half-built tonight as
`EbaySandboxReadAdapter.search_market` / `/api/ebay/market-search`.

This is the human-facing page on top of that JSON endpoint: search a term,
see what eBay has listed for it (sandbox's fake test catalog until
production eBay credentials are configured -- see ebay_oauth.py's
`configured()`), and see it next to this app's own last-known market price
for the same card when one exists, so "is this actually a deal" has a
second number to compare against, not just a raw listing.

Three ways in, all of which end up at the same plain text query + results
view: typing a term, pasting a listing/product link (its page title is
scraped server-side and used as the query), or uploading a screenshot
(run through the same Google Lens actor collectibles.py already uses for
reverse image search -- its hits are shown as pickable titles rather than
auto-searched, since a Lens guess is exactly the kind of thing a human
should confirm before it's treated as a market query).
"""

import re
import tempfile
from decimal import Decimal
from html import escape
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters.ebay import EbayApiError, EbaySandboxReadAdapter, normalize_market
from .auth import require_dashboard_user
from .database import get_session
from .ebay_oauth import OAuthError, configured, ebay_services
from .inventory_review import _aud, _latest_prices
from .market_watch_service import COUNTRY_LABELS, allowed_country_codes
from .models import CatalogCard
from .reverse_image_search import ReverseImageSearchError, search_image
from .ui import brand_header, page

router = APIRouter(tags=["market-search"])

# Search on the Australian eBay marketplace, restricting the seller/item
# location with Browse API's official itemLocationCountry filter. There is
# no supported EBAY_JP marketplace in the modern Buy API, so Japan is a
# location, not a marketplace-header value.
DEFAULT_COUNTRY = "AU"

TITLE_FETCH_TIMEOUT_SECONDS = 15.0
_OG_TITLE_RE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE)
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


async def _title_from_url(url: str) -> str | None:
    """Best-effort page title for a pasted listing/product link, used as
    the search query in its place. og:title first -- product pages set it
    to the item name specifically, while <title> often has the site name
    tacked on too (e.g. "... | eBay")."""
    try:
        async with httpx.AsyncClient(timeout=TITLE_FETCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    html = response.text
    match = _OG_TITLE_RE.search(html) or _TITLE_TAG_RE.search(html)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    # Strip a trailing "| Site name" / "- Site name" suffix a bare <title>
    # tag usually carries (og:title rarely does).
    title = re.split(r"\s*[|–-]\s*eBay\b.*$", title, flags=re.IGNORECASE)[0].strip()
    return title or None


def _country_options(selected: str, allowed: tuple[str, ...]) -> str:
    return "".join(
        f"<option value='{code}'{' selected' if code == selected else ''}>{escape(COUNTRY_LABELS.get(code, code))}</option>"
        for code in allowed
    )


def _own_catalog_matches(session: Session, query: str, limit: int = 5) -> list[tuple[CatalogCard, float | None]]:
    cards = session.scalars(select(CatalogCard).where(CatalogCard.name.ilike(f"%{query}%")).limit(limit)).all()
    prices = _latest_prices(session, {c.id for c in cards})
    results = []
    for card in cards:
        price = next((p for (card_id, _), p in prices.items() if card_id == card.id), None)
        results.append((card, _aud(price)))
    return results


async def _ebay_results_panel(session: Session, settings, query: str, country: str) -> str:
    if not configured(settings):
        return (
            f"<div class='panel'><p>eBay {escape(settings.ebay_env.title())} OAuth isn't configured, so there's "
            "nothing to search against yet. See ARCHITECTURE.md for what needs to be set in <code>.env.local</code>.</p></div>"
        )
    try:
        _, oauth = ebay_services(settings)
        token_payload = await oauth.application_token()
        payload = await EbaySandboxReadAdapter(token_payload["access_token"], environment=settings.ebay_env).search_market(
            query, marketplace="EBAY_AU", item_location_country=country,
        )
        items = [
            item for item in normalize_market(payload)
            if (item.get("marketplace") or "").upper() == country
        ]
    except (OAuthError, EbayApiError, KeyError):
        return "<div class='panel'><p>eBay search failed -- try again shortly.</p></div>"

    own_matches = _own_catalog_matches(session, query)
    own_html = "".join(
        f"<div class='msearch-own-row'><span>{escape(card.name)}</span>"
        f"<span>{f'${price:,.2f}' if price is not None else 'no price on file'}</span></div>"
        for card, price in own_matches
    ) or "<p class='muted'>Nothing matching in your own catalogue.</p>"
    own_panel = f"<div class='panel'><h2>Your catalogue</h2>{own_html}</div>"

    prices = [Decimal(str(i["price"])) for i in items if i.get("price")]
    summary = ""
    if prices:
        summary = f"<p class='subtitle'>{len(items)} result(s) &middot; avg ${sum(prices) / len(prices):,.2f} &middot; low ${min(prices):,.2f} &middot; high ${max(prices):,.2f}</p>"

    row_html = []
    for i in items:
        price_text = f"${Decimal(str(i['price'])):,.2f}" if i.get("price") else "—"
        row_html.append(
            "<tr>"
            f"<td>{escape(i['title'] or '—')}</td>"
            f"<td>{price_text} {escape(i.get('currency') or '')}</td>"
            f"<td class='muted'>{escape(i.get('marketplace') or '—')}</td>"
            "</tr>"
        )
    rows = "".join(row_html) or "<tr><td colspan=3>No results.</td></tr>"

    country_label = COUNTRY_LABELS.get(country, country)
    env_note = (
        f"<p class='muted'>Seller location: {escape(country_label)} only.</p>" if settings.ebay_env == "production"
        else "<p class='muted'>Sandbox mode -- these are eBay's fake test-catalogue items, not real listings.</p>"
    )

    return (
        own_panel
        + f"<div class='panel'><h2>eBay results</h2>{env_note}{summary}"
        + f"<div class='table-wrap'><table><thead><tr><th>Title</th><th>Price</th><th>Location</th></tr></thead>"
        + f"<tbody>{rows}</tbody></table></div></div>"
    )


def _lens_picker_panel(items: list[dict], country: str, error: str | None = None) -> str:
    if error:
        return f"<div class='panel'><p>Google Lens search failed: {escape(error)}</p></div>"
    if not items:
        return "<div class='panel'><p>Google Lens didn't return anything for that screenshot -- try a text search instead.</p></div>"
    rows = "".join(
        f"<a class='msearch-lens-pick' href='/market-search?q={quote(item.get('title') or '')}&country={quote(country)}'>"
        f"{escape(item.get('title') or 'untitled result')}</a>"
        for item in items if item.get("title")
    )
    return (
        "<div class='panel'><h2>What this looks like</h2>"
        "<p class='muted'>Pick the one that matches -- it becomes the search below.</p>"
        f"<div class='msearch-lens-picks'>{rows}</div></div>"
    )


def _page_shell(query: str, country: str, allowed: tuple[str, ...], extra_html: str) -> str:
    body = (
        brand_header("Market search")
        + "<form method='get' action='/market-search' class='msearch-form'>"
        + f"<input name='q' value='{escape(query)}' placeholder='Charizard ex 199' autofocus>"
        + f"<select name='country' aria-label='Seller location'>{_country_options(country, allowed)}</select>"
        + "<button type='submit'>Search</button>"
        + "</form>"
        + "<form method='get' action='/market-search' class='msearch-form msearch-secondary'>"
        + "<input name='url' type='url' placeholder='Paste a listing or product link instead'>"
        + f"<input type='hidden' name='country' value='{escape(country)}'>"
        + "<button type='submit'>Look up link</button>"
        + "</form>"
        + "<form method='post' action='/market-search/from-photo' enctype='multipart/form-data' class='msearch-form msearch-secondary' onsubmit='return marketSearchPhotoSubmitting(this)'>"
        + "<input name='photo' type='file' accept='image/*' required>"
        + f"<input type='hidden' name='country' value='{escape(country)}'>"
        + "<button type='submit'>Search from a screenshot</button>"
        + "</form>"
        + extra_html
        + _STYLE
        + _SCRIPT
    )
    return page("EzBay — Market search", body)


@router.get("/market-search", response_class=HTMLResponse)
async def market_search_page(
    request: Request, q: str = Query(""), url: str = Query(""), country: str = Query(DEFAULT_COUNTRY),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    settings = request.app.state.settings
    allowed = allowed_country_codes(settings.market_allowed_countries)
    country = country.upper()
    if country not in allowed:
        country = allowed[0]

    query = q.strip()
    link = url.strip()
    extra_html = ""

    if not query and link:
        title = await _title_from_url(link)
        if title:
            query = title
        else:
            extra_html = (
                "<div class='panel'><p>Couldn't read a title from that link -- paste the item name directly instead.</p></div>"
            )

    if query:
        extra_html = await _ebay_results_panel(session, settings, query, country)
    elif not extra_html:
        extra_html = (
            "<div class='panel'><p>Search a card, figure, or set name to see what's listed on eBay right now -- "
            "or paste a link, or upload a screenshot, using the options below.</p></div>"
        )

    return HTMLResponse(_page_shell(query, country, allowed, extra_html))


@router.post("/market-search/from-photo", response_class=HTMLResponse)
async def market_search_from_photo(
    request: Request, photo: UploadFile = File(...), country: str = Form(DEFAULT_COUNTRY),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    settings = request.app.state.settings
    allowed = allowed_country_codes(settings.market_allowed_countries)
    country = country.upper()
    if country not in allowed:
        country = allowed[0]

    if not settings.apify_token:
        extra_html = (
            "<div class='panel'><p>Screenshot search isn't configured (APIFY_TOKEN missing), so there's nothing "
            "to run the image against yet.</p></div>"
        )
        return HTMLResponse(_page_shell("", country, allowed, extra_html))

    data = await photo.read()
    suffix = Path(photo.filename or "screenshot.jpg").suffix or ".jpg"
    with tempfile.TemporaryDirectory() as tmp_dir:
        photo_path = Path(tmp_dir) / f"screenshot{suffix}"
        photo_path.write_bytes(data)
        try:
            items = await search_image(photo_path, settings.apify_token, settings.apify_google_lens_actor_id)
        except ReverseImageSearchError as exc:
            extra_html = _lens_picker_panel([], country, error=str(exc))
        else:
            extra_html = _lens_picker_panel(items, country)

    return HTMLResponse(_page_shell("", country, allowed, extra_html))


_STYLE = """<style>
.msearch-form{display:flex;gap:10px;margin:0 0 14px;max-width:480px;align-items:center;flex-wrap:wrap}
.msearch-form input{flex:1;min-width:0;background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.msearch-form select{background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.msearch-secondary{margin-bottom:22px;opacity:.9}
.msearch-secondary input[type=file]{flex:1;min-width:0;font-size:13px}
.msearch-own-row{display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid var(--panel-border);font-size:13.5px}
.msearch-own-row:last-child{border-bottom:none}
.msearch-own-row span:first-child{overflow-wrap:anywhere}
.msearch-lens-picks{display:flex;flex-direction:column;gap:8px;margin-top:10px;max-width:100%}
.msearch-lens-pick{display:block;padding:10px 12px;border:1px solid var(--panel-border);border-radius:8px;
  color:var(--text);text-decoration:none;background:var(--surface-sunken);overflow-wrap:anywhere;word-break:break-word}
.msearch-lens-pick:hover{border-color:var(--accent)}
.table-wrap table{table-layout:fixed;width:100%}
.table-wrap td,.table-wrap th{overflow-wrap:anywhere;word-break:break-word}
.table-wrap td:first-child,.table-wrap th:first-child{width:55%}
.muted{color:var(--text-dim);font-size:13px}
@media (max-width: 500px) {
  .msearch-form input, .msearch-form select{flex-basis:100%}
}
</style>"""

_SCRIPT = """<script>
function marketSearchPhotoSubmitting(form) {
  if (!form.photo.files.length) { return true; }
  var btn = form.querySelector('button');
  btn.disabled = true;
  btn.textContent = 'Searching (can take up to a minute)…';
  return true;
}
</script>"""
