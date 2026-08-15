from decimal import Decimal

import pytest

from automation_control.listing_pipeline.config import (
    EbayFieldsConfig,
    ImagesConfig,
    PipelineConfig,
    PricingConfig,
    TitleConfig,
)
from automation_control.listing_pipeline.errors import GroupingError, SalePlanMatchError
from automation_control.listing_pipeline.group import group_cards
from automation_control.listing_pipeline.models import CardRecord, SalePlanRow

CARDS = [
    CardRecord("1", "Pikachu", "Base Set", "58/102", "Common", "English", "Raw", "SINGLE"),
    CardRecord("2", "Charizard", "Base Set", "4/102", "Holo Rare", "English", "Raw", "BUNDLE A - Test lot"),
    CardRecord("3", "Bulbasaur", "Base Set", "44/102", "Common", "English", "Raw", "BUNDLE A - Test lot"),
]


DEFAULT_BUNDLE_TITLES = {"BUNDLE A - Test lot": "Bundle Title"}


def _config(bundle_titles=DEFAULT_BUNDLE_TITLES):
    return PipelineConfig(
        ebay=EbayFieldsConfig(
            category_id="1", condition_id="1", item_location="x",
            shipping_profile_name="x", return_profile_name="x", payment_profile_name="x",
        ),
        images=ImagesConfig(base_url="https://x", filename_pattern="{custom_label}_{index}.jpg"),
        pricing=PricingConfig(auto_accept_pct="0.9"),
        title=TitleConfig(term_order=["game", "character"]),
        bundle_titles=bundle_titles,
    )


def test_every_card_lands_in_exactly_one_listing():
    sale_plan = [
        SalePlanRow(1, "Pikachu 58/102", 1, Decimal("45"), Decimal("35")),
        SalePlanRow(2, "BUNDLE A - Test lot", 2, Decimal("90"), Decimal("70")),
    ]
    listings = group_cards(CARDS, sale_plan, _config())

    all_card_ids = [card_id for listing in listings for card_id in listing.card_ids]
    assert sorted(all_card_ids) == ["1", "2", "3"]
    assert len(all_card_ids) == len(set(all_card_ids))
    assert {listing.custom_label for listing in listings} == {"NIC-01", "NIC-02"}


def test_orphaned_card_raises_grouping_error():
    # Pikachu (single, card 1) never appears in the Sale Plan
    sale_plan = [SalePlanRow(1, "BUNDLE A - Test lot", 2, Decimal("90"), Decimal("70"))]
    with pytest.raises(GroupingError) as excinfo:
        group_cards(CARDS, sale_plan, _config())
    assert excinfo.value.orphaned == ["1"]


def test_bundle_tag_not_matched_in_tracker_raises_sale_plan_match_error():
    sale_plan = [
        SalePlanRow(1, "Pikachu 58/102", 1, Decimal("45"), Decimal("35")),
        SalePlanRow(2, "BUNDLE B - Nonexistent lot", 2, Decimal("90"), Decimal("70")),
    ]
    with pytest.raises(SalePlanMatchError, match="no exact match"):
        group_cards(CARDS, sale_plan, _config())


def test_bundle_tag_close_but_not_exact_match_raises_with_hint():
    # Mirrors the real "BUNDLE F - Promo lot" vs "BUNDLE F - Promo lot (pending photos)" mismatch
    cards = CARDS[:1] + [
        CardRecord("2", "Charizard", "Base Set", "4/102", "Holo Rare", "English", "Raw", "BUNDLE A - Test lot (pending)"),
        CardRecord("3", "Bulbasaur", "Base Set", "44/102", "Common", "English", "Raw", "BUNDLE A - Test lot (pending)"),
    ]
    sale_plan = [
        SalePlanRow(1, "Pikachu 58/102", 1, Decimal("45"), Decimal("35")),
        SalePlanRow(2, "BUNDLE A - Test lot", 2, Decimal("90"), Decimal("70")),
    ]
    with pytest.raises(SalePlanMatchError, match="Closest candidate"):
        group_cards(cards, sale_plan, _config())


def test_single_card_number_ambiguous_across_two_sale_plan_rows_raises():
    cards = [
        CardRecord("1", "Pikachu", "Base Set", "1/1", "Common", "English", "Raw", "SINGLE"),
        CardRecord("2", "Charizard", "Base Set", "1/1", "Rare", "English", "Raw", "SINGLE"),
    ]
    sale_plan = [SalePlanRow(1, "Some card 1/1 special", 1, Decimal("45"), Decimal("35"))]
    with pytest.raises(SalePlanMatchError, match="ambiguous"):
        group_cards(cards, sale_plan, _config())


def test_bundle_without_config_title_raises():
    sale_plan = [
        SalePlanRow(1, "Pikachu 58/102", 1, Decimal("45"), Decimal("35")),
        SalePlanRow(2, "BUNDLE A - Test lot", 2, Decimal("90"), Decimal("70")),
    ]
    with pytest.raises(SalePlanMatchError, match="bundle_titles"):
        group_cards(CARDS, sale_plan, _config(bundle_titles={}))


def test_custom_labels_use_sale_plan_number_and_prefix():
    sale_plan = [
        SalePlanRow(1, "Pikachu 58/102", 1, Decimal("45"), Decimal("35")),
        SalePlanRow(2, "BUNDLE A - Test lot", 2, Decimal("90"), Decimal("70")),
    ]
    listings = group_cards(CARDS, sale_plan, _config())
    labels = {listing.custom_label: listing for listing in listings}
    assert "NIC-01" in labels
    assert "NIC-02" in labels
    assert labels["NIC-02"].title_override == "Bundle Title"
