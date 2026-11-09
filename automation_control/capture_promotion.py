"""Safe handoff from an approved photo capture to Bulk Upload inventory."""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .adapters.r2 import R2UploadError, is_configured as r2_is_configured, upload_card_image
from .classification import load_pipeline_config
from .models import (
    CapturePromotion, CapturePromotionStatus, CapturedCard, CardSet, CardVariant,
    CatalogCard, CatalogItem, CatalogItemType, EbayExportItem, EbayExportItemStatus,
    InventoryItem, InventoryStatus, PricingExternalId, PricingProviderCategory,
    PricingProviderGroup, PricingProviderProduct, ShopListing, ShopListingStatus,
    TcgCard,
)


@dataclass(frozen=True)
class PromotionResult:
    promoted: bool
    issue: str | None = None
    shop_listing_id: str | None = None


def _normalise(value: str | None) -> str:
    ascii_value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())


def _latin_prefixes(value: str | None) -> list[str]:
    """Every plausible SQL name prefix: the whole name and each bracketed part.

    ``モンキー・D・ルフィ (Monkey D. Luffy)`` keeps a stray Latin ``D`` from the
    katakana, so the first Latin word of the whole string is not the English
    name. A single letter is never a useful prefix; the bracketed translation
    is tried alongside the whole-name prefix so ``Pikachu (Full Art)`` still
    searches ``Pikachu``.
    """
    sources = [value or "", *re.findall(r"\(([^()]*)\)", value or "")]
    prefixes: list[str] = []
    for source in sources:
        ascii_value = unicodedata.normalize("NFKD", source).encode("ascii", "ignore").decode()
        word = next((
            word for word in re.findall(r"[A-Za-z0-9]+", ascii_value)
            if len(re.sub(r"[^A-Za-z]", "", word)) >= 2
        ), "")
        if word and word not in prefixes:
            prefixes.append(word)
    return prefixes


def _language_key(value: str | None) -> str:
    key = _normalise(value)
    return {"jp": "japanese", "jpn": "japanese", "ja": "japanese", "en": "english", "eng": "english"}.get(key, key)


def _prefer_language(candidates, language: str | None, *, strict: bool = False):
    """Keep rows printed in the detected language when any exist.

    A Japanese photo used to compete with the English rows of the same code
    and then lose on set-name scoring. ``strict`` drops every other language
    even when that leaves nothing (for looser fallbacks).
    """
    wanted = _language_key(language)
    if not wanted:
        return candidates
    same = [pair for pair in candidates if _language_key(pair[1].language) == wanted]
    return same if (same or strict) else candidates


def _names_close(catalogue_name: str | None, detected: str | None, threshold: float = 0.8) -> bool:
    """Tolerate romanisation only (``Zorojuro`` / ``Zoro-Juurou``)."""
    from difflib import SequenceMatcher

    left = _name_aliases(catalogue_name) | _name_aliases(_catalogue_character_name(catalogue_name))
    right = _name_aliases(detected)
    return any(
        len(a) >= 4 and len(b) >= 4 and SequenceMatcher(None, a, b).ratio() >= threshold
        for a in left for b in right
    )


def _name_aliases(value: str | None) -> set[str]:
    """Comparable full and translated names without guessing translations."""
    raw = value or ""
    aliases = {_normalise(raw)}
    aliases.update(_normalise(part) for part in re.findall(r"\(([^()]*)\)", raw))
    return {alias for alias in aliases if alias}


def _number(value: str | None) -> str:
    raw = (value or "").split("/", 1)[0]
    # Vision occasionally appends the printed rarity to the number field,
    # for example ``P-553 PR``. It is not part of the collector number.
    raw = re.sub(r"\s+(?:PR|C|UC|R|SR|SPR|SCR|L)\s*$", "", raw, flags=re.IGNORECASE)
    return _normalise(raw).lstrip("0") or "0"


