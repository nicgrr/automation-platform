"""Ordered front/back photo batches for repairing existing eBay listings.

Photos are uploaded as fronts only or alternating front/back pairs. Recognition and pricing run
in the background, producing CRM draft proposals. Applying a proposal never
writes to eBay; the existing Listing Studio confirmation remains the only live
revision path.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.convert_to_ebay_template import sanitize_title
from scripts.list_for_sale import best_price

from .audit import record_event
from .auth import require_dashboard_user
from .card_recognition import RecognitionError, extract_card_details, suggest_ebay_listing_copy
from .capture_promotion import resolve_catalogue_card
from .database import SessionLocal, get_session
from .ebay_publish import (
    EBAY_MIN_FIXED_PRICE,
    _confirmed_export_target,
    _current_live_ebay_price,
    _store_listing_photo,
    revise_listing_on_ebay,
)
from .listing_quality import generated_ebay_description, generated_ebay_title
from .models import (
    CardSet,
    CapturedCard,
    CatalogCard,
    CatalogItem,
    EbayListingLink,
    EbayListingLinkStatus,
    FoilLabel,
    InventoryPlacement,
    InventoryItem,
    ListingPhotoBatch,
    ListingPhotoBatchStatus,
    ListingPhotoPair,
    ListingPhotoPairStatus,
    ShopListing,
    ShopListingStatus,
    StorageLocation,
    TcgCard,
)
from .images import serve_image, serve_thumb
from .ui import brand_header, page, pill


router = APIRouter(tags=["ebay-listing-photo-batches"])

MAX_BATCH_PHOTOS = 40
MAX_UNCONFIRMED_PRICE_RATIO = Decimal("2")
MIN_UNCONFIRMED_PRICE_RATIO = Decimal("0.5")
GAME_HINTS = {
    "auto": "trading card",
    "pokemon": "Pokemon TCG",
    "one_piece": "One Piece Card Game",
    "dragon_ball": "Dragon Ball trading card",
}
TRAY_CODE_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")


def _normalise(value: str | None) -> str:
    ascii_value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())


def _tray_code(value: str | None) -> str:
    code = re.sub(r"\s+", "-", (value or "").strip().upper())
    code = re.sub(r"-+", "-", code).strip("-")
    if not code:
        return ""
    if len(code) > 32 or not TRAY_CODE_RE.fullmatch(code):
        raise ValueError("Tray code must use only letters, numbers and hyphens (for example DB-01).")
    return code


def _reserve_storage_slots(
    session: Session, tray: str, count: int, requested_start: int | None,
) -> tuple[int, list[StorageLocation]]:
    """Reserve a stable, never-renumbered run of physical tray slots."""
    existing = list(session.scalars(
        select(StorageLocation).where(StorageLocation.name.like(f"{tray}-%"))
    ))
    used: dict[int, StorageLocation] = {}
    prefix = f"{tray}-"
    for location in existing:
        suffix = location.name.removeprefix(prefix)
        if suffix.isdigit():
            used[int(suffix)] = location

    start = requested_start if requested_start is not None else (max(used, default=0) + 1)
    if start < 1:
        raise ValueError("Starting slot must be 1 or greater.")
    collisions = [slot for slot in range(start, start + count) if slot in used]
    if collisions:
        first = f"{tray}-{collisions[0]:03d}"
        raise ValueError(f"Storage slot {first} already exists. Leave starting slot blank to use the next free run.")

    locations = []
    for slot in range(start, start + count):
        location = StorageLocation(name=f"{tray}-{slot:03d}", kind="card_slot")
        session.add(location)
        locations.append(location)
    session.flush()
    return start, locations


def _placement_for_pair(session: Session, pair_id: str) -> InventoryPlacement | None:
    return session.scalar(
        select(InventoryPlacement).where(InventoryPlacement.listing_photo_pair_id == pair_id)
    )


def _link_pair_inventory(session: Session, pair: ListingPhotoPair, inventory: InventoryItem | None) -> None:
    if inventory is None:
        return
    placement = _placement_for_pair(session, pair.id)
    if placement is None:
        return
    placement.inventory_item_id = inventory.id


def _name_aliases(value: str | None) -> set[str]:
    raw = value or ""
    aliases = {_normalise(raw)}
    aliases.update(_normalise(part) for part in re.findall(r"\(([^()]*)\)", raw))
    return {alias for alias in aliases if alias}


def _display_name(detected_name: str | None, fallback: str) -> str:
    """Prefer the readable English portion of a bilingual recognition."""
    ascii_value = unicodedata.normalize("NFKD", detected_name or "").encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[()]", "", ascii_value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return cleaned if re.search(r"[A-Za-z]", cleaned) else fallback


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _proposal_price(
    session: Session, item: CatalogItem, listing: ShopListing,
) -> tuple[Decimal, str]:
    """Use exact market data without one outlier repricing a live listing.

    A manual valuation remains authoritative. Automated signals more than
    2x or less than half the current live amount are shown as needing review,
    while the one-click eBay action keeps the existing live price unchanged.
    """
    found = best_price(session, item.id)
    if found is None:
        return listing.price, "current listing (no exact market signal)"
    market_price, source = found
    proposed = max(_money(market_price), EBAY_MIN_FIXED_PRICE)
    live_price = _current_live_ebay_price(session, listing.id)
    if source != "manual" and live_price and live_price > 0:
        ratio = proposed / live_price
        if ratio > MAX_UNCONFIRMED_PRICE_RATIO or ratio < MIN_UNCONFIRMED_PRICE_RATIO:
            return _money(live_price), f"current live eBay (market outlier held: {source})"[:64]
    return proposed, source


def _active_listing_candidates(
    session: Session, catalog_item: CatalogItem, extracted_name: str,
) -> list[ShopListing]:
    """Find one existing listing, preferring exact canonical identity.

    The name fallback lets a newly corrected catalogue record reconnect an
    old listing whose previous number was wrong. More than one match is held
    for review rather than selecting whichever row happened to be returned.
    """
    statuses = (ShopListingStatus.ACTIVE, ShopListingStatus.DRAFT)
    exact = list(session.scalars(
        select(ShopListing).where(
            ShopListing.catalog_item_id == catalog_item.id,
            ShopListing.status.in_(statuses),
        )
    ))
    if exact:
        active = [listing for listing in exact if listing.status == ShopListingStatus.ACTIVE]
        return active or exact

    target_card = session.get(TcgCard, catalog_item.id)
    rows = session.execute(
        select(ShopListing, CatalogItem, TcgCard)
        .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
        .outerjoin(TcgCard, TcgCard.catalog_item_id == CatalogItem.id)
        .where(ShopListing.status.in_(statuses))
    ).all()
    wanted = _name_aliases(catalog_item.name).union(_name_aliases(extracted_name))

    def number_matches(listing: ShopListing, candidate_card: TcgCard | None) -> bool:
        if target_card is None or not target_card.number:
            return False
        wanted_number = _normalise(target_card.number)
        if target_card.set_code and not wanted_number.startswith(_normalise(target_card.set_code)):
            wanted_number = _normalise(f"{target_card.set_code}-{target_card.number}")
        if candidate_card and candidate_card.number:
            candidate_number = _normalise(candidate_card.number)
            if candidate_card.set_code and not candidate_number.startswith(_normalise(candidate_card.set_code)):
                candidate_number = _normalise(f"{candidate_card.set_code}-{candidate_card.number}")
            if candidate_number == wanted_number:
                return True
        listing_text = _normalise(f"{listing.title} {listing.ebay_title or ''}")
        return bool(wanted_number and wanted_number in listing_text)

    matches = [
        listing for listing, item, candidate_card in rows
        if _name_aliases(item.name).intersection(wanted)
        and number_matches(listing, candidate_card)
    ]
    active = [listing for listing in matches if listing.status == ShopListingStatus.ACTIVE]
    return active or matches


def _refresh_batch_counts(session: Session, batch: ListingPhotoBatch) -> None:
    statuses = list(session.scalars(
        select(ListingPhotoPair.status).where(ListingPhotoPair.batch_id == batch.id)
    ))
    batch.ready_pairs = statuses.count(ListingPhotoPairStatus.READY)
    batch.review_pairs = statuses.count(ListingPhotoPairStatus.NEEDS_REVIEW)
    batch.failed_pairs = statuses.count(ListingPhotoPairStatus.FAILED)
    batch.processed_pairs = len([status for status in statuses if status != ListingPhotoPairStatus.PROCESSING])


def _known_facts(
    listing: ShopListing,
    item: CatalogItem,
    card: TcgCard,
    card_set: CardSet | None,
    inventory: InventoryItem | None,
    detected_name: str | None = None,
) -> dict[str, str]:
    variant = ""
    if inventory and inventory.variant:
        variant = inventory.variant.value.replace("_", " ").title()
    elif card.variant:
        variant = card.variant.replace("_", " ").title()
    return {
        "card name": _display_name(detected_name, item.name),
        "game": card.game,
        "set": card_set.name if card_set else "",
        "card number": card.number or "",
        "rarity": card.rarity or "",
        "parallel": card.parallel or "",
        "finish": variant,
        "language": card.language or "English",
        "condition": inventory.condition if inventory and inventory.condition else (listing.condition or "Ungraded"),
        "current eBay title": listing.ebay_title or listing.title,
    }


def _prepare_pair(pair_id: str, settings) -> None:
    with SessionLocal() as session:
        pair = session.get(ListingPhotoPair, pair_id)
        if pair is None:
            return
        batch = session.get(ListingPhotoBatch, pair.batch_id)
        game = GAME_HINTS.get(batch.game_hint or "auto", GAME_HINTS["auto"]) if batch else GAME_HINTS["auto"]
        try:
            extracted = extract_card_details(
                Path(pair.front_image_path),
                Path(pair.back_image_path) if pair.back_image_path else None,
                api_key=settings.anthropic_api_key,
                game=game,
            )
            if len(extracted) != 1:
                pair.status = ListingPhotoPairStatus.NEEDS_REVIEW
                pair.issue = (
                    "No card was confidently detected in this pair."
                    if not extracted else
                    f"{len(extracted)} cards were detected; each pair must show one card."
                )
                pair.extracted = {"cards": [card.model_dump() for card in extracted]}
                return

            found = extracted[0]
            pair.extracted = found.model_dump()
            capture = CapturedCard(
                front_image_path=pair.front_image_path,
                back_image_path=pair.back_image_path,
                character=found.character,
                set_name=found.set_name,
                card_number=found.card_number,
                rarity=found.rarity,
                language=found.language,
                graded=found.graded,
            )
            resolved = resolve_catalogue_card(session, capture)
            if isinstance(resolved, str):
                pair.status = ListingPhotoPairStatus.NEEDS_REVIEW
                pair.issue = resolved
                return
            item, card = resolved
            card_set = session.get(CardSet, card.set_id) if card.set_id else None
            inventory_matches = list(session.scalars(
                select(InventoryItem).where(
                    InventoryItem.catalog_item_id == item.id,
                    InventoryItem.quantity > 0,
                )
            ))
            if len(inventory_matches) == 1:
                _link_pair_inventory(session, pair, inventory_matches[0])
            candidates = _active_listing_candidates(session, item, found.character)
            if len(candidates) != 1:
                pair.status = ListingPhotoPairStatus.NEEDS_REVIEW
                pair.proposed_catalog_item_id = item.id
                pair.issue = (
                    f"No active/draft CRM listing matches {item.name} #{card.number}."
                    if not candidates else
                    f"{len(candidates)} CRM listings could match {item.name} #{card.number}; choose one manually."
                )
                return

            listing = candidates[0]
            inventory = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
            _link_pair_inventory(session, pair, inventory)
            pair.matched_shop_listing_id = listing.id
            pair.proposed_catalog_item_id = item.id

            fallback_title = generated_ebay_title(listing, item, card, card_set, inventory)
            fallback_description = generated_ebay_description(
                listing, item, card, card_set, inventory, has_own_photo=True,
            )
            try:
                suggestion = suggest_ebay_listing_copy(
                    [Path(pair.front_image_path), *([Path(pair.back_image_path)] if pair.back_image_path else [])],
                    api_key=settings.anthropic_api_key,
                    known_facts=_known_facts(
                        listing, item, card, card_set, inventory,
                        detected_name=found.character,
                    ),
                )
                proposed_title = sanitize_title(suggestion.title).strip()
                proposed_description = suggestion.description.strip()
                if not proposed_title or len(proposed_title) > 80 or not proposed_description:
                    proposed_title, proposed_description = fallback_title, fallback_description
            except RecognitionError:
                proposed_title, proposed_description = fallback_title, fallback_description

            pair.proposed_price, pair.price_source = _proposal_price(session, item, listing)
            pair.proposed_title = proposed_title
            pair.proposed_description = proposed_description[:5000]
            pair.status = ListingPhotoPairStatus.READY
            pair.issue = None
        except Exception as exc:
            pair.status = ListingPhotoPairStatus.FAILED
            pair.issue = f"{type(exc).__name__}: {exc}"[:512]
        finally:
            pair.updated_at = datetime.now(UTC)
            session.commit()


def _run_batch(batch_id: str, settings, user: str) -> None:
    """Process unfinished pairs and safely resume an interrupted batch."""
    with SessionLocal() as session:
        pair_ids = list(session.scalars(
            select(ListingPhotoPair.id)
            .where(
                ListingPhotoPair.batch_id == batch_id,
                ListingPhotoPair.status == ListingPhotoPairStatus.PROCESSING,
            )
            .order_by(ListingPhotoPair.position)
        ))
    for pair_id in pair_ids:
        _prepare_pair(pair_id, settings)
        with SessionLocal() as session:
            batch = session.get(ListingPhotoBatch, batch_id)
            if batch is None:
                continue
            _refresh_batch_counts(session, batch)
            session.commit()

    with SessionLocal() as session:
        batch = session.get(ListingPhotoBatch, batch_id)
        if batch:
            _refresh_batch_counts(session, batch)
            unfinished = batch.processed_pairs < batch.total_pairs
            batch.status = ListingPhotoBatchStatus.RUNNING if unfinished else ListingPhotoBatchStatus.DONE
            batch.finished_at = None if unfinished else datetime.now(UTC)
            session.commit()
            record_event(
                session,
                actor_type="user", actor_id=user, action="listing_photo_batch.recognise",
                resource_type="listing_photo_batch", resource_id=batch_id,
                outcome="incomplete" if unfinished else ("success" if not batch.failed_pairs else "partial"),
                correlation_id=str(uuid.uuid4()),
                details={
                    "total_pairs": batch.total_pairs,
                    "processed_pairs": batch.processed_pairs,
                    "ready_pairs": batch.ready_pairs,
                    "review_pairs": batch.review_pairs,
                    "failed_pairs": batch.failed_pairs,
                },
            )


def _retry_batch(batch_id: str, settings, user: str) -> None:
    """Re-run only held pairs after catalogue/matching rules improve."""
    with SessionLocal() as session:
        batch = session.get(ListingPhotoBatch, batch_id)
        if batch is None:
            return
        pair_ids = list(session.scalars(
            select(ListingPhotoPair.id).where(
                ListingPhotoPair.batch_id == batch_id,
                ListingPhotoPair.status.in_((
                    ListingPhotoPairStatus.NEEDS_REVIEW,
                    ListingPhotoPairStatus.FAILED,
                )),
            ).order_by(ListingPhotoPair.position)
        ))
        for pair_id in pair_ids:
            pair = session.get(ListingPhotoPair, pair_id)
            if pair:
                pair.status = ListingPhotoPairStatus.PROCESSING
                pair.issue = None
        batch.status = ListingPhotoBatchStatus.RUNNING
        batch.finished_at = None
        _refresh_batch_counts(session, batch)
        session.commit()

    for pair_id in pair_ids:
        _prepare_pair(pair_id, settings)
        with SessionLocal() as session:
            batch = session.get(ListingPhotoBatch, batch_id)
            if batch:
                _refresh_batch_counts(session, batch)
                session.commit()

    with SessionLocal() as session:
        batch = session.get(ListingPhotoBatch, batch_id)
        if batch:
            batch.status = ListingPhotoBatchStatus.DONE
            batch.finished_at = datetime.now(UTC)
            _refresh_batch_counts(session, batch)
            session.commit()
            record_event(
                session,
                actor_type="user", actor_id=user, action="listing_photo_batch.retry",
                resource_type="listing_photo_batch", resource_id=batch_id,
                outcome="success" if not batch.failed_pairs else "partial",
                correlation_id=str(uuid.uuid4()),
                details={
                    "retried_pairs": len(pair_ids),
                    "ready_pairs": batch.ready_pairs,
                    "review_pairs": batch.review_pairs,
                    "failed_pairs": batch.failed_pairs,
                },
            )


def _live_ebay_target(session: Session, listing: ShopListing) -> str | None:
    link = session.scalar(select(EbayListingLink).where(
        EbayListingLink.shop_listing_id == listing.id,
        EbayListingLink.status == EbayListingLinkStatus.ACTIVE,
        EbayListingLink.ebay_listing_id.is_not(None),
    ))
    if link and link.ebay_listing_id:
        return link.ebay_listing_id
    legacy = _confirmed_export_target(session, listing.id)
    return legacy.ebay_listing_id if legacy else None


def _set_confirmed_quantity(
    session: Session, pair: ListingPhotoPair, listing: ShopListing, quantity: int | None,
) -> str | None:
    if quantity is None:
        return None
    if quantity < 1 or quantity > 999:
        return "Quantity must be between 1 and 999."
    listing.quantity = quantity
    inventory = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    if inventory:
        inventory.quantity = quantity
    placement = _placement_for_pair(session, pair.id)
    if placement:
        placement.quantity = quantity
    return None


def _quantity_issue(quantity: int) -> str | None:
    return None if 1 <= quantity <= 999 else "Quantity must be between 1 and 999."


def _apply_pair(session: Session, pair: ListingPhotoPair, quantity: int | None = None) -> str | None:
    if pair.status != ListingPhotoPairStatus.READY:
        return "This pair is not ready to apply."
    listing = session.get(ShopListing, pair.matched_shop_listing_id)
    item = session.get(CatalogItem, pair.proposed_catalog_item_id)
    card = session.get(TcgCard, pair.proposed_catalog_item_id)
    if listing is None or item is None or card is None:
        return "The matched listing or verified card identity no longer exists."

    if listing.catalog_item_id != item.id and listing.inventory_item_id:
        inventory = session.get(InventoryItem, listing.inventory_item_id)
        if inventory is None:
            return "The listing's inventory row no longer exists."
        duplicate = session.scalar(
            select(InventoryItem).where(
                InventoryItem.catalog_item_id == item.id,
                InventoryItem.variant == inventory.variant,
                InventoryItem.condition == inventory.condition,
                InventoryItem.id != inventory.id,
            )
        )
        if duplicate:
            return "Correct identity already has a separate inventory row; merge it manually before relinking."
        target_reference = session.get(CatalogCard, item.id)
        if inventory.card_id is not None and target_reference is None:
            return "Correct catalogue item has no scan-reference row; identity relink needs review."
        inventory.catalog_item_id = item.id
        inventory.card_id = item.id if target_reference else None
        if target_reference:
            for label in session.scalars(
                select(FoilLabel).where(FoilLabel.inventory_item_id == inventory.id)
            ):
                label.card_id = item.id

    listing.catalog_item_id = item.id
    listing.title = f"{item.name} #{card.number}" if card.number else item.name
    listing.ebay_title = pair.proposed_title
    listing.ebay_description = pair.proposed_description
    listing.ebay_photo_paths = [
        pair.front_image_path,
        *([pair.back_image_path] if pair.back_image_path else []),
    ]
    if pair.proposed_price is not None:
        listing.price = pair.proposed_price
    _link_pair_inventory(
        session, pair,
        session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None,
    )
    quantity_issue = _set_confirmed_quantity(session, pair, listing, quantity)
    if quantity_issue:
        return quantity_issue
    pair.status = ListingPhotoPairStatus.APPLIED
    pair.issue = None
    pair.updated_at = datetime.now(UTC)
    return None


@router.get("/ebay-listing-bulk-photos", response_class=HTMLResponse)
def bulk_photo_page(
    error: str = Query(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    recent = list(session.scalars(
        select(ListingPhotoBatch).order_by(ListingPhotoBatch.started_at.desc()).limit(5)
    ))
    recent_html = "".join(
        f"<li><a href='/ebay-listing-bulk-photos/{batch.id}'>"
        f"{batch.started_at:%d %b %H:%M} · {batch.total_pairs} pair(s) · {escape(batch.status.value)}</a></li>"
        for batch in recent
    ) or "<li>No previous batches.</li>"
    error_html = f"<div class='callout bad'>{escape(error)}</div>" if error else ""
    body = (
        brand_header("Photograph & store cards")
        + "<p class='subtitle'>Choose fronts only or front + back pairs below. "
        "EzBay identifies each card, checks its exact listing price, reserves a permanent tray slot and prepares CRM drafts. Nothing is sent to eBay automatically.</p>"
        + error_html
        + f"<section class='panel'><form method='post' action='/ebay-listing-bulk-photos' enctype='multipart/form-data' id='batch-form' data-max-photos='{MAX_BATCH_PHOTOS}'>"
        + "<label>Card family <select name='game_hint' id='game-hint'>"
        + "<option value='auto'>Mixed / detect automatically</option><option value='pokemon'>Pokémon</option>"
        + "<option value='one_piece'>One Piece</option><option value='dragon_ball'>Dragon Ball</option></select></label>"
        + "<fieldset class='photo-mode'><legend>Which photos are you uploading?</legend>"
        + "<label class='check'><input type='radio' name='has_backs' value='no' checked> Fronts only — one photo per card</label>"
        + "<label class='check'><input type='radio' name='has_backs' value='yes'> Front + back pairs — two photos per card</label>"
        + "</fieldset><p id='photo-mode-help' class='subtitle' aria-live='polite'>Select front, front, front. Keep any shared back photo out of this selection.</p>"
        + "<div class='storage-fields'><label>Storage tray code<input name='storage_tray' id='storage-tray' "
        + "placeholder='DB-01' maxlength='32' autocomplete='off'></label>"
        + "<label>Starting slot (optional)<input name='starting_slot' type='number' min='1' inputmode='numeric' "
        + "placeholder='Next free'></label></div>"
        + "<p class='subtitle'>Add a tray code to reserve one permanent slot per card. Leave the starting slot blank and EzBay continues after the last slot already used.</p>"
        + f"<label class='picker'>Add photos — up to {MAX_BATCH_PHOTOS} total<input id='batch-photos' type='file' name='photos' "
        + "accept='image/*,.heic,.heif' multiple></label>"
        + "<div class='photo-pick-actions'><button type='button' class='btn small ghost' id='add-photo-group'>Add another group</button>"
        + "<button type='button' class='btn small ghost' id='clear-photo-groups'>Clear selected</button></div>"
        + "<p class='subtitle'>On iPhone, select smaller groups if the Photos picker will not accept all 40 at once. Each group is appended in order.</p>"
        + "<p id='pair-summary' class='subtitle'>No photos selected.</p><div id='pair-preview' class='pair-preview'></div>"
        + "<div id='upload-progress-panel' class='upload-state' aria-live='polite' hidden>"
        + "<div class='progress-label'><strong id='upload-status'>Preparing photos…</strong>"
        + "<span id='upload-percent'>0%</span></div>"
        + "<div class='progress-track' role='progressbar' aria-label='Photo upload progress' "
        + "aria-valuemin='0' aria-valuemax='100' aria-valuenow='0' id='upload-track'>"
        + "<span id='upload-fill' class='progress-fill'></span></div>"
        + "<small id='upload-detail'>Keep this screen open while the photos upload.</small></div>"
        + "<button id='upload-button'>Upload and identify</button></form></section>"
        + f"<section class='panel'><h2>Recent batches</h2><ul class='events'>{recent_html}</ul></section>"
        + """<style>
