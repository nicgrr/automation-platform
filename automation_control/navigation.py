"""Return the operator to the page they were on after a form post.

The CRM is mostly used as an iOS home-screen app with no browser back button,
so a save that lands on a fixed list page loses the operator's place. ui.py's
NAV_RETURN_SCRIPT appends two query parameters to every POST form's action:

- `_return_to`: the page the form was submitted from (path + query).
- `_return_prev`: the page before that in this tab's trail.

`redirect_back` sends the operator to `_return_to`, or to `_return_prev` when
the form posted back to its own page (a dedicated edit page, where "back"
means the list the edit was opened from), and falls back to the handler's
existing default. Only same-site relative paths are accepted, so a crafted
link cannot turn a save into a redirect to another site.

Handlers whose next page is deliberately somewhere else (save-and-next review
queues, create-then-open, batch status pages, eBay confirm steps) keep their
own RedirectResponse.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Request
from fastapi.responses import RedirectResponse

SAVED_PARAM = "_saved"
_NEVER_RETURN_TO = ("/login", "/logout")


def safe_return_path(value: str | None) -> str | None:
    """`value` if it is a same-site relative path, else None."""
    if not value or len(value) > 2000:
        return None
    if not value.startswith("/") or value.startswith("//") or value.startswith("/\\"):
        return None
    if any(ch in value for ch in "\r\n\t\\") or any(ord(ch) < 32 for ch in value):
        return None
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return None
    if parts.path.rstrip("/") in _NEVER_RETURN_TO:
        return None
    return value


def _with_saved(path: str, saved: str | None) -> str:
    parts = urlsplit(path)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != SAVED_PARAM]
    if saved:
        query.append((SAVED_PARAM, saved))
    return urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))


def return_target(request: Request, default: str) -> str:
    """Where `redirect_back` would send this request, without the toast."""
    current = safe_return_path(request.query_params.get("_return_to"))
    if current and urlsplit(current).path == request.url.path:
        current = safe_return_path(request.query_params.get("_return_prev"))
    return current or default


def redirect_back(request: Request, default: str, *, saved: str | None = "Saved") -> RedirectResponse:
    """303 to the page the operator came from (see module docstring).

    `saved` shows a brief confirmation toast on arrival; pass None when the
    destination already renders its own notice or error. A post without the
    return parameters (no JS, scripts, tests) gets exactly `default`.
    """
    if "_return_to" not in request.query_params:
        return RedirectResponse(default, status_code=303)
    return RedirectResponse(_with_saved(return_target(request, default), saved), status_code=303)


# An upload starts a multi-step flow (review each capture, maybe price it)
# whose last step used to land on an empty queue page. The upload remembers
# the page it was started from; the step that empties the queue returns
# there. Short-lived, so an abandoned upload can't hijack a much later queue.
FLOW_COOKIE = "ezbay_flow_origin"
FLOW_MAX_AGE_SECONDS = 60 * 60


def remember_flow_origin(request: Request, response: RedirectResponse) -> RedirectResponse:
    """Record where this upload was started from, for `finish_flow`."""
    origin = safe_return_path(request.query_params.get("_return_to"))
    if origin:
        response.set_cookie(FLOW_COOKIE, origin, max_age=FLOW_MAX_AGE_SECONDS,
                            httponly=True, samesite="lax", path="/")
    return response


def finish_flow(request: Request, default: str, *, saved: str = "All reviewed") -> RedirectResponse:
    """303 back to where the upload started, or `default` with no upload."""
    origin = safe_return_path(request.cookies.get(FLOW_COOKIE))
    if origin is None:
        return RedirectResponse(default, status_code=303)
    response = RedirectResponse(_with_saved(origin, saved), status_code=303)
    response.delete_cookie(FLOW_COOKIE, path="/")
    return response
