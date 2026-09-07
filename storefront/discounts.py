"""Buy-more-from-a-set discounts for the cart -- "buy multiple from the
same set, pay less per card".

Grouping is by the TCG set or collectible series a catalogue item
actually belongs to, read from TcgCard.set_id / SealedProduct.set_id /
CollectibleProduct.series -- never by title text, which would silently
split "Base Set" from "base set" or a typo. Bulk lots are excluded
entirely: a lot already IS a whole set in one line, so discounting it
against itself has no meaning.

Tiers live in the set_discount_tiers table (see
scripts/migrate_add_set_discount_tiers.py) rather than as constants here,
same reasoning as ShippingRate: the numbers should be tunable without a
redeploy.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from automation_control.models import (
    CardSet, CatalogItem, CatalogItemType, CollectibleProduct, SealedProduct,
    SetDiscountTier, TcgCard,
)

CENT = Decimal("0.01")


@dataclass(frozen=True)
class SetGroup:
    """One set/series with enough of it in the cart to earn a discount."""

    key: str
    label: str
    quantity: int
    subtotal: Decimal
    discount_pct: Decimal
    discount_amount: Decimal


@dataclass(frozen=True)
class NearMiss:
    """A set/series that doesn't qualify yet, but is close -- shown as a
    nudge ("2 more from Base Set for 5% off"), not a discount."""

    label: str
    quantity: int
    needed: int
    next_pct: Decimal


def _tiers(session: Session) -> list[tuple[int, Decimal]]:
    rows = session.scalars(
        select(SetDiscountTier)
        .where(SetDiscountTier.is_active.is_(True))
        .order_by(SetDiscountTier.min_quantity)
    ).all()
    return [(row.min_quantity, Decimal(str(row.discount_pct))) for row in rows]


def _group_key(session: Session, item: CatalogItem | None) -> tuple[str, str] | None:
    """(key, label) for the set/series a catalogue item belongs to, or
    None if it isn't the kind of item this feature groups at all."""
    if item is None:
        return None
    if item.item_type in (CatalogItemType.TCG_CARD, CatalogItemType.GRADED_CARD):
        tcg = session.get(TcgCard, item.id)
        if tcg and tcg.set_id:
            card_set = session.get(CardSet, tcg.set_id)
            return f"set:{tcg.set_id}", card_set.name if card_set else tcg.set_id
    elif item.item_type == CatalogItemType.SEALED_PRODUCT:
        sealed = session.get(SealedProduct, item.id)
        if sealed and sealed.set_id:
            card_set = session.get(CardSet, sealed.set_id)
            return f"set:{sealed.set_id}", card_set.name if card_set else sealed.set_id
    elif item.item_type == CatalogItemType.COLLECTIBLE:
        product = session.get(CollectibleProduct, item.id)
        if product and product.series:
            return f"series:{product.series}", product.series
    return None


def evaluate(session: Session, lines: list[dict]) -> tuple[list[SetGroup], list[NearMiss]]:
    """Group cart lines by set/series and work out what each has earned.

    Entirely conservative: a line with no recognised set/series (sealed
    product with no set on file, apparel, accessories) or a bulk lot is
    left out of the grouping altogether rather than guessed into one.
    """
    tiers = _tiers(session)
    if not tiers:
        return [], []

    buckets: dict[str, dict] = {}
    for line in lines:
        if line["is_lot"]:
            continue
        key_label = _group_key(session, line["item"])
        if key_label is None:
            continue
        key, label = key_label
        bucket = buckets.setdefault(key, {"label": label, "quantity": 0, "subtotal": Decimal("0")})
        bucket["quantity"] += line["quantity"]
        bucket["subtotal"] += line["line_total"]

    groups: list[SetGroup] = []
    near_misses: list[NearMiss] = []
    for key, bucket in buckets.items():
        quantity = bucket["quantity"]
        pct = Decimal("0")
        for min_qty, tier_pct in tiers:
            if quantity >= min_qty:
                pct = tier_pct

        if pct > 0:
            amount = (bucket["subtotal"] * pct / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
            groups.append(SetGroup(
                key=key, label=bucket["label"], quantity=quantity,
                subtotal=bucket["subtotal"], discount_pct=pct, discount_amount=amount,
            ))
        else:
            # Only nudge when exactly one more item reaches the next tier --
            # anything looser and every single-card set in the cart would
            # show a nudge, which is noise rather than a useful prompt.
            next_tier = next(((min_qty, tier_pct) for min_qty, tier_pct in tiers if min_qty > quantity), None)
            if next_tier and next_tier[0] - quantity <= 1:
                near_misses.append(NearMiss(
                    label=bucket["label"], quantity=quantity,
                    needed=next_tier[0] - quantity, next_pct=next_tier[1],
                ))

    groups.sort(key=lambda g: -g.discount_amount)
    near_misses.sort(key=lambda n: n.needed)
    return groups, near_misses


def total_discount(groups: list[SetGroup]) -> Decimal:
    return sum((g.discount_amount for g in groups), Decimal("0"))