def _number_matches(captured_number: str | None, tcg: TcgCard) -> bool:
    """Match either a stored suffix (``119``) or full printed code.

    Phone recognition usually reads ``OP17-119`` as one field, while older
    catalogue imports often split that into set_code=OP17 and number=119.
    Requiring those two storage shapes to be identical was leaving a clearly
    readable card unmatched in bulk-photo workflows.
    """
    wanted = {_number(captured_number), _normalise(captured_number)}
    available = {_number(tcg.number), _normalise(tcg.number)}
    if tcg.set_code and tcg.number:
        raw_number = tcg.number or ""
        full = raw_number if raw_number.upper().startswith(f"{tcg.set_code.upper()}-") else f"{tcg.set_code}-{raw_number}"
        available.update({_number(full), _normalise(full)})
    return bool((wanted - {"", "0"}).intersection(available - {"", "0"}))


def _full_card_number(tcg: TcgCard) -> str:
    """Canonical printed code even when an import split prefix and suffix."""
    raw_number = tcg.number or ""
    if not tcg.set_code or raw_number.upper().startswith(f"{tcg.set_code.upper()}-"):
        return _normalise(raw_number)
    return _normalise(f"{tcg.set_code}-{raw_number}")


def _one_code_ocr_confusion(observed: str | None, expected: str) -> bool:
    """Allow one shape-confusion only; never general edit-distance guessing."""
    left = _normalise(observed)
    right = _normalise(expected)
    if len(left) != len(right) or not left or left == right:
        return False
    differences = [(a, b) for a, b in zip(left, right) if a != b]
    return len(differences) == 1 and set(differences[0]).issubset({"0", "o", "d", "q"})


def _set_key(value: str | None) -> str:
    return _normalise(re.sub(r"\([^)]*\)", "", value or ""))


def _set_aliases(value: str | None) -> set[str]:
    """Names plus explicit printed abbreviations such as ``(SVP)``/``ASC``."""
    raw = value or ""
    aliases = {_normalise(raw), _set_key(raw)}
    for token in re.findall(r"[A-Za-z0-9]+", raw):
        if len(token) >= 3 and (token.isupper() or any(char.isdigit() for char in token)):
            aliases.add(_normalise(token))
    return {alias for alias in aliases if alias}


def _set_matches(card_set_name: str | None, card_set: CardSet | None, tcg: TcgCard) -> bool:
    """Match a reviewed set against every stable spelling we retain.

    Reviewers naturally enter either a full set name ("Ascended Heroes")
    or its printed code ("ASC").  Looking only at CardSet.name was the
    reason otherwise exact cards stayed in handoff review indefinitely.
    """
    return _set_match_score(card_set_name, card_set, tcg) > 0


def _set_match_score(card_set_name: str | None, card_set: CardSet | None, tcg: TcgCard) -> int:
    """Prefer a real set-name match over a reused printed card prefix."""
    wanted = _set_aliases(card_set_name)
    if not wanted:
        return 1
    name_values = set().union(
        _set_aliases(card_set.id if card_set else None),
        _set_aliases(card_set.name if card_set else None),
    )
    if wanted.intersection(name_values):
        return 3
    if any(
        len(left) >= 5 and len(right) >= 5 and (left in right or right in left)
        for left in wanted for right in name_values
    ):
        return 2
    code_values = set().union(
        _set_aliases(card_set.code if card_set else None),
        _set_aliases(tcg.set_code),
    )
    return 1 if wanted.intersection(code_values) else 0


def _provider_set_match_score(card_set_name: str | None, group: PricingProviderGroup) -> int:
    wanted = _set_aliases(card_set_name)
    available = set().union(
        _set_aliases(group.name), _set_aliases(group.abbreviation),
        _set_aliases(group.provider_group_id),
    )
    if not wanted:
        return 1
    if wanted.intersection(available):
        return 3
    if any(
        len(left) >= 5 and len(right) >= 5 and (left in right or right in left)
        for left in wanted for right in available
    ):
        return 2
    return 0


def _strip_display_number(value: str | None) -> str:
    """Remove a display-only printed-number suffix while retaining printing."""
    return re.sub(
        r"\s+-\s+(?:[A-Z]{1,10}\d{0,4}-)?\d+[A-Z]?(?:/\d+)?(?P<printing>\s+\([^)]*\))?$",
        r"\g<printing>",
        value or "",
        flags=re.IGNORECASE,
    ).strip()


