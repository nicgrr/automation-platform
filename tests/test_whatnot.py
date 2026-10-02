from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CatalogItem, CatalogItemType, Customer, InventoryItem, InventoryStatus, Marketplace,
    MarketplaceFeeRule, Sale, SaleItem, ShopListing, ShopListingStatus, WhatnotShow,
    WhatnotShowItem, WhatnotShowItemOutcome, WhatnotShowStatus,
)
from automation_control.whatnot import _eligible_inventory


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Marketplace(id="whatnot", name="Whatnot"))
        db.add(MarketplaceFeeRule(
            id="r1", marketplace_id="whatnot", category="TCG",
            commission_pct=Decimal("8"), processing_pct=Decimal("2.9"), fixed_fee=Decimal("0.30"),
        ))
        db.commit()
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _inventory(session, suffix="1", *, quantity=1, cost="20.00"):
    catalog = CatalogItem(id=f"catalog-{suffix}", name=f"Kaido {suffix}", item_type=CatalogItemType.TCG_CARD)
    inventory = InventoryItem(
        id=f"inventory-{suffix}", catalog_item_id=catalog.id, quantity=quantity,
        status=InventoryStatus.AVAILABLE, allocated_cost_basis=Decimal(cost), condition="Near Mint",
    )
    session.add_all([catalog, inventory])
    session.commit()
    return inventory


