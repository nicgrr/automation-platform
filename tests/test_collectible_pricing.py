from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.collectible_pricing_service import (
    CategoryRoutingError, add_manual_comparable, calculate_valuation,
    provider_rules, require_collectible,
)
from automation_control.database import Base, get_session
from automation_control.models import (
    CardVariant, CatalogItem, CatalogItemType, CollectibleProduct, InventoryItem,
    PricingObservation, PricingProvider, PricingProviderRule, PricingSourceKind,
    TcgCard,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'collectible-pricing.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for key, mode in (("ebay", "automatic"), ("depop", "manual"), ("vinted", "manual")):
            db.add(PricingProvider(
                key=key, display_name=key.title(), enabled=key == "ebay",
                capabilities=["active_listings"], configuration={},
                commercial_use_status="approved" if key == "ebay" else "manual_only",
            ))
            db.add(PricingProviderRule(
                catalog_item_type=CatalogItemType.COLLECTIBLE, provider_key=key,
                enabled=True, access_mode=mode, allow_active=True, allow_sold=False,
                priority=10, refresh_seconds=86400,
            ))
        db.commit()
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "tester"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _collectible(session):
    item = CatalogItem(id="sonny-pig", item_type=CatalogItemType.COLLECTIBLE, name="Sonny Angel Animal Pig")
    session.add_all([
        item,
        CollectibleProduct(
            catalog_item_id=item.id, brand="Sonny Angel", series="Animal 1",
            character="Pig", variant="Regular", is_secret=False,
        ),
    ])
    session.commit()
    return item


def test_tcg_items_cannot_resolve_collectible_providers(session):
    card = CatalogItem(id="pokemon-1", item_type=CatalogItemType.TCG_CARD, name="Pikachu")
    session.add(card)
    session.add(TcgCard(catalog_item_id=card.id, game="pokemon", number="25", language="English"))
    session.commit()

    assert provider_rules(session, card) == []
    with pytest.raises(CategoryRoutingError):
        require_collectible(session, card.id)


def test_manual_comparables_keep_asking_sold_and_unknown_offer_distinct(session):
    item = _collectible(session)
    active = add_manual_comparable(
        session, catalog_item_id=item.id, provider_key="depop",
        url="https://www.depop.com/products/example-sonny", title="Pig figure",
        price="40", currency="AUD", status="active",
    )
    sold = add_manual_comparable(
        session, catalog_item_id=item.id, provider_key="vinted",
        url="https://www.vinted.com/items/123", title="Pig sold",
        price="32", currency="AUD", status="sold_verified",
    )
    unknown = add_manual_comparable(
        session, catalog_item_id=item.id, provider_key="ebay",
        url="https://www.ebay.com.au/itm/123", title="Best offer accepted",
        price="45", currency="AUD", status="accepted_unknown",
    )

    assert active.source_kind == PricingSourceKind.ACTIVE_LISTING
    assert sold.source_kind == PricingSourceKind.SOLD_LISTING
    assert sold.listing_type == "verified_sold_manual"
    assert unknown.source_kind == PricingSourceKind.MANUAL
    assert unknown.listing_type == "accepted_offer_unknown"
    value = calculate_valuation([active, sold, unknown], now=datetime.now(UTC))
    assert value.median_ask == Decimal("40.00")
    assert value.sold_low == value.sold_high == Decimal("32")
    assert value.realistic_resale == Decimal("32.00")
    assert value.active_count == 1
    assert value.sold_count == 1


def test_non_aud_comparable_retains_original_and_normalized_amount(session):
    item = _collectible(session)
    row = add_manual_comparable(
        session, catalog_item_id=item.id, provider_key="vinted",
        url="https://www.vinted.fr/items/456", title="French listing",
        price="20", currency="EUR", aud_price=Decimal("33.25"), status="active",
    )
    assert row.original_price == Decimal("20")
    assert row.original_currency == "EUR"
    assert row.aud_price == Decimal("33.25")


def test_inventory_detail_is_native_and_shows_provider_limitations(client, session):
    item = _collectible(session)
    inventory = InventoryItem(
        catalog_item_id=item.id, card_id=None, variant=CardVariant.NORMAL,
        condition="New", quantity=1, purchase_cost_basis=Decimal("70"),
        allocated_shipping_cost=Decimal("14"), allocated_cost_basis=Decimal("84"),
    )
    session.add(inventory)
    session.commit()

    response = client.get(f"/collectibles/pricing/item/{inventory.id}")
    assert response.status_code == 200
    assert "Sonny Angel Animal Pig" in response.text
    assert "Purchase price" in response.text
    assert "A$70.00" in response.text
    assert "Allocated shipping" in response.text
    assert "Manual / search link" in response.text
    assert "Accepted offers with unknown final prices" in response.text


def test_tcg_inventory_is_rejected_by_collectible_page(client, session):
    card = CatalogItem(id="one-piece-1", item_type=CatalogItemType.TCG_CARD, name="Luffy")
    session.add(card)
    session.add(TcgCard(catalog_item_id=card.id, game="one_piece", number="OP01-001", language="English"))
    inventory = InventoryItem(
        catalog_item_id=card.id, card_id=None, variant=CardVariant.NORMAL,
        condition="Near Mint", quantity=1,
    )
    session.add(inventory)
    session.commit()
    response = client.get(f"/collectibles/pricing/item/{inventory.id}")
    assert response.status_code == 409
    assert "restricted to non-TCG" in response.text


def test_failed_marketplace_refresh_does_not_break_other_crm_routes(client, session):
    item = _collectible(session)
    from automation_control.collectible_pricing_service import CollectiblePricingError
    with patch(
        "automation_control.collectible_pricing.refresh_ebay",
        new=AsyncMock(side_effect=CollectiblePricingError("eBay pricing is temporarily unavailable")),
    ):
        response = client.post(
            f"/collectibles/pricing/product/{item.id}/refresh-ebay",
            follow_redirects=False,
        )
    assert response.status_code == 503
    assert "temporarily unavailable" in response.text
    assert client.get("/collectibles/pricing").status_code == 200