def _provider_base_name(product: PricingProviderProduct) -> str:
    """Remove TCGCSV's display-only printed-number suffix from card names.

    Older products use ``Gothitelle - 211`` while recent Fusion World rows
    use ``King Piccolo - SB02-058``. Both suffixes are identifiers rather
    than part of the character name.
    """
    return _strip_display_number(product.name)


def _provider_character_name(product: PricingProviderProduct) -> str:
    """The character portion shared by regular and named parallel rows."""
    return re.sub(r"\s+\([^)]*\)\s*$", "", _provider_base_name(product)).strip()


def _catalogue_character_name(value: str | None) -> str:
    """Remove a catalogue printing suffix such as ``(SPR)`` for matching."""
    without_number = _strip_display_number(value)
    return re.sub(r"\s+\([^)]*\)\s*$", "", without_number).strip()


def _collapse_equivalent_catalogue_candidates(candidates):
    """Prefer the stable canonical row when a provider cache duplicates it."""
    if len(candidates) < 2:
        return candidates

    def signature(pair):
        item, tcg = pair
        return (
            _normalise(_catalogue_character_name(item.name)),
            _full_card_number(tcg),
            _normalise(tcg.rarity),
            _normalise(tcg.parallel),
            _normalise(tcg.set_code),
        )

    if len({signature(pair) for pair in candidates}) != 1:
        return candidates
    return [next((pair for pair in candidates if not pair[0].id.startswith("tcgp-")), candidates[0])]


def _printing_candidates(candidates, rarity: str | None, *, name_at, rarity_at):
    """Narrow duplicate catalogue rows using explicit printing marks only.

    Dragon Ball Masters labels special artwork ``SPR`` and Fusion World
    prints one or two stars beside the rarity. Those marks identify the
    physical printing more reliably than colour or glare in a phone photo.
    """
    if len(candidates) < 2:
        return candidates
    observed = (rarity or "").upper().replace(" ", "")
    star_count = observed.count("★") + observed.count("*")

    def label(candidate) -> str:
        return _normalise(f"{name_at(candidate)} {rarity_at(candidate)}")

    if "SPR" in observed or "SPECIALRARE" in _normalise(rarity):
        narrowed = [
            candidate for candidate in candidates
            if "spr" in label(candidate) or "specialrare" in label(candidate)
        ]
    elif star_count >= 2:
        narrowed = [candidate for candidate in candidates if "superalternateart" in label(candidate)]
    elif star_count == 1 or "ALTART" in observed or "ALTERNATEART" in observed:
        narrowed = [
            candidate for candidate in candidates
            if "alternateart" in label(candidate) and "superalternateart" not in label(candidate)
        ]
    else:
        narrowed = [
            candidate for candidate in candidates
            if not any(mark in label(candidate) for mark in (
                "alternateart", "superalternateart", "spr", "gold", "reprint", "eventpack",
                "championship", "winner", "finals",
            ))
        ]
    # More than one narrowed row can simply be the canonical record plus a
    # provider-cache copy. Keep the narrowed set so the caller can collapse
    # equivalent duplicates; only fall back when the mark matched nothing.
    return narrowed or candidates


def _game_for_category(category: PricingProviderCategory) -> str:
    value = _normalise(f"{category.name} {category.display_name or ''}")
    if "onepiece" in value:
        return "one_piece"
    if "dragonball" in value:
        return "dragon_ball"
    if "pokemon" in value:
        return "pokemon"
    return "other"


