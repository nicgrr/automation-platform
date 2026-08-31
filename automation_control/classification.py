"""Automatic bulk-lot vs. single-card classification for captured cards.

Runs as the last step of price_review._run_price_check, once a batch of
cards has a suggested_price. It never overrides a human's existing choice
(a card a human has already bundled, or one still sitting unpriced, is
left untouched), and everything it decides can be re-grouped by hand on
/cards/pricing before a human approves a price -- see the bundle_with
checkboxes on that page.
"""

import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .listing_pipeline.config import PipelineConfig, load_config
from .models import CapturedCard, PricingStatus


def load_pipeline_config() -> PipelineConfig:
    """Load listing_pipeline's TOML config fresh (no caching -- this runs at
    most once per price-check background job, so the cost of re-reading a
    small file is trivial, and it means an edited config.toml takes effect
    without restarting the server)."""
    settings = get_settings()
    return load_config(Path(settings.listing_pipeline_config_path))


def classify_pending_cards(session: Session, *, threshold_aud: Decimal, max_lot_size: int) -> None:
    """Group newly-priced cards under `threshold_aud` into lots of up to
    `max_lot_size`, assigning each lot a shared bundle_id. Cards at or above
    the threshold, already bundled, or not yet priced are left alone.

    A leftover group smaller than 2 cards (the remainder after chunking)
    stays ungrouped -- a "lot" of one card is just a single listing.
    """
    if max_lot_size < 2:
        return

    candidates = session.scalars(
        select(CapturedCard)
        .where(
            CapturedCard.pricing_status == PricingStatus.PENDING_PRICE_REVIEW,
            CapturedCard.bundle_id.is_(None),
            CapturedCard.suggested_price.is_not(None),
            CapturedCard.suggested_price < threshold_aud,
        )
        .order_by(CapturedCard.price_checked_at)
    ).all()

    for start in range(0, len(candidates), max_lot_size):
        lot = candidates[start : start + max_lot_size]
        if len(lot) < 2:
            break
        bundle_id = str(uuid.uuid4())
        for card in lot:
            card.bundle_id = bundle_id

    session.commit()
