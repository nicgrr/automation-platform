from dataclasses import dataclass
from decimal import Decimal

from automation_control.listing_pipeline.config import EbayFieldsConfig, ImagesConfig, PipelineConfig, PricingConfig, TitleConfig
from automation_control.listing_pipeline.ingest_db import build_listings, next_label_number

TERM_ORDER = ["game", "character", "card_number", "rarity", "set_name"]


def _config(floor_price_pct="0.70"):
    return PipelineConfig(
        ebay=EbayFieldsConfig(
            category_id="1", condition_id="1", item_location="x",
            shipping_profile_name="x", return_profile_name="x", payment_profile_name="x",
            custom_label_prefix="NIC", default_condition_text="Near Mint or Better",
        ),
        images=ImagesConfig(base_url="https://x", filename_pattern="{custom_label}_{index}.jpg"),
        pricing=PricingConfig(auto_accept_pct="0.9", floor_price_pct=Decimal(floor_price_pct)),
        title=TitleConfig(term_order=TERM_ORDER),
    )


@dataclass
class FakeCapturedCard:
    id: str
    character: str
    set_name: str = "Promo"
    card_number: str = "1"
    rarity: str = "Common"
    language: str = "English"
    graded: str = "Raw"
    approved_price: Decimal = Decimal("10.00")
    bundle_id: str | None = None


def test_next_label_number_continues_after_highest_existing():
    assert next_label_number(["NIC-01", "NIC-07", "NIC-03"]) == 8


def test_next_label_number_starts_at_one_with_no_existing_labels():
    assert next_label_number([]) == 1


def test_single_card_becomes_its_own_listing_with_derived_floor_price():
    card = FakeCapturedCard(id="a", character="Flareon", approved_price=Decimal("10.00"))
    listings = build_listings([card], _config(floor_price_pct="0.70"), existing_labels=[])

    assert len(listings) == 1
    listing = listings[0]
    assert listing.custom_label == "NIC-01"
    assert not listing.is_bundle
    assert listing.list_price == Decimal("10.00")
    assert listing.floor_price == Decimal("7.00")
    assert listing.title_override is None  # singles derive their title mechanically at render time
    assert listing.cards[0].condition == "Near Mint or Better"


def test_cards_sharing_a_bundle_id_become_one_bundle_listing():
    members = [
        FakeCapturedCard(id="a", character="Flareon", bundle_id="bundle-1", approved_price=Decimal("2.00")),
        FakeCapturedCard(id="b", character="Vaporeon", bundle_id="bundle-1", approved_price=Decimal("2.00")),
        FakeCapturedCard(id="c", character="Jolteon", bundle_id="bundle-1", approved_price=Decimal("2.00")),
    ]
    listings = build_listings(members, _config(), existing_labels=[])

    assert len(listings) == 1
    listing = listings[0]
    assert listing.is_bundle
    assert len(listing.cards) == 3
    assert listing.list_price == Decimal("2.00")
    assert listing.title_override is not None
    assert "Flareon" in listing.title_override and "Vaporeon" in listing.title_override


def test_bundle_title_caps_at_three_named_characters():
    members = [FakeCapturedCard(id=str(i), character=f"Card{i}", bundle_id="bundle-1") for i in range(5)]
    listings = build_listings(members, _config(), existing_labels=[])
    title = listings[0].title_override
    assert "+ 2 more" in title
    assert len(title) <= 80


def test_numbering_continues_from_existing_labels_and_singles_then_bundles():
    single = FakeCapturedCard(id="a", character="Flareon")
    bundle_members = [
        FakeCapturedCard(id="b", character="Vaporeon", bundle_id="bundle-1"),
        FakeCapturedCard(id="c", character="Jolteon", bundle_id="bundle-1"),
    ]
    listings = build_listings([single, *bundle_members], _config(), existing_labels=["NIC-05"])

    labels = sorted(listing.custom_label for listing in listings)
    assert labels == ["NIC-06", "NIC-07"]


def test_floor_price_is_rounded_to_cents():
    card = FakeCapturedCard(id="a", character="Flareon", approved_price=Decimal("9.99"))
    listings = build_listings([card], _config(floor_price_pct="0.70"), existing_labels=[])
    assert listings[0].floor_price == Decimal("6.99")  # 9.99 * 0.70 = 6.993 -> rounds to 6.99