def _materialise_provider_card(
    session: Session,
    card: CapturedCard,
) -> tuple[CatalogItem, TcgCard] | str | None:
    """Promote one exact local provider-catalogue match into EzBay's catalogue.

    The TCGCSV catalogue is already downloaded by EzBay and covers recent
    promos that the older canonical import does not.  This is a local lookup
    (no credits and no network request), and only an exact name+number+set
    match is allowed to create a row.
    """
    prefixes = _latin_prefixes(card.character)
    query = (
        select(PricingProviderProduct, PricingProviderGroup, PricingProviderCategory)
        .join(PricingProviderGroup, PricingProviderGroup.id == PricingProviderProduct.group_id)
        .join(PricingProviderCategory, PricingProviderCategory.id == PricingProviderGroup.category_id)
        .where(PricingProviderProduct.product_type == "single")
    )
    if prefixes:
        query = query.where(or_(*(PricingProviderProduct.name.ilike(f"{word}%") for word in prefixes)))
    rows = session.execute(query).all()
    wanted_names = _name_aliases(card.character)
    candidates = [
        (product, group, category)
        for product, group, category in rows
        if _number(product.card_number) == _number(card.card_number)
        and wanted_names.intersection(set().union(
            _name_aliases(product.clean_name),
            _name_aliases(product.name),
            _name_aliases(_provider_base_name(product)),
            _name_aliases(_provider_character_name(product)),
        ))
    ]
    if card.set_name:
        scored = [(row, _provider_set_match_score(card.set_name, row[1])) for row in candidates]
        best = max((score for _row, score in scored), default=0)
        if best:
            candidates = [row for row, score in scored if score == best]
    if not candidates:
        return None

    candidates = _printing_candidates(
        candidates,
        card.rarity,
        name_at=lambda row: row[0].name,
        rarity_at=lambda row: row[0].rarity or "",
    )

    # Some promos have a regular and a named store-exclusive product with
    # the same clean name/number. Prefer the base product only when it is the
    # sole literal name match; never guess between two parallel artworks.
    if len(candidates) > 1:
        exact_names = [
            row for row in candidates
            if _name_aliases(row[0].name).intersection(wanted_names)
        ]
        if len(exact_names) == 1:
            candidates = exact_names
        else:
            base = [row for row in candidates if "(" not in row[0].name and "[" not in row[0].name]
            if len(base) == 1:
                candidates = base
    if len(candidates) != 1:
        return (
            f"{len(candidates)} provider catalogue matches for {card.character} "
            f"#{card.card_number}; choose the exact set or printing first."
        )

    product, group, category = candidates[0]
    group_keys = {
        _set_key(group.name), _set_key(group.abbreviation), _set_key(group.provider_group_id),
    }
    card_sets = session.scalars(select(CardSet)).all()
    card_set = next(
        (
            existing for existing in card_sets
            if group_keys.intersection({
                _set_key(existing.id), _set_key(existing.name), _set_key(existing.code),
            })
        ),
        None,
    )
    if card_set is None:
        safe_group_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", group.provider_group_id).strip("-")
        set_id = f"tcgcsv-{safe_group_id}"[:32]
        card_set = session.get(CardSet, set_id)
        if card_set is None:
            card_set = CardSet(
                id=set_id,
                name=group.name,
                code=group.abbreviation,
                game=_game_for_category(category),
            )
            session.add(card_set)
            session.flush()

    item_id = f"tcgp-{product.provider_product_id}"[:64]
    item = session.get(CatalogItem, item_id)
    # TCGCSV's clean_name still includes its display number for recent
    # promos ("Gothitelle 211").  EzBay keeps number in TcgCard.number, not
    # duplicated inside CatalogItem.name.
    display_name = _provider_base_name(product)
    if item is None:
        item = CatalogItem(
            id=item_id,
            item_type=CatalogItemType.TCG_CARD,
            name=display_name,
            image_url=product.image_url,
        )
        session.add(item)
        session.flush()
    else:
        item.name = display_name
        if not item.image_url and product.image_url:
            item.image_url = product.image_url
    tcg = session.get(TcgCard, item_id)
    if tcg is None:
        tcg = TcgCard(
            catalog_item_id=item_id,
            game=_game_for_category(category),
            set_id=card_set.id,
            set_code=group.abbreviation,
            number=product.card_number,
            character=display_name,
            rarity=product.rarity,
            variant=product.printing,
            language=card.language or "English",
        )
        session.add(tcg)
    else:
        tcg.character = display_name
    mapping = session.scalar(
        select(PricingExternalId).where(
            PricingExternalId.catalog_item_id == item_id,
            PricingExternalId.provider_key == product.provider_key,
            PricingExternalId.provider_product_id == product.provider_product_id,
        )
    )
    if mapping is None:
        session.add(PricingExternalId(
            catalog_item_id=item_id,
            provider_key=product.provider_key,
            provider_product_id=product.provider_product_id,
            provider_group_id=group.provider_group_id,
            match_confidence=Decimal("1"),
            match_method="exact_capture",
        ))
    return item, tcg


