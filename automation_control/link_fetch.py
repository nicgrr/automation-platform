"""Fetching a user-supplied URL, safely.

Every other outbound call in this app goes to a base URL fixed in code
(eBay, pokemontcg.io, the FX feed). This is the first that goes wherever
someone types, which makes it the first that can be pointed *inward* --
at the Tailscale interface, at localhost, at cloud metadata endpoints.
The control plane sits on a private network with the database and the
storefront beside it, so "fetch this URL" is exactly the primitive an
attacker would want.

The guards, and what each is actually for:

  * http/https only. Rules out file://, gopher://, and the rest.
  * The hostname is resolved *first*, and every address it resolves to is
    checked. Checking the name would be theatre: `evil.com` resolving to
    127.0.0.1 is the whole trick.
  * Loopback, private, link-local, reserved and unspecified ranges are all
    refused -- including IPv6, and including the IPv4-mapped IPv6 forms
    that make ::ffff:127.0.0.1 look like a public address to a naive check.
  * Redirects are NOT followed. A public URL that 302s to 169.254.169.254
    defeats a check done only on the URL you started with.
  * Response size and time are capped, so a hostile endpoint can't hold a
    worker open or feed a gigabyte into the model.

This does not make arbitrary fetching risk-free, and it is deliberately
not exposed to the storefront -- only to the authenticated control plane.
"""

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

TIMEOUT_SECONDS = 10.0
MAX_BYTES = 2 * 1024 * 1024  # 2 MB is a generous marketplace page
ALLOWED_SCHEMES = {"http", "https"}

# A browser-ish UA: several marketplaces serve a bare error page to
# clients that don't send one, which reads as "no listing found".
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# Ranges Python's ipaddress does NOT count as private, but which are just
# as much "inward" here. 100.64.0.0/10 is RFC 6598 shared address space --
# and it is the range Tailscale hands out, so on this box it is the
# private network, not an edge case. The rest are carrier/test ranges with
# no business being fetched from a listing box.
EXTRA_BLOCKED = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("100.64.0.0/10", "192.0.0.0/24", "198.18.0.0/15", "64:ff9b::/96")
)


class UnsafeUrl(ValueError):
    """The URL is refused before any request is made."""


class FetchFailed(RuntimeError):
    """The request was attempted and didn't produce usable content."""


def _address_is_public(raw: str) -> bool:
    address = ipaddress.ip_address(raw)
    # ::ffff:127.0.0.1 is a loopback address wearing an IPv6 costume; the
    # is_private check below doesn't see through it on its own.
    if getattr(address, "ipv4_mapped", None) is not None:
        address = address.ipv4_mapped
    if any(address in network for network in EXTRA_BLOCKED if address.version == network.version):
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def assert_safe(url: str) -> str:
    """Validate a URL and return it, or raise UnsafeUrl.

    Separate from the fetch so the rule is directly testable without a
    network round trip -- this is the check that keeps a paste box from
    becoming a window onto the private network.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrl("only http and https links are supported")
    if not parsed.hostname:
        raise UnsafeUrl("that doesn't look like a link")

    try:
        resolved = socket.getaddrinfo(parsed.hostname, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrl("couldn't look up that address") from exc

    addresses = {info[4][0] for info in resolved}
    if not addresses:
        raise UnsafeUrl("couldn't look up that address")
    for address in addresses:
        try:
            public = _address_is_public(address)
        except ValueError as exc:
            raise UnsafeUrl("couldn't understand that address") from exc
        if not public:
            # Deliberately vague: confirming *which* internal address a
            # name resolves to is itself a small disclosure.
            raise UnsafeUrl("that link points somewhere internal, so it wasn't fetched")
    return parsed.geturl()


def fetch_text(url: str, *, client: httpx.Client | None = None) -> str:
    """Fetch a page and return its text, capped in size and time."""
    safe = assert_safe(url)
    owned = client is None
    client = client or httpx.Client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,  # see the module docstring
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    try:
        response = client.get(safe)
    except httpx.HTTPError as exc:
        raise FetchFailed(f"couldn't reach that page: {exc.__class__.__name__}") from exc
    finally:
        if owned:
            client.close()

    if response.status_code in (301, 302, 303, 307, 308):
        raise FetchFailed(
            "that link redirects, and redirects aren't followed automatically. "
            "Open it in a browser and paste the address it lands on."
        )
    if response.status_code >= 400:
        raise FetchFailed(f"the page returned HTTP {response.status_code}")

    content = response.content[:MAX_BYTES]
    if not content:
        raise FetchFailed("that page came back empty")
    return content.decode(response.encoding or "utf-8", errors="replace")
