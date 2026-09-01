"""Write identified cards into inventory.

Deliberately the only module in scan_ingest that mutates owned-inventory
state, so the rules about what may be written live in one place:

* A low-confidence identification is never committed (see `CommitRefused`).
  Identification already flags these; this is the enforcement point, so a
  caller that forgets to check still can't corrupt inventory.
* Duplicates increment an existing row rather than inserting a new one --
  inventory answers "how many do I have", not "tell me about copy #2".
* Every write emits an audit event, matching how the rest of the app
  records mutating actions.
"""

import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_event
from ..models import CardVariant, CatalogCard, InventoryItem, ScanSession
from .identify import Identification


class CommitRefused(Exception):
    """The identification isn't safe to commit unconfirmed."""


@dataclass
class CommitResult:
    item_id: str
    card_id: str
    card_name: str
    variant: CardVariant
    quantity: int
    created: bool
    image_replaced: bool = False

    @property
    def summary(self) -> str:
        action = "added" if self.created else f"incremented to x{self.quantity}"
        return f"{self.card_name} [{self.variant.value}] {action}"


def _image_quality(path: Path) -> int:
    """Cheap stand-in for image quality: pixel count, falling back to file
    size. Good enough to prefer a 600 DPI rescan over an earlier 190 DPI
    one, which is the case that actually matters -- not a perceptual
    quality model, and intentionally not worth one yet.
    """
    try:
        import cv2

        image = cv2.imread(str(path))
        if image is not None:
            return int(image.shape[0]) * int(image.shape[1])
    except Exception:
        pass
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _condition_slug(condition: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", condition.strip().lower()).strip("-") or "unknown"


def _store_scan_image(source: Path, media_dir: Path, set_code: str, number: str, variant: CardVariant, condition: str) -> Path:
    """Copy a crop into permanent media storage under a predictable name,
    so a listing can pull it straight from the inventory row later.

    The name carries every field that makes a holding distinct
    (card + variant + condition), because those are separate inventory rows
    with separate photos -- an earlier version keyed only on card+variant
    and silently had a Played scan overwrite the Near Mint one. Being
    derived rather than sequential also means replacing a photo with a
    better rescan overwrites in place instead of orphaning the old file.
    """
    destination_dir = media_dir / "cards"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{set_code}-{number}-{variant.value}-{_condition_slug(condition)}.jpg"
    # copyfile, not copy/copy2: those also set mode and timestamps on the
    # destination, which requires owning it. This file may have been written
    # by a different system user (the background service vs. a manual
    # reprocess or a web review), and only its contents need replacing.
    shutil.copyfile(source, destination)
    return destination


def commit_card(
    session: Session,
    identification: Identification,
    scan_image: Path,
    media_dir: Path,
    variant: CardVariant = CardVariant.NORMAL,
    condition: str = "Near Mint",
    scan_session: ScanSession | None = None,
    user: str = "scan-ingest",
    allow_unconfirmed: bool = False,
    notes: str | None = None,
) -> CommitResult:
    """Add one identified card to inventory, or increment the matching
    existing holding.

    Refuses anything still needing confirmation unless `allow_unconfirmed`
    is set -- which callers use only *after* a human has confirmed the
    identification, since confirming is precisely what clears the flag.
    """
    if identification.card_id is None:
        raise CommitRefused("cannot commit an unidentified card")
    if identification.needs_confirmation and not allow_unconfirmed:
        raise CommitRefused(
            f"identification needs confirmation (source={identification.source.value}, "
            f"confidence={identification.confidence:.2f}) -- confirm it before committing"
        )

    card = session.get(CatalogCard, identification.card_id)
    if card is None:
        raise CommitRefused(f"no catalog card {identification.card_id!r} -- is the set cached?")

    existing = session.scalar(
        select(InventoryItem).where(
            InventoryItem.card_id == card.id,
            InventoryItem.variant == variant,
            InventoryItem.condition == condition,
        )
    )

    set_code = (card.card_set.code or card.set_id) if card.card_set else card.set_id
    image_replaced = False

    if existing:
        existing.quantity += 1
        # Keep the better photo: a later rescan at higher resolution should
        # win, since this image is what ends up in a listing.
        if existing.scan_image_path and _image_quality(scan_image) > _image_quality(Path(existing.scan_image_path)):
            stored = _store_scan_image(scan_image, media_dir, set_code, card.number, variant, condition)
            existing.scan_image_path = str(stored)
            image_replaced = True
        elif not existing.scan_image_path:
            stored = _store_scan_image(scan_image, media_dir, set_code, card.number, variant, condition)
            existing.scan_image_path = str(stored)
        item = existing
        created = False
    else:
        stored = _store_scan_image(scan_image, media_dir, set_code, card.number, variant, condition)
        item = InventoryItem(
            id=str(uuid.uuid4()), card_id=card.id, variant=variant, condition=condition,
            quantity=1, scan_image_path=str(stored),
            session_id=scan_session.id if scan_session else None, notes=notes,
        )
        session.add(item)
        created = True

    if scan_session is not None:
        scan_session.cards_committed += 1

    session.commit()

    record_event(
        session, actor_type="user", actor_id=user, action="inventory.commit",
        resource_type="inventory_item", resource_id=item.id, outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={
            "card_id": card.id, "card_name": card.name, "number": card.number,
            "variant": variant.value, "condition": condition, "quantity": item.quantity,
            "created": created, "identification_source": identification.source.value,
            "confidence": identification.confidence,
        },
    )

    return CommitResult(
        item_id=item.id, card_id=card.id, card_name=card.name, variant=variant,
        quantity=item.quantity, created=created, image_replaced=image_replaced,
    )


def inventory_for_set(session: Session, set_id: str) -> list[InventoryItem]:
    return list(
        session.scalars(
            select(InventoryItem)
            .join(CatalogCard, InventoryItem.card_id == CatalogCard.id)
            .where(CatalogCard.set_id == set_id)
            .order_by(CatalogCard.number)
        )
    )


def inventory_totals(session: Session, set_id: str | None = None) -> tuple[int, int]:
    """(distinct holdings, total cards) -- the running count a resumed
    session reports so the operator knows where they left off."""
    query = select(InventoryItem)
    if set_id:
        query = query.join(CatalogCard, InventoryItem.card_id == CatalogCard.id).where(CatalogCard.set_id == set_id)
    items = list(session.scalars(query))
    return len(items), sum(item.quantity for item in items)
