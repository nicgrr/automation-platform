"""Privacy policy and terms of sale -- Module 27.

Checkout collects a name, email and postal address, and there was
nowhere on the shop explaining what happens to that. These exist to fill
that gap, not to finish it: the business facts inside brackets --
[BUSINESS_NAME], [BUSINESS_ABN], [CONTACT_EMAIL] -- are placeholders,
because this app has no way to know a legal entity name or an ABN or
which inbox a shopper's email should reach. This is drafted content for
NL Vault to review and complete, not delivered legal advice; an actual
solicitor should look at both pages before they go live to real
customers, particularly the returns/refund wording against Australian
Consumer Law.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from automation_control.database import get_session

from .common import shell

router = APIRouter()

_DRAFT_NOTICE = (
    "<div class='notice'><strong>Draft for review.</strong> The bracketed details on this page "
    "are placeholders. Fill them in — and have this reviewed against Australian Consumer Law and "
    "the Privacy Act before the shop goes live.</div>"
)


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    body = (
        "<div class='page-head'><h1>Privacy policy</h1>"
        "<p>What's collected when you shop here, and what it's used for.</p></div>"
        f"{_DRAFT_NOTICE}"
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
        "<p class='muted'>Order records are kept for [RETENTION_PERIOD — e.g. 7 years, per Australian "
        "tax record-keeping requirements].</p>"
        "<h2 style='margin-top:22px'>Contact</h2>"
        "<p class='muted'>Questions about your data, or a request to access or delete it: "
        "[CONTACT_EMAIL].</p>"
        "</div>"
    )
    # noindex until the [PLACEHOLDER] facts above are filled in -- a
    # search engine indexing a page that still says "[BUSINESS_NAME]"
    # would be worse than not indexing it yet. Drop this once the real
    # ABN/GST/contact facts replace the brackets.
    crumbs = (("Home", "/"), ("Privacy policy", ""))
    return shell(session, "Privacy policy — NL Vault", body, request=request,
                crumbs=crumbs, robots="noindex,follow")


@router.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    body = (
        "<div class='page-head'><h1>Terms of sale</h1>"
        "<p>The short version of what buying here means.</p></div>"
        f"{_DRAFT_NOTICE}"
        "<div class='panel'>"
        "<h2>Who you're buying from</h2>"
        "<p class='muted'>NL Vault ([BUSINESS_NAME], ABN [BUSINESS_ABN]), operating from Australia.</p>"
        "<h2 style='margin-top:22px'>Prices</h2>"
        "<p class='muted'>Listed in Australian dollars. [GST_STATEMENT — e.g. \"Prices include GST\" "
        "or \"NL Vault is not currently registered for GST\", depending on registration status and "
        "turnover.]</p>"
        "<h2 style='margin-top:22px'>Condition</h2>"
        "<p class='muted'>Trading cards are graded by eye against standard TCG condition terms (Near "
        "Mint, Lightly Played, etc.) unless a listing says otherwise. Photos of the actual card are shown "
        "where available; reference artwork is clearly labelled as such and is not a photo of the item "
        "you'll receive.</p>"
        "<h2 style='margin-top:22px'>Returns and refunds</h2>"
        "<p class='muted'>If something arrives damaged, not as described, or faulty, contact "
        "[CONTACT_EMAIL] within 7 days of delivery. Nothing here limits the consumer guarantees you're "
        "entitled to under the <em>Australian Consumer Law</em>, which apply regardless of this or any "
        "other policy.</p>"
        "<h2 style='margin-top:22px'>Shipping</h2>"
        "<p class='muted'>See <a href='/shipping'>Shipping &amp; returns</a> for rates and timing.</p>"
        "<h2 style='margin-top:22px'>Contact</h2>"
        "<p class='muted'>[CONTACT_EMAIL]</p>"
        "</div>"
    )
    crumbs = (("Home", "/"), ("Terms of sale", ""))
    return shell(session, "Terms of sale — NL Vault", body, request=request,
                crumbs=crumbs, robots="noindex,follow")
