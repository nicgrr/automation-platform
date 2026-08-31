"""Builds Listing objects directly from priced, approved CapturedCard rows
in the database -- the DB-driven counterpart to ingest.py's Excel Tracker
reader. Feeds straight into render.render_all unchanged; nothing downstream
(render.py, export.py) needs to know a Listing came from here instead of
the spreadsheet.
"""

import re
from decimal import ROUND_HALF_UP, Decimal

from .config import PipelineConfig
from .models import CardRecord, Listing

CENTS = Decimal("0.01")
_LABEL_SUFFIX = re.compile(r"-(\d+)$")
_MAX_TITLE_LENGTH = 80


def next_label_number(existing_labels: list[str]) -> int:
    """Continues numbering after every custom_label already assigned (by
    either this pipeline or the Excel one, since both share one eBay
    account and custom_label_prefix) so a fresh batch never collides with
    an earlier one."""
    numbers = [int(match.group(1)) for label in existing_labels if (match := _LABEL_SUFFIX.search(label))]
    return max(numbers, default=0) + 1


def _card_record(card, condition: str) -> CardRecord:
    return CardRecord(
        card_id=card.id,
        character=card.character or "",
        set_name=card.set_name or "",
        card_number=card.card_number or "",
        rarity=card.rarity or "",
        language=card.language or "",
        graded=card.graded or "",
        bundle_tag="SINGLE" if not card.bundle_id else f"AUTO-{card.bundle_id}",
        condition=condition,
    )


def _bundle_title(cards) -> str:
    """Bundles built from Excel get a hand-written title in config's
    [bundle_titles]; auto-classified DB bundles have no such entry to key
    off (there's no human-authored bundle tag to match), so this generates
    a reasonable placeholder from the cards it contains. It's meant to be
    edited by a human on /listings/review before approving, not shipped
    verbatim -- see listings_review.py's PendingListing.title, which is
    editable independently of this.
    """
    characters = [c.character for c in cards if c.character]
    lead = ", ".join(characters[:3])
    more = f" + {len(characters) - 3} more" if len(characters) > 3 else ""
    title = f"Pokemon TCG Bundle x{len(cards)} — {lead}{more}"
    return title[:_MAX_TITLE_LENGTH]


def _floor_price(list_price: Decimal, config: PipelineConfig) -> Decimal:
    return (list_price * config.pricing.floor_price_pct).quantize(CENTS, rounding=ROUND_HALF_UP)


def build_listings(approved_cards: list, config: PipelineConfig, existing_labels: list[str]) -> list[Listing]:
    """approved_cards: CapturedCard rows with pricing_status ==
    PricingStatus.PRICE_APPROVED. existing_labels: every custom_label
    already assigned to a PendingListing, so numbering continues rather
    than colliding with a prior batch.

    Cards without a bundle_id become their own single-card Listing; cards
    sharing a bundle_id (set either by a human on /cards/pricing or
    automatically by classification.classify_pending_cards) become one
    bundle Listing together.
    """
    singles = [card for card in approved_cards if not card.bundle_id]
    bundles: dict[str, list] = {}
    for card in approved_cards:
        if card.bundle_id:
            bundles.setdefault(card.bundle_id, []).append(card)

    condition = config.ebay.default_condition_text
    next_number = next_label_number(existing_labels)
    listings: list[Listing] = []

    def _label(number: int) -> str:
        return f"{config.ebay.custom_label_prefix}-{number:02d}"

    for card in singles:
        list_price = card.approved_price
        listings.append(
            Listing(
                custom_label=_label(next_number),
                sale_plan_number=next_number,
                cards=(_card_record(card, condition),),
                list_price=list_price,
                floor_price=_floor_price(list_price, config),
            )
        )
        next_number += 1

    for members in bundles.values():
        list_price = members[0].approved_price  # price_review.py assigns the same approved_price to every bundle member
        listings.append(
            Listing(
                custom_label=_label(next_number),
                sale_plan_number=next_number,
                cards=tuple(_card_record(card, condition) for card in members),
                list_price=list_price,
                floor_price=_floor_price(list_price, config),
                title_override=_bundle_title(members),
            )
        )
        next_number += 1

    return listings
