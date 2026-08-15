from decimal import Decimal

import pytest

from automation_control.listing_pipeline.config import EbayFieldsConfig, ImagesConfig, PipelineConfig, PricingConfig, TitleConfig
from automation_control.listing_pipeline.models import CardRecord, Listing
from automation_control.listing_pipeline.render import CONDITION_PLACEHOLDER, MAX_TITLE_LENGTH, build_description, build_title

TERM_ORDER = ["game", "character", "card_number", "rarity", "set_name"]


def _config(term_order=TERM_ORDER, game="Pokemon TCG"):
    return PipelineConfig(
        ebay=EbayFieldsConfig(
            category_id="1", condition_id="1", item_location="x",
            shipping_profile_name="x", return_profile_name="x", payment_profile_name="x",
            game=game,
        ),
        images=ImagesConfig(base_url="https://x", filename_pattern="{custom_label}_{index}.jpg"),
        pricing=PricingConfig(auto_accept_pct="0.9"),
        title=TitleConfig(term_order=term_order),
    )


def _single(character, set_name="Base Set", card_number="1/1", rarity="Rare", language="English", grade="Raw", condition=None, **kwargs):
    card = CardRecord("1", character, set_name, card_number, rarity, language, grade, "SINGLE", condition=condition)
    return Listing(custom_label="NIC-01", sale_plan_number=1, cards=(card,), list_price=Decimal("45"), floor_price=Decimal("35"), **kwargs)


def test_title_under_limit_is_unchanged():
    listing = _single("Pikachu")
    title = build_title(listing, _config())
    assert title == "Pokemon TCG Pikachu 1/1 Rare Base Set"
    assert len(title) <= MAX_TITLE_LENGTH


def test_title_truncates_at_80_chars_dropping_lowest_priority_first():
    listing = _single(
        character="Pikachu Illustrator Special Holographic Promo Character",
        set_name="Base Set Unlimited First Print Run Extended Edition",
        card_number="58/102",
        rarity="Ultra Rare Secret Rainbow",
    )
    config = _config()
    full_length = len(" ".join([config.ebay.game, listing.cards[0].character, listing.cards[0].card_number, listing.cards[0].rarity, listing.cards[0].set_name]))
    assert full_length > MAX_TITLE_LENGTH

    title = build_title(listing, config)

    assert len(title) <= MAX_TITLE_LENGTH
    # lowest-priority term (set_name, last in TERM_ORDER) is dropped first
    assert listing.cards[0].set_name not in title
    # highest-priority term (game) survives
    assert config.ebay.game in title


def test_bundle_without_title_override_raises():
    card_a = CardRecord("1", "Pikachu", "Base Set", "58/102", "Common", "English", "Raw", "BUNDLE A")
    card_b = CardRecord("2", "Charizard", "Base Set", "4/102", "Holo Rare", "English", "Raw", "BUNDLE A")
    bundle = Listing(custom_label="NIC-02", sale_plan_number=2, cards=(card_a, card_b), list_price=Decimal("120"), floor_price=Decimal("95"))
    with pytest.raises(ValueError, match="bundle_titles"):
        build_title(bundle, _config())


def test_bundle_with_title_override_is_used_verbatim():
    card_a = CardRecord("1", "Pikachu", "Base Set", "58/102", "Common", "English", "Raw", "BUNDLE A")
    card_b = CardRecord("2", "Charizard", "Base Set", "4/102", "Holo Rare", "English", "Raw", "BUNDLE A")
    bundle = Listing(
        custom_label="NIC-02", sale_plan_number=2, cards=(card_a, card_b), list_price=Decimal("120"), floor_price=Decimal("95"),
        title_override="Pikachu & Charizard Bundle",
    )
    assert build_title(bundle, _config()) == "Pikachu & Charizard Bundle"


def test_description_escapes_html_in_card_names():
    listing = _single(character='<script>alert(1)</script> & "Quoted" Name')
    html = build_description(listing)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&amp;" in html


def test_missing_condition_renders_placeholder():
    listing = _single("Pikachu", condition=None)
    html = build_description(listing)
    assert CONDITION_PLACEHOLDER in html


def test_present_condition_renders_actual_value():
    listing = _single("Pikachu", condition="Near Mint")
    html = build_description(listing)
    assert "Near Mint" in html
    assert CONDITION_PLACEHOLDER not in html


def test_bundle_disagreeing_conditions_render_placeholder():
    card_a = CardRecord("1", "Pikachu", "Base Set", "58/102", "Common", "English", "Raw", "BUNDLE A", condition="Near Mint")
    card_b = CardRecord("2", "Charizard", "Base Set", "4/102", "Holo Rare", "English", "Raw", "BUNDLE A", condition="Lightly Played")
    bundle = Listing(custom_label="NIC-02", sale_plan_number=2, cards=(card_a, card_b), list_price=Decimal("120"), floor_price=Decimal("95"), title_override="x")
    html = build_description(bundle)
    assert CONDITION_PLACEHOLDER in html