def _create_show(client) -> str:
    response = client.post(
        "/whatnot",
        data={"title": "Friday One Piece Night", "show_date": "2026-09-12", "category": "TCG"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"]


def _queue(client, session, show_url, inventory, starting="50") -> WhatnotShowItem:
    response = client.post(
        f"{show_url}/items",
        data={"inventory_item_id": inventory.id, "starting_price": starting},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return session.scalars(select(WhatnotShowItem).where(
        WhatnotShowItem.inventory_item_id == inventory.id
    )).one()


def _start(client, show_url):
    response = client.post(f"{show_url}/start", follow_redirects=False)
    assert response.status_code == 303, response.text


def test_create_show_and_queue_owned_inventory_reserves_one_unit(client, session):
    inventory = _inventory(session, quantity=2)
    show_url = _create_show(client)
    item = _queue(client, session, show_url, inventory)

    session.refresh(inventory)
    show = session.get(WhatnotShow, show_url.rsplit("/", 1)[-1])
    assert show.status == WhatnotShowStatus.DRAFT
    assert item.inventory_item_id == inventory.id
    assert item.stock_reserved_at is not None
    assert item.description.startswith("Kaido 1")
    assert inventory.quantity == 1
    assert inventory.status == InventoryStatus.AVAILABLE
    body = client.get(show_url).text
    assert "Kaido 1" in body
    assert "$50.00" in body


def test_last_unit_becomes_reserved_when_queued(client, session):
    inventory = _inventory(session)
    item = _queue(client, session, _create_show(client), inventory)
    session.refresh(inventory)
    assert item.stock_reserved_at is not None
    assert inventory.quantity == 0
    assert inventory.status == InventoryStatus.RESERVED


def test_inventory_already_allocated_to_shop_is_blocked(client, session):
    inventory = _inventory(session)
    session.add(ShopListing(
        catalog_item_id=inventory.catalog_item_id, inventory_item_id=inventory.id,
        title="Kaido", price=Decimal("50"), quantity=1, status=ShopListingStatus.ACTIVE,
    ))
    session.commit()

    response = client.post(
        f"{_create_show(client)}/items",
        data={"inventory_item_id": inventory.id, "starting_price": "50"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    session.refresh(inventory)
    assert inventory.quantity == 1
    assert session.query(WhatnotShowItem).count() == 0


def test_show_cannot_start_empty_and_queue_cannot_settle_before_start(client, session):
    show_url = _create_show(client)
    assert client.post(f"{show_url}/start", follow_redirects=False).status_code == 409
    item = _queue(client, session, show_url, _inventory(session))
    response = client.post(
        f"{show_url}/items/{item.id}/outcome",
        data={"outcome": "sold", "final_price": "60", "buyer_handle": "buyer"},
        follow_redirects=False,
    )
    assert response.status_code == 409


def test_sold_item_creates_sale_customer_and_truthful_estimated_fee(client, session):
    inventory = _inventory(session)
    show_url = _create_show(client)
    item = _queue(client, session, show_url, inventory)
    _start(client, show_url)

    response = client.post(
        f"{show_url}/items/{item.id}/outcome",
        data={
            "outcome": "sold", "final_price": "60", "buyer_handle": "@BuyerOne",
            "whatnot_order_id": "WN-1001", "buyer_shipping": "10", "buyer_tax": "6",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    sale = session.scalars(select(Sale)).one()
    sale_item = session.scalars(select(SaleItem)).one()
    customer = session.scalars(select(Customer)).one()
    session.refresh(inventory)
    session.refresh(item)
    assert sale.gross_amount == Decimal("60")
    assert sale.fees_amount == Decimal("7.30")  # 8% of 60 + 2.9% of 76 + 0.30
    assert sale_item.inventory_item_id == inventory.id
    assert sale_item.cost_basis == Decimal("20")
    assert customer.platform_handles == {"whatnot": "buyerone"}
    assert sale.customer_id == customer.id
    assert item.sale_id == sale.id
    assert item.whatnot_order_id == "WN-1001"
    assert inventory.status == InventoryStatus.SOLD


def test_exact_seller_hub_fee_works_without_a_fee_rule(client, session):
    session.query(MarketplaceFeeRule).delete()
    session.commit()
    inventory = _inventory(session)
    show_url = _create_show(client)
    item = _queue(client, session, show_url, inventory)
    _start(client, show_url)

    response = client.post(
        f"{show_url}/items/{item.id}/outcome",
        data={"outcome": "sold", "final_price": "60", "actual_fees": "8.17", "buyer_handle": "buyer"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert session.scalars(select(Sale)).one().fees_amount == Decimal("8.17")


def test_sale_without_exact_fee_or_rule_is_blocked_not_recorded_as_zero(client, session):
    session.query(MarketplaceFeeRule).delete()
    session.commit()
    inventory = _inventory(session)
    show_url = _create_show(client)
    item = _queue(client, session, show_url, inventory)
    _start(client, show_url)

    response = client.post(
        f"{show_url}/items/{item.id}/outcome",
        data={"outcome": "sold", "final_price": "60", "buyer_handle": "buyer"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    session.refresh(item)
    assert item.outcome == WhatnotShowItemOutcome.PENDING
    assert session.query(Sale).count() == 0


@pytest.mark.parametrize("data", [
    {"outcome": "sold", "final_price": "0", "buyer_handle": "buyer"},
    {"outcome": "sold", "final_price": "not-money", "buyer_handle": "buyer"},
    {"outcome": "sold", "final_price": "10", "buyer_handle": ""},
    {"outcome": "unknown"},
])
def test_invalid_settlement_is_rejected(client, session, data):
    inventory = _inventory(session)
    show_url = _create_show(client)
    item = _queue(client, session, show_url, inventory)
    _start(client, show_url)
    assert client.post(f"{show_url}/items/{item.id}/outcome", data=data, follow_redirects=False).status_code == 400
    session.refresh(item)
    assert item.outcome == WhatnotShowItemOutcome.PENDING


def test_retried_sold_post_does_not_create_duplicate_sale(client, session):
    inventory = _inventory(session)
    show_url = _create_show(client)
    item = _queue(client, session, show_url, inventory)
    _start(client, show_url)
    data = {"outcome": "sold", "final_price": "60", "buyer_handle": "buyer", "actual_fees": "7"}
    client.post(f"{show_url}/items/{item.id}/outcome", data=data, follow_redirects=False)
    client.post(f"{show_url}/items/{item.id}/outcome", data=data, follow_redirects=False)
    assert session.query(Sale).count() == 1
    assert session.query(SaleItem).count() == 1


def test_duplicate_order_id_does_not_settle_or_consume_second_item(client, session):
    first, second = _inventory(session, "1"), _inventory(session, "2")
    show_url = _create_show(client)
    first_item = _queue(client, session, show_url, first)
    second_item = _queue(client, session, show_url, second)
    _start(client, show_url)
    data = {
        "outcome": "sold", "final_price": "60", "buyer_handle": "buyer",
        "actual_fees": "7", "whatnot_order_id": "WN-DUPLICATE",
    }
    assert client.post(
        f"{show_url}/items/{first_item.id}/outcome", data=data, follow_redirects=False,
    ).status_code == 303

    response = client.post(
        f"{show_url}/items/{second_item.id}/outcome", data=data, follow_redirects=False,
    )

    assert response.status_code == 409
    session.refresh(second)
    session.refresh(second_item)
    assert second_item.outcome == WhatnotShowItemOutcome.PENDING
    assert second_item.sale_id is None
    assert second.quantity == 0
    assert second.status == InventoryStatus.RESERVED
    assert session.query(Sale).count() == 1
    assert session.query(SaleItem).count() == 1


def test_buyer_handle_variants_reuse_existing_customer(client, session):
    customer = Customer(
        id="existing-customer", display_name="Buyer One",
        platform_handles={"whatnot": "@BuyerOne"}, segment="Whatnot",
    )
    session.add(customer)
    session.commit()
    inventory = _inventory(session)
    show_url = _create_show(client)
    item = _queue(client, session, show_url, inventory)
    _start(client, show_url)

    response = client.post(
        f"{show_url}/items/{item.id}/outcome",
        data={
            "outcome": "sold", "final_price": "60", "buyer_handle": "buyerone",
            "actual_fees": "7",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert session.query(Customer).count() == 1
    assert session.scalars(select(Sale)).one().customer_id == customer.id


def test_exact_fee_still_requires_canonical_whatnot_marketplace(client, session):
    session.query(MarketplaceFeeRule).delete()
    session.query(Marketplace).delete()
    session.commit()
    inventory = _inventory(session)
    show_url = _create_show(client)
    item = _queue(client, session, show_url, inventory)
    _start(client, show_url)

    response = client.post(
        f"{show_url}/items/{item.id}/outcome",
        data={
            "outcome": "sold", "final_price": "60", "buyer_handle": "buyer",
            "actual_fees": "7",
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "configure the Whatnot marketplace" in response.text
    session.refresh(item)
    assert item.outcome == WhatnotShowItemOutcome.PENDING
    assert session.query(Sale).count() == 0


def test_inventory_search_filter_applies_before_result_limit(session):
    matching = _inventory(session, "matching")
    _inventory(session, "hidden")

    rows = _eligible_inventory(session, "matching")

    assert [inventory.id for inventory, _catalog, _location in rows] == [matching.id]


def test_unsold_item_restores_inventory_once(client, session):
    inventory = _inventory(session)
    show_url = _create_show(client)
    item = _queue(client, session, show_url, inventory)
    _start(client, show_url)
    url = f"{show_url}/items/{item.id}/outcome"
    client.post(url, data={"outcome": "unsold"}, follow_redirects=False)
    client.post(url, data={"outcome": "unsold"}, follow_redirects=False)
    session.refresh(inventory)
    session.refresh(item)
    assert inventory.quantity == 1
    assert inventory.status == InventoryStatus.AVAILABLE
    assert item.stock_released_at is not None


def test_giveaway_consumes_inventory_without_creating_sale(client, session):
    inventory = _inventory(session)
    show_url = _create_show(client)
    item = _queue(client, session, show_url, inventory)
    _start(client, show_url)
    client.post(f"{show_url}/items/{item.id}/outcome", data={"outcome": "giveaway"}, follow_redirects=False)
    session.refresh(inventory)
    assert inventory.quantity == 0
    assert inventory.status == InventoryStatus.GIVEAWAY
    assert session.query(Sale).count() == 0


def test_cancelling_show_releases_all_pending_inventory(client, session):
    first, second = _inventory(session, "1"), _inventory(session, "2")
    show_url = _create_show(client)
    _queue(client, session, show_url, first)
    _queue(client, session, show_url, second)
    response = client.post(f"{show_url}/cancel", follow_redirects=False)
    assert response.status_code == 303
    session.refresh(first)
    session.refresh(second)
    show = session.get(WhatnotShow, show_url.rsplit("/", 1)[-1])
    assert show.status == WhatnotShowStatus.CANCELLED
    assert first.quantity == second.quantity == 1
    assert all(i.outcome == WhatnotShowItemOutcome.UNSOLD for i in session.scalars(select(WhatnotShowItem)))


def test_show_completion_requires_every_item_settled_and_records_metrics(client, session):
    inventory = _inventory(session)
    show_url = _create_show(client)
    item = _queue(client, session, show_url, inventory)
    _start(client, show_url)
    assert client.post(f"{show_url}/complete", follow_redirects=False).status_code == 409
    client.post(f"{show_url}/items/{item.id}/outcome", data={"outcome": "unsold"}, follow_redirects=False)
    response = client.post(
        f"{show_url}/complete", data={"viewer_count": "42", "follower_count": "100"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    show = session.get(WhatnotShow, show_url.rsplit("/", 1)[-1])
    assert show.status == WhatnotShowStatus.COMPLETED
    assert show.viewer_count == 42
    assert show.follower_count == 100
    assert show.completed_at is not None


def test_detail_warns_when_fee_rule_is_missing(client, session):
    session.query(MarketplaceFeeRule).delete()
    session.commit()
    body = client.get(_create_show(client)).text
    assert "No active Whatnot fee rule" in body
    assert "invented zero fee" in body


def test_whatnot_pages_require_login():
    app.dependency_overrides.clear()
    response = TestClient(app).get("/whatnot", follow_redirects=False)
    assert response.status_code in (302, 303, 401)
