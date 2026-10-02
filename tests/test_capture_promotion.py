from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from automation_control.capture_promotion import infer_unambiguous_variant, promote_approved_card
from automation_control.models import (
    CardCaptureStatus, CardSet, CardVariant, CapturedCard, CatalogItem, CatalogItemType,
    CapturePromotion, CapturePromotionStatus, EbayExportBatch, EbayExportItem,
    EbayExportItemStatus, InventoryItem, PricingStatus, ShopListing,
    ShopListingStatus, TcgCard,
)


def _settings():
    return SimpleNamespace(
        r2_endpoint_url="https://r2.example",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
        r2_bucket_name="bucket",
    )


def _catalogue(session, *, item_id="sv-test-1", name="Pikachu", number="025/165"):
    session.add(CardSet(id="sv-test", name="Test Set", code="TST", game="pokemon"))
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name))
    session.add(TcgCard(catalog_item_id=item_id, game="pokemon", set_id="sv-test",
                        set_code="TST", number=number, character=name, language="English"))
    session.flush()


def _capture(**overrides):
    values = dict(
        id="capture-1", front_image_path="captured_cards/one/front.jpg",
        character="Pikachu", set_name="Test Set", card_number="025/165",
        language="English", status=CardCaptureStatus.REVIEWED,
        pricing_status=PricingStatus.PRICE_APPROVED,
        approved_price=Decimal("4.50"), variant=CardVariant.NORMAL, ai_raw_response={},
    )
    values.update(overrides)
    return CapturedCard(**values)


@patch("automation_control.capture_promotion.load_pipeline_config")
@patch("automation_control.capture_promotion.upload_card_image", return_value="https://img.example/card.jpg")
@patch("automation_control.capture_promotion.r2_is_configured", return_value=True)
def test_safe_approval_creates_inventory_and_active_bulk_listing(_configured, _upload, config, session):
    config.return_value = SimpleNamespace(images=SimpleNamespace(base_url="https://img.example"))
    _catalogue(session)
    card = _capture()
    session.add(card)
    session.flush()

    result = promote_approved_card(session, card, _settings())
    session.commit()

    assert result.promoted is True
    inventory = session.query(InventoryItem).one()
    listing = session.query(ShopListing).one()
    promotion = session.query(CapturePromotion).one()
    assert inventory.quantity == 1
    assert listing.status == ShopListingStatus.ACTIVE
    assert listing.price == Decimal("4.50")
    assert listing.inventory_item_id == inventory.id
    assert promotion.status == CapturePromotionStatus.PROMOTED


@patch("automation_control.capture_promotion.load_pipeline_config")
@patch("automation_control.capture_promotion.upload_card_image", return_value="https://img.example/card.jpg")
@patch("automation_control.capture_promotion.r2_is_configured", return_value=True)
def test_successful_promotion_is_idempotent(_configured, _upload, config, session):
    config.return_value = SimpleNamespace(images=SimpleNamespace(base_url="https://img.example"))
    _catalogue(session)
    card = _capture()
    session.add(card)
    session.flush()
    promote_approved_card(session, card, _settings())
    session.commit()

    again = promote_approved_card(session, card, _settings())
    session.commit()

    assert again.promoted is True
    assert session.query(InventoryItem).one().quantity == 1
    assert session.query(ShopListing).count() == 1


@patch("automation_control.capture_promotion.load_pipeline_config")
@patch("automation_control.capture_promotion.upload_card_image", return_value="https://img.example/own-card.jpg")
@patch("automation_control.capture_promotion.r2_is_configured", return_value=True)
def test_safe_approval_reuses_an_existing_draft_instead_of_creating_a_duplicate(
    _configured, _upload, config, session,
):
    config.return_value = SimpleNamespace(images=SimpleNamespace(base_url="https://img.example"))
    _catalogue(session)
    draft = ShopListing(
        id="existing-draft", catalog_item_id="sv-test-1", title="Old title",
        condition="Near Mint · Normal", price=Decimal("3.00"), quantity=1,
        status=ShopListingStatus.DRAFT, image_url="reference.jpg",
    )
    card = _capture(approved_price=Decimal("4.50"))
    session.add_all([draft, card])
    session.flush()

    result = promote_approved_card(session, card, _settings())
    session.commit()

    assert result.promoted is True
    assert result.shop_listing_id == "existing-draft"
    assert session.query(ShopListing).count() == 1
    assert draft.status == ShopListingStatus.ACTIVE
    assert draft.price == Decimal("4.50")
    assert draft.image_url == "https://img.example/own-card.jpg"
    assert draft.inventory_item_id == session.query(InventoryItem).one().id


