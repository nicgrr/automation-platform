from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
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


def test_quick_links_to_every_module_are_present(client):
    response = client.get("/dashboard")
    for path in ["/purchase-lots", "/potential-stock", "/sales", "/customers", "/suppliers",
                 "/goals", "/release-calendar", "/sealed-products", "/collectibles", "/whatnot",
                 "/search", "/inventory", "/feed", "/export", "/market-search", "/deal-check"]:
        assert f'href="{path}"' in response.text, path


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