.picker{display:grid;gap:8px;padding:16px;border:1px dashed var(--panel-border);border-radius:10px}.check{display:flex;gap:8px;align-items:center}.storage-fields{display:grid;grid-template-columns:1fr 1fr;gap:10px}.photo-pick-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.photo-mode{display:grid;gap:12px;margin:16px 0;padding:14px;border:1px solid var(--panel-border);border-radius:10px}.photo-mode legend{font-weight:600}.photo-mode .check{margin:0;cursor:pointer}.check input{width:auto}.pair-preview{display:grid;grid-template-columns:repeat(auto-fill,minmax(115px,1fr));gap:8px;margin:12px 0}
.pair-preview figure{margin:0;padding:6px;border:1px solid var(--panel-border);border-radius:8px}.pair-preview img{width:100%;aspect-ratio:3/4;object-fit:contain;background:#fff;border-radius:5px}
.pair-preview figcaption{font-size:11px;margin-top:5px}
.upload-state{display:grid;gap:8px;margin:14px 0;padding:14px;border:1px solid var(--panel-border);border-radius:10px;background:var(--surface-sunken)}
.upload-state[hidden]{display:none}.progress-label{display:flex;justify-content:space-between;gap:12px}.progress-track{height:12px;overflow:hidden;border-radius:999px;background:rgba(127,127,127,.22)}
.progress-fill{display:block;width:0;height:100%;border-radius:inherit;background:linear-gradient(90deg,#2878ff,#62d5ff);transition:width .2s ease}
#upload-button:disabled{opacity:.65;cursor:wait}
@media(max-width:600px){.storage-fields{grid-template-columns:1fr}}
</style><script>
(() => {
 const form=document.getElementById('batch-form'), input=document.getElementById('batch-photos'), preview=document.getElementById('pair-preview'), summary=document.getElementById('pair-summary');
 const modes=document.querySelectorAll("input[name='has_backs']"), modeHelp=document.getElementById('photo-mode-help'), game=document.getElementById('game-hint'), tray=document.getElementById('storage-tray');
 const panel=document.getElementById('upload-progress-panel'), track=document.getElementById('upload-track'), fill=document.getElementById('upload-fill');
 const status=document.getElementById('upload-status'), percent=document.getElementById('upload-percent'), detail=document.getElementById('upload-detail'), button=document.getElementById('upload-button');
 const addGroup=document.getElementById('add-photo-group'), clearGroups=document.getElementById('clear-photo-groups');
 const maxPhotos=Number(form.dataset.maxPhotos)||40, previewLimit=12; let selectedFiles=[], previewUrls=[];
 function isPaired(){return document.querySelector("input[name='has_backs']:checked").value==='yes';}
 function render(){
  const files=selectedFiles, paired=isPaired();
  modeHelp.textContent=paired?'Select front, back, front, back. Each back must immediately follow its matching front.':'Select front, front, front. Keep any shared back photo out of this selection.';
  input.setCustomValidity('');
  previewUrls.forEach(url=>URL.revokeObjectURL(url)); previewUrls=[];
  preview.innerHTML='';
  files.slice(0,previewLimit).forEach((file,index)=>{const fig=document.createElement('figure'),img=document.createElement('img'),cap=document.createElement('figcaption');
   const url=URL.createObjectURL(file); previewUrls.push(url); img.src=url; const pair=paired?Math.floor(index/2)+1:index+1; const side=paired?(index%2?'Back':'Front'):'Front';
   cap.textContent=`Card ${pair} · ${side}`; fig.append(img,cap); preview.append(fig);});
  const count=paired?Math.floor(files.length/2):files.length;
  const limited=files.length>previewLimit?` · previewing first ${previewLimit}`:'';
  summary.textContent=!files.length?'No photos selected.':`${files.length} photo(s) · ${count} card(s) · ${paired?'front + back pairs':'fronts only'}${limited}`+(paired&&files.length%2?' · one back photo is missing':'' );
 }
 input.addEventListener('change',()=>{
  const incoming=[...input.files]; input.value=''; input.setCustomValidity('');
  if(selectedFiles.length+incoming.length>maxPhotos){
   const remaining=maxPhotos-selectedFiles.length;
   summary.textContent=`That group was not added. Select ${remaining} or fewer more photo(s).`;
   return;
  }
  const known=new Set(selectedFiles.map(file=>`${file.name}:${file.size}:${file.lastModified}`));
  incoming.forEach(file=>{const key=`${file.name}:${file.size}:${file.lastModified}`; if(!known.has(key)){selectedFiles.push(file); known.add(key);}});
  render();
 });
 addGroup.addEventListener('click',()=>input.click());
 clearGroups.addEventListener('click',()=>{selectedFiles=[]; input.value=''; input.setCustomValidity(''); render();});
 modes.forEach(mode=>mode.addEventListener('change',render));
 render();
 game.addEventListener('change',()=>{if(game.value==='dragon_ball'&&!tray.value)tray.value='DB-01';});
 form.addEventListener('submit',event=>{
  event.preventDefault();
  input.setCustomValidity(!selectedFiles.length?'Select at least one photo.':(isPaired()&&selectedFiles.length%2?'Front/back mode needs an even number of photos.':''));
  if(!form.reportValidity()||button.disabled)return;
  panel.hidden=false; button.disabled=true; button.textContent='Uploading…';
  status.textContent='Uploading photos…'; detail.textContent='Keep this screen open while the photos upload.';
  const xhr=new XMLHttpRequest(); xhr.open('POST',form.action); xhr.responseType='text';
  xhr.upload.addEventListener('progress',upload=>{
   if(!upload.lengthComputable)return;
   const value=Math.min(100,Math.round(upload.loaded/upload.total*100));
   fill.style.width=`${value}%`; percent.textContent=`${value}%`; track.setAttribute('aria-valuenow',String(value));
   status.textContent=`Uploading photos — ${value}%`;
   detail.textContent=`${(upload.loaded/1048576).toFixed(1)} MB of ${(upload.total/1048576).toFixed(1)} MB sent`;
  });
  xhr.upload.addEventListener('load',()=>{
   fill.style.width='100%'; percent.textContent='100%'; track.setAttribute('aria-valuenow','100');
   status.textContent='Photos uploaded. Preparing card recognition…';
   detail.textContent='The review screen will open automatically.';
  });
  xhr.addEventListener('load',()=>{
   if(xhr.status>=200&&xhr.status<400){window.location.assign(xhr.responseURL||form.action);return;}
   button.disabled=false; button.textContent='Try upload again'; status.textContent='Upload could not be completed';
   detail.textContent=`Server response ${xhr.status}. Your selected photos are still on this screen.`;
  });
  xhr.addEventListener('error',()=>{
   button.disabled=false; button.textContent='Try upload again'; status.textContent='Upload interrupted';
   detail.textContent='Check your connection, then try again. Your selected photos are still on this screen.';
  });
  const payload=new FormData(form); payload.delete('photos');
  selectedFiles.forEach(file=>payload.append('photos',file,file.name));
  xhr.send(payload);
 });
})();
</script>"""
    )
    return HTMLResponse(page("EzBay — Photograph & store cards", body))


@router.post("/ebay-listing-bulk-photos")
async def bulk_photo_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    photos: list[UploadFile] = File(...),
    game_hint: str = Form("auto"),
    has_backs: str = Form(""),
    storage_tray: str = Form(""),
    starting_slot: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    uploads = [photo for photo in photos if photo.filename]
    paired = has_backs == "yes"
    if not uploads:
        return RedirectResponse("/ebay-listing-bulk-photos?error=Select+at+least+one+photo.", status_code=303)
    if len(uploads) > MAX_BATCH_PHOTOS:
        return RedirectResponse(
            f"/ebay-listing-bulk-photos?error={quote(f'Maximum {MAX_BATCH_PHOTOS} photos per batch.')}", status_code=303,
        )
    if paired and len(uploads) % 2:
        return RedirectResponse(
            "/ebay-listing-bulk-photos?error=Front%2Fback+mode+needs+an+even+number+of+photos.", status_code=303,
        )
    if game_hint not in GAME_HINTS:
        game_hint = "auto"
    try:
        tray = _tray_code(storage_tray)
        requested_start = int(starting_slot) if starting_slot.strip() else None
        if requested_start is not None and requested_start < 1:
            raise ValueError("Starting slot must be 1 or greater.")
    except ValueError as exc:
        return RedirectResponse(
            f"/ebay-listing-bulk-photos?error={quote(str(exc))}", status_code=303,
        )
    settings = request.app.state.settings
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="card recognition is not configured")

    batch = ListingPhotoBatch(
        total_pairs=len(uploads) // 2 if paired else len(uploads),
        game_hint=game_hint,
        storage_tray=tray or None,
        started_by=user,
        status=ListingPhotoBatchStatus.RUNNING,
    )
    session.add(batch)
    session.flush()
    try:
        start = None
        locations: list[StorageLocation] = []
        if tray:
            start, locations = _reserve_storage_slots(session, tray, batch.total_pairs, requested_start)
            batch.starting_slot = start
        step = 2 if paired else 1
        for offset in range(0, len(uploads), step):
            front = _store_listing_photo(f"bulk-{batch.id}", await uploads[offset].read(), settings)
            back = (
                _store_listing_photo(f"bulk-{batch.id}", await uploads[offset + 1].read(), settings)
                if paired else None
            )
            pair = ListingPhotoPair(
                batch_id=batch.id,
                position=offset // step + 1,
                front_image_path=front,
                back_image_path=back,
                status=ListingPhotoPairStatus.PROCESSING,
            )
            session.add(pair)
            session.flush()
            if locations:
                session.add(InventoryPlacement(
                    listing_photo_pair_id=pair.id,
                    storage_location_id=locations[pair.position - 1].id,
                    quantity=1,
                ))
        session.commit()
    except ValueError as exc:
        session.rollback()
        return RedirectResponse(
            f"/ebay-listing-bulk-photos?error={quote(str(exc))}", status_code=303,
        )

    background_tasks.add_task(_run_batch, batch.id, settings, user)
    return RedirectResponse(f"/ebay-listing-bulk-photos/{batch.id}", status_code=303)


def _pair_card(pair: ListingPhotoPair, session: Session) -> str:
    extracted = pair.extracted or {}
    name = extracted.get("character") or "Unidentified card"
    number = extracted.get("card_number") or "?"
    listing = session.get(ShopListing, pair.matched_shop_listing_id) if pair.matched_shop_listing_id else None
    placement = _placement_for_pair(session, pair.id)
    location = session.get(StorageLocation, placement.storage_location_id) if placement else None
    storage_line = (
        f"<div class='storage-slot'><strong>{'Stored in' if placement.confirmed_at else 'Put this card in'} {escape(location.name)}</strong>"
        f"<span>{'Put-away confirmed' if placement.confirmed_at else 'Reserved — confirm after physically filing it'} · {placement.quantity} cop{'y' if placement.quantity == 1 else 'ies'}</span></div>"
        if location else ""
    )
    live_ebay_id = _live_ebay_target(session, listing) if listing else None
    quantity = max(1, listing.quantity) if listing else 1
    price = f"AU ${pair.proposed_price:.2f}" if pair.proposed_price is not None else "unchanged"
    listing_line = (
        f"<div class='muted'>Matched: {escape(listing.title)} · proposed {price} ({escape(pair.price_source or 'no source')})"
        + (f" · eBay {escape(live_ebay_id)}" if live_ebay_id else " · no verified live eBay link")
        + "</div>"
        if listing else ""
    )
    proposed = (
        f"<div class='proposal'><strong>{escape(pair.proposed_title or '')}</strong>"
        f"<small>{escape((pair.proposed_description or '')[:220])}</small></div>"
        if pair.proposed_title else ""
    )
    issue = f"<div class='callout bad'>{escape(pair.issue)}</div>" if pair.issue else ""
    review_facts = ""
    if pair.status in (ListingPhotoPairStatus.NEEDS_REVIEW, ListingPhotoPairStatus.FAILED):
        facts = [
            ("Detected name", extracted.get("character")),
            ("Set / product", extracted.get("set_name")),
            ("Printed number", extracted.get("card_number") or "Not visible / not printed"),
            ("Rarity", extracted.get("rarity") or "Not visible / not printed"),
            ("Language", extracted.get("language")),
            ("Finish observed", extracted.get("foil_observation")),
        ]
        rows = "".join(
            f"<dt>{escape(label)}</dt><dd>{escape(str(value))}</dd>"
            for label, value in facts if value
        )
        if rows:
            review_facts = (
                "<div class='review-facts'><strong>What the photos tell us</strong>"
                f"<dl>{rows}</dl></div>"
            )
    action = ""
    if pair.status == ListingPhotoPairStatus.READY:
        action = (
            "<div class='pair-actions'>"
            f"<form method='post' action='/ebay-listing-bulk-photos/pair/{pair.id}/apply'>"
            f"<label class='pair-quantity'>Total quantity<input type='number' name='quantity' value='{quantity}' min='1' max='999' inputmode='numeric' required></label>"
            "<button class='btn small ghost'>Apply to CRM draft only</button></form>"
            + (
                f"<form method='post' action='/ebay-listing-bulk-photos/pair/{pair.id}/publish'>"
                f"<label class='pair-quantity'>Total quantity<input type='number' name='quantity' value='{quantity}' min='1' max='999' inputmode='numeric' required></label>"
                "<button class='btn small' name='confirmation' value='live'>"
                f"Apply &amp; update eBay {escape(live_ebay_id)}</button></form>"
                if live_ebay_id else
                "<small>No verified live eBay link was found. Apply the draft and review it in Listing Studio before creating anything new.</small>"
            )
            + "</div>"
        )
    elif pair.status == ListingPhotoPairStatus.APPLIED and listing:
        action = (
            "<div class='pair-actions'>"
            + (
                f"<form method='post' action='/ebay-listing-bulk-photos/pair/{pair.id}/publish'>"
                f"<label class='pair-quantity'>Total quantity<input type='number' name='quantity' value='{quantity}' min='1' max='999' inputmode='numeric' required></label>"
                "<button class='btn small' name='confirmation' value='live'>"
                f"Update eBay {escape(live_ebay_id)} now</button></form>"
                if live_ebay_id else ""
            )
            + f"<a class='btn small ghost' href='/shop-listings/{listing.id}/ebay-preview'>Review in Listing Studio</a></div>"
        )
    elif pair.status == ListingPhotoPairStatus.PUBLISHED and listing:
        action = (
            f"<div class='callout ok'>eBay {escape(live_ebay_id or '')} was updated.</div>"
            f"<a class='btn small ghost' href='/shop-listings/{listing.id}/ebay-preview'>Open Listing Studio</a>"
        )
    back = (
        f"<a href='/ebay-listing-bulk-photos/{pair.batch_id}/image/{pair.id}/back' target='_blank'>"
        f"<img loading='lazy' decoding='async' src='/ebay-listing-bulk-photos/{pair.batch_id}/image/{pair.id}/back?thumb=1' alt='Back'></a>"
        if pair.back_image_path else ""
    )
    card_body = (
        "<div class='pair-card'><div class='pair-images'>"
        f"<a href='/ebay-listing-bulk-photos/{pair.batch_id}/image/{pair.id}/front' target='_blank'>"
        f"<img loading='lazy' decoding='async' src='/ebay-listing-bulk-photos/{pair.batch_id}/image/{pair.id}/front?thumb=1' alt='Front'></a>{back}</div>"
        f"<div class='pair-copy'><div>{pill(pair.status.value.upper(), 'ok' if pair.status in (ListingPhotoPairStatus.READY, ListingPhotoPairStatus.APPLIED, ListingPhotoPairStatus.PUBLISHED) else 'warn')}</div>"
        f"<h2>Card {pair.position}: {escape(name)} #{escape(number)}</h2>{storage_line}"
        f"{listing_line}{proposed}{review_facts}{issue}{action}</div></div>"
    )
    if pair.status == ListingPhotoPairStatus.PUBLISHED:
        ebay_text = f" · eBay {escape(live_ebay_id)}" if live_ebay_id else ""
        return (
            "<details class='pair-done'>"
            f"<summary>{pill('PUBLISHED', 'ok')}<strong>Card {pair.position}: "
            f"{escape(name)} #{escape(number)}</strong><span>{ebay_text} updated · tap to view</span></summary>"
            f"{card_body}</details>"
        )
    return f"<article>{card_body}</article>"


@router.get("/ebay-listing-bulk-photos/{batch_id}", response_class=HTMLResponse)
def bulk_photo_status(
    batch_id: str,
    notice: str = Query(""),
    error: str = Query(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    batch = session.get(ListingPhotoBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    pairs = list(session.scalars(
        select(ListingPhotoPair).where(ListingPhotoPair.batch_id == batch_id).order_by(ListingPhotoPair.position)
    ))
    running = batch.status == ListingPhotoBatchStatus.RUNNING
    progress_percent = min(100, round(batch.processed_pairs * 100 / batch.total_pairs)) if batch.total_pairs else 0
    progress_text = (
        f"Identifying card {min(batch.processed_pairs + 1, batch.total_pairs)} of {batch.total_pairs}…"
        if running and batch.total_pairs else
        f"Processing complete — {batch.processed_pairs} of {batch.total_pairs} cards checked"
    )
    progress = (
        "<section class='processing-progress' aria-live='polite'>"
        f"<div class='progress-label'><strong>{escape(progress_text)}</strong>"
        f"<span>{progress_percent}% complete</span></div>"
        f"<div class='progress-track' role='progressbar' aria-label='Card recognition progress' "
        f"aria-valuemin='0' aria-valuemax='100' aria-valuenow='{progress_percent}'>"
        f"<span class='progress-fill{' active' if running else ''}' style='width:{progress_percent}%'></span></div>"
        "<small>Each card is checked for identity, language, listing match and price.</small></section>"
    )
    refresh = r"""<script>
(function(){
  var timer = null;
  function stop(){ if (timer) { clearTimeout(timer); timer = null; } }
  function schedule(){
    stop();
    if (document.visibilityState !== 'visible') return;
    timer = setTimeout(function(){ location.reload(); }, 3000);
  }
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('pagehide', stop);
  window.addEventListener('pageshow', schedule);
  schedule();
})();
</script>""" if running else ""
    notice_html = f"<div class='callout ok'>{escape(notice)}</div>" if notice else ""
    error_html = f"<div class='callout bad'>{escape(error)}</div>" if error else ""
    storage_plan = (
        "<div class='callout ok'><strong>Storage reserved:</strong> "
        f"{escape(batch.storage_tray)} from slot {batch.starting_slot:03d}. Keep the physical cards in upload order. "
        f"<a href='/ebay-listing-bulk-photos/{batch.id}/storage-labels'>Print slot labels</a> · "
        "<a href='/inventory/storage'>Open storage map</a></div>"
        if batch.storage_tray and batch.starting_slot else ""
    )
    unconfirmed_slots = session.scalar(
        select(InventoryPlacement.id)
        .join(ListingPhotoPair, ListingPhotoPair.id == InventoryPlacement.listing_photo_pair_id)
        .where(
            ListingPhotoPair.batch_id == batch.id,
            InventoryPlacement.confirmed_at.is_(None),
        )
        .limit(1)
    )
    confirm_storage = (
        f"<form method='post' action='/ebay-listing-bulk-photos/{batch.id}/confirm-storage' class='apply-all'>"
        "<label class='check'><input type='checkbox' name='confirmation' value='stored' required> "
        "I have physically placed every card in the displayed tray slot.</label>"
        "<button class='btn'>Mark this batch as stored</button></form>"
        if unconfirmed_slots else ""
    )
    apply_all = (
        f"<form method='post' action='/ebay-listing-bulk-photos/{batch.id}/apply-all' class='apply-all'>"
        "<label class='check'><input type='checkbox' name='confirmation' value='apply' required> "
        "Apply every ready proposal to its CRM draft. This will not update eBay.</label>"
        "<button class='btn'>Apply all ready drafts</button></form>"
        if batch.ready_pairs else ""
    )
    retry_held = (
        f"<form method='post' action='/ebay-listing-bulk-photos/{batch.id}/retry' class='retry-held'>"
        f"<button class='btn small ghost'>Retry {batch.review_pairs + batch.failed_pairs} held card(s)</button>"
        "<small>Rechecks them against the latest catalogue rules and AI recognition.</small></form>"
        if not running and (batch.review_pairs or batch.failed_pairs) else ""
    )
    body = (
        brand_header("Bulk listing photo review")
        + f"<p class='subtitle'>{batch.processed_pairs}/{batch.total_pairs} processed · "
        f"{batch.ready_pairs} ready · {batch.review_pairs} need review · {batch.failed_pairs} failed</p>"
        + progress
        + notice_html
        + error_html
        + storage_plan
        + confirm_storage
        + ("<div class='callout warn'>Identification is running. This page refreshes automatically.</div>" if running else "")
        + apply_all
        + retry_held
        + "<section class='pair-list'>" + "".join(_pair_card(pair, session) for pair in pairs) + "</section>"
        + "<p><a class='btn small ghost' href='/ebay-listing-bulk-photos'>Start another batch</a> "
        + "<a class='btn small ghost' href='/ebay-listing-cleanup'>Back to listing cleanup</a></p>"
        + refresh
        + """<style>
.apply-all{display:grid;gap:10px;margin:16px 0}.retry-held{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}.check{display:flex;gap:8px;align-items:flex-start}.check input{width:auto;margin-top:3px}
.processing-progress{display:grid;gap:9px;margin:14px 0;padding:14px;border:1px solid var(--panel-border);border-radius:10px;background:var(--surface-sunken)}
.progress-label{display:flex;justify-content:space-between;gap:12px}.progress-track{height:13px;overflow:hidden;border-radius:999px;background:rgba(127,127,127,.22)}
.progress-fill{display:block;height:100%;min-width:0;border-radius:inherit;background:linear-gradient(90deg,#2878ff,#62d5ff);transition:width .35s ease}
.progress-fill.active{min-width:9%;background:linear-gradient(100deg,#2878ff 15%,#72dcff 45%,#2878ff 75%);background-size:220% 100%;animation:processing-shimmer 1.2s linear infinite}
@keyframes processing-shimmer{to{background-position:-220% 0}}
.pair-list{display:grid;gap:12px}.pair-card{display:grid;grid-template-columns:minmax(220px,34%) 1fr;gap:16px;padding:14px;border:1px solid var(--panel-border);border-radius:12px;background:var(--panel)}
.pair-done{border:1px solid var(--panel-border);border-radius:12px;background:var(--panel);overflow:hidden}.pair-done>summary{display:flex;align-items:center;gap:9px;min-height:48px;padding:8px 12px;cursor:pointer;list-style:none}.pair-done>summary::-webkit-details-marker{display:none}.pair-done>summary:after{content:'›';margin-left:auto;font-size:24px;color:var(--text-dim);transform:rotate(90deg);transition:transform .15s ease}.pair-done[open]>summary:after{transform:rotate(-90deg)}.pair-done>summary strong{min-width:0}.pair-done>summary span:last-of-type{color:var(--text-dim);font-size:12px}.pair-done>.pair-card{border:0;border-top:1px solid var(--panel-border);border-radius:0}
.pair-images{display:grid;grid-template-columns:1fr 1fr;gap:6px}.pair-images img{width:100%;aspect-ratio:3/4;object-fit:contain;background:#fff;border-radius:7px}.pair-copy h2{margin:8px 0}.proposal{display:grid;gap:6px;padding:10px;background:var(--surface-sunken);border-radius:8px;margin:10px 0}.proposal small{white-space:pre-line;color:var(--text-dim)}
.storage-slot{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:9px 11px;margin:8px 0;border:1px solid #57a773;border-radius:8px;background:rgba(46,160,90,.08)}.storage-slot span{font-size:12px;color:var(--text-dim)}
.pair-actions{display:flex;align-items:flex-end;gap:10px;flex-wrap:wrap;margin-top:10px}.pair-actions form{margin:0;display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap}.pair-actions small{max-width:560px;color:var(--text-dim)}.pair-quantity{display:grid;gap:4px;font-size:12px;color:var(--text-dim)}.pair-quantity input{width:92px}
.review-facts{margin:10px 0;padding:11px;border:1px solid var(--panel-border);border-radius:9px;background:var(--surface-sunken)}.review-facts dl{display:grid;grid-template-columns:minmax(105px,auto) 1fr;gap:5px 10px;margin:8px 0 0}.review-facts dt{font-size:12px;font-weight:700;color:var(--text-dim)}.review-facts dd{margin:0;font-size:13px}
@media(max-width:720px){.pair-card{grid-template-columns:1fr}.pair-images{max-height:330px}.pair-images img{height:100%}.pair-done>summary{align-items:flex-start;flex-wrap:wrap}.pair-done>summary span:last-of-type{width:100%;padding-left:1px}}
</style>"""
    )
    return HTMLResponse(page("EzBay — Bulk listing photo review", body))


@router.get("/ebay-listing-bulk-photos/{batch_id}/storage-labels", response_class=HTMLResponse)
def bulk_storage_labels(
    batch_id: str,
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    batch = session.get(ListingPhotoBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    rows = session.execute(
        select(ListingPhotoPair, InventoryPlacement, StorageLocation)
        .join(InventoryPlacement, InventoryPlacement.listing_photo_pair_id == ListingPhotoPair.id)
        .join(StorageLocation, StorageLocation.id == InventoryPlacement.storage_location_id)
        .where(ListingPhotoPair.batch_id == batch_id)
        .order_by(ListingPhotoPair.position)
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="this batch has no reserved storage slots")

    labels = []
    for pair, placement, location in rows:
        facts = pair.extracted or {}
        name = facts.get("character") or "Review needed"
        number = facts.get("card_number") or "Number unknown"
        labels.append(
            "<div class='slot-label'>"
            f"<strong>{escape(location.name)}</strong>"
            f"<span>Card {pair.position}</span>"
            f"<b>{escape(str(name))}</b>"
            f"<small>{escape(str(number))}</small>"
            "</div>"
        )
    body = (
        brand_header("Storage slot labels")
        + f"<p class='subtitle'>{escape(batch.storage_tray or 'Tray')} · {len(rows)} physical card(s). "
          "Print, cut and place each label behind its card or on a divider.</p>"
        + "<p class='screen-only'><button class='btn' onclick='window.print()'>Print labels</button> "
          f"<a class='btn ghost' href='/ebay-listing-bulk-photos/{batch.id}'>Back to batch</a></p>"
        + "<section class='label-grid'>" + "".join(labels) + "</section>"
        + """<style>
.label-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}.slot-label{min-height:96px;padding:10px;border:1px dashed #222;display:grid;grid-template-columns:1fr auto;gap:4px 8px;align-content:start;background:#fff;color:#111}.slot-label strong{font-size:22px;grid-column:1}.slot-label span{font-size:11px;align-self:center}.slot-label b,.slot-label small{grid-column:1/-1}.slot-label small{color:#444}
@media print{.screen-only,header,nav{display:none!important}body{background:#fff!important}.label-grid{grid-template-columns:repeat(3,1fr);gap:5mm}.slot-label{break-inside:avoid}}
</style>"""
    )
    return HTMLResponse(page("EzBay — Storage labels", body))


@router.post("/ebay-listing-bulk-photos/{batch_id}/confirm-storage")
def confirm_batch_storage(
    batch_id: str,
    confirmation: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    if confirmation != "stored":
        raise HTTPException(status_code=400, detail="confirm that the cards were physically stored first")
    batch = session.get(ListingPhotoBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    placements = list(session.scalars(
        select(InventoryPlacement)
        .join(ListingPhotoPair, ListingPhotoPair.id == InventoryPlacement.listing_photo_pair_id)
        .where(ListingPhotoPair.batch_id == batch_id)
    ))
    if not placements:
        raise HTTPException(status_code=400, detail="this batch has no reserved storage slots")
    now = datetime.now(UTC)
    confirmed = 0
    for placement in placements:
        if placement.confirmed_at is None:
            placement.confirmed_at = now
            placement.confirmed_by = user
            confirmed += 1
        if placement.inventory_item_id:
            inventory = session.get(InventoryItem, placement.inventory_item_id)
            if inventory and inventory.storage_location_id is None:
                inventory.storage_location_id = placement.storage_location_id
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="inventory.storage_confirm",
        resource_type="listing_photo_batch", resource_id=batch_id,
        outcome="success", correlation_id=str(uuid.uuid4()),
        details={"confirmed": confirmed, "total_slots": len(placements)},
    )
    return RedirectResponse(
        f"/ebay-listing-bulk-photos/{batch_id}?notice={quote(f'{len(placements)} card(s) confirmed in their tray slots.')}",
        status_code=303,
    )


@router.get("/ebay-listing-bulk-photos/{batch_id}/image/{pair_id}/{side}")
def bulk_pair_image(
    batch_id: str,
    pair_id: str,
    side: str,
    thumb: bool = Query(False),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    if side not in {"front", "back"}:
        raise HTTPException(status_code=404, detail="unknown side")
    pair = session.get(ListingPhotoPair, pair_id)
    if pair is None or pair.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="photo pair not found")
    path = pair.front_image_path if side == "front" else pair.back_image_path
    if thumb:
        if not path:
            raise HTTPException(status_code=404, detail="no photo for this side")
        source = Path(path)
        if not source.exists():
            raise HTTPException(status_code=404, detail="image file missing")
        return serve_thumb(
            source, f"{pair.id}-{side}.jpg", 480, subdir="ebay-photo-batches",
        )
    return serve_image(path, unset="no photo for this side")


@router.post("/ebay-listing-bulk-photos/pair/{pair_id}/apply")
def apply_bulk_pair(
    pair_id: str,
    quantity: int = Form(1),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    pair = session.get(ListingPhotoPair, pair_id)
    if pair is None:
        raise HTTPException(status_code=404, detail="photo pair not found")
    invalid_quantity = _quantity_issue(quantity)
    if invalid_quantity:
        return RedirectResponse(
            f"/ebay-listing-bulk-photos/{pair.batch_id}?error={quote(invalid_quantity)}", status_code=303,
        )
    issue = _apply_pair(session, pair, quantity)
    if issue:
        pair.status = ListingPhotoPairStatus.NEEDS_REVIEW
        pair.issue = issue
        batch = session.get(ListingPhotoBatch, pair.batch_id)
        if batch:
            _refresh_batch_counts(session, batch)
        session.commit()
        return RedirectResponse(
            f"/ebay-listing-bulk-photos/{pair.batch_id}?error={quote(issue)}", status_code=303,
        )
    batch = session.get(ListingPhotoBatch, pair.batch_id)
    if batch:
        _refresh_batch_counts(session, batch)
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="listing_photo_pair.apply",
        resource_type="listing_photo_pair", resource_id=pair.id, outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={"shop_listing_id": pair.matched_shop_listing_id, "price_source": pair.price_source, "quantity": quantity},
    )
    return RedirectResponse(
        f"/ebay-listing-bulk-photos/{pair.batch_id}?notice={quote('CRM draft updated. Nothing was sent to eBay.')}",
        status_code=303,
    )


@router.post("/ebay-listing-bulk-photos/pair/{pair_id}/publish")
async def publish_bulk_pair(
    pair_id: str,
    confirmation: str = Form(""),
    quantity: int = Form(1),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    """Apply one reviewed proposal and revise its verified live eBay item."""
    if confirmation != "live":
        raise HTTPException(status_code=400, detail="confirm the live eBay update first")
    pair = session.get(ListingPhotoPair, pair_id)
    if pair is None:
        raise HTTPException(status_code=404, detail="photo pair not found")
    invalid_quantity = _quantity_issue(quantity)
    if invalid_quantity:
        return RedirectResponse(
            f"/ebay-listing-bulk-photos/{pair.batch_id}?error={quote(invalid_quantity)}", status_code=303,
        )
    if pair.status == ListingPhotoPairStatus.READY:
        issue = _apply_pair(session, pair, quantity)
        if issue:
            pair.status = ListingPhotoPairStatus.NEEDS_REVIEW
            pair.issue = issue
            batch = session.get(ListingPhotoBatch, pair.batch_id)
            if batch:
                _refresh_batch_counts(session, batch)
            session.commit()
            return RedirectResponse(
                f"/ebay-listing-bulk-photos/{pair.batch_id}?error={quote(issue)}", status_code=303,
            )
        session.commit()
    elif pair.status != ListingPhotoPairStatus.APPLIED:
        raise HTTPException(status_code=400, detail="this proposal is not ready for eBay")

    listing = session.get(ShopListing, pair.matched_shop_listing_id)
    if listing is None:
        raise HTTPException(status_code=400, detail="the matched CRM listing no longer exists")
    quantity_issue = _set_confirmed_quantity(session, pair, listing, quantity)
    if quantity_issue:
        return RedirectResponse(
            f"/ebay-listing-bulk-photos/{pair.batch_id}?error={quote(quantity_issue)}", status_code=303,
        )
    session.commit()
    ebay_listing_id = _live_ebay_target(session, listing)
    if not ebay_listing_id:
        return RedirectResponse(
            f"/ebay-listing-bulk-photos/{pair.batch_id}?error="
            f"{quote('CRM draft saved, but no verified live eBay listing is linked. Review it in Listing Studio before publishing a new listing.')}",
            status_code=303,
        )

    try:
        response = await revise_listing_on_ebay(
            listing.id, confirmation="revise", user=user, session=session,
        )
    except HTTPException as exc:
        message = str(exc.detail)
        return RedirectResponse(
            f"/ebay-listing-bulk-photos/{pair.batch_id}?error={quote(f'eBay update blocked: {message}')}",
            status_code=303,
        )
    location = response.headers.get("location", "")
    remote_error = parse_qs(urlparse(location).query).get("error", [])
    if remote_error:
        return RedirectResponse(
            f"/ebay-listing-bulk-photos/{pair.batch_id}?error={quote(remote_error[0])}", status_code=303,
        )

    pair.status = ListingPhotoPairStatus.PUBLISHED
    pair.issue = None
    pair.updated_at = datetime.now(UTC)
    batch = session.get(ListingPhotoBatch, pair.batch_id)
    if batch:
        _refresh_batch_counts(session, batch)
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="listing_photo_pair.publish",
        resource_type="listing_photo_pair", resource_id=pair.id, outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={"shop_listing_id": listing.id, "ebay_listing_id": ebay_listing_id, "quantity": quantity},
    )
    return RedirectResponse(
        f"/ebay-listing-bulk-photos/{pair.batch_id}?notice="
        f"{quote(f'eBay {ebay_listing_id} updated with the reviewed title, description, price and photos.')}",
        status_code=303,
    )


@router.post("/ebay-listing-bulk-photos/{batch_id}/retry")
def retry_bulk_batch(
    batch_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    batch = session.get(ListingPhotoBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    if batch.status == ListingPhotoBatchStatus.RUNNING:
        return RedirectResponse(f"/ebay-listing-bulk-photos/{batch_id}", status_code=303)
    held = session.scalar(
        select(ListingPhotoPair.id).where(
            ListingPhotoPair.batch_id == batch_id,
            ListingPhotoPair.status.in_((
                ListingPhotoPairStatus.NEEDS_REVIEW,
                ListingPhotoPairStatus.FAILED,
            )),
        ).limit(1)
    )
    if held is None:
        return RedirectResponse(
            f"/ebay-listing-bulk-photos/{batch_id}?notice={quote('No held cards need retrying.')}", status_code=303,
        )
    background_tasks.add_task(_retry_batch, batch_id, request.app.state.settings, user)
    return RedirectResponse(f"/ebay-listing-bulk-photos/{batch_id}", status_code=303)


@router.post("/ebay-listing-bulk-photos/{batch_id}/apply-all")
def apply_bulk_batch(
    batch_id: str,
    confirmation: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    if confirmation != "apply":
        raise HTTPException(status_code=400, detail="confirm the CRM draft updates first")
    batch = session.get(ListingPhotoBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    pairs = list(session.scalars(
        select(ListingPhotoPair).where(
            ListingPhotoPair.batch_id == batch_id,
            ListingPhotoPair.status == ListingPhotoPairStatus.READY,
        ).order_by(ListingPhotoPair.position)
    ))
    applied = 0
    held = 0
    for pair in pairs:
        issue = _apply_pair(session, pair)
        if issue:
            pair.status = ListingPhotoPairStatus.NEEDS_REVIEW
            pair.issue = issue
            held += 1
        else:
            applied += 1
    _refresh_batch_counts(session, batch)
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="listing_photo_batch.apply",
        resource_type="listing_photo_batch", resource_id=batch_id,
        outcome="success" if not held else "partial", correlation_id=str(uuid.uuid4()),
        details={"applied": applied, "held": held},
    )
    return RedirectResponse(
        f"/ebay-listing-bulk-photos/{batch_id}?notice={quote(f'{applied} CRM draft(s) updated; {held} held for review. Nothing was sent to eBay.')}",
        status_code=303,
    )
