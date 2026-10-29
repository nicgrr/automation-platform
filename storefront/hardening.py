"""Security headers, branded error pages, and basic SEO plumbing --
the unglamorous things a page needs before real strangers see it.

None of this existed before: a 404 came back as bare unstyled JSON
(`{"detail":"item not found"}`), there were no security headers at all,
and a search engine had nothing but a bare <title> to go on.
"""

from html import escape

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from automation_control.config import get_settings
from automation_control.database import get_session
from automation_control.models import BulkLot, CatalogItem, ShopListing, ShopListingStatus
from automation_control.storefront_readiness import storefront_legal_issues

from .ui import page

# The CSP is compatible with what this app's plain-string HTML actually
# does -- inline <style> attributes and small inline <script> blocks are
# used throughout ui.py, so 'unsafe-inline' is a deliberate, documented
# trade-off here rather than an oversight. A nonce-based CSP would be
# stricter but means threading a per-request nonce through every page
# function; not worth it yet for a single-operator shop with no
# user-generated content to inject through.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    # static.cloudflareinsights.com / cloudflareinsights.com: Cloudflare Web
    # Analytics, which Cloudflare injects on the public domain -- cookieless
    # visitor counts, consistent with the privacy page's "no tracking cookies".
    "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://static.cloudflareinsights.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: https://images.pokemontcg.io https://images.scrydex.com https://i.ebayimg.com",
    "connect-src 'self' https://cloudflareinsights.com",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(self)",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}


def install_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response


def install_canonical_host(app: FastAPI) -> None:
    """www.nlvault.com.au -> nlvault.com.au, so the shop has one address
    for search engines and links rather than two copies of every page."""
    @app.middleware("http")
    async def strip_www(request: Request, call_next):
        host = request.headers.get("host", "")
        if host.lower().startswith("www."):
            target = request.url.replace(netloc=host[4:])
            return RedirectResponse(str(target), status_code=301)
        return await call_next(request)


def _branded_error(status_code: int, headline: str, detail: str) -> HTMLResponse:
    body = (
        "<div class='panel' style='max-width:520px;margin:60px auto;text-align:center'>"
        f"<h1>{escape(headline)}</h1>"
        f"<p class='muted'>{escape(detail)}</p>"
        "<p style='margin-top:20px'><a class='btn ghost' href='/'>Back to the vault</a></p>"
        "</div>"
    )
    return HTMLResponse(page(f"{headline} — NL Vault", body, robots="noindex,follow"), status_code=status_code)


def install_error_pages(app: FastAPI) -> None:
    """Every error a shopper can hit gets the shop's own chrome, not a
    bare `{"detail": "..."}` JSON blob -- the previous default for
    anything from a bad URL to a 500."""

    # Registered on Starlette's base HTTPException rather than
    # fastapi.HTTPException: FastAPI's is a SUBCLASS, and a routing-level
    # "no route matched" 404 is raised as the Starlette base -- a handler
    # registered on the subclass never sees it (isinstance only works one
    # way), so it fell straight through to Starlette's bare JSON default.
    @app.exception_handler(StarletteHTTPException)
    async def branded_http_exception(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
        if exc.status_code == 404:
            return _branded_error(404, "Page not found", "That page doesn't exist, or has moved.")
        return _branded_error(exc.status_code, "Something went wrong",
                              str(exc.detail) or "Please try again in a moment.")

    @app.exception_handler(Exception)
    async def branded_server_error(request: Request, exc: Exception) -> HTMLResponse:
        return _branded_error(500, "Something went wrong",
                              "That's on us, not you — please try again shortly.")


def install_seo_routes(app: FastAPI) -> None:
    @app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
    def robots(request: Request) -> str:
        # The Sitemap line must be an absolute URL -- crawlers ignore a
        # bare path.
        base = str(request.base_url).rstrip("/")
        return (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /cart\n"
            "Disallow: /checkout\n"
            f"Sitemap: {base}/sitemap.xml\n"
        )

    @app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
    def llms_txt(request: Request) -> str:
        # Same idea as robots.txt, but written for an LLM deciding whether
        # (and how) to describe this site, rather than a crawler deciding
        # whether to fetch it -- prose the model can quote, not directives.
        base = str(request.base_url).rstrip("/")
        return (
            "# NL Vault\n\n"
            "> Trading card singles, sealed product, sorted bulk lots and collectible "
            "figures, hand-photographed and shipped tracked from Australia.\n\n"
            "NL Vault is a small, single-operator online shop -- not a marketplace or "
            "reseller platform. Every listing is real stock the operator personally holds; "
            "ungraded singles are photographed as the actual card being sold, not a "
            "publisher's reference image.\n\n"
            "## Key pages\n\n"
            f"- [Browse everything]({base}/browse)\n"
            f"- [Bulk Master (sorted bulk lots by set)]({base}/bulk)\n"
            f"- [Shipping & returns]({base}/shipping)\n"
            f"- [Contact]({base}/contact)\n"
        )

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap(request: Request, session: Session = Depends(get_session)) -> Response:
        # Absolute URLs built from whatever host actually served the
        # request rather than a hardcoded domain -- there isn't a public
        # one yet, and this stays correct once there is.
        from . import bulk_ebay, departments

        base = str(request.base_url).rstrip("/")
        # /privacy and /terms are left out while they're noindexed (still
        # carrying [PLACEHOLDER] legal facts or not yet approved) -- a
        # noindexed page has no business being in the sitemap.
        paths = ["/", "/browse", "/bulk", "/shipping", "/contact"]
        if not storefront_legal_issues(get_settings()):
            paths += ["/privacy", "/terms"]
        paths += [f"/shop/{d.slug}" for d, _count in departments.in_stock(session)
                  if not departments.is_coming_soon(d)]
        if bulk_ebay.bulk_listings(session):
            paths.append(f"/shop/{bulk_ebay.SLUG}")
        paths += [
            f"/item/{listing_id}"
            for listing_id in session.scalars(
                select(ShopListing.id)
                .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id, isouter=True)
                .where(
                    ShopListing.status == ShopListingStatus.ACTIVE, ShopListing.quantity > 0,
                    departments.visible_listing_filter(),
                )
            )
        ]
        paths += [
            f"/bulk/{lot_id}"
            for lot_id in session.scalars(
                select(BulkLot.id).where(
                    BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0
                )
            )
        ]
        body = (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
            + "".join(f"<url><loc>{escape(base + p)}</loc></url>" for p in paths)
            + "</urlset>"
        )
        return Response(content=body, media_type="application/xml")
