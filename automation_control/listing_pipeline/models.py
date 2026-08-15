from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CardRecord:
    card_id: str
    character: str
    set_name: str
    card_number: str
    rarity: str
    language: str
    graded: str
    bundle_tag: str  # "SINGLE" or the bundle name, e.g. "BUNDLE A - Mega Evolution Double Rare lot"
    condition: str | None = None  # blank in the source until physically inspected


@dataclass(frozen=True)
class SalePlanRow:
    number: int
    listing_text: str
    card_count: int
    list_price: Decimal
    floor_price: Decimal


@dataclass(frozen=True)
class Listing:
    custom_label: str
    sale_plan_number: int
    cards: tuple[CardRecord, ...]
    list_price: Decimal
    floor_price: Decimal
    title_override: str | None = None  # required for bundles; singles derive their title mechanically

    @property
    def is_bundle(self) -> bool:
        return len(self.cards) > 1

    @property
    def card_ids(self) -> tuple[str, ...]:
        return tuple(card.card_id for card in self.cards)
