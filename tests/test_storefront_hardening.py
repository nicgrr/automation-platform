from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base, get_session
from automation_control.models import (
    BulkLot, CatalogItem, CatalogItemType, ShopListing, ShopListingStatus,
)
from storefront.app import create_app


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- security headers --------------------------------------------------------

@pytest.mark.parametrize("header", [
    "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy",
    "Permissions-Policy", "Content-Security-Policy",
])
def test_every_response_carries_the_security_headers(client, header):
    response = client.get("/")
    assert header in response.headers, header


def test_x_frame_options_blocks_framing(client):
    assert client.get("/").headers["X-Frame-Options"] == "DENY"


def test_headers_are_present_on_a_404_too(client):
    response = client.get("/totally/bogus/path")
    assert "Content-Security-Policy" in response.headers


def test_csp_allows_google_fonts_which_the_page_actually_loads(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "fonts.googleapis.com" in csp
    assert "fonts.gstatic.com" in csp


def test_csp_allows_the_reference_art_domains_actually_used(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "images.pokemontcg.io" in csp
    assert "images.scrydex.com" in csp


# --- branded error pages ------------------------------------------------------

def test_404_is_branded_html_not_bare_json(client):
    response = client.get("/totally/bogus/path")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "NL Vault" in response.text
    # the raw JSON error shape must not leak through -- ".detail"/".detail-price"
    # are legitimate CSS classes on this page, so check for the JSON key
    # specifically rather than the bare word
    assert '"detail":' not in response.text


def test_a_missing_item_gets_the_branded_404_too(client):
    response = client.get("/item/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Back to the vault" in response.text


def test_an_unhandled_exception_gets_a_branded_500_not_a_stack_trace(session, monkeypatch):
    import storefront.app as app_module

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(app_module, "_active_listings", boom)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    # The default TestClient re-raises server exceptions for the test
    # writer's convenience; here the exception handler itself is what's
    # under test, so it needs to actually run rather than be bypassed.
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/browse")
    app.dependency_overrides.clear()

    assert response.status_code == 500
    assert "kaboom" not in response.text
    assert "NL Vault" in response.text


# --- SEO plumbing --------------------------------------------------------------

def test_robots_txt_allows_crawling_and_points_at_the_sitemap(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "Allow: /" in response.text
    assert "Sitemap: http://testserver/sitemap.xml" in response.text


def test_www_redirects_to_the_bare_domain(client):
    response = client.get("/shipping?x=1", headers={"host": "www.nlvault.com.au"}, follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "http://nlvault.com.au/shipping?x=1"


def test_bare_domain_is_served_directly(client):
    response = client.get("/shipping", headers={"host": "nlvault.com.au"}, follow_redirects=False)
    assert response.status_code == 200


def test_sitemap_includes_static_pages(client):
    body = client.get("/sitemap.xml").text
    assert "<loc>" in body
    assert "/browse</loc>" in body
    assert "/shipping</loc>" in body


def test_sitemap_includes_active_listings_and_lots(client, session):
    session.add(CatalogItem(id="c1", item_type=CatalogItemType.TCG_CARD, name="Charizard"))
    session.add(ShopListing(id="l1", catalog_item_id="c1", title="Charizard #4",
                            price=Decimal("20.00"), currency="AUD", quantity=1,
                            status=ShopListingStatus.ACTIVE))
    session.add(BulkLot(id="b1", set_id=None, game="pokemon", title="Base bulk", card_count=50,
                        price=Decimal("15.00"), currency="AUD", quantity=1,
                        status=ShopListingStatus.ACTIVE))
    session.commit()
    body = client.get("/sitemap.xml").text
    assert "/item/l1</loc>" in body
    assert "/bulk/b1</loc>" in body


def test_sitemap_excludes_sold_out_and_withdrawn_listings(client, session):
    session.add(CatalogItem(id="c1", item_type=CatalogItemType.TCG_CARD, name="Charizard"))
    session.add(ShopListing(id="l1", catalog_item_id="c1", title="Sold out card",
                            price=Decimal("20.00"), currency="AUD", quantity=0,
                            status=ShopListingStatus.SOLD_OUT))
    session.commit()
    assert "/item/l1</loc>" not in client.get("/sitemap.xml").text


def test_sitemap_urls_are_absolute(client):
    body = client.get("/sitemap.xml").text
    assert "<loc>http" in body


def test_home_page_has_a_meta_description(client):
    body = client.get("/").text
    assert "<meta name='description'" in body
    assert "og:title" in body and "og:description" in body


def test_item_page_description_mentions_the_actual_item(client, session):
    session.add(CatalogItem(id="c1", item_type=CatalogItemType.TCG_CARD, name="Charizard"))
    session.add(ShopListing(id="l1", catalog_item_id="c1", title="Charizard #4",
                            price=Decimal("20.00"), currency="AUD", quantity=1,
                            status=ShopListingStatus.ACTIVE))
    session.commit()
    body = client.get("/item/l1").text
    assert "Charizard #4" in body
    assert "$20.00" in body


# --- GSAP -----------------------------------------------------------------

def test_gsap_loads_from_cdnjs(client):
    body = client.get("/").text
    assert "cdnjs.cloudflare.com/ajax/libs/gsap" in body
    assert "gsap.min.js" in body
    assert "ScrollTrigger.min.js" in body


def test_csp_allows_the_gsap_cdn(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "cdnjs.cloudflare.com" in csp


def test_gsap_scripts_load_before_the_code_that_uses_them(client):
    """gsap.min.js and ScrollTrigger.min.js are loaded WITHOUT defer/async
    specifically so they finish before the inline script below them runs
    -- a deferred script and a following inline script do not execute in
    that order, so this ordering is load-bearing, not incidental."""
    body = client.get("/").text
    gsap_pos = body.index("gsap.min.js")
    scrolltrigger_pos = body.index("ScrollTrigger.min.js")
    usage_pos = body.index("gsap.from")
    assert gsap_pos < scrolltrigger_pos < usage_pos


def test_gsap_scripts_are_not_deferred_or_async(client):
    body = client.get("/").text
    tag_start = body.index("<script src='https://cdnjs.cloudflare.com/ajax/libs/gsap")
    tag_end = body.index("</script>", tag_start)
    tag = body[tag_start:tag_end]
    assert "defer" not in tag
    assert "async" not in tag


def test_gsap_respects_reduced_motion(client):
    body = client.get("/").text
    assert "prefers-reduced-motion" in body


def test_gsap_animates_the_hero_and_product_cards_only(client):
    """Kept to what it's good at -- no gimmicks elsewhere."""
    body = client.get("/").text
    assert "querySelector('.hero-inner')" in body
    assert "gsap.from(hero.children" in body
    assert ".dept-card, .grid .card" in body
