from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from automation_control.capture_promotion import (infer_unambiguous_variant,
                                                  promote_approved_card,
                                                  resolve_catalogue_card)
from automation_control.models import (
    CardCaptureStatus, CardSet, CardVariant, CapturedCard, CatalogItem, CatalogItemType,
    CapturePromotion, CapturePromotionStatus, EbayExportBatch, EbayExportItem,
    EbayExportItemStatus, InventoryItem, PricingStatus, ShopListing,
    PricingExternalId, PricingProvider, PricingProviderCategory,
    PricingProviderGroup, PricingProviderProduct, ShopListingStatus, TcgCard,
)


def _settings():
    return SimpleNamespace(
        r2_endpoint_url="https://r2.example",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
        r2_bucket_name="bucket",
    )


def _catalogue(session, *, item_id="sv-test-1", name="Pikachu", number="025/165", set_code="TST"):
    session.add(CardSet(id="sv-test", name="Test Set", code="TST", game="pokemon"))
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name))
    session.add(TcgCard(catalog_item_id=item_id, game="pokemon", set_id="sv-test",
                        set_code=set_code, number=number, character=name, language="English"))
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

    assert result.promoted is True
    assert "Update the live eBay quantity to 2" in result.issue
    assert session.get(InventoryItem, inventory.id).quantity == 2
    assert session.get(ShopListing, listing.id).quantity == 2
    assert session.query(CapturePromotion).one().status == CapturePromotionStatus.REVISION_REQUIRED

    # Re-approving or refreshing the page cannot count the same physical
    # card twice while the operator is still updating eBay.
    again = promote_approved_card(session, card, _settings())
    session.commit()
    assert again.promoted is True
    assert session.get(InventoryItem, inventory.id).quantity == 2


def test_missing_catalogue_match_is_a_visible_issue(session):
    card = _capture(character="Not A Real Card")
    session.add(card)
    session.flush()

    result = promote_approved_card(session, card, _settings())
    session.commit()

    assert result.promoted is False
    assert "No catalogue match" in result.issue
    assert session.query(InventoryItem).count() == 0


def test_catalogue_identity_ignores_name_punctuation_and_accepts_set_code(session):
    _catalogue(session, name="Terrakion-EX", number="071/124")
    card = _capture(character="Terrakion EX", card_number="71/124", set_name="TST")
    session.add(card)
    session.flush()

    resolved = resolve_catalogue_card(session, card)

    assert not isinstance(resolved, str)
    assert resolved[0].id == "sv-test-1"


def test_catalogue_identity_accepts_japanese_name_with_english_translation(session):
    _catalogue(
        session,
        item_id="dbsd-sdvp-029",
        name="Android 17",
        number="SDVP-029",
    )
    card = _capture(
        character="人造人間17号 (Android 17)",
        card_number="SDVP-029",
        set_name="Test Set",
        language="Japanese",
    )
    session.add(card)
    session.flush()

    resolved = resolve_catalogue_card(session, card)

    assert not isinstance(resolved, str)
    assert resolved[0].id == "dbsd-sdvp-029"


def test_unique_registered_unnumbered_card_can_resolve_without_fake_ocr_number(session):
    _catalogue(
        session,
        item_id="sdbh-hero-avatar-card",
        name="Hero Avatar Card",
        number="n/a",
        set_code="HAC",
    )
    card = _capture(
        character="ヒーローアバターカード (Hero Avatar Card)",
        card_number="",
        set_name="Test Set",
        language="Japanese",
    )
    session.add(card)
    session.flush()

    resolved = resolve_catalogue_card(session, card)

    assert not isinstance(resolved, str)
    assert resolved[0].id == "sdbh-hero-avatar-card"


def test_missing_number_stays_held_when_registry_card_should_be_numbered(session):
    _catalogue(session, item_id="turles", name="Turles", number="DBHA10-08")
    card = _capture(character="Turles", card_number="", set_name="Test Set", language="Japanese")
    session.add(card)
    session.flush()

    resolved = resolve_catalogue_card(session, card)

    assert isinstance(resolved, str)
    assert "no unique unnumbered registry record" in resolved.casefold()


def test_catalogue_identity_uses_unique_full_code_when_imported_name_is_placeholder(session):
    _catalogue(
        session,
        item_id="sdbh-um9-019",
        name="SUPER DRAGON BALL HEROES UM9-019",
        number="019",
        set_code="UM9",
    )
    card = _capture(
        character="ジャネンバ (Janemba)",
        card_number="UM9-019",
        set_name="Super Dragon Ball Heroes UM9",
        language="Japanese",
    )
    session.add(card)
    session.flush()

    resolved = resolve_catalogue_card(session, card)

    assert not isinstance(resolved, str)
    assert resolved[0].id == "sdbh-um9-019"


def test_catalogue_identity_corrects_one_zero_as_d_ocr_error_with_same_name_and_set(session):
    _catalogue(
        session,
        item_id="dbsd-sdv1-070",
        name="Vegeta: DA",
        number="070",
        set_code="SDV1",
    )
    card = _capture(
        character="ベジータ:DA (Vegeta:DA)",
        card_number="SDV1-07D",
        set_name="Dragon Ball Super Divers",
        language="Japanese",
    )
    session.add(card)
    session.flush()

    resolved = resolve_catalogue_card(session, card)

    assert not isinstance(resolved, str)
    assert resolved[0].id == "dbsd-sdv1-070"


