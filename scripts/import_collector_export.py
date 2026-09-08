"""One-off (but safely re-runnable) import of a collector-app CSV export
into a NEW inventory lane: InventoryStatus.PERSONAL_COLLECTION.

The model already had this status defined (see InventoryStatus) but nothing
in the app ever set it -- these are cards the user owns and wants tracked
and reviewable, but NOT mixed into for-sale stock until they explicitly
promote one (see automation_control/personal_collection.py's "list for
sale" action, which just flips the status to AVAILABLE).

Row handling:
  - `Watchlist=true` rows are wishlist items, not owned -- excluded.
  - Game comes from the CSV `Category` column (free-text on TcgCard.game,
    so a brand-new game like Dragon Ball Super Fusion World needs no
    schema change -- same reasoning as import_one_piece_from_optcgapi.py).
  - Wherever possible each row is matched onto the catalogue that's
    *already* cached (20k+ Pokemon cards, 2.3k+ One Piece cards) so the
    resulting InventoryItem points at the same real CatalogItem a normal
    scan or a future shop listing would use:
      * Pokemon: CSV `Set` name -> CardSet.id (exact + light aliasing),
        CSV `Card Number` normalized (strip "/total", strip leading
        zeros unless alphanumeric like "TG01") -> catalog_item_id =
        f"{set_id}-{number}", the same id shape already in the DB.
      * One Piece / Dragon Ball: CSV `Card Number` is already
        "{SET_CODE}-{NUMBER}" (e.g. "OP15-032") -- catalog_item_id is
        derived with the exact same `_slug(game, set_code, number)`
        convention import_one_piece_from_optcgapi.py uses, so a later
        run of that script enriches these rows instead of duplicating
        them.
  - Whenever no existing catalogue row matches (unparsable card number,
    DON!! cards with no number, a set not yet cached), a NEW CatalogItem
    + TcgCard is created from whatever the CSV itself says, prefixed
    "pc-" so it can never collide with a real pokemontcg.io/optcgapi id.
    No data is ever dropped for lack of a match.
  - The 4 graded rows (Grade not blank/"Ungraded") get
    CatalogItemType.GRADED_CARD and grading_company parsed off the front
    of the Grade string ("PSA 9.0 MINT" -> "PSA").
  - `Average Cost Paid` -> allocated_cost_basis, but only when > 0 (a
    "0.0000" in this export means unknown/untracked, not free -- same
    convention as scanned stock, which leaves this blank rather than
    fabricate a cost).
  - `Market Price` -> one CardPrice(source="collector_import") row per
    item, for reference only -- nothing else reads this source, so it's
    inert until someone chooses to.

    .venv/bin/python -m scripts.import_collector_export <path.csv> --dry-run
    .venv/bin/python -m scripts.import_collector_export <path.csv>
"""

import argparse
import csv
import hashlib
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import (
    CardPrice,
    CardSet,
    CardVariant,
    CatalogItem,
    CatalogItemType,
    InventoryItem,
    InventoryStatus,
    TcgCard,
)

GAME_BY_CATEGORY = {
    "pokemon": "pokemon",
    "one piece": "one_piece",
    "dragon ball super fusion world": "dragon_ball_fusion_world",
}

# Known near-miss aliases between this export's Set names and this DB's
# CardSet.name, found by inspecting the 69 unmatched pairs by hand.
SET_NAME_ALIASES = {
    "pokemon 151": "151",
    "sv: 151": "151",
    "base set (unlimited)": "base set",
    "base set (shadowless)": "base set",
    "xy base set": "xy",
    "sword & shield base set": "sword & shield",
    "scarlet & violet base set": "scarlet & violet",
    "sun & moon base set": "sun & moon",
    "diamond & pearl base set": "diamond & pearl",
    "black & white base set": "black & white",
    "shining fates: shiny vault": "shining fates",
    "hidden fates: shiny vault": "hidden fates",
}

CODE_NUMBER_RE = re.compile(r"^([A-Za-z]+\d+)-(\d+)$")
GRADING_COMPANY_RE = re.compile(r"^([A-Za-z]+)\s")


def _slug(*parts: str) -> str:
    joined = "-".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")


def _pc_id(*parts: str) -> str:
    """Synthetic id for a row with no catalogue match -- short, deterministic
    (re-running the import lands on the same row), and namespaced so it can
    never collide with a real pokemontcg.io/optcgapi id."""
    digest = hashlib.md5("|".join(parts).encode()).hexdigest()[:16]
    return f"pc-{digest}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _decimal(raw: str) -> Decimal | None:
    if not raw or not raw.strip():
        return None
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _variant_for(text: str) -> CardVariant:
    low = (text or "").lower()
    if "reverse" in low:
        return CardVariant.REVERSE_HOLO
    if "holo" in low or "foil" in low:
        return CardVariant.HOLO
    return CardVariant.NORMAL


def _is_graded(grade: str) -> bool:
    return bool(grade and grade.strip() and grade.strip().lower() != "ungraded")


def _grading_company(grade: str) -> str | None:
    match = GRADING_COMPANY_RE.match(grade.strip())
    return match.group(1).upper() if match else None


def _pokemon_set_lookup(session) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for set_id, name in session.execute(select(CardSet.id, CardSet.name)).all():
        lookup[_norm(name)] = set_id
    return lookup


