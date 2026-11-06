from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CardPrice, CardSet, CardVariant, CatalogCard, CatalogItem, CatalogItemType,
    InventoryItem, InventoryStatus, MarketListing, Marketplace,
    PotentialPurchaseStatus, Sale, TcgCard,
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


def test_empty_platform_shows_zeroes_not_errors(client):
    response = client.get("/analytics")
    assert response.status_code == 200
    assert "$0.00" in response.text


def test_dead_stock_is_flagged_for_old_still_available_items(client, session):
    old_added = datetime.now(UTC) - timedelta(days=120)
    session.add(CardSet(id="sv1", name="Scarlet & Violet"))
    session.add(CatalogItem(id="sv1-1", item_type=CatalogItemType.TCG_CARD, name="Sprigatito"))
    session.add(CatalogCard(id="sv1-1", set_id="sv1", number="1", name="Sprigatito"))
    session.add(InventoryItem(
        id="inv1", card_id="sv1-1", variant=CardVariant.NORMAL, quantity=1,
        status=InventoryStatus.AVAILABLE, added_at=old_added,
    ))
    session.commit()

    response = client.get("/analytics")

    assert "Sprigatito" in response.text
    assert "age-bucket dead" in response.text


def test_recent_available_stock_is_not_flagged_as_dead(client, session):
    session.add(CardSet(id="sv1", name="Scarlet & Violet"))
    session.add(CatalogCard(id="sv1-2", set_id="sv1", number="2", name="Fuecoco"))
    session.add(InventoryItem(id="inv2", card_id="sv1-2", variant=CardVariant.NORMAL, quantity=1, status=InventoryStatus.AVAILABLE))
    session.commit()

    response = client.get("/analytics")

    assert "Fuecoco" not in response.text


def test_inventory_value_uses_canonical_identity_without_legacy_card_link(client, session):
    session.add(CatalogItem(id="op-1", item_type=CatalogItemType.TCG_CARD, name="Zoro"))
    session.add(TcgCard(catalog_item_id="op-1", game="one_piece", number="1"))
    session.add(InventoryItem(
        id="inv-op", card_id=None, catalog_item_id="op-1",
        variant=CardVariant.NORMAL, quantity=2, status=InventoryStatus.AVAILABLE,
    ))
    session.add(CardPrice(
        catalog_item_id="op-1", variant=CardVariant.NORMAL,
        source="test", price=Decimal("5.00"), currency="AUD",
    ))
    session.commit()

    response = client.get("/analytics")

    assert "Inventory market value" in response.text
    assert "$10.00" in response.text


def test_monthly_revenue_only_counts_sales_from_this_month(client, session):
    session.add(Sale(id="s1", gross_amount=Decimal("100"), sold_at=datetime.now(UTC)))
    session.add(Sale(id="s2", gross_amount=Decimal("50"), sold_at=datetime.now(UTC) - timedelta(days=90)))
    session.commit()

    response = client.get("/analytics")

    assert "$100.00" in response.text
    assert "$50.00" not in response.text


def test_best_channel_picks_the_highest_grossing_marketplace(client, session):
    session.add(Marketplace(id="m1", name="Whatnot"))
    session.add(Marketplace(id="m2", name="eBay"))
    session.add(Sale(id="s1", marketplace_id="m1", gross_amount=Decimal("200"), sold_at=datetime.now(UTC)))
    session.add(Sale(id="s2", marketplace_id="m2", gross_amount=Decimal("50"), sold_at=datetime.now(UTC)))
    session.commit()

    response = client.get("/analytics")

    assert "Whatnot" in response.text
    assert "$200.00" in response.text


def test_candidate_landed_cost_only_counts_open_pipeline_statuses(client, session):
    session.add(MarketListing(
        id="p1", source="test", external_id="1", title="A", url="https://example.test/1",
        price=Decimal("100"), shipping=Decimal("0"), currency="AUD",
        landed_aud=Decimal("100"), candidate_status=PotentialPurchaseStatus.WATCHING,
    ))
    session.add(MarketListing(
        id="p2", source="test", external_id="2", title="B", url="https://example.test/2",
        price=Decimal("999"), shipping=Decimal("0"), currency="AUD",
        landed_aud=Decimal("999"), candidate_status=PotentialPurchaseStatus.DECLINED,
    ))
    session.commit()

    response = client.get("/analytics")

    assert "$100.00" in response.text
    assert "$999.00" not in response.text
    assert "Candidate landed cost" in response.text


def test_analytics_requires_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/analytics", follow_redirects=False)
    assert response.status_code in (302, 303, 401)


def test_ebay_order_revenue_is_visible_and_not_duplicated_by_lines_or_manual_sales(client,session):
    from automation_control.models import EbayImportedOrder
    now=datetime.now(UTC)
    session.add(Marketplace(id='ebay',name='eBay'))
    session.add(Marketplace(id='vinted',name='Vinted'))
    session.add(Sale(marketplace_id='ebay',gross_amount=Decimal('100'),sold_at=now))
    session.add(Sale(marketplace_id='vinted',gross_amount=Decimal('24.50'),sold_at=now))
    snapshot={'creationDate':now.isoformat(),'orderPaymentStatus':'PAID',
              'pricingSummary':{'total':{'value':'100.00','currency':'AUD'}}}
    for sku in ['one','two']:
        session.add(EbayImportedOrder(ebay_order_id='order1',sku=sku,quantity=1,
                                     marketplace_id='EBAY_AU',raw_snapshot=snapshot))
    session.commit()
    text=client.get('/analytics').text
    assert 'eBay revenue' in text and 'eBay this month' in text
    assert '$124.50' in text
    assert '$224.50' not in text
    assert '1 paid orders imported since' in text
    assert 'before fees and refunds' in text