def test_catalogue_identity_does_not_apply_general_fuzzy_number_matching(session):
    _catalogue(
        session,
        item_id="dbsd-sdv1-070",
        name="Vegeta: DA",
        number="070",
        set_code="SDV1",
    )
    card = _capture(
        character="Vegeta: DA",
        card_number="SDV1-07X",
        set_name="Dragon Ball Super Divers",
        language="Japanese",
    )
    session.add(card)
    session.flush()

    resolved = resolve_catalogue_card(session, card)

    assert isinstance(resolved, str)
    assert "No catalogue match" in resolved


def test_local_provider_catalogue_fills_a_missing_canonical_promo(session):
    session.add(PricingProvider(
        key="tcgcsv", display_name="TCGCSV", enabled=True,
        capabilities=["catalogue"], configuration={},
    ))
    category = PricingProviderCategory(
        provider_key="tcgcsv", provider_category_id="3", name="Pokemon", raw={},
    )
    session.add(category)
    session.flush()
    group = PricingProviderGroup(
        provider_key="tcgcsv", category_id=category.id, provider_group_id="svp",
        name="Scarlet & Violet Promo Cards", abbreviation="SVP", raw={},
    )
    session.add(group)
    session.flush()
    session.add(PricingProviderProduct(
        provider_key="tcgcsv", group_id=group.id, provider_product_id="647306",
        name="Gothitelle - 211", clean_name="Gothitelle 211", product_type="single",
        card_number="211", rarity="Promo", raw={},
    ))
    session.flush()
    card = _capture(character="Gothitelle", card_number="211",
                    set_name="Scarlet & Violet Black Star Promos (SVP)")
    session.add(card)
    session.flush()

    resolved = resolve_catalogue_card(session, card)

    assert not isinstance(resolved, str)
    item, tcg = resolved
    assert item.id == "tcgp-647306"
    assert item.name == "Gothitelle"
    assert tcg.set_code == "SVP"
    assert session.scalar(select(PricingExternalId)).provider_product_id == "647306"


def test_local_provider_catalogue_accepts_recent_full_code_name_suffix(session):
    session.add(PricingProvider(
        key="tcgcsv", display_name="TCGCSV", enabled=True,
        capabilities=["catalogue"], configuration={},
    ))
    category = PricingProviderCategory(
        provider_key="tcgcsv", provider_category_id="dragon-ball-fw",
        name="Dragon Ball Super: Fusion World", raw={},
    )
    session.add(category)
    session.flush()
    group = PricingProviderGroup(
        provider_key="tcgcsv", category_id=category.id, provider_group_id="sb02",
        name="Manga Booster 02", abbreviation="SB02", raw={},
    )
    session.add(group)
    session.flush()
    session.add(PricingProviderProduct(
        provider_key="tcgcsv", group_id=group.id, provider_product_id="656985",
        name="King Piccolo - SB02-058", clean_name="King Piccolo - SB02-058",
        product_type="single", card_number="SB02-058", rarity="Super Rare", raw={},
    ))
    session.flush()
    card = _capture(
        character="King Piccolo", card_number="SB02-058",
        set_name="Fusion World SB02", rarity="SR",
    )

    resolved = resolve_catalogue_card(session, card)

    assert not isinstance(resolved, str)
    assert resolved[0].id == "tcgp-656985"
    assert resolved[0].name == "King Piccolo"


def test_local_provider_catalogue_uses_fusion_world_star_for_alternate_art(session):
    session.add(PricingProvider(
        key="tcgcsv", display_name="TCGCSV", enabled=True,
        capabilities=["catalogue"], configuration={},
    ))
    category = PricingProviderCategory(
        provider_key="tcgcsv", provider_category_id="dragon-ball-fw",
        name="Dragon Ball Super: Fusion World", raw={},
    )
    session.add(category)
    session.flush()
    group = PricingProviderGroup(
        provider_key="tcgcsv", category_id=category.id, provider_group_id="fb08",
        name="Saiyan's Pride", abbreviation="FB08", raw={},
    )
    session.add(group)
    session.flush()
    session.add_all([
        PricingProviderProduct(
            provider_key="tcgcsv", group_id=group.id, provider_product_id="666769",
            name="Bardock - FB08-112", clean_name="Bardock - FB08-112",
            product_type="single", card_number="FB08-112", rarity="Super Rare", raw={},
        ),
        PricingProviderProduct(
            provider_key="tcgcsv", group_id=group.id, provider_product_id="666770",
            name="Bardock - FB08-112 (Alternate Art)",
            clean_name="Bardock - FB08-112 (Alternate Art)", product_type="single",
            card_number="FB08-112", rarity="Super Rare", raw={},
        ),
    ])
    session.flush()
    card = _capture(
        character="Bardock", card_number="FB08-112",
        set_name="Fusion World FB08", rarity="SR★",
    )

    resolved = resolve_catalogue_card(session, card)

    assert not isinstance(resolved, str)
    assert resolved[0].id == "tcgp-666770"


@patch("automation_control.capture_promotion.load_pipeline_config")
@patch("automation_control.capture_promotion.upload_card_image", return_value="https://img.example/card.jpg")
@patch("automation_control.capture_promotion.r2_is_configured", return_value=True)
def test_condition_is_preserved_through_inventory_and_listing(_configured, _upload, config, session):
    config.return_value = SimpleNamespace(images=SimpleNamespace(base_url="https://img.example"))
    _catalogue(session)
    card = _capture(condition="Lightly Played")
    session.add(card)
    session.flush()

    promote_approved_card(session, card, _settings())
    session.commit()

    assert session.query(InventoryItem).one().condition == "Lightly Played"
    assert session.query(ShopListing).one().condition == "Lightly Played · Normal"


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
