import pytest

from automation_control.link_fetch import UnsafeUrl, _address_is_public, assert_safe


def _resolves_to(monkeypatch, address: str):
    """Pin DNS so the guard is tested, not the internet."""
    monkeypatch.setattr(
        "automation_control.link_fetch.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", (address, 443))],
    )


# --- scheme and shape -------------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://example.com/",
    "ftp://example.com/x",
    "javascript:alert(1)",
    "",
    "not a url",
])
def test_only_http_and_https_are_accepted(url):
    with pytest.raises(UnsafeUrl):
        assert_safe(url)


# --- the addresses that must never be fetched -------------------------------

@pytest.mark.parametrize("address", [
    "127.0.0.1",        # loopback
    "0.0.0.0",          # unspecified
    "10.0.0.5",         # private
    "192.168.1.1",      # private
    "172.16.0.1",       # private
    "169.254.169.254",  # cloud metadata
    "100.64.0.1",       # CGNAT / Tailscale range
    "::1",              # IPv6 loopback
    "fe80::1",          # IPv6 link-local
    "fc00::1",          # IPv6 unique local
])
def test_internal_addresses_are_refused(monkeypatch, address):
    """The control plane sits on a private network beside the database and
    the storefront. A fetch-any-URL box is exactly the primitive needed to
    reach them, so every inward-pointing range is refused."""
    _resolves_to(monkeypatch, address)
    with pytest.raises(UnsafeUrl):
        assert_safe("https://example.com/listing")


def test_a_hostname_resolving_to_loopback_is_refused(monkeypatch):
    """Checking the *name* would be theatre -- evil.com pointing at
    127.0.0.1 is the entire attack."""
    _resolves_to(monkeypatch, "127.0.0.1")
    with pytest.raises(UnsafeUrl):
        assert_safe("https://totally-legit-shop.example/item/1")


def test_ipv4_mapped_ipv6_loopback_is_seen_through(monkeypatch):
    """::ffff:127.0.0.1 is loopback in an IPv6 costume; is_private alone
    does not catch it."""
    assert _address_is_public("::ffff:127.0.0.1") is False
    assert _address_is_public("::ffff:10.0.0.1") is False
    _resolves_to(monkeypatch, "::ffff:169.254.169.254")
    with pytest.raises(UnsafeUrl):
        assert_safe("https://example.com/x")


def test_a_public_address_is_allowed(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    assert assert_safe("https://example.com/itm/123").startswith("https://example.com")


def test_every_resolved_address_is_checked(monkeypatch):
    """A name resolving to one public and one internal address must be
    refused -- otherwise which one gets connected to is a coin toss."""
    monkeypatch.setattr(
        "automation_control.link_fetch.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443)),
                         (2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(UnsafeUrl):
        assert_safe("https://example.com/x")


def test_a_name_that_does_not_resolve_is_refused(monkeypatch):
    import socket as _socket

    def boom(*a, **k):
        raise _socket.gaierror("nope")

    monkeypatch.setattr("automation_control.link_fetch.socket.getaddrinfo", boom)
    with pytest.raises(UnsafeUrl):
        assert_safe("https://does-not-exist.invalid/x")


def test_the_error_does_not_confirm_the_internal_address(monkeypatch):
    """Saying which internal address a name resolved to is itself a small
    disclosure."""
    _resolves_to(monkeypatch, "10.1.2.3")
    with pytest.raises(UnsafeUrl) as exc:
        assert_safe("https://example.com/x")
    assert "10.1.2.3" not in str(exc.value)


# --- fetch behaviour --------------------------------------------------------

def test_redirects_are_not_followed(monkeypatch):
    """A public URL that 302s to the metadata endpoint would defeat a
    check done only on the URL you started with."""
    import httpx

    from automation_control.link_fetch import FetchFailed, fetch_text

    _resolves_to(monkeypatch, "93.184.216.34")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    client = httpx.Client(transport=transport, follow_redirects=False)
    with pytest.raises(FetchFailed) as exc:
        fetch_text("https://example.com/x", client=client)
    assert "redirect" in str(exc.value).lower()


def test_an_error_status_is_reported_not_parsed(monkeypatch):
    import httpx

    from automation_control.link_fetch import FetchFailed, fetch_text

    _resolves_to(monkeypatch, "93.184.216.34")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(FetchFailed):
        fetch_text("https://example.com/x", client=client)


def test_response_size_is_capped(monkeypatch):
    import httpx

    from automation_control import link_fetch

    _resolves_to(monkeypatch, "93.184.216.34")
    monkeypatch.setattr(link_fetch, "MAX_BYTES", 100)
    body = "x" * 5000
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body)))
    assert len(link_fetch.fetch_text("https://example.com/x", client=client)) == 100


def test_a_good_page_comes_back(monkeypatch):
    import httpx

    from automation_control.link_fetch import fetch_text

    _resolves_to(monkeypatch, "93.184.216.34")
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<h1>Sonny Angel</h1>"))
    )
    assert "Sonny Angel" in fetch_text("https://example.com/x", client=client)
