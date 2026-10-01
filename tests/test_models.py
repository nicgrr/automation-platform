from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from automation_control.approvals import ApprovalStateError, decide_approval
from automation_control.models import (
    Approval,
    ApprovalStatus,
    CardPrice,
    CardSet,
    CardVariant,
    CatalogCard,
    CatalogItem,
    CatalogItemType,
    EbayCategoryMapping,
    EbayCredential,
    EbayImportedOrder,
    EbayListing,
    EbayListingLink,
    EbayListingLinkStatus,
    JobRun,
    JobStatus,
    MarketObservation,
    PriceRecommendation,
    RecommendationStatus,
    ShopListing,
    ShopListingStatus,
    ToolExecution,
    ToolExecutionStatus,
    User,
    InventoryItem,
)


def test_card_price_uses_catalogue_identity_with_legacy_alias(session):
    item = CatalogItem(id="generic-item", item_type=CatalogItemType.COLLECTIBLE,
                       name="Generic collectible")
    price = CardPrice(catalog_item_id=item.id, source="test", price=Decimal("12.34"),
                      currency="AUD")
    session.add_all([item, price])
    session.commit()

    assert price.catalog_item_id == item.id
    assert price.card_id == item.id  # compatibility during the retirement window


def test_legacy_inventory_card_write_populates_canonical_identity(session):
    session.add(CardSet(id="set-1", name="Test set"))
    session.add(CatalogItem(id="set-1-card-1", item_type=CatalogItemType.TCG_CARD,
                            name="Test card"))
    session.add(CatalogCard(id="set-1-card-1", set_id="set-1", number="1",
                            name="Test card"))
    holding = InventoryItem(card_id="set-1-card-1", variant=CardVariant.NORMAL,
                            condition="Near Mint", quantity=1)
    session.add(holding)
    session.commit()

    assert holding.catalog_item_id == "set-1-card-1"


def test_approval_can_only_be_decided_once(session):
    approval = Approval(requested_by="agent", action="future.write", arguments={})
    session.add(approval)
    session.commit()
    decide_approval(session, approval, approved=True, actor="owner")
    assert approval.status is ApprovalStatus.APPROVED

    try:
        decide_approval(session, approval, approved=False, actor="owner")
    except ApprovalStateError:
        pass
    else:
        raise AssertionError("second decision was accepted")


def test_job_defaults_to_pending(session):
    job = JobRun(job_type="grocery.snapshot", arguments={})
    session.add(job)
    session.commit()
    assert job.status is JobStatus.PENDING


def test_phase_1a_schema_records_ebay_observations_and_recommendations(session):
    user = User(username="owner")
    listing = EbayListing(
        ebay_listing_id="sandbox-1",
        sku="SKU-1",
        title="Test item",
        currency="AUD",
        current_price_minor=1000,
        listing_status="active",
    )
    session.add_all([user, listing])
    session.flush()
    observation = MarketObservation(
        listing_id=listing.id,
        query="test item",
        source_item_id="market-1",
        title="Comparable item",
        currency="AUD",
        price_minor=1200,
    )
    recommendation = PriceRecommendation(
        listing_id=listing.id,
        currency="AUD",
        current_price_minor=1000,
        recommended_price_minor=1100,
        rationale="Sandbox evidence only",
    )
    execution = ToolExecution(
        correlation_id="00000000-0000-0000-0000-000000000001",
        principal="agent:pricing",
        tool_name="recommend_ebay_price",
    )
    session.add_all([observation, recommendation, execution])
    session.commit()
    assert recommendation.status is RecommendationStatus.PROPOSED
    assert execution.status is ToolExecutionStatus.REQUESTED


def _make_shop_listing(session, listing_id="listing-1"):
    item = CatalogItem(id=f"item-{listing_id}", item_type=CatalogItemType.TCG_CARD, name="Test Card")
    session.add(item)
    session.flush()
    listing = ShopListing(id=listing_id, catalog_item_id=item.id, title="Test Card #1",
                          price=Decimal("9.99"), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE)
    session.add(listing)
    session.flush()
    return listing


def test_ebay_listing_link_round_trips_and_defaults_to_draft(session):
    listing = _make_shop_listing(session)
    link = EbayListingLink(shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU")
    session.add(link)
    session.commit()
    assert link.status is EbayListingLinkStatus.DRAFT
    assert link.sku == listing.id


def test_ebay_listing_link_is_unique_per_shop_listing(session):
    listing = _make_shop_listing(session)
    session.add(EbayListingLink(shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU"))
    session.commit()
    session.add(EbayListingLink(shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU"))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
    else:
        raise AssertionError("a second EbayListingLink for the same shop_listing_id was accepted")


def test_ebay_imported_order_is_unique_per_ebay_order_id(session):
    session.add(EbayImportedOrder(ebay_order_id="ord-1", sku="listing-1", quantity=1, marketplace_id="EBAY_AU"))
    session.commit()
    session.add(EbayImportedOrder(ebay_order_id="ord-1", sku="listing-1", quantity=1, marketplace_id="EBAY_AU"))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
    else:
        raise AssertionError("a duplicate ebay_order_id was accepted")


def test_ebay_imported_order_defaults_to_unapplied(session):
    order = EbayImportedOrder(ebay_order_id="ord-2", sku="listing-1", quantity=2, marketplace_id="EBAY_AU")
    session.add(order)
    session.commit()
    assert order.applied_at is None


def test_ebay_category_mapping_is_unique_per_marketplace_type_game(session):
    session.add(EbayCategoryMapping(marketplace_id="EBAY_AU", catalog_item_type="tcg_card", game="pokemon", ebay_category_id="183454"))
    session.commit()
    session.add(EbayCategoryMapping(marketplace_id="EBAY_AU", catalog_item_type="tcg_card", game="pokemon", ebay_category_id="999999"))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
    else:
        raise AssertionError("a duplicate (marketplace, item_type, game) mapping was accepted")


def test_ebay_category_mapping_allows_a_game_none_fallback_row(session):
    session.add(EbayCategoryMapping(marketplace_id="EBAY_AU", catalog_item_type="tcg_card", game=None, ebay_category_id="183454"))
    session.add(EbayCategoryMapping(marketplace_id="EBAY_AU", catalog_item_type="tcg_card", game="pokemon", ebay_category_id="183050"))
    session.commit()  # both coexist -- NULL doesn't collide with a real value under this UNIQUE


def test_ebay_credential_has_a_separate_order_sync_cursor(session):
    credential = EbayCredential(
        id=1, encrypted_access_token=b"x", encrypted_refresh_token=b"y",
        access_token_expires_at=datetime.now(UTC), scopes="sell.inventory",
    )
    session.add(credential)
    session.commit()
    assert credential.last_order_sync_at is None
    assert credential.last_successful_sync_at is None
