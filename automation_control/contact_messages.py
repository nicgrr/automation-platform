"""Contact-form inbox -- Module 28.

The storefront's /contact page has nowhere to send a message (no
outbound email exists anywhere in this app), so it lands in
contact_messages and gets read here instead, the same way shop_orders
are worked from a queue rather than an inbox.
"""

from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import ContactMessage
from .ui import brand_header, page, pill

router = APIRouter(tags=["contact-messages"])


def _message_card(msg: ContactMessage) -> str:
    action = (
        f"<form method='post' action='/contact-messages/{escape(msg.id)}/read'>"
        "<button class='btn small'>Mark read</button></form>"
        if not msg.is_read else "<span class='muted'>Read</span>"
    )
    return (
        f"<div class='panel message-card{'' if msg.is_read else ' unread'}'>"
        "<div class='message-head'>"
        f"<div><strong>{escape(msg.name)}</strong> &lt;{escape(msg.email)}&gt;"
        f"<span class='muted'> · {msg.created_at:%Y-%m-%d %H:%M}</span></div>"
        f"{'' if msg.is_read else pill('NEW', 'warn')}"
        "</div>"
        f"<p class='message-body'>{escape(msg.message)}</p>"
        f"<div style='margin-top:10px'>{action}</div>"
        "</div>"
    )


@router.get("/contact-messages", response_class=HTMLResponse)
def contact_messages_page(
    show: str = Query("unread"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    stmt = select(ContactMessage).order_by(ContactMessage.created_at.desc())
    if show == "unread":
        stmt = stmt.where(ContactMessage.is_read.is_(False))
    messages = session.scalars(stmt.limit(200)).all()

    cards = "".join(_message_card(m) for m in messages) or (
        "<div class='panel'><p class='muted'>Nothing here.</p></div>"
    )
    tabs = (
        f"<a class='filter-tab{' active' if show == 'unread' else ''}' href='/contact-messages?show=unread'>Unread</a>"
        f"<a class='filter-tab{' active' if show == 'all' else ''}' href='/contact-messages?show=all'>All</a>"
    )

    body = (
        brand_header("Contact messages")
        + f"<p class='subtitle'>{len(messages)} message(s).</p>"
        + f"<div class='filter-tabs'>{tabs}</div>"
        + cards + _STYLE
    )
    return HTMLResponse(page("EzBay — Contact messages", body))


@router.post("/contact-messages/{message_id}/read")
def mark_read(message_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    message = session.get(ContactMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")
    if not message.is_read:
        message.is_read = True
        message.read_at = datetime.now(UTC)
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="contact_message.read",
                    resource_type="contact_message", resource_id=message_id, outcome="success",
                    correlation_id=message_id, details={})
    return RedirectResponse("/contact-messages", status_code=303)


_STYLE = """<style>
.filter-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 18px}
.filter-tab{padding:7px 13px;border-radius:999px;font-size:13px;font-weight:600;
  background:var(--surface-sunken);color:var(--text-dim)}
.filter-tab.active{background:var(--accent);color:#fff}
.message-card{margin-bottom:14px}
.message-card.unread{border-left:3px solid var(--accent)}
.message-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.message-body{white-space:pre-wrap;font-size:14px;color:var(--text-dim);margin:0}
.btn.small{padding:6px 12px;font-size:13px}
</style>"""
