"""Confirm held bulk listing photos one card at a time.

After a photo batch, the cards the recogniser couldn't settle wait as
NEEDS_REVIEW. This walks through them one per screen: the operator's own
photo, what it was identified as, what they already hold of that card, a tap
to choose the finish, and one of three outcomes:

- a new photo for an existing current listing (photos and finish only; stock
  and price never change),
- a card already in stock without a current listing (a new draft on that
  stock row, correcting the row's finish if the photo shows otherwise),
- an extra copy (new stock row and a new draft).

Nothing here publishes to eBay: every outcome ends as a Listing Studio draft
or an updated listing awaiting review. photo_identification_review.py stays
the fallback for a card that needs a full catalogue search.
"""

import hashlib
import uuid
from decimal import Decimal
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .ebay_photo_batch import _link_pair_inventory, _name_aliases, _normalise, _refresh_batch_counts
from .listing_finishes import save_finish
from .listing_quality import generated_ebay_description, generated_ebay_title
from .models import (
    CardSet,
    CardVariant,
    CatalogItem,
    CatalogItemType,
    InventoryItem,
    InventoryStatus,
    ListingPhotoBatch,
    ListingPhotoPair,
    ListingPhotoPairStatus,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)
from .representative_backs import attach_to_pair, with_notice
from .ui import brand_header, page

router = APIRouter(tags=["photo-confirm"])

FINISH_CHOICES = (
    ("normal", "Normal"),
    ("holo", "Holo"),
    ("reverse_holo", "Reverse holo"),
    ("pokeball_pattern", "Poké Ball"),
    ("masterball_pattern", "Master Ball"),
    ("cosmos_holo", "Cosmos holo"),
)
_FINISH_KEYS = {key for key, _ in FINISH_CHOICES}
_CURRENT = (ShopListingStatus.ACTIVE, ShopListingStatus.DRAFT)
_IN_STOCK = (InventoryStatus.AVAILABLE, InventoryStatus.LISTED)


def _variant(finish: str) -> CardVariant:
    # Cosmos is a holo treatment; the stock row records it as holo and the
    # listing carries the exact finish.
    return CardVariant.HOLO if finish == "cosmos_holo" else CardVariant(finish)


def guess_finish(facts: dict) -> str:
    """Preselect the likeliest finish from the recogniser's foil notes."""
    text = " ".join(str(facts.get(k) or "") for k in ("foil_observation", "rarity")).lower().replace("é", "e")
    if "master ball" in text or "masterball" in text:
        return "masterball_pattern"
    if "poke ball" in text or "pokeball" in text:
        return "pokeball_pattern"
    if "cosmos" in text:
        return "cosmos_holo"
    if "reverse" in text:
        return "reverse_holo"
    if any(word in text for word in ("holo", "foil", "sheen", "sparkl", "glitter", "rainbow")):
        return "holo"
    return "normal"


def catalogue_guess(session: Session, pair: ListingPhotoPair) -> tuple[CatalogItem, TcgCard] | None:
    """The proposed catalogue card, or an exact number + name match.

    Bilingual recognitions ("象牙猪ex (Mamoswine ex)") and set codes the
    catalogue spells differently ("csv10C" vs "Chasing Glory Together")
    defeat the batch matcher; the printed number plus either name is enough
    to find the card when exactly one entry fits.
    """
    if pair.proposed_catalog_item_id:
        item = session.get(CatalogItem, pair.proposed_catalog_item_id)
        card = session.get(TcgCard, pair.proposed_catalog_item_id)
        return (item, card) if item and card else None
    facts = pair.extracted or {}
    number = str(facts.get("card_number") or "").strip()
    if not number:
        return None
    names = _name_aliases(facts.get("character"))
    rows = session.execute(
        select(CatalogItem, TcgCard).join(TcgCard, TcgCard.catalog_item_id == CatalogItem.id)
        .where(TcgCard.number == number)
    ).all()
    matches = [(item, card) for item, card in rows if _normalise(item.name) in names]
    return matches[0] if len(matches) == 1 else None


def _sibling_set(session: Session, pair: ListingPhotoPair) -> CardSet | None:
    """The set a missing card most likely belongs to: one whose cards share
    this card's printed total and language family."""
    facts = pair.extracted or {}
    number = str(facts.get("card_number") or "")
    if "/" not in number:
        return None
    total = number.split("/", 1)[1]
    language = str(facts.get("language") or "").lower().split(" ")[0].strip("()")
    rows = session.execute(
        select(TcgCard.set_id, TcgCard.language).where(TcgCard.number.like(f"%/{total}"))
    ).all()
    set_ids = {set_id for set_id, lang in rows if set_id and language and language in (lang or "").lower()}
    return session.get(CardSet, set_ids.pop()) if len(set_ids) == 1 else None


