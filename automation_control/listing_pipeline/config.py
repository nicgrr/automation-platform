import tomllib
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field


class EbayFieldsConfig(BaseModel):
    category_id: str
    condition_id: str
    item_location: str
    shipping_profile_name: str
    return_profile_name: str
    payment_profile_name: str
    game: str = "Pokemon TCG"
    default_condition_text: str = "Near Mint or Better"
    custom_label_prefix: str = "NIC"
    site_id: str = "Australia"
    country: str = "AU"
    currency: str = "AUD"
    bulk_upload_version: str = "1193"
    default_quantity: str = "1"


class ImagesConfig(BaseModel):
    base_url: str
    filename_pattern: str
    photos_per_card_single: int = 2
    photos_per_card_bundle: int = 1


class PricingConfig(BaseModel):
    auto_accept_pct: Decimal
    # Used only by the DB-driven capture pipeline (ingest_db.py) to derive a
    # floor price from list_price -- the Excel-driven CLI gets floor_price
    # straight from the Sale Plan sheet instead and never reads this field.
    floor_price_pct: Decimal = Decimal("0.70")


class TitleConfig(BaseModel):
    term_order: list[str]


class ClassificationConfig(BaseModel):
    # Cards priced below this (in AUD) are grouped into bulk lots instead of
    # listed as singles -- see automation_control/classification.py, which
    # runs this rule automatically at the end of each price-check pass. A
    # human can still override the grouping on /cards/pricing before
    # approving, so this only sets the *default* the human starts from.
    bulk_price_threshold_aud: Decimal
    max_lot_size: int = 8


class PipelineConfig(BaseModel):
    ebay: EbayFieldsConfig
    images: ImagesConfig
    pricing: PricingConfig
    title: TitleConfig
    # Only used by the DB-driven capture -> price-review pipeline
    # (automation_control/classification.py), not by the Excel-driven CLI
    # stages (ingest/group/render/export) -- optional so config.toml files
    # written before this feature existed, and tests constructing
    # PipelineConfig directly for CLI-stage tests, don't need it.
    classification: ClassificationConfig | None = None
    # Bundle listings have no single card to derive a title from, so each
    # bundle tag (matching Tracker's Notes column, e.g. "BUNDLE A - Mega
    # Evolution Double Rare lot") needs an explicit title here.
    bundle_titles: dict[str, str] = Field(default_factory=dict)


def load_config(path: str | Path) -> PipelineConfig:
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    return PipelineConfig.model_validate(data)