def resolve_catalogue_card(
    session: Session,
    card: CapturedCard,
    choose_printing=None,
) -> tuple[CatalogItem, TcgCard] | str:
    """Return one exact name+number match, or a human-readable issue.

    ``choose_printing`` is an optional tie-break for the last step only: when
    the remaining rows are printings of the same card in one language (base
    print and parallels), it may return one of them (by comparing artwork) or
    None to leave the choice to the owner.
    """
    if not card.character:
        return "Card name is required before it can be listed."

    # A small class of official gameplay/access cards genuinely has no
    # collector number. Their catalogue rows use an explicit N/A sentinel.
    # Resolve one only when the detected name (and set, when supplied) leaves
    # exactly one such record; never use this path for an ordinary numbered
    # card whose OCR merely failed.
    if not card.card_number:
        unnumbered_rows = session.execute(
            select(CatalogItem, TcgCard).join(
                TcgCard, TcgCard.catalog_item_id == CatalogItem.id
            )
        ).all()
        unnumbered = [
            (item, tcg) for item, tcg in unnumbered_rows
            if _normalise(tcg.number) in {"na", "unnumbered", "notapplicable"}
            and _name_aliases(item.name).intersection(_name_aliases(card.character))
        ]
        if card.set_name and unnumbered:
            set_ids = {tcg.set_id for _item, tcg in unnumbered if tcg.set_id}
            sets = {
                row.id: row for row in session.scalars(
                    select(CardSet).where(CardSet.id.in_(set_ids))
                ).all()
            }
            set_matches = [
                pair for pair in unnumbered
                if _set_matches(card.set_name, sets.get(pair[1].set_id), pair[1])
            ]
            if set_matches:
                unnumbered = set_matches
        if len(unnumbered) == 1:
            return unnumbered[0]
        if len(unnumbered) > 1:
            return f"{len(unnumbered)} unnumbered catalogue matches for {card.character}; choose the set first."
        return "No printed card number was detected and no unique unnumbered registry record matched."

    prefixes = _latin_prefixes(card.character)
    query = select(CatalogItem, TcgCard).join(
        TcgCard, TcgCard.catalog_item_id == CatalogItem.id
    )
    if prefixes:
        query = query.where(or_(*(CatalogItem.name.ilike(f"{word}%") for word in prefixes)))
    rows = session.execute(query).all()
    candidates = [
        (item, tcg) for item, tcg in rows
        if (
            _name_aliases(item.name).union(_name_aliases(_catalogue_character_name(item.name)))
        ).intersection(_name_aliases(card.character))
        and _number_matches(card.card_number, tcg)
    ]
    candidates = _prefer_language(candidates, card.language)
    if card.set_name and candidates:
        set_ids = {tcg.set_id for _item, tcg in candidates if tcg.set_id}
        sets = {
            row.id: row for row in session.scalars(select(CardSet).where(CardSet.id.in_(set_ids))).all()
        }
        scored = [
            (pair, _set_match_score(card.set_name, sets.get(pair[1].set_id), pair[1]))
            for pair in candidates
        ]
        best = max((score for _pair, score in scored), default=0)
        if best:
            candidates = [pair for pair, score in scored if score == best]
        else:
            candidates = []
    candidates = _printing_candidates(
        candidates,
        card.rarity,
        name_at=lambda pair: pair[0].name,
        rarity_at=lambda pair: pair[1].rarity or "",
    )
    candidates = _collapse_equivalent_catalogue_candidates(candidates)
    # Some older Dragon Ball catalogue rows were imported with a placeholder
    # display name (for example "SUPER DRAGON BALL HEROES UM9-019") even
    # though their printed number and set are correct. A full printed code is
    # a stronger identity key than that weak name. Accept it only when the
    # code resolves to exactly one catalogue row; short/numberless values are
    # never allowed through this fallback.
    if not candidates:
        printed_code = _normalise(card.card_number)
        has_prefix = bool(re.search(r"[A-Za-z]", card.card_number or ""))
        if has_prefix:
            number_rows = session.execute(
                select(CatalogItem, TcgCard)
                .join(TcgCard, TcgCard.catalog_item_id == CatalogItem.id)
            ).all()
            exact_code = [
                pair for pair in number_rows
                if _full_card_number(pair[1]) == printed_code
            ]
            if len(exact_code) == 1:
                candidates = exact_code
            if not candidates and "-" in (card.card_number or ""):
                observed_prefix = _normalise((card.card_number or "").split("-", 1)[0])
                ocr_candidates = [
                    pair for pair in number_rows
                    if _one_code_ocr_confusion(card.card_number, _full_card_number(pair[1]))
                    and observed_prefix == _normalise(pair[1].set_code)
                    and _name_aliases(pair[0].name).intersection(_name_aliases(card.character))
                ]
                if len(ocr_candidates) == 1:
                    candidates = ocr_candidates
            if not candidates and card.language:
                # Same full printed code, same language, and a name that differs
                # only by romanisation. The code and language carry the identity;
                # the name check just stops a misread code jumping to another card.
                romanised = [
                    pair for pair in _prefer_language(
                        # _number_matches, not _full_card_number: some parallels are
                        # filed under the set they were reprinted in (ST18-004_p1
                        # under OP12), and must stay in the running for the
                        # artwork tie-break instead of silently losing to the base.
                        [pair for pair in number_rows if _number_matches(card.card_number, pair[1])],
                        card.language, strict=True,
                    )
                    if _names_close(pair[0].name, card.character)
                ]
                candidates = _collapse_equivalent_catalogue_candidates(_printing_candidates(
                    romanised,
                    card.rarity,
                    name_at=lambda pair: pair[0].name,
                    rarity_at=lambda pair: pair[1].rarity or "",
                ))
    if not candidates:
        provider_match = _materialise_provider_card(session, card)
        if provider_match is not None:
            return provider_match
        return f"No catalogue match for {card.character} #{card.card_number}; correct the identity first."
    if len(candidates) != 1:
        if (
            choose_printing is not None
            and 2 <= len(candidates) <= 6
            and len({_language_key(tcg.language) for _item, tcg in candidates}) == 1
        ):
            chosen = choose_printing(candidates)
            if chosen is not None and chosen in candidates:
                return chosen
        return f"{len(candidates)} catalogue matches for {card.character} #{card.card_number}; choose the set first."
    return candidates[0]


