"""scripts/audit_inventory_vs_listings.py: a read-only recurring check
formalizing four real incidents found and fixed by hand on 2026-09-28/29
(sold-but-available inventory, stale active listings, duplicate active
listings, and language-mixed variation listings)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import (
    CardVariant,
    CatalogItem,
    CatalogItemType,
    EbayExportBatch,
    EbayExportItem,
    EbayExportItemStatus,
    EbayImportedOrder,
    InventoryItem,
    InventoryStatus,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)
from scripts.audit_inventory_vs_listings import (
    check_active_listing_gone_inventory,
    check_duplicate_active_listings,
    check_language_mixed_variation_listings,
    check_sold_but_available,
    main,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def make_inventory(session, *, status=InventoryStatus.AVAILABLE, quantity=1):
    catalog = CatalogItem(id=str(uuid.uuid4()), item_type=CatalogItemType.TCG_CARD,
                          name="Audit fixture item")
    item = InventoryItem(id=str(uuid.uuid4()), catalog_item_id=catalog.id,
                         status=status, quantity=quantity)
    session.add_all([catalog, item])
    session.commit()
    return item


def make_listing(session, *, inventory_item_id=None, status=ShopListingStatus.ACTIVE,
                 title="Test Card", price="5.00", catalog_item_id=None):
    if catalog_item_id is None:
        catalog = CatalogItem(id=str(uuid.uuid4()), item_type=CatalogItemType.TCG_CARD, name=title)
        session.add(catalog)
        session.commit()
        catalog_item_id = catalog.id
    listing = ShopListing(id=str(uuid.uuid4()), catalog_item_id=catalog_item_id, title=title, price=price,
                          quantity=1, status=status, inventory_item_id=inventory_item_id)
    session.add(listing)
    session.commit()
    return listing


def test_a_clean_database_has_no_findings(session):
    assert check_sold_but_available(session) == []
    assert check_active_listing_gone_inventory(session) == []
    assert check_duplicate_active_listings(session) == []
    assert check_language_mixed_variation_listings(session) == []


def test_catches_a_sold_order_whose_inventory_is_still_available(session):
    inv = make_inventory(session, status=InventoryStatus.AVAILABLE)
    listing = make_listing(session, inventory_item_id=inv.id, title="Charizard #4")
    session.add(EbayImportedOrder(
        ebay_order_id="ORD-1", sku=listing.id, quantity=1, marketplace_id="EBAY_AU",
        observed_at=datetime.now(UTC), applied_at=datetime.now(UTC),
        shop_listing_id=listing.id, raw_snapshot={},
    ))
    session.commit()

    findings = check_sold_but_available(session)
    assert len(findings) == 1
    assert "Charizard #4" in findings[0]
    assert "ORD-1" in findings[0]


def test_an_unapplied_order_is_not_a_finding(session):
    inv = make_inventory(session, status=InventoryStatus.AVAILABLE)
    listing = make_listing(session, inventory_item_id=inv.id)
    session.add(EbayImportedOrder(
        ebay_order_id="ORD-1", sku=listing.id, quantity=1, marketplace_id="EBAY_AU",
        observed_at=datetime.now(UTC), applied_at=None,
        shop_listing_id=listing.id, raw_snapshot={},
    ))
    session.commit()

    assert check_sold_but_available(session) == []


def test_catches_an_active_listing_whose_inventory_already_sold(session):
    inv = make_inventory(session, status=InventoryStatus.SOLD, quantity=0)
    listing = make_listing(session, inventory_item_id=inv.id, status=ShopListingStatus.ACTIVE,
                           title="Pikachu #25")

    findings = check_active_listing_gone_inventory(session)
    assert len(findings) == 1
    assert "Pikachu #25" in findings[0]
    assert "sold" in findings[0]


def test_catches_an_active_listing_pointing_at_a_deleted_inventory_row(session):
    listing = make_listing(session, inventory_item_id="does-not-exist", title="Ghost Card")
    findings = check_active_listing_gone_inventory(session)
    assert len(findings) == 1
    assert "deleted row" in findings[0]


def test_a_withdrawn_listing_with_sold_inventory_is_not_a_finding(session):
    inv = make_inventory(session, status=InventoryStatus.SOLD, quantity=0)
    make_listing(session, inventory_item_id=inv.id, status=ShopListingStatus.WITHDRAWN)
    assert check_active_listing_gone_inventory(session) == []


def test_catches_two_active_listings_sharing_one_inventory_item(session):
    inv = make_inventory(session)
    make_listing(session, inventory_item_id=inv.id, title="Listing A", price="10.00")
    make_listing(session, inventory_item_id=inv.id, title="Listing B", price="12.00")

    findings = check_duplicate_active_listings(session)
    assert len(findings) == 1
    assert "2 active listings" in findings[0]
    assert "Listing A" in findings[0] and "Listing B" in findings[0]


def test_a_single_listing_per_inventory_item_is_not_a_finding(session):
    inv = make_inventory(session)
    make_listing(session, inventory_item_id=inv.id)
    assert check_duplicate_active_listings(session) == []


def make_catalog_card(session, *, language):
    catalog = CatalogItem(id=str(uuid.uuid4()), item_type=CatalogItemType.TCG_CARD, name=f"Card ({language})")
    session.add(catalog)
    session.commit()
    tcg = TcgCard(catalog_item_id=catalog.id, game="pokemon", variant=CardVariant.NORMAL, language=language)
    session.add(tcg)
    session.commit()
    return catalog


def test_catches_a_variation_parent_whose_children_mix_languages(session, monkeypatch):
    en_catalog = make_catalog_card(session, language="English")
    jp_catalog = make_catalog_card(session, language="Japanese")
    en_listing = make_listing(session, title="Card (English)", catalog_item_id=en_catalog.id)
    jp_listing = make_listing(session, title="Card (Japanese)", catalog_item_id=jp_catalog.id)

    batch = EbayExportBatch(filename="t.csv", row_count=1, selected_count=1)
    session.add(batch)
    session.commit()
    parent = EbayExportItem(batch_id=batch.id, kind="variation_parent", status=EbayExportItemStatus.CONFIRMED,
                            ebay_listing_id="12345", title="Complete Your Master Set", sku="parent-1")
    session.add(parent)
    session.commit()

    import scripts.audit_inventory_vs_listings as audit_mod

    monkeypatch.setattr(audit_mod, "_latest_export_items", lambda s: {parent.id: parent})
    monkeypatch.setattr(audit_mod, "_variation_group_children", lambda s, p: [
        type("Child", (), {"sku": en_listing.id})(),
        type("Child", (), {"sku": jp_listing.id})(),
    ])

    findings = check_language_mixed_variation_listings(session)
    assert len(findings) == 1
    assert "12345" in findings[0]
    assert "English" in findings[0] and "Japanese" in findings[0]


def test_main_returns_zero_and_prints_all_clean_when_nothing_found(session, capsys, monkeypatch):
    import scripts.audit_inventory_vs_listings as audit_mod

    monkeypatch.setattr(audit_mod, "SessionLocal", lambda: session)
    session.close = lambda: None
    session.__enter__ = lambda s=session: s
    session.__exit__ = lambda *a: None

    assert main() == 0
    assert "All checks clean." in capsys.readouterr().out


def test_main_returns_one_when_something_is_found(session, capsys, monkeypatch):
    import scripts.audit_inventory_vs_listings as audit_mod

    inv = make_inventory(session, status=InventoryStatus.SOLD, quantity=0)
    make_listing(session, inventory_item_id=inv.id, status=ShopListingStatus.ACTIVE)

    monkeypatch.setattr(audit_mod, "SessionLocal", lambda: session)
    session.close = lambda: None
    session.__enter__ = lambda s=session: s
    session.__exit__ = lambda *a: None

    assert main() == 1
    assert "total finding(s)" in capsys.readouterr().out


def test_a_finding_posts_a_discord_summary_but_a_clean_run_does_not(session, monkeypatch):
    import scripts.audit_inventory_vs_listings as audit_mod

    sent = []
    monkeypatch.setattr(audit_mod, "send_discord_message", lambda content, url: sent.append((content, url)))
    monkeypatch.setattr(audit_mod, "get_settings", lambda: type("S", (), {"discord_webhook_url": "https://example/hook"})())
    monkeypatch.setattr(audit_mod, "SessionLocal", lambda: session)
    session.close = lambda: None
    session.__enter__ = lambda s=session: s
    session.__exit__ = lambda *a: None

    main()
    assert sent == []

    inv = make_inventory(session, status=InventoryStatus.SOLD, quantity=0)
    make_listing(session, inventory_item_id=inv.id, status=ShopListingStatus.ACTIVE, title="Snorlax #143")

    main()
    assert len(sent) == 1
    assert "Snorlax #143" in sent[0][0]
    assert sent[0][1] == "https://example/hook"
