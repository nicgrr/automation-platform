"""Shared Discord-webhook posting, generalized out of
market_watch_service.py's send_alerts (the only other place this codebase
posts to Discord). Anything that wants to push a message -- the nightly
reconciliation audit, a future release announcement -- goes through here
instead of re-implementing the same httpx POST.

Synchronous on purpose: the audit script that first used this runs as a
one-off process from a systemd timer, not inside the app's event loop.
"""

import httpx

DISCORD_MESSAGE_LIMIT = 2000


def send_discord_message(content: str, webhook_url: str | None) -> bool:
    """POST `content` to `webhook_url`. Returns False (no-op) if no webhook
    is configured; never raises -- a notification failure shouldn't crash
    the caller. Truncated to Discord's own 2000-char message cap -- a
    long finding list would otherwise get a silent 400 back."""
    if not webhook_url:
        return False
    if len(content) > DISCORD_MESSAGE_LIMIT:
        cut = DISCORD_MESSAGE_LIMIT - len("\n…(truncated)")
        content = content[:cut] + "\n…(truncated)"
    try:
        response = httpx.post(webhook_url, json={"content": content}, timeout=15)
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False
