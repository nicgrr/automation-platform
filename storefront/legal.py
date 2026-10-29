"""Privacy policy and terms of sale -- Module 27.

Checkout collects a name, email and postal address, and there was
nowhere on the shop explaining what happens to that. Empty business facts
render as unmistakable bracketed placeholders and are supplied through
the service environment only after the owner confirms them. This is drafted
content for NL Vault to review and complete, not delivered legal advice; an actual
solicitor should look at both pages before they go live to real
customers, particularly the returns/refund wording against Australian
Consumer Law.
"""

from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from automation_control.database import get_session
from automation_control.config import get_settings
from automation_control.storefront_readiness import storefront_legal_issues

from .catalogue import is_catalogue
from .common import shell

router = APIRouter()

_DRAFT_NOTICE = (
    "<div class='notice'><strong>Draft for review.</strong> The bracketed details on this page "
    "are placeholders. Fill them in — and have this reviewed against Australian Consumer Law and "
    "the Privacy Act before the shop goes live.</div>"
)


def _seller(business_name: str, business_abn: str) -> str:
    # "NL Vault (NL Vault, ABN ...)" reads like a typo when the business
    # trades under the shop's own name.
    if business_name == "NL Vault":
        return f"NL Vault (ABN {business_abn})"
    return f"NL Vault ({business_name}, ABN {business_abn})"


