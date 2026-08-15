import csv as csv_module
import filecmp
from decimal import Decimal

from automation_control.listing_pipeline.config import EbayFieldsConfig, ImagesConfig, PipelineConfig, PricingConfig, TitleConfig
from automation_control.listing_pipeline.export import derive_image_filenames, write_bulk_upload_csv, write_image_checklist_csv, write_validation_report
from automation_control.listing_pipeline.group import group_cards
from automation_control.listing_pipeline.models import CardRecord, SalePlanRow
from automation_control.listing_pipeline.render import render_all

CARDS = [
    CardRecord("1", "Pikachu", "Base Set", "58/102", "Common", "English", "Raw", "SINGLE"),
    CardRecord("2", "Alcremie VMAX", "Base Set", "4/102", "Holo Rare", "English", "Raw", "BUNDLE A - Test lot", condition="Near Mint"),
    CardRecord("3", "Bulbasaur", "Base Set", "44/102", "Common", "English", "Raw", "BUNDLE A - Test lot", condition="Lightly Played"),
]

SALE_PLAN = [
    SalePlanRow(1, "Pikachu 58/102", 1, Decimal("45.00"), Decimal("35.00")),
    SalePlanRow(2, "BUNDLE A - Test lot", 2, Decimal("120.00"), Decimal("95.00")),
]


def _config():
    return PipelineConfig(
        ebay=EbayFieldsConfig(
            category_id="183454", condition_id="4000", item_location="Sydney, NSW",
            shipping_profile_name="Standard Shipping", return_profile_name="No Returns",
            payment_profile_name="eBay Managed Payments", game="Pokemon TCG",
            default_condition_text="Near Mint or Better",
        ),
        images=ImagesConfig(base_url="https://img.example.com/cards", filename_pattern="{custom_label}_{index}.jpg"),
        pricing=PricingConfig(auto_accept_pct="0.92"),
        title=TitleConfig(term_order=["game", "character", "card_number", "rarity", "set_name"]),
        bundle_titles={"BUNDLE A - Test lot": "Pokemon TCG Mixed Bundle Lot of 2"},
    )


def _rendered():
    config = _config()
    listings = group_cards(CARDS, SALE_PLAN, config)
    return render_all(listings, config), config


def _read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv_module.reader(f))
    return rows[0], rows[1:]


def test_auto_accept_price_uses_decimal_arithmetic(tmp_path):
    rendered, config = _rendered()
    warnings = []
    out_path = tmp_path / "ebay_bulk_upload.csv"
    write_bulk_upload_csv(rendered, config, out_path, warnings)

    header, rows = _read_rows(out_path)
    idx = header.index("BestOfferAutoAcceptPrice")
    single_row = next(row for row in rows if row[1] == "NIC-01")

    expected = (Decimal("45.00") * Decimal("0.92")).quantize(Decimal("0.01"))
    assert Decimal(single_row[idx]) == expected
    assert single_row[idx] == "41.40"


def test_c_game_and_c_card_condition_come_from_config_not_cards(tmp_path):
    rendered, config = _rendered()
    warnings = []
    out_path = tmp_path / "ebay_bulk_upload.csv"
    write_bulk_upload_csv(rendered, config, out_path, warnings)

    header, rows = _read_rows(out_path)
    game_idx = header.index("C:Game")
    condition_idx = header.index("C:Card Condition")
    for row in rows:
        assert row[game_idx] == "Pokemon TCG"
        assert row[condition_idx] == "Near Mint or Better"


def test_character_suffix_is_stripped_for_single_card(tmp_path):
    # NIC-01 is Pikachu (no suffix); add a VMAX single via its own fixtures
    cards = [CardRecord("9", "Alcremie VMAX", "Shining Fates", "073/072", "Rainbow Secret Rare", "English", "Raw", "SINGLE")]
    sale_plan = [SalePlanRow(1, "Alcremie VMAX 073/072", 1, Decimal("32.00"), Decimal("22.00"))]
    config = _config()
    listings = group_cards(cards, sale_plan, config)
    rendered = render_all(listings, config)

    warnings = []
    out_path = tmp_path / "ebay_bulk_upload.csv"
    write_bulk_upload_csv(rendered, config, out_path, warnings)
    header, rows = _read_rows(out_path)

    character_idx = header.index("C:Character")
    title_idx = header.index("*Title")
    assert rows[0][character_idx] == "Alcremie"
    assert "VMAX" in rows[0][title_idx]  # Title keeps the full card name


def test_bundle_forced_mixed_fields(tmp_path):
    rendered, config = _rendered()
    warnings = []
    out_path = tmp_path / "ebay_bulk_upload.csv"
    write_bulk_upload_csv(rendered, config, out_path, warnings)
    header, rows = _read_rows(out_path)
    bundle_row = next(row for row in rows if row[1] == "NIC-02")
    for column in ("C:Set", "C:Card Number", "C:Rarity", "C:Character"):
        assert bundle_row[header.index(column)] == "Mixed"


def test_idempotent_two_runs_produce_identical_bytes(tmp_path):
    rendered, config = _rendered()

    for out_dir in (tmp_path / "run1", tmp_path / "run2"):
        out_dir.mkdir()
        warnings = []
        write_bulk_upload_csv(rendered, config, out_dir / "ebay_bulk_upload.csv", warnings)
        write_image_checklist_csv(rendered, config, out_dir / "image_naming_checklist.csv")
        write_validation_report(warnings, out_dir / "validation_report.csv")

    for filename in ("ebay_bulk_upload.csv", "image_naming_checklist.csv", "validation_report.csv"):
        assert filecmp.cmp(tmp_path / "run1" / filename, tmp_path / "run2" / filename, shallow=False), f"{filename} differs between runs"


def test_image_filenames_match_between_picurl_and_checklist(tmp_path):
    rendered, config = _rendered()
    warnings = []
    write_bulk_upload_csv(rendered, config, tmp_path / "ebay_bulk_upload.csv", warnings)
    write_image_checklist_csv(rendered, config, tmp_path / "image_naming_checklist.csv")

    bundle_listing = next(item.listing for item in rendered if item.listing.custom_label == "NIC-02")
    expected_filenames = derive_image_filenames(bundle_listing, config.images)

    checklist_text = (tmp_path / "image_naming_checklist.csv").read_text()
    bulk_text = (tmp_path / "ebay_bulk_upload.csv").read_text()
    for filename in expected_filenames:
        assert filename in checklist_text
        assert filename in bulk_text
