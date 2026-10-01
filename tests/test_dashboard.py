from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.ui import ADMIN_NAV, TOOL_NAV
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CardPrice, CardSet, CardVariant, CatalogCard, CatalogItem, CatalogItemType, InventoryItem,
    Marketplace, PotentialPurchase, PotentialPurchaseStatus, Sale, TcgCard,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_empty_platform_renders_without_error(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Sales Executive Dashboard" in response.text


def test_recent_sale_appears_in_the_widget(client, session):
    session.add(Marketplace(id="m1", name="Whatnot"))
    session.add(Sale(id="s1", marketplace_id="m1", gross_amount=Decimal("42.50")))
    session.commit()

    response = client.get("/dashboard")

    assert "Whatnot" in response.text
    assert "$42.50" in response.text


def test_top_potential_buy_ranked_by_margin_appears(client, session):
    session.add(PotentialPurchase(id="p1", description="12 Sonny Angels", market_value=Decimal("450"), asking_price=Decimal("300"), status=PotentialPurchaseStatus.WATCHING))
    session.add(PotentialPurchase(id="p2", description="Declined lot", market_value=Decimal("999"), asking_price=Decimal("1"), status=PotentialPurchaseStatus.DECLINED))
    session.commit()

    response = client.get("/dashboard")

    assert "12 Sonny Angels" in response.text
    assert "Declined lot" not in response.text  # not an open pipeline status


def test_inventory_priced_gauge_reflects_real_coverage(client, session):
    session.add(CardSet(id="sv1", name="Scarlet & Violet"))
    session.add(CatalogCard(id="sv1-1", set_id="sv1", number="1", name="Sprigatito"))
    session.add(CatalogItem(id="sv1-1", item_type=CatalogItemType.TCG_CARD, name="Sprigatito"))
    session.add(TcgCard(catalog_item_id="sv1-1", game="pokemon", number="1"))
    session.add(InventoryItem(id="inv1", card_id="sv1-1", catalog_item_id="sv1-1", variant=CardVariant.NORMAL, quantity=1))
    session.commit()

    response = client.get("/dashboard")

    assert "0%" in response.text  # no CardPrice row exists yet -> 0% priced
    assert "0 of 1 holdings" in response.text


def test_priced_gauge_counts_a_holding_with_no_legacy_card_id(client, session):
    """The bug this guards: One Piece, Personal Collection, and
    collectible holdings all have card_id=None (only Pokemon's legacy
    CatalogCard path sets it) and are priced by catalog_item_id -- the
    gauge used to look prices up by card_id and silently missed every
    one of them, undercounting real coverage."""
    session.add(CatalogItem(id="one-piece-op01-1", item_type=CatalogItemType.TCG_CARD, name="Roronoa Zoro"))
    session.add(TcgCard(catalog_item_id="one-piece-op01-1", game="one_piece", number="1"))
    session.add(InventoryItem(id="inv1", card_id=None, catalog_item_id="one-piece-op01-1",
                              variant=CardVariant.NORMAL, quantity=1))
    session.add(CardPrice(id="p1", card_id="one-piece-op01-1", source="ebay_active_low", price=Decimal("5.00")))
    session.commit()

    response = client.get("/dashboard")

    assert "100%" in response.text
    assert "1 of 1 holdings" in response.text


def test_priced_gauge_excludes_personal_collection(client, session):
    """Personal Collection items are held privately, off the shop -- a
    sales-facing coverage metric shouldn't be diluted by what isn't for
    sale, priced or not."""
    from automation_control.models import InventoryStatus

    session.add(CatalogItem(id="item-1", item_type=CatalogItemType.TCG_CARD, name="Kept Card"))
    session.add(TcgCard(catalog_item_id="item-1", game="pokemon", number="1"))
    session.add(InventoryItem(id="inv1", card_id=None, catalog_item_id="item-1", variant=CardVariant.NORMAL,
                              quantity=1, status=InventoryStatus.PERSONAL_COLLECTION))
    session.commit()

    response = client.get("/dashboard")

    assert "0 of 0 holdings" in response.text


def test_collectible_value_is_grouped_by_brand_not_unknown(client, session):
    """A Sonny Angel figure has no TCG `game` at all -- that's not a data
    gap, it's a different product type, so it should show up as its own
    brand in the value breakdown rather than a bare "unknown" bucket that
    reads as a bug next to "pokemon"/"one_piece"."""
    from automation_control.models import CollectibleProduct

    session.add(CatalogItem(id="figure-1", item_type=CatalogItemType.COLLECTIBLE, name="Kappa"))
    session.add(CollectibleProduct(catalog_item_id="figure-1", brand="Sonny Angel"))
    session.add(InventoryItem(id="inv1", card_id=None, catalog_item_id="figure-1",
                              variant=CardVariant.NORMAL, quantity=1))
    session.add(CardPrice(id="p1", card_id="figure-1", source="ebay_active_low", price=Decimal("40.00")))
    session.commit()

    response = client.get("/dashboard")

    assert "Sonny Angel" in response.text
    assert "unknown" not in response.text.lower()


def test_quick_links_are_rendered_from_the_shared_nav(client):
    """The dashboard used to hand-maintain its own copy of the nav, and the
    two drifted: photo audit reached only from here, eight links reachable
    only from the drawer. Both now come from ui.ADMIN_NAV, so the real
    invariant is that they agree -- not that a hardcoded list is present."""
    response = client.get("/dashboard")
    # scoped to the quicklinks block -- the drawer is on every page and
    # carries every link, including the collapsed ones
    quicklinks = response.text.split("class='quicklinks'")[1]
    for _title, links, collapsed in ADMIN_NAV:
        for href, _label in links:
            if collapsed:
                # "Not set up yet" is deliberately absent here: there's no
                # disclosure to fold it into on the dashboard. It stays
                # reachable from the drawer and the Ctrl-K palette.
                assert f"href='{href}'" not in quicklinks, href
            else:
                assert f"href='{href}'" in quicklinks, href


def test_every_listed_module_is_reachable_from_the_drawer(client):
    """Every condensed workflow entrance is present in the drawer."""
    response = client.get("/dashboard")
    for _title, links, _collapsed in ADMIN_NAV:
        for href, _label in links:
            assert f"data-nav-href='{href}'" in response.text, href


def test_review_centre_carries_the_combined_count_badge(client):
    response = client.get("/dashboard")
    assert "data-count-for='/review-centre'" in response.text


def test_jump_to_palette_lists_every_destination(client):
    response = client.get("/dashboard")
    for _title, links, _collapsed in ADMIN_NAV + TOOL_NAV:
        for href, _label in links:
            assert f"data-href='{href}'" in response.text, href


def test_topbar_search_has_autocomplete_wiring(client):
    response = client.get("/dashboard")
    assert "global-search-input" in response.text
    assert "global-search-results" in response.text
    assert "/search/suggest" in response.text


def test_dashboard_requires_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code in (302, 303, 401)


# --- the background-jobs banner --------------------------------------------

def test_the_dashboard_names_a_failing_background_job(client):
    """The failure this exists for looked exactly like healthy numbers: the
    price backfill died and every figure on this page carried on rendering
    a value, just a staler one each day."""
    from automation_control.job_health import FAILED, JOBS, JobStatus

    broken = JobStatus(JOBS[1], FAILED, detail="last run exited 1")
    with patch("automation_control.job_health.all_statuses", lambda *a, **k: [broken]):
        page = client.get("/dashboard").text

    assert "1 background job needs attention" in page
    assert "Card price backfill" in page
    assert "last run exited 1" in page
    # and says what it costs you, not just that it broke
    assert "held cards stop getting a market value" in page


def test_a_healthy_dashboard_says_so_in_one_line(client):
    """Healthy must not cost the page real estate -- only trouble gets room."""
    from automation_control.job_health import JOBS, OK, JobStatus

    fine = [JobStatus(job, OK) for job in JOBS]
    from automation_control.recovery_health import RecoveryStatus
    recovery = [
        RecoveryStatus("Encrypted backup", True, "last success 2h ago"),
        RecoveryStatus("Restore drill", True, "last success 10d ago"),
    ]
    with patch("automation_control.job_health.all_statuses", lambda *a, **k: fine), \
         patch("automation_control.recovery_health.recovery_statuses", lambda: recovery):
        page = client.get("/dashboard").text

    assert f"All {len(JOBS)} background jobs healthy" in page
    assert "needs attention" not in page
    assert "Recovery checks healthy" in page


def test_dashboard_warns_when_restore_evidence_is_missing(client):
    from automation_control.recovery_health import RecoveryStatus

    statuses = [RecoveryStatus("Restore drill", False, "no status has been recorded")]
    with patch("automation_control.recovery_health.recovery_statuses", lambda: statuses):
        page = client.get("/dashboard").text

    assert "1 recovery check needs attention" in page
    assert "Restore drill" in page
    assert "no status has been recorded" in page


# --- the eBay Listings widget ----------------------------------------------

def test_dashboard_shows_never_checked_when_ebay_listings_cache_is_empty(client):
    """Must never show 0 for something never actually asked about -- that
    is the exact gap that prompted /ebay-listings to exist: the Sell API
    this app is built around reported 0 while two real listings were live."""
    page = client.get("/dashboard").text
    assert "eBay Listings" in page
    assert "Never checked" in page


def test_dashboard_shows_the_cached_active_listing_count(client, session):
    from automation_control.models import EbayListing

    session.add(EbayListing(ebay_listing_id="267785450003", title="Pikachu Card",
                            currency="AUD", current_price_minor=30000, listing_status="Active",
                            raw_snapshot={"source": "trading_api"}))
    session.commit()

    page = client.get("/dashboard").text
    assert "Active listings" in page
    assert ">1<" in page or ">1 <" in page  # the count renders somewhere in the widget


def test_dashboard_ebay_widget_ignores_sell_api_rows(client, session):
    """The Sell API sync (/api/ebay/listings) writes into the same table
    with no source tag -- the dashboard widget must only count rows this
    session's Trading API path actually wrote."""
    from automation_control.models import EbayListing

    session.add(EbayListing(ebay_listing_id="sell-api-row", title="X", currency="AUD",
                            current_price_minor=100, listing_status="ACTIVE", raw_snapshot={}))
    session.commit()

    page = client.get("/dashboard").text
    assert "Never checked" in page