def test_shared_uncropped_photo_records_issue_without_touching_inventory(session):
    _catalogue(session)
    card = _capture()
    sibling = _capture(id="capture-2", character="Raichu")
    session.add_all([card, sibling])
    session.flush()

    result = promote_approved_card(session, card, _settings())
    session.commit()

    assert result.promoted is False
    assert "card-specific crop" in result.issue
    assert session.query(InventoryItem).count() == 0
    assert session.query(ShopListing).count() == 0
    assert session.query(CapturePromotion).one().status == CapturePromotionStatus.NEEDS_REVIEW


def test_already_exported_listing_becomes_stock_revision_issue(session):
    _catalogue(session)
    inventory = InventoryItem(catalog_item_id="sv-test-1", quantity=1)
    session.add(inventory)
    session.flush()
    listing = ShopListing(catalog_item_id="sv-test-1", inventory_item_id=inventory.id,
                          title="Pikachu", price=Decimal("4.00"), quantity=1,
                          status=ShopListingStatus.ACTIVE)
    session.add(listing)
    session.flush()
    batch = EbayExportBatch(filename="x.csv", row_count=1, selected_count=1)
    session.add(batch)
    session.flush()
    session.add(EbayExportItem(batch_id=batch.id, sku=listing.id, kind="single",
                               status=EbayExportItemStatus.CONFIRMED))
    card = _capture()
    session.add(card)
    session.flush()

    result = promote_approved_card(session, card, _settings())
    session.commit()

    assert result.promoted is False
    assert "stock-quantity revision" in result.issue
    assert session.get(InventoryItem, inventory.id).quantity == 1
    assert session.get(ShopListing, listing.id).quantity == 1


def test_missing_catalogue_match_is_a_visible_issue(session):
    card = _capture(character="Not A Real Card")
    session.add(card)
    session.flush()

    result = promote_approved_card(session, card, _settings())
    session.commit()

    assert result.promoted is False
    assert "No catalogue match" in result.issue
    assert session.query(InventoryItem).count() == 0


def test_missing_printing_is_an_issue_before_inventory_changes(session):
    _catalogue(session)
    card = _capture(variant=None)
    session.add(card)
    session.flush()

    result = promote_approved_card(session, card, _settings())
    session.commit()

    assert result.promoted is False
    assert "physical printing" in result.issue
    assert session.query(InventoryItem).count() == 0


def test_catalogue_can_infer_one_physical_printing(session):
    _catalogue(session)
    from automation_control.models import CatalogCard
    session.add(CatalogCard(
        id="sv-test-1", set_id="sv-test", number="025/165", name="Pikachu",
        raw_prices={"tcgplayer": {"prices": {"holofoil": {"market": 4.5}}}},
    ))
    session.flush()

    assert infer_unambiguous_variant(session, "sv-test-1") == CardVariant.HOLO


def test_catalogue_does_not_guess_when_two_printings_exist(session):
    _catalogue(session)
    from automation_control.models import CatalogCard
    session.add(CatalogCard(
        id="sv-test-1", set_id="sv-test", number="025/165", name="Pikachu",
        raw_prices={"tcgplayer": {"prices": {
            "normal": {"market": 1.0}, "reverseHolofoil": {"market": 2.0},
        }}},
    ))
    session.flush()

    assert infer_unambiguous_variant(session, "sv-test-1") is None
