from sqlalchemy import func, select

from automation_control.models import (
    CardSet,
    CatalogItem,
    CatalogItemType,
    InventoryItem,
    PricingExternalId,
    PricingProviderCategory,
    PricingProviderGroup,
    PricingProviderProduct,
    TcgCard,
)
from automation_control.pricing.registry import ensure_provider_rows
from automation_control.pricing.tcgcsv_mapping import resolve_tcgcsv_ids


def _provider_catalog(session):
    ensure_provider_rows(session)
    category = PricingProviderCategory(
        provider_key="tcgcsv", provider_category_id="68", name="One Piece Card Game",
        display_name="One Piece Card Game", raw={},
    )
    session.add(category)
    session.flush()
    group = PricingProviderGroup(
        provider_key="tcgcsv", category_id=category.id, provider_group_id="999",
        name="Test Set", abbreviation="OP99", raw={},
    )
    session.add(group)
    session.flush()
    return group


def _card(session, *, item_id, number, name, language="English", owned=True):
    if session.get(CardSet, "op-999") is None:
        session.add(CardSet(id="op-999", name="Test Set", code="OP99", game="one_piece"))
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name))
    session.add(TcgCard(
        catalog_item_id=item_id, game="one_piece", set_id="op-999", set_code="OP99",
        number=number, character=name, language=language,
    ))
    if owned:
        session.add(InventoryItem(catalog_item_id=item_id, quantity=1))
    session.flush()


def test_direct_tcgplayer_id_maps_without_fuzzy_search(session):
    group = _provider_catalog(session)
    product = PricingProviderProduct(
        provider_key="tcgcsv", group_id=group.id, provider_product_id="12345",
        name="Parallel Card", product_type="single", card_number="OP99-001", raw={},
    )
    session.add(product)
    _card(session, item_id="tcgp-12345", number="OP99-001", name="Parallel Card")

    result = resolve_tcgcsv_ids(session)
    mapping = session.scalar(select(PricingExternalId))
    assert result.mapped == 1
    assert mapping is not None
    assert mapping.provider_product_id == "12345"
    assert mapping.match_method == "exact_provider_id"


def test_exact_set_number_and_name_maps_unique_product(session):
    group = _provider_catalog(session)
    session.add(PricingProviderProduct(
        provider_key="tcgcsv", group_id=group.id, provider_product_id="222",
        name="Monkey D. Luffy", clean_name="Monkey D Luffy", product_type="single",
        card_number="OP99-002", raw={},
    ))
    _card(session, item_id="one-piece-op99-002", number="OP99-002", name="Monkey D. Luffy")

    result = resolve_tcgcsv_ids(session)
    assert result.mapped == 1
    assert session.scalar(select(PricingExternalId.provider_product_id)) == "222"


def test_exact_name_keeps_regular_separate_from_parallel(session):
    group = _provider_catalog(session)
    session.add_all([
        PricingProviderProduct(
            provider_key="tcgcsv", group_id=group.id, provider_product_id="regular",
            name="Nami", product_type="single", card_number="OP99-003", raw={},
        ),
        PricingProviderProduct(
            provider_key="tcgcsv", group_id=group.id, provider_product_id="alt",
            name="Nami (Alternate Art)", product_type="single", card_number="OP99-003", raw={},
        ),
    ])
    _card(session, item_id="one-piece-op99-003", number="OP99-003", name="Nami")

    result = resolve_tcgcsv_ids(session)
    assert result.mapped == 1
    assert session.scalar(select(PricingExternalId.provider_product_id)) == "regular"


def test_unidentified_parallel_stays_ambiguous(session):
    group = _provider_catalog(session)
    session.add_all([
        PricingProviderProduct(
            provider_key="tcgcsv", group_id=group.id, provider_product_id="alt-a",
            name="Nami (Alternate Art A)", product_type="single", card_number="OP99-006", raw={},
        ),
        PricingProviderProduct(
            provider_key="tcgcsv", group_id=group.id, provider_product_id="alt-b",
            name="Nami (Alternate Art B)", product_type="single", card_number="OP99-006", raw={},
        ),
    ])
    _card(session, item_id="one-piece-op99-006", number="OP99-006", name="Nami (Alternate Art)")

    result = resolve_tcgcsv_ids(session)
    assert result.ambiguous == 1
    assert session.scalar(select(func.count()).select_from(PricingExternalId)) == 0


def test_pokemon_style_set_prefix_total_and_code_zero_are_exact(session):
    ensure_provider_rows(session)
    category = PricingProviderCategory(
        provider_key="tcgcsv", provider_category_id="3", name="Pokemon",
        display_name="Pokemon", raw={},
    )
    session.add(category)
    session.flush()
    group = PricingProviderGroup(
        provider_key="tcgcsv", category_id=category.id, provider_group_id="888",
        name="SV08: Surging Sparks", abbreviation="SV08", raw={},
    )
    session.add(group)
    session.flush()
    session.add(PricingProviderProduct(
        provider_key="tcgcsv", group_id=group.id, provider_product_id="8881",
        name="Alolan Diglett", product_type="single", card_number="012/191", raw={},
    ))
    session.add(CardSet(id="sv8", name="Surging Sparks", code="sv8", game="pokemon"))
    session.add(CatalogItem(
        id="sv8-12", item_type=CatalogItemType.TCG_CARD, name="Alolan Diglett",
    ))
    session.add(TcgCard(
        catalog_item_id="sv8-12", game="pokemon", set_id="sv8", set_code="sv8",
        number="12", character="Alolan Diglett", language="English",
    ))
    session.add(InventoryItem(catalog_item_id="sv8-12", quantity=1))
    session.flush()

    result = resolve_tcgcsv_ids(session)
    assert result.mapped == 1
    assert session.scalar(select(PricingExternalId.provider_product_id)) == "8881"


def test_japanese_card_never_silently_maps_to_english_product(session):
    group = _provider_catalog(session)
    session.add(PricingProviderProduct(
        provider_key="tcgcsv", group_id=group.id, provider_product_id="333",
        name="Zoro", product_type="single", card_number="OP99-004", raw={},
    ))
    _card(
        session, item_id="one-piece-jp-op99-004", number="OP99-004",
        name="Zoro", language="Japanese",
    )

    result = resolve_tcgcsv_ids(session)
    assert result.unmatched == 1
    assert session.scalar(select(func.count()).select_from(PricingExternalId)) == 0


def test_mapping_defaults_to_owned_inventory_only(session):
    group = _provider_catalog(session)
    session.add(PricingProviderProduct(
        provider_key="tcgcsv", group_id=group.id, provider_product_id="444",
        name="Unowned", product_type="single", card_number="OP99-005", raw={},
    ))
    _card(
        session, item_id="one-piece-op99-005", number="OP99-005",
        name="Unowned", owned=False,
    )

    result = resolve_tcgcsv_ids(session)
    assert result.considered == 0
    assert session.scalar(select(func.count()).select_from(PricingExternalId)) == 0