def infer_unambiguous_variant(session: Session, catalog_item_id: str) -> CardVariant | None:
    """Use catalogue price variants only when exactly one printing exists."""
    reference = session.get(CatalogCard, catalog_item_id)
    prices = ((reference.raw_prices or {}).get("tcgplayer") or {}).get("prices") if reference else None
    if not isinstance(prices, dict):
        return None
    mapping = {
        "normal": CardVariant.NORMAL,
        "holofoil": CardVariant.HOLO,
        "reverseHolofoil": CardVariant.REVERSE_HOLO,
    }
    possibilities = {printing for key, printing in mapping.items() if prices.get(key)}
    return next(iter(possibilities)) if len(possibilities) == 1 else None


def _set_issue(session: Session, card: CapturedCard, issue: str,
               catalog_item_id: str | None = None) -> PromotionResult:
    row = session.scalar(select(CapturePromotion).where(CapturePromotion.captured_card_id == card.id))
    if row is None:
        row = CapturePromotion(captured_card_id=card.id, status=CapturePromotionStatus.NEEDS_REVIEW)
        session.add(row)
    row.status = CapturePromotionStatus.NEEDS_REVIEW
    row.catalog_item_id = catalog_item_id
    row.inventory_item_id = None
    row.shop_listing_id = None
    row.issue = issue
    return PromotionResult(False, issue=issue)


def _latest_export(session: Session, sku: str) -> EbayExportItem | None:
    return session.scalar(
        select(EbayExportItem).where(EbayExportItem.sku == sku)
        .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc()).limit(1)
    )