def _pokemon_number(raw: str) -> str | None:
    if not raw or not raw.strip():
        return None
    number = raw.strip().split("/")[0]
    if number.isdigit():
        return str(int(number))
    return number.upper()


def resolve_pokemon_item(session, set_lookup, row) -> tuple[str, bool]:
    """Returns (catalog_item_id, matched_existing)."""
    set_name = row["Set"].strip()
    number = _pokemon_number(row["Card Number"])
    normalized = _norm(set_name)
    set_id = set_lookup.get(normalized) or set_lookup.get(SET_NAME_ALIASES.get(normalized, ""))

    if set_id and number:
        candidate = f"{set_id}-{number}"
        if session.get(TcgCard, candidate):
            return candidate, True

    return _pc_id("pokemon", set_name, row["Card Number"], row["Product Name"]), False


def resolve_coded_item(session, game: str, row) -> tuple[str, bool]:
    """One Piece / Dragon Ball: Card Number is already SETCODE-NUMBER."""
    raw = row["Card Number"].strip()
    match = CODE_NUMBER_RE.match(raw)
    if not match:
        return _pc_id(game, row["Set"], raw, row["Product Name"]), False

    set_code, number = match.group(1).upper(), match.group(2)
    candidate = _slug(game, set_code, number)
    existing = session.get(TcgCard, candidate)
    return candidate, existing is not None


def import_rows(session, rows: list[dict], apply: bool) -> dict:
    set_lookup = _pokemon_set_lookup(session)
    stats = {"imported": 0, "skipped_watchlist": 0, "matched_existing": 0, "created_new": 0, "priced": 0, "graded": 0}

    for row in rows:
        if row["Watchlist"].strip().lower() == "true":
            stats["skipped_watchlist"] += 1
            continue

        category = row["Category"].strip().lower()
        game = GAME_BY_CATEGORY.get(category)
        if game is None:
            print(f"  skip (unknown category {row['Category']!r}): {row['Product Name']}")
            continue

        if game == "pokemon":
            item_id, matched = resolve_pokemon_item(session, set_lookup, row)
        else:
            item_id, matched = resolve_coded_item(session, game, row)

        stats["matched_existing" if matched else "created_new"] += 1

        grade = row["Grade"].strip()
        graded = _is_graded(grade)
        if graded:
            stats["graded"] += 1

        market_price = _decimal(row["Market Price (As of 2026-09-08)"])
        cost_paid = _decimal(row["Average Cost Paid"])
        quantity = int(row["Quantity"]) if row["Quantity"].strip().isdigit() else 1
        condition = grade if graded else (row["Card Condition"].strip() or "Near Mint")

        stats["imported"] += 1
        if not apply:
            continue

        if not matched:
            catalog_item = session.get(CatalogItem, item_id)
            if catalog_item is None:
                catalog_item = CatalogItem(
                    id=item_id,
                    item_type=CatalogItemType.GRADED_CARD if graded else CatalogItemType.TCG_CARD,
                    name=row["Product Name"].strip(),
                )
                session.add(catalog_item)
            tcg_card = session.get(TcgCard, item_id)
            if tcg_card is None:
                tcg_card = TcgCard(catalog_item_id=item_id)
                session.add(tcg_card)
            tcg_card.game = game
            tcg_card.set_code = None
            tcg_card.number = row["Card Number"].strip() or None
            tcg_card.character = row["Product Name"].strip()
            tcg_card.rarity = row["Rarity"].strip() or None
            tcg_card.variant = row["Variance"].strip() or None
            if game == "pokemon":
                tcg_card.set_id = set_lookup.get(_norm(row["Set"].strip()))
            else:
                m = CODE_NUMBER_RE.match(row["Card Number"].strip())
                tcg_card.set_id = m.group(1).upper() if m else None
                tcg_card.set_code = m.group(1).upper() if m else None

        added_at = None
        if row["Date Added"].strip():
            try:
                added_at = datetime.strptime(row["Date Added"].strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                added_at = None

        inventory_item = InventoryItem(
            card_id=None,
            catalog_item_id=item_id,
            variant=_variant_for(row["Variance"]),
            condition=condition,
            quantity=quantity,
            status=InventoryStatus.PERSONAL_COLLECTION,
            allocated_cost_basis=cost_paid,
            grading_company=_grading_company(grade) if graded else None,
            notes=row["Notes"].strip() or None,
        )
        if added_at is not None:
            inventory_item.added_at = added_at
        session.add(inventory_item)

        if market_price is not None:
            session.add(CardPrice(
                id=str(uuid.uuid4()),
                card_id=item_id,
                variant=None,
                source="collector_import",
                price=market_price,
                currency="USD",
            ))
            stats["priced"] += 1

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path")
    parser.add_argument("--dry-run", action="store_true", help="report what would happen, write nothing")
    args = parser.parse_args(argv)

    with open(args.csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    with SessionLocal() as session:
        stats = import_rows(session, rows, apply=not args.dry_run)
        if not args.dry_run:
            session.commit()

    print(f"rows in file: {len(rows)}")
    print(f"skipped (watchlist): {stats['skipped_watchlist']}")
    print(f"{'would import' if args.dry_run else 'imported'}: {stats['imported']}")
    print(f"  matched existing catalogue item: {stats['matched_existing']}")
    print(f"  new catalogue item created: {stats['created_new']}")
    print(f"  graded: {stats['graded']}")
    print(f"  priced (collector_import): {stats['priced']}")
    if args.dry_run:
        print("\n--dry-run: nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
