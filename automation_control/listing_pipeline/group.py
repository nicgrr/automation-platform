from .config import PipelineConfig
from .errors import GroupingError, SalePlanMatchError
from .models import CardRecord, Listing, SalePlanRow


def _match_bundle(sale_row: SalePlanRow, bundle_groups: dict[str, list[CardRecord]]) -> list[CardRecord]:
    tag = sale_row.listing_text
    if tag in bundle_groups:
        return bundle_groups[tag]
    close = [candidate for candidate in bundle_groups if candidate.startswith(tag) or tag in candidate]
    hint = f" Closest candidate(s) in Tracker: {', '.join(close)}." if close else ""
    raise SalePlanMatchError(f"Sale Plan #{sale_row.number} '{sale_row.listing_text}': no exact match among Tracker bundle tags.{hint}")


def _match_single(sale_row: SalePlanRow, singles: list[CardRecord]) -> CardRecord:
    matches = [card for card in singles if card.card_number and card.card_number in sale_row.listing_text]
    if len(matches) == 0:
        raise SalePlanMatchError(f"Sale Plan #{sale_row.number} '{sale_row.listing_text}': no single card's Card No. found in this text.")
    if len(matches) > 1:
        names = ", ".join(f"{card.character} ({card.card_number})" for card in matches)
        raise SalePlanMatchError(f"Sale Plan #{sale_row.number} '{sale_row.listing_text}': ambiguous, matches multiple cards: {names}")
    return matches[0]


def group_cards(cards: list[CardRecord], sale_plan: list[SalePlanRow], config: PipelineConfig) -> list[Listing]:
    bundle_groups: dict[str, list[CardRecord]] = {}
    singles: list[CardRecord] = []
    for card in cards:
        if card.bundle_tag == "SINGLE":
            singles.append(card)
        else:
            bundle_groups.setdefault(card.bundle_tag, []).append(card)

    label_width = max(len(str(len(sale_plan))), 2)
    listings: list[Listing] = []
    assigned_card_ids: set[str] = set()
    duplicated: list[str] = []

    for sale_row in sale_plan:
        if sale_row.listing_text.startswith("BUNDLE"):
            member_cards = _match_bundle(sale_row, bundle_groups)
            title_override = config.bundle_titles.get(sale_row.listing_text)
            if not title_override:
                raise SalePlanMatchError(f"Sale Plan #{sale_row.number} '{sale_row.listing_text}': no entry in config's [bundle_titles] for this bundle tag.")
        else:
            member_cards = [_match_single(sale_row, singles)]
            title_override = None

        for card in member_cards:
            if card.card_id in assigned_card_ids:
                duplicated.append(card.card_id)
            assigned_card_ids.add(card.card_id)

        custom_label = f"{config.ebay.custom_label_prefix}-{sale_row.number:0{label_width}d}"
        listings.append(
            Listing(
                custom_label=custom_label,
                sale_plan_number=sale_row.number,
                cards=tuple(member_cards),
                list_price=sale_row.list_price,
                floor_price=sale_row.floor_price,
                title_override=title_override,
            )
        )

    all_card_ids = {card.card_id for card in cards}
    orphaned = sorted(all_card_ids - assigned_card_ids)
    if orphaned or duplicated:
        raise GroupingError(orphaned=orphaned, duplicated=sorted(set(duplicated)))

    return listings
