"""Contact Us -- Module 28.

No outbound email exists anywhere in this app (see checkout.py's removed
"confirmation is on its way to your inbox" claim), so a message can't be
forwarded to an inbox. It lands in contact_messages instead, read the
same way shop orders are: at a CRM page, not by email.

A honeypot field (`website`) is the only anti-spam measure -- a real
visitor never sees or fills it (hidden off-screen, not display:none,
which some bots specifically skip filling because they detect it), so
anything landing in it is almost certainly a bot and is dropped silently
rather than stored. This is not real spam protection, but it costs
nothing and stops the least sophisticated bots.
"""

from html import escape

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from automation_control.database import get_session
from automation_control.models import ContactMessage

from .common import shell

router = APIRouter()

MAX_MESSAGE_LENGTH = 4000


def _form(name: str = "", email: str = "", message: str = "") -> str:
    return (
        "<form method='post' action='/contact' class='calc-form'>"
        f"<label>Name<input name='name' value='{escape(name)}' required></label>"
        f"<label>Email<input name='email' type='email' value='{escape(email)}' required></label>"
        f"<label>Message<textarea name='message' rows='6' required>{escape(message)}</textarea></label>"
        # Off-screen, not display:none -- some bots specifically skip
        # fields hidden that way, so this is the more effective of the
        # two cheap honeypot patterns.
        "<label style='position:absolute;left:-9999px' aria-hidden='true'>"
        "Leave this field blank<input name='website' tabindex='-1' autocomplete='off'></label>"
        "<button class='btn brass' type='submit'>Send message</button>"
        "</form>"
    )


@router.get("/contact", response_class=HTMLResponse)
def contact_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    body = (
        "<div class='page-head'><h1>Contact us</h1>"
        "<p>Question about an order, a listing, or anything else — send it here.</p></div>"
        f"<div class='panel' style='max-width:520px'>{_form()}</div>"
    )
    crumbs = (("Home", "/"), ("Contact us", ""))
    return shell(session, "Contact us — NL Vault", body, request=request, crumbs=crumbs)


@router.post("/contact", response_class=HTMLResponse)
def contact_submit(
    request: Request,
    name: str = Form(...), email: str = Form(...), message: str = Form(...),
    website: str = Form(""),
    session: Session = Depends(get_session),
):
    if website.strip():
        # A filled honeypot means a bot, not a person -- redirect to the
        # same "thanks" page a real sender sees, so a scraper learns
        # nothing about being caught, but nothing is stored.
        return RedirectResponse("/contact/sent", status_code=303)

    session.add(ContactMessage(
        name=name.strip()[:256], email=email.strip()[:256],
        message=message.strip()[:MAX_MESSAGE_LENGTH],
    ))
    session.commit()
    return RedirectResponse("/contact/sent", status_code=303)


@router.get("/contact/sent", response_class=HTMLResponse)
def contact_sent(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    body = (
        "<div class='panel' style='max-width:520px;margin:0 auto;text-align:center'>"
        "<h1>Message sent</h1>"
        "<p class='muted'>Thanks — we'll get back to you as soon as we can.</p>"
        "<p style='margin-top:20px'><a class='btn ghost' href='/'>Back to the vault</a></p>"
        "</div>"
    )
    return shell(session, "Message sent — NL Vault", body, request=request, robots="noindex,follow")