def _next_pair(session: Session, batch_id: str, skipped: set[int]) -> ListingPhotoPair | None:
    pairs = session.scalars(
        select(ListingPhotoPair).where(
            ListingPhotoPair.batch_id == batch_id,
            ListingPhotoPair.status == ListingPhotoPairStatus.NEEDS_REVIEW,
        ).order_by(ListingPhotoPair.position)
    )
    return next((p for p in pairs if p.position not in skipped), None)


def _parse_skip(skip: str) -> set[int]:
    return {int(part) for part in skip.split(",") if part.strip().isdigit()}


def _confirm_url(batch_id: str, skipped: set[int], **extra: str) -> str:
    query = [f"skip={','.join(str(p) for p in sorted(skipped))}"] if skipped else []
    query += [f"{key}={quote(value)}" for key, value in extra.items() if value]
    return f"/ebay-listing-bulk-photos/{batch_id}/confirm" + ("?" + "&".join(query) if query else "")


def _holdings(session: Session, catalog_id: str) -> tuple[list[ShopListing], list[InventoryItem]]:
    """Current listings, and in-stock rows that don't already have one."""
    listings = list(session.scalars(select(ShopListing).where(
        ShopListing.catalog_item_id == catalog_id, ShopListing.status.in_(_CURRENT), ShopListing.quantity > 0,
    )))
    listed_stock = {l.inventory_item_id for l in listings}
    stock = [i for i in session.scalars(select(InventoryItem).where(
        InventoryItem.catalog_item_id == catalog_id, InventoryItem.status.in_(_IN_STOCK), InventoryItem.quantity > 0,
    )) if i.id not in listed_stock]
    return listings, stock


def _finish_label(key: str | None) -> str:
    return dict(FINISH_CHOICES).get(key or "", (key or "").replace("_", " ").title())