def promote_approved_card(session: Session, card: CapturedCard, settings) -> PromotionResult:
    """Promote one approved capture, without committing the transaction.

    Every possible issue is decided before inventory is touched. This is
    important: a human can correct and retry without adding the same copy a
    second time.
    """
    existing_promotion = session.scalar(
        select(CapturePromotion).where(CapturePromotion.captured_card_id == card.id)
    )
    if existing_promotion and existing_promotion.status == CapturePromotionStatus.REVISION_REQUIRED:
        return PromotionResult(
            True,
            issue=existing_promotion.issue,
            shop_listing_id=existing_promotion.shop_listing_id,
        )
    if existing_promotion and existing_promotion.status == CapturePromotionStatus.PROMOTED:
        # Reopening a successfully handed-off card is primarily a price edit.
        # Keep the existing physical stock link intact; if the listing has
        # already left EzBay, queue the external price revision explicitly.
        listing = session.get(ShopListing, existing_promotion.shop_listing_id)
        if listing is not None and card.approved_price is not None:
            desired_price = Decimal(str(card.approved_price))
            if listing.price != desired_price:
                listing.price = desired_price
                export = _latest_export(session, listing.id)
                if export and export.status in (
                    EbayExportItemStatus.PENDING,
                    EbayExportItemStatus.CONFIRMED,
                ):
                    existing_promotion.status = CapturePromotionStatus.REVISION_REQUIRED
                    existing_promotion.issue = (
                        f"EzBay price updated to AUD ${desired_price:.2f}. "
                        "Update the live eBay price, then mark this update complete."
                    )
                    return PromotionResult(
                        True,
                        issue=existing_promotion.issue,
                        shop_listing_id=listing.id,
                    )
        return PromotionResult(True, shop_listing_id=existing_promotion.shop_listing_id)

    resolved = resolve_catalogue_card(session, card)
    if isinstance(resolved, str):
        return _set_issue(session, card, resolved)
    catalog_item, tcg = resolved

    if card.variant is None:
        card.variant = infer_unambiguous_variant(session, catalog_item.id)
    if card.variant is None:
        return _set_issue(
            session, card,
            "Choose the physical printing (Normal, Holo, Reverse Holo, Poké Ball or Master Ball) first.",
            catalog_item.id,
        )

    shared = session.scalar(
        select(func.count()).select_from(CapturedCard).where(
            CapturedCard.front_image_path == card.front_image_path,
            CapturedCard.id != card.id,
        )
    ) or 0
    if shared and not re.match(r"card\d+_", Path(card.front_image_path).name):
        return _set_issue(
            session, card,
            "This photo is shared by multiple cards and needs a card-specific crop before listing.",
            catalog_item.id,
        )

    variant = card.variant
    condition = card.condition or "Near Mint"
    # Reuse a draft before creating another listing for the same holding.
    # The historical code looked only for ACTIVE rows, so approving a photo
    # while an older DRAFT existed created two independently publishable
    # listings backed by one inventory record. Prefer ACTIVE when both are
    # already present, otherwise promote the newest draft in place.
    active = session.scalar(
        select(ShopListing)
        .outerjoin(InventoryItem, InventoryItem.id == ShopListing.inventory_item_id)
        .where(
            ShopListing.catalog_item_id == catalog_item.id,
            ShopListing.status.in_((ShopListingStatus.ACTIVE, ShopListingStatus.DRAFT)),
            (InventoryItem.variant == variant) | (ShopListing.inventory_item_id.is_(None)),
            (InventoryItem.condition == condition) | (ShopListing.inventory_item_id.is_(None)),
        )
        .order_by(
            (ShopListing.status == ShopListingStatus.ACTIVE).desc(),
            ShopListing.created_at.desc(),
        )
        .limit(1)
    )
    export = _latest_export(session, active.id) if active else None
    exported_active = bool(
        active
        and export
        and export.status in (EbayExportItemStatus.PENDING, EbayExportItemStatus.CONFIRMED)
    )

    inventory = session.scalar(select(InventoryItem).where(
        InventoryItem.catalog_item_id == catalog_item.id,
        InventoryItem.variant == variant,
        InventoryItem.condition == condition,
    ))
    if exported_active:
        if inventory is None:
            catalog_card = session.get(CatalogCard, catalog_item.id)
            inventory = InventoryItem(
                card_id=catalog_card.id if catalog_card else None,
                catalog_item_id=catalog_item.id,
                variant=variant,
                condition=condition,
                quantity=1,
                scan_image_path=card.front_image_path,
                status=InventoryStatus.AVAILABLE,
            )
            session.add(inventory)
            session.flush()
        else:
            inventory.quantity += 1
            inventory.status = InventoryStatus.AVAILABLE
            if not inventory.scan_image_path:
                inventory.scan_image_path = card.front_image_path
        active.inventory_item_id = inventory.id
        active.quantity = inventory.quantity

        promotion = existing_promotion or CapturePromotion(captured_card_id=card.id)
        if existing_promotion is None:
            session.add(promotion)
        promotion.status = CapturePromotionStatus.REVISION_REQUIRED
        promotion.catalog_item_id = catalog_item.id
        promotion.inventory_item_id = inventory.id
        promotion.shop_listing_id = active.id
        promotion.issue = (
            f"Inventory recorded. Update the live eBay quantity to {inventory.quantity}, "
            "then mark this stock update complete."
        )
        return PromotionResult(True, issue=promotion.issue, shop_listing_id=active.id)

    if not r2_is_configured(settings):
        return _set_issue(session, card, "R2 image hosting is not configured.", catalog_item.id)
    try:
        image_url = upload_card_image(
            card.front_image_path, f"captured-approved/{card.id}.jpg",
            endpoint_url=settings.r2_endpoint_url,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket_name=settings.r2_bucket_name,
            public_base_url=load_pipeline_config().images.base_url,
        )
    except (R2UploadError, OSError) as exc:
        return _set_issue(session, card, f"Could not host the card photo: {exc}", catalog_item.id)

    if inventory is None:
        catalog_card = session.get(CatalogCard, catalog_item.id)
        inventory = InventoryItem(
            card_id=catalog_card.id if catalog_card else None,
            catalog_item_id=catalog_item.id, variant=variant, condition=condition,
            quantity=1, scan_image_path=card.front_image_path,
            status=InventoryStatus.AVAILABLE,
        )
        session.add(inventory)
        session.flush()
    else:
        inventory.quantity += 1
        inventory.status = InventoryStatus.AVAILABLE
        if not inventory.scan_image_path:
            inventory.scan_image_path = card.front_image_path

    price = Decimal(str(card.approved_price))
    title = catalog_item.name + (f" #{tcg.number}" if tcg.number else "")
    from .representative_backs import select_back
    representative = None if card.back_image_path else select_back(tcg.game, card.language or tcg.language, tcg.rarity)
    ebay_photo_paths = [
        path for path in (card.front_image_path, card.back_image_path or representative) if path
    ][:24]
    if active is None:
        printing_label = {
            CardVariant.NORMAL: "Normal",
            CardVariant.HOLO: "Holo",
            CardVariant.REVERSE_HOLO: "Reverse Holo",
            CardVariant.POKEBALL_PATTERN: "Poké Ball pattern",
            CardVariant.MASTERBALL_PATTERN: "Master Ball pattern",
        }[variant]
        active = ShopListing(
            catalog_item_id=catalog_item.id, inventory_item_id=inventory.id,
            title=title, condition=f"{condition} · {printing_label}", price=price,
            currency="AUD", quantity=inventory.quantity,
            status=ShopListingStatus.ACTIVE, image_url=image_url,
            ebay_photo_paths=ebay_photo_paths or None,
        )
        session.add(active)
        session.flush()
    else:
        active.inventory_item_id = inventory.id
        active.quantity = inventory.quantity
        active.price = price
        active.image_url = image_url
        if not active.ebay_photo_paths and ebay_photo_paths:
            active.ebay_photo_paths = ebay_photo_paths
        active.status = ShopListingStatus.ACTIVE

    promotion = existing_promotion or CapturePromotion(captured_card_id=card.id)
    if existing_promotion is None:
        session.add(promotion)
    promotion.status = CapturePromotionStatus.PROMOTED
    promotion.catalog_item_id = catalog_item.id
    promotion.inventory_item_id = inventory.id
    promotion.shop_listing_id = active.id
    promotion.issue = None
    return PromotionResult(True, shop_listing_id=active.id)
