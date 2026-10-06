"""Conservative local matching from EzBay catalogue items to TCGCSV products."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CardSet,
    CatalogItem,
    InventoryItem,
    PricingExternalId,
    PricingProviderCategory,
    PricingProviderGroup,
    PricingProviderProduct,
    TcgCard,
)
from .providers.tcgcsv import CATEGORY_NAMES, normalize_name


PROVIDER_KEY = "tcgcsv"
_TRAILING_NUMBER = re.compile(r"(?:\s*\()?([A-Z]{0,8}\d{1,4}(?:[-/][A-Z0-9]{1,8})+)(?:\))?$")
_PARENS = re.compile(r"\s*\([^)]*\)\s*")


@dataclass(frozen=True, slots=True)
class MappingSummary:
    considered: int
    mapped: int
    already_mapped: int
    ambiguous: int
    unmatched: int


def _number(value: str | None) -> str:
    # Pokemon providers disagree on whether the printed total is part of the
    # number ("122" versus "122/191"). The numerator is the card identity;
    # One Piece/Dragon Ball codes contain no slash and remain intact.
    numerator = (value or "").split("/", 1)[0]
    compact = re.sub(r"[^A-Z0-9]", "", numerator.upper())
    return str(int(compact)) if compact.isdigit() else compact


def _set_name(value: str | None) -> str:
    normalized = normalize_name(value or "")
    # TCGplayer commonly prefixes the display set with its abbreviation,
    # e.g. "SV08: Surging Sparks" while EzBay stores "Surging Sparks".
    if ":" in (value or ""):
        tail = normalize_name((value or "").split(":", 1)[1])
        return tail or normalized
    return normalized


def _set_code(value: str | None) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    match = re.fullmatch(r"([A-Z]+)0*(\d+)", compact)
    return f"{match.group(1)}{int(match.group(2))}" if match else compact


def _full_name(value: str | None) -> str:
    return normalize_name(_TRAILING_NUMBER.sub("", value or ""))


def _base_name(value: str | None) -> str:
    without_code = _TRAILING_NUMBER.sub("", value or "")
    without_labels = _PARENS.sub(" ", without_code)
    return normalize_name(without_labels)


def _category_game(category: PricingProviderCategory) -> str | None:
    name = normalize_name(category.display_name or category.name)
    matches: list[tuple[int, str]] = []
    for game, aliases in CATEGORY_NAMES.items():
        for alias in aliases:
            if name == alias or alias in name:
                matches.append((len(alias), game))
    return max(matches, default=(0, None))[1]


def _write_mapping(
    session: Session, *, catalog_item_id: str, product: PricingProviderProduct,
    group: PricingProviderGroup, confidence: Decimal, method: str,
) -> PricingExternalId:
    existing = session.scalar(select(PricingExternalId).where(
        PricingExternalId.catalog_item_id == catalog_item_id,
        PricingExternalId.provider_key == PROVIDER_KEY,
        PricingExternalId.provider_product_id == product.provider_product_id,
    ))
    if existing is not None:
        return existing
    row = PricingExternalId(
        catalog_item_id=catalog_item_id,
        provider_key=PROVIDER_KEY,
        provider_product_id=product.provider_product_id,
        provider_group_id=group.provider_group_id,
        external_ids={
            "tcgplayer_product_id": product.provider_product_id,
            "tcgplayer_group_id": group.provider_group_id,
        },
        match_confidence=confidence,
        match_method=method,
        resolved_at=datetime.now(UTC),
    )
    session.add(row)
    return row


def resolve_tcgcsv_ids(session: Session, *, only_owned: bool = True) -> MappingSummary:
    """Persist only deterministic mappings; return ambiguity as a count.

    No fuzzy name score is accepted here. Direct ``tcgp-<id>`` catalogue IDs
    map immediately. Other cards require an exact game/set and card-number
    match, with exact normalized name used to disambiguate parallel products.
    """
    query = (
        select(CatalogItem, TcgCard, CardSet)
        .join(TcgCard, TcgCard.catalog_item_id == CatalogItem.id)
        .outerjoin(CardSet, CardSet.id == TcgCard.set_id)
    )
    if only_owned:
        query = query.join(InventoryItem, InventoryItem.catalog_item_id == CatalogItem.id).distinct()
    catalogue_rows = session.execute(query).all()

    categories = session.scalars(select(PricingProviderCategory).where(
        PricingProviderCategory.provider_key == PROVIDER_KEY,
    )).all()
    game_by_category_id = {category.id: _category_game(category) for category in categories}
    groups = session.scalars(select(PricingProviderGroup).where(
        PricingProviderGroup.provider_key == PROVIDER_KEY,
    )).all()
    group_by_id = {group.id: group for group in groups}
    groups_by_game: dict[str, list[PricingProviderGroup]] = {}
    for group in groups:
        game = game_by_category_id.get(group.category_id)
        if game:
            groups_by_game.setdefault(game, []).append(group)
    products = session.scalars(select(PricingProviderProduct).where(
        PricingProviderProduct.provider_key == PROVIDER_KEY,
    )).all()
    product_by_external = {product.provider_product_id: product for product in products}
    products_by_group_number: dict[tuple[str, str], list[PricingProviderProduct]] = {}
    for product in products:
        products_by_group_number.setdefault(
            (product.group_id, _number(product.card_number)), [],
        ).append(product)

    existing_catalog_ids = set(session.scalars(select(PricingExternalId.catalog_item_id).where(
        PricingExternalId.provider_key == PROVIDER_KEY,
    )))
    mapped = already = ambiguous = unmatched = 0
    for item, card, card_set in catalogue_rows:
        if item.id in existing_catalog_ids:
            already += 1
            continue
        # TCGCSV's product-level feed does not expose SKU language. Its game
        # categories are the English catalogue, so a Japanese holding must
        # remain separate unless a future provider supplies an explicit ID.
        if (card.language or "").strip().casefold() not in {"english", "en"}:
            unmatched += 1
            continue
        direct_id = item.id.removeprefix("tcgp-") if item.id.startswith("tcgp-") else None
        direct = product_by_external.get(direct_id) if direct_id else None
        if direct is not None:
            _write_mapping(
                session,
                catalog_item_id=item.id,
                product=direct,
                group=group_by_id[direct.group_id],
                confidence=Decimal("1"),
                method="exact_provider_id",
            )
            mapped += 1
            continue

        candidate_groups = []
        set_name = _set_name(card_set.name) if card_set else ""
        set_codes = {
            _set_code(value) for value in (
                card.set_code, card_set.code if card_set else None,
            ) if value
        }
        for group in groups_by_game.get(card.game, []):
            if set_name and _set_name(group.name) == set_name:
                candidate_groups.append(group)
            elif group.abbreviation and _set_code(group.abbreviation) in set_codes:
                candidate_groups.append(group)
            elif card.set_id and card.set_id.rsplit("-", 1)[-1] == group.provider_group_id:
                candidate_groups.append(group)
        # A group can satisfy more than one exact criterion.
        candidate_groups = list({group.id: group for group in candidate_groups}.values())
        candidates: list[PricingProviderProduct] = []
        for group in candidate_groups:
            candidates.extend(products_by_group_number.get((group.id, _number(card.number)), []))
        if not candidates:
            unmatched += 1
            continue
        if len(candidates) > 1:
            wanted_full_names = {
                name for name in (_full_name(item.name), _full_name(card.character)) if name
            }
            exact_full_names = [
                candidate for candidate in candidates
                if _full_name(candidate.clean_name or candidate.name) in wanted_full_names
            ]
            if exact_full_names:
                candidates = exact_full_names
        if len(candidates) > 1:
            wanted_names = {
                name for name in (_base_name(item.name), _base_name(card.character)) if name
            }
            exact_names = [
                candidate for candidate in candidates
                if _base_name(candidate.clean_name or candidate.name) in wanted_names
            ]
            candidates = exact_names
        if len(candidates) != 1:
            ambiguous += 1
            continue
        product = candidates[0]
        _write_mapping(
            session,
            catalog_item_id=item.id,
            product=product,
            group=group_by_id[product.group_id],
            confidence=Decimal("0.99"),
            method="exact_set_number_name" if _base_name(product.name) == _base_name(item.name) else "exact_set_unique_number",
        )
        mapped += 1
    session.flush()
    return MappingSummary(
        considered=len(catalogue_rows), mapped=mapped, already_mapped=already,
        ambiguous=ambiguous, unmatched=unmatched,
    )