@router.get("/ebay-listing-bulk-photos/{batch_id}/confirm", response_class=HTMLResponse)
def confirm_next(
    batch_id: str, skip: str = Query(""), error: str = Query(""), done: str = Query(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    batch = session.get(ListingPhotoBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    skipped = _parse_skip(skip)
    pair = _next_pair(session, batch_id, skipped)
    if pair is None:
        if skipped:
            body = (brand_header("Confirm cards")
                    + f"<div class='callout ok'>{escape(done) if done else 'You reached the end.'}</div>"
                    + f"<p>You skipped {len(skipped)} card(s).</p>"
                    + f"<p><a class='btn' href='/ebay-listing-bulk-photos/{batch_id}/confirm'>Go through the skipped cards</a> "
                    + f"<a class='btn ghost' href='/ebay-listing-bulk-photos/{batch_id}'>Back to the batch</a></p>")
            return HTMLResponse(page("EZBay — Confirm cards", body))
        return RedirectResponse(f"/ebay-listing-bulk-photos/{batch_id}?notice={quote('All cards confirmed.')}", status_code=303)

    remaining = session.scalars(select(ListingPhotoPair.id).where(
        ListingPhotoPair.batch_id == batch_id, ListingPhotoPair.status == ListingPhotoPairStatus.NEEDS_REVIEW)).all()
    facts = pair.extracted or {}
    guess = catalogue_guess(session, pair)
    finish = guess_finish(facts)
    photo = f"/ebay-listing-bulk-photos/{batch_id}/image/{pair.id}/front"
    action = _confirm_url(batch_id, skipped).replace("/confirm", f"/confirm/{pair.id}", 1)

    if guess is None:
        identity, choices, submit = _missing_card_html(session, pair, batch_id, skipped), "", ""
    else:
        item, card = guess
        card_set = session.get(CardSet, card.set_id) if card.set_id else None
        identity = (
            f"<h2 class='card-name'>{escape(item.name)}</h2>"
            f"<p class='card-facts'>{escape(card_set.name if card_set else card.set_id or '')} · "
            f"<b>{escape(card.number or '')}</b> · {escape(card.rarity or '')} · {escape(card.language or '')}</p>"
        )
        listings, stock = _holdings(session, item.id)
        choices = _choice_html(listings, stock)
        submit = (
            f"<input type='hidden' name='catalog' value='{escape(item.id)}'>"
            "<button class='btn confirm-btn'>Confirm and next card</button>"
        )

    finish_html = "".join(
        f"<label class='pill-choice'><input type='radio' name='finish' value='{key}'"
        f"{' checked' if key == finish else ''} required><span>{label}</span></label>"
        for key, label in FINISH_CHOICES
    )
    notes = str(facts.get("foil_observation") or "")
    detected = escape(notes if len(notes) <= 160 else notes[:157].rsplit(" ", 1)[0] + "…")
    form = (
        f"<form method='post' action='{escape(action, quote=True)}' class='confirm-form'>"
        "<fieldset><legend>Finish</legend>"
        f"<div class='pills'>{finish_html}</div>"
        + (f"<small>Photo notes: {detected}</small>" if detected else "")
        + "</fieldset>" + choices + submit + "</form>"
    ) if guess is not None else ""
    body = (
        brand_header("Confirm cards")
        + f"<p class='subtitle'>{len(remaining)} left to confirm · card {pair.position} of {batch.total_pairs} in this batch</p>"
        + (f"<div class='callout bad'>{escape(error)}</div>" if error else "")
        + (f"<div class='callout ok'>{escape(done)}</div>" if done else "")
        + "<div class='confirm-layout'>"
        + f"<a class='own-photo' href='{photo}' target='_blank' rel='noopener'><img src='{photo}' alt='Your photo of this card'></a>"
        + "<section class='confirm-panel'>" + identity + form
        + "<p class='secondary'>"
        + f"<a class='btn ghost' href='{escape(_confirm_url(batch_id, skipped | {pair.position}), quote=True)}'>Skip for now</a> "
        + f"<a class='btn ghost' href='/photo-review/{pair.id}'>Wrong card? Search the catalogue</a></p>"
        + "</section></div>"
        + _STYLE
    )
    return HTMLResponse(page("EZBay — Confirm cards", body))


def _choice_html(listings: list[ShopListing], stock: list[InventoryItem]) -> str:
    options = []
    for listing in listings:
        options.append((f"existing:{listing.id}",
                        f"<b>Same card, new photo</b> for your {listing.status.value} listing "
                        f"({_finish_label(listing.finish) or 'finish not set'}, qty {listing.quantity}, ${listing.price})",
                        "Stock and price stay as they are."))
    for row in stock:
        options.append((f"stock:{row.id}",
                        f"<b>Card I already have</b>: {_finish_label(row.variant.value)} in stock, qty {row.quantity}",
                        "Makes a draft from this stock. If the finish you pick differs, the stock record is corrected."))
    options.append(("extra", "<b>Extra copy</b>: add new stock", "Use the box below for how many."))
    rows = "".join(
        f"<label class='choice'><input type='radio' name='choice' value='{escape(value)}'{' checked' if i == 0 else ''} required>"
        f"<span><span>{label}</span>" + (f"<small>{hint}</small>" if hint else "") + "</span></label>"
        for i, (value, label, hint) in enumerate(options)
    )
    return (
        "<fieldset><legend>What is it?</legend>" + rows
        + "<label class='qty'>Extra copies: how many?<input type='number' name='quantity' min='1' max='99' value='1'></label>"
        + "</fieldset>"
    )


def _missing_card_html(session: Session, pair: ListingPhotoPair, batch_id: str, skipped: set[int]) -> str:
    facts = pair.extracted or {}
    names = sorted(_name_aliases(facts.get("character")))
    english = next((n for n in names if n.isascii()), "")
    display = english.title() if english else str(facts.get("character") or "")
    card_set = _sibling_set(session, pair)
    action = _confirm_url(batch_id, skipped).replace("/confirm", f"/confirm/{pair.id}/catalogue", 1)
    set_field = (
        f"<input type='hidden' name='set_id' value='{escape(card_set.id)}'><p>Set: <b>{escape(card_set.name)}</b></p>"
        if card_set else
        f"<label>Set name<input name='set_name' value='{escape(str(facts.get('set_name') or ''), quote=True)}' required></label>"
    )
    return (
        "<h2 class='card-name'>Not in the catalogue yet</h2>"
        f"<p class='card-facts'>Read from the photo: {escape(str(facts.get('character') or ''))} · "
        f"{escape(str(facts.get('card_number') or ''))} · {escape(str(facts.get('language') or ''))}</p>"
        f"<form method='post' action='{escape(action, quote=True)}' class='confirm-form'>"
        f"<label>Card name<input name='name' value='{escape(display, quote=True)}' required maxlength='128'></label>"
        f"<label>Printed number<input name='number' value='{escape(str(facts.get('card_number') or ''), quote=True)}' required maxlength='32'></label>"
        f"{set_field}"
        f"<label>Language<input name='language' value='{escape(str(facts.get('language') or ''), quote=True)}' required maxlength='32'></label>"
        f"<label>Rarity<input name='rarity' value='{escape(str(facts.get('rarity') or ''), quote=True)}' maxlength='64' placeholder='e.g. Common, RR, AR'></label>"
        "<button class='btn confirm-btn'>Add to catalogue</button></form>"
    )


@router.post("/ebay-listing-bulk-photos/{batch_id}/confirm/{pair_id}/catalogue")
def confirm_add_catalogue(
    batch_id: str, pair_id: str, skip: str = Query(""),
    name: str = Form(...), number: str = Form(...), language: str = Form(...), rarity: str = Form(""),
    set_id: str = Form(""), set_name: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    pair = session.get(ListingPhotoPair, pair_id)
    if pair is None or pair.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="photo not found")
    name, number, language, rarity = name.strip(), number.strip(), language.strip(), rarity.strip()
    if not (name and number and language) or len(name) > 128 or len(number) > 32:
        raise HTTPException(status_code=400, detail="name, number and language are required")
    card_set = session.get(CardSet, set_id) if set_id else None
    if card_set is None:
        if not set_name.strip():
            raise HTTPException(status_code=400, detail="choose the card's set")
        sid = "local-" + hashlib.sha256(("pokemon|" + set_name.strip().lower()).encode()).hexdigest()[:24]
        card_set = session.get(CardSet, sid) or CardSet(id=sid, name=set_name.strip(), game="pokemon")
        session.add(card_set)
        session.flush()
    cid = "local-" + hashlib.sha256("|".join((card_set.id, name.lower(), number.lower())).encode()).hexdigest()[:24]
    if session.get(CatalogItem, cid) is None:
        session.add(CatalogItem(id=cid, item_type=CatalogItemType.TCG_CARD, name=name))
        session.flush()
        session.add(TcgCard(catalog_item_id=cid, game=card_set.game or "pokemon", set_id=card_set.id,
                            number=number, language=language, rarity=rarity or None))
    pair.proposed_catalog_item_id = cid
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="photo_confirm.catalogue_add",
                 resource_type="catalog_item", resource_id=cid, outcome="success",
                 correlation_id=str(uuid.uuid4()), details={"pair_id": pair_id, "set_id": card_set.id})
    return RedirectResponse(_confirm_url(batch_id, _parse_skip(skip)), status_code=303)


@router.post("/ebay-listing-bulk-photos/{batch_id}/confirm/{pair_id}")
def confirm_card(
    batch_id: str, pair_id: str, skip: str = Query(""),
    catalog: str = Form(...), finish: str = Form(...), choice: str = Form(...), quantity: str = Form("1"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    skipped = _parse_skip(skip)
    pair = session.get(ListingPhotoPair, pair_id)
    if pair is None or pair.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="photo not found")
    if pair.status != ListingPhotoPairStatus.NEEDS_REVIEW:
        # Already confirmed (double tap or a second device): just move on.
        return RedirectResponse(_confirm_url(batch_id, skipped), status_code=303)
    item, card = session.get(CatalogItem, catalog), session.get(TcgCard, catalog)
    if item is None or card is None:
        raise HTTPException(status_code=404, detail="catalogue card not found")

    def retry(message: str) -> RedirectResponse:
        return RedirectResponse(_confirm_url(batch_id, skipped, error=message), status_code=303)

    if finish not in _FINISH_KEYS:
        return retry("Choose the card's finish.")
    variant = _variant(finish)
    details: dict = {"catalog_item_id": catalog, "finish": finish, "choice": choice.split(":", 1)[0]}

    if choice.startswith("existing:"):
        listing = session.get(ShopListing, choice.split(":", 1)[1])
        if listing is None or listing.catalog_item_id != catalog or listing.status not in _CURRENT:
            return retry("That listing changed. Pick again.")
        inventory = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    elif choice.startswith("stock:"):
        inventory = session.get(InventoryItem, choice.split(":", 1)[1])
        if inventory is None or inventory.catalog_item_id != catalog or inventory.quantity <= 0:
            return retry("That stock record changed. Pick again.")
        if any(l.inventory_item_id == inventory.id for l in _holdings(session, catalog)[0]):
            return retry("That stock already has a listing. Choose 'Same card, new photo'.")
        if inventory.variant != variant:
            details["stock_finish_corrected_from"] = inventory.variant.value
            inventory.variant = variant
        listing = _new_draft(item, card, inventory)
        session.add(listing)
    elif choice == "extra":
        try:
            count = int(quantity)
        except ValueError:
            return retry("Enter how many extra copies you have.")
        if not 1 <= count <= 99:
            return retry("Extra copies must be between 1 and 99.")
        inventory = InventoryItem(
            catalog_item_id=catalog, variant=variant, condition="Near Mint", quantity=count,
            status=InventoryStatus.AVAILABLE, scan_image_path=pair.front_image_path,
            notes=f"Extra copy confirmed from photo batch {batch_id} by {user}.",
        )
        session.add(inventory)
        session.flush()
        listing = _new_draft(item, card, inventory)
        session.add(listing)
        details["quantity"] = count
    else:
        return retry("Choose what this card is.")

    session.flush()
    save_finish(listing, finish)
    attach_to_pair(pair, card.game, card.language, card.rarity)
    listing.ebay_photo_paths = [pair.front_image_path] + ([pair.back_image_path] if pair.back_image_path else [])
    card_set = session.get(CardSet, card.set_id) if card.set_id else None
    if not listing.ebay_title:
        listing.ebay_title = generated_ebay_title(listing, item, card, card_set, inventory)
    if not listing.ebay_description:
        listing.ebay_description = generated_ebay_description(listing, item, card, card_set, inventory, has_own_photo=True)
    listing.ebay_description = with_notice(listing.ebay_description, listing.ebay_photo_paths)
    pair.proposed_catalog_item_id = catalog
    pair.matched_shop_listing_id = listing.id
    pair.proposed_title, pair.proposed_description = listing.ebay_title, listing.ebay_description
    pair.proposed_price = listing.price
    pair.status, pair.issue = ListingPhotoPairStatus.APPLIED, None
    _link_pair_inventory(session, pair, inventory)
    batch = session.get(ListingPhotoBatch, batch_id)
    if batch:
        _refresh_batch_counts(session, batch)
    session.commit()
    details["listing_id"] = listing.id
    record_event(session, actor_type="user", actor_id=user, action="photo_confirm.confirm",
                 resource_type="listing_photo_pair", resource_id=pair.id, outcome="success",
                 correlation_id=str(uuid.uuid4()), details=details)
    done = f"Confirmed {item.name} {card.number or ''} ({_finish_label(finish)})."
    return RedirectResponse(_confirm_url(batch_id, skipped, done=done), status_code=303)


def _new_draft(item: CatalogItem, card: TcgCard, inventory: InventoryItem) -> ShopListing:
    """A Listing Studio draft for this stock; priced there, never published here."""
    return ShopListing(
        catalog_item_id=item.id, inventory_item_id=inventory.id, title=f"{item.name} #{card.number or ''}",
        price=Decimal("0"), currency="AUD", quantity=inventory.quantity, status=ShopListingStatus.DRAFT,
        condition=inventory.condition or "Near Mint",
    )


_STYLE = """<style>
.confirm-layout{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,6fr);gap:20px;align-items:start}
.own-photo img{display:block;width:100%;max-height:78vh;object-fit:contain;border-radius:14px;background:var(--surface-sunken)}
.confirm-panel{display:grid;gap:14px;min-width:0}
.card-name{margin:0;font-size:26px;line-height:1.15}
.card-facts{margin:0;color:var(--muted,#5d6673)}
.confirm-form{display:grid;gap:16px}
.confirm-form fieldset{border:0;padding:0;margin:0;display:grid;gap:10px}
.confirm-form legend{font-weight:700;margin-bottom:6px}
.pills{display:flex;flex-wrap:wrap;gap:8px}
.pill-choice input,.choice input{position:absolute;opacity:0;pointer-events:none}
.pill-choice span{display:inline-block;padding:10px 16px;border-radius:999px;border:1.5px solid var(--panel-border);font-weight:600;cursor:pointer}
.pill-choice input:checked+span{background:#0891b2;border-color:#0891b2;color:#fff}
.pill-choice input:focus-visible+span,.choice input:focus-visible+span{outline:2px solid #0891b2;outline-offset:2px}
.choice span{display:grid;gap:3px;padding:12px 14px;border-radius:12px;border:1.5px solid var(--panel-border);cursor:pointer}
.choice input:checked+span{border-color:#0891b2;box-shadow:inset 0 0 0 1px #0891b2}
.choice small,.confirm-form small{color:var(--muted,#5d6673)}
.qty{display:grid;gap:6px;max-width:220px}.qty input,.confirm-form label input{padding:10px}
.confirm-form>label{display:grid;gap:6px}
.confirm-btn{width:100%;padding:14px;font-size:17px}
.secondary{display:flex;flex-wrap:wrap;gap:8px;margin:0}
@media(max-width:720px){.confirm-layout{grid-template-columns:1fr}.own-photo img{max-height:52vh}}
</style>"""