def _catalogue_privacy(retention: str, contact: str) -> str:
    """Display-only mode: nothing is bought on this site, so the only
    personal data it ever holds is what someone types into the contact
    form. Orders happen on eBay/Vinted under their own policies."""
    return (
        "<h2>What we collect</h2>"
        "<p class='muted'>Browsing this site needs no account and sets no tracking or advertising "
        "cookies. If you use the contact form, we keep your name, email address and message so we "
        "can reply.</p>"
        "<h2 style='margin-top:22px'>When you buy</h2>"
        "<p class='muted'>Purchases are made on eBay or Vinted, not on this site. Your payment, order and "
        "delivery details are collected by that marketplace under its own privacy policy, and we "
        "receive only what it shares with sellers to send your order.</p>"
        "<h2 style='margin-top:22px'>What it's used for</h2>"
        "<p class='muted'>Solely to answer your message or send your order. It is not sold, and not used "
        "for marketing.</p>"
        "<h2 style='margin-top:22px'>Who it's shared with</h2>"
        "<p class='muted'>Australia Post or the relevant courier, to deliver an order. Nobody else.</p>"
        "<h2 style='margin-top:22px'>How long it's kept</h2>"
        f"<p class='muted'>Sales records are kept for {retention}. Contact messages are kept only as "
        "long as needed to deal with your enquiry.</p>"
        "<h2 style='margin-top:22px'>Contact</h2>"
        "<p class='muted'>Questions about your data, or a request to access or delete it: "
        f"{contact}.</p>"
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    settings = get_settings()
    retention = escape(settings.storefront_retention_period or "[RETENTION_PERIOD — e.g. 7 years, per Australian tax record-keeping requirements]")
    contact = escape(settings.storefront_contact_email or "[CONTACT_EMAIL]")
    draft = _DRAFT_NOTICE if storefront_legal_issues(settings) else ""
    if is_catalogue():
        body = (
            "<div class='page-head'><h1>Privacy policy</h1>"
            "<p>What's collected when you use this site, and what it's used for.</p></div>"
            f"{draft}<div class='panel'>{_catalogue_privacy(retention, contact)}</div>"
        )
        return _legal_response(session, request, settings, "Privacy policy", body)
    body = (
        "<div class='page-head'><h1>Privacy policy</h1>"
        "<p>What's collected when you shop here, and what it's used for.</p></div>"
        f"{draft}"
        "<div class='panel'>"
        "<h2>What we collect</h2>"
        "<p class='muted'>At checkout: your name, email address, and postal address, to fulfil and "
        "ship your order. Payment is handled by Stripe — your card details are entered on Stripe's own "
        "page and never reach this site or its operator.</p>"
        "<h2 style='margin-top:22px'>What it's used for</h2>"
        "<p class='muted'>Solely to process and ship your order, and to contact you about it if needed. "
        "It is not sold, and not used for marketing unless you separately opt in somewhere that says so.</p>"
        "<h2 style='margin-top:22px'>Who it's shared with</h2>"
        "<p class='muted'>Stripe, to process payment, and Australia Post or the relevant courier, to "
        "deliver your order. Nobody else.</p>"
        "<h2 style='margin-top:22px'>How long it's kept</h2>"
        f"<p class='muted'>Order records are kept for {retention}.</p>"
        "<h2 style='margin-top:22px'>Contact</h2>"
        "<p class='muted'>Questions about your data, or a request to access or delete it: "
        f"{contact}.</p>"
        "</div>"
    )
    return _legal_response(session, request, settings, "Privacy policy", body)


def _legal_response(session, request, settings, title: str, body: str) -> HTMLResponse:
    # noindex until the facts are filled in and explicitly approved -- a
    # search engine indexing a page that still says "[BUSINESS_NAME]"
    # would be worse than not indexing it yet.
    robots = "noindex,follow" if storefront_legal_issues(settings) else ""
    return shell(session, f"{title} — NL Vault", body, request=request,
                 crumbs=(("Home", "/"), (title, "")), robots=robots)


@router.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    settings = get_settings()
    business_name = escape(settings.storefront_business_name or "[BUSINESS_NAME]")
    business_abn = escape(settings.storefront_business_abn or "[BUSINESS_ABN]")
    contact = escape(settings.storefront_contact_email or "[CONTACT_EMAIL]")
    gst_statement = escape(settings.storefront_gst_statement or
                           '[GST_STATEMENT — e.g. "Prices include GST" or "NL Vault is not currently registered for GST"]')
    draft = _DRAFT_NOTICE if storefront_legal_issues(settings) else ""
    body = (
        "<div class='page-head'><h1>Terms of sale</h1>"
        "<p>The short version of what buying here means.</p></div>"
        f"{draft}"
        "<div class='panel'>"
        "<h2>Who you're buying from</h2>"
        f"<p class='muted'>{_seller(business_name, business_abn)}, operating from Australia.</p>"
        + (
            "<h2 style='margin-top:22px'>Where sales happen</h2>"
            "<p class='muted'>Everything shown here is sold through our eBay and Vinted shops. When you buy, "
            "that marketplace's own terms, payment and buyer protection apply alongside these.</p>"
            "<h2 style='margin-top:22px'>Prices</h2>"
            "<p class='muted'>Shown in Australian dollars. The price on the eBay or Vinted listing is the "
            f"one you pay. {gst_statement}</p>"
            if is_catalogue() else
            "<h2 style='margin-top:22px'>Prices</h2>"
            f"<p class='muted'>Listed in Australian dollars. {gst_statement}</p>"
        ) +
        "<h2 style='margin-top:22px'>Condition</h2>"
        "<p class='muted'>Trading cards are graded by eye against standard TCG condition terms (Near "
        "Mint, Lightly Played, etc.) unless a listing says otherwise. Photos of the actual card are shown "
        "where available; reference artwork is clearly labelled as such and is not a photo of the item "
        "you'll receive.</p>"
        "<h2 style='margin-top:22px'>Returns and refunds</h2>"
        "<p class='muted'>If something arrives damaged, not as described, or faulty, contact "
        f"{contact}{' or message us through the marketplace you bought on' if is_catalogue() else ''} "
        "within 7 days of delivery. Nothing here limits the consumer guarantees you're "
        "entitled to under the <em>Australian Consumer Law</em>, which apply regardless of this or any "
        "other policy.</p>"
        "<h2 style='margin-top:22px'>Shipping</h2>"
        "<p class='muted'>See <a href='/shipping'>Shipping &amp; returns</a> for rates and timing.</p>"
        "<h2 style='margin-top:22px'>Contact</h2>"
        f"<p class='muted'>{contact}</p>"
        "</div>"
    )
    return _legal_response(session, request, settings, "Terms of sale", body)
