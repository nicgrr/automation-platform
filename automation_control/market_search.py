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
"""

from decimal import Decimal
from html import escape

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters.ebay import EbayApiError, EbaySandboxReadAdapter, normalize_market
from .auth import require_dashboard_user
from .database import get_session
from .ebay_oauth import OAuthError, configured, ebay_services
from .inventory_review import _aud, _latest_prices
from .models import CatalogCard
from .ui import brand_header, page

router = APIRouter(tags=["market-search"])


def _own_catalog_matches(session: Session, query: str, limit: int = 5) -> list[tuple[CatalogCard, float | None]]:
    cards = session.scalars(select(CatalogCard).where(CatalogCard.name.ilike(f"%{query}%")).limit(limit)).all()
    prices = _latest_prices(session, {c.id for c in cards})
    results = []
    for card in cards:
        price = next((p for (card_id, _), p in prices.items() if card_id == card.id), None)
        results.append((card, _aud(price)))
    return results


@router.get("/market-search", response_class=HTMLResponse)
async def market_search_page(
    request: Request, q: str = Query(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    settings = request.app.state.settings
    query = q.strip()
    results_html = ""

    if query:
        if not configured(settings):
            results_html = (
                f"<div class='panel'><p>eBay {escape(settings.ebay_env.title())} OAuth isn't configured, so there's "
                "nothing to search against yet. See ARCHITECTURE.md for what needs to be set in <code>.env.local</code>.</p></div>"
            )
        else:
            try:
                _, oauth = ebay_services(settings)
                token_payload = await oauth.application_token()
                payload = await EbaySandboxReadAdapter(token_payload["access_token"], environment=settings.ebay_env).search_market(query)
                items = normalize_market(payload)
            except (OAuthError, EbayApiError, KeyError):
                results_html = "<div class='panel'><p>eBay search failed -- try again shortly.</p></div>"
            else:
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

                env_note = (
                    "" if settings.ebay_env == "production"
                    else "<p class='muted'>Sandbox mode -- these are eBay's fake test-catalogue items, not real listings.</p>"
                )

                results_html = (
                    own_panel
                    + f"<div class='panel'><h2>eBay results</h2>{env_note}{summary}"
                    + f"<div class='table-wrap'><table><thead><tr><th>Title</th><th>Price</th><th>Marketplace</th></tr></thead>"
                    + f"<tbody>{rows}</tbody></table></div></div>"
                )
    else:
        results_html = "<div class='panel'><p>Search a card or set name to see what's listed on eBay right now, next to your own last-known price.</p></div>"

    body = (
        brand_header("Market search")
        + "<p class='subtitle'>Module 21 -- what's this actually worth, right now.</p>"
        + "<form method='get' action='/market-search' class='msearch-form'>"
        + f"<input name='q' value='{escape(query)}' placeholder='Charizard ex 199' autofocus>"
        + "<button type='submit'>Search</button>"
        + "</form>"
        + results_html
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Market search", body))


_STYLE = """<style>
.msearch-form{display:flex;gap:10px;margin:0 0 22px;max-width:480px}
.msearch-form input{flex:1;background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.msearch-own-row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--panel-border);font-size:13.5px}
.msearch-own-row:last-child{border-bottom:none}
.muted{color:var(--text-dim);font-size:13px}
</style>"""
