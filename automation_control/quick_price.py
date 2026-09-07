"""Quick photo-to-market-value lookup -- shared by the buying calculator,
purchase lots, potential stock, and Whatnot's item queue (2026-09-07).

Deliberately distinct from capture.py's inventory-capture pipeline: this
never creates a CapturedCard row and never touches inventory. It's a
stateless "what's this worth" lookup used while evaluating a purchase or
pricing a listing -- the photo is processed in a temp file and discarded
immediately after, and the caller (a page's own form) still requires a
human to review the suggested price and explicitly submit the form before
anything is persisted. Scoped to TCG cards only for now, reusing the
existing extract_card_details() -- collectibles (Sonny Angel/Smiski) have
no live market-price feed the way card_prices does for cards, so there's
nothing to look up yet for that side.
"""

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .card_recognition import ExtractedCard, extract_card_details
from .config import get_settings
from .database import get_session
from .inventory_review import _aud, _latest_prices
from .models import CardSet, CardVariant, CatalogCard

router = APIRouter(prefix="/quick-price", tags=["quick-price"])


def _normalized_number(raw: str) -> str:
    return raw.split("/")[0].strip().lstrip("0") or raw.strip()


def match_card(session: Session, extracted: ExtractedCard) -> CatalogCard | None:
    """Best-effort match of an AI-identified card against the reference
    catalogue -- exact card number (normalized: "58/102" -> "58") plus a
    fuzzy name match, narrowed by set name when the AI read one. Numbers
    alone collide across sets/games, so number+name together is the
    practical minimum for a confident match; anything less falls back to
    no match rather than guessing, since a wrong price is worse than none."""
    if not extracted.character:
        return None
    query = select(CatalogCard).where(CatalogCard.name.ilike(f"%{extracted.character}%"))
    if extracted.card_number and "card_number" not in extracted.unreadable_fields:
        query = query.where(CatalogCard.number == _normalized_number(extracted.card_number))
    if extracted.set_name and "set_name" not in extracted.unreadable_fields:
        query = query.join(CardSet).where(CardSet.name.ilike(f"%{extracted.set_name}%"))
    return session.scalars(query.limit(1)).first()


@router.post("/identify")
async def quick_price_identify(
    photo: UploadFile = File(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> dict:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="card recognition is not configured (ANTHROPIC_API_KEY missing)")

    data = await photo.read()
    suffix = Path(photo.filename or "photo.jpg").suffix or ".jpg"
    with tempfile.TemporaryDirectory() as tmp_dir:
        photo_path = Path(tmp_dir) / f"photo{suffix}"
        photo_path.write_bytes(data)
        try:
            extracted_list = extract_card_details(photo_path, None, api_key=settings.anthropic_api_key)
        except Exception as exc:
            record_event(
                session, actor_type="user", actor_id=user, action="quick_price.identify",
                resource_type="quick_price", resource_id=None, outcome="failed",
                correlation_id=str(uuid.uuid4()), details={"error": str(exc)},
            )
            raise HTTPException(status_code=502, detail="card recognition failed") from exc

    matches = [(extracted, match_card(session, extracted)) for extracted in extracted_list]
    prices = _latest_prices(session, {card.id for _, card in matches if card is not None})

    results = []
    for extracted, card in matches:
        market_value = None
        if card is not None:
            price = prices.get((card.id, CardVariant.NORMAL)) or next(
                (p for (card_id, _), p in prices.items() if card_id == card.id), None
            )
            market_value = _aud(price)
        results.append({
            "character": extracted.character, "set_name": extracted.set_name,
            "card_number": extracted.card_number, "rarity": extracted.rarity,
            "matched": card is not None, "catalog_item_id": card.id if card else None,
            "market_value": market_value,
        })

    record_event(
        session, actor_type="user", actor_id=user, action="quick_price.identify",
        resource_type="quick_price", resource_id=None, outcome="success",
        correlation_id=str(uuid.uuid4()), details={"count": len(results), "matched": sum(1 for r in results if r["matched"])},
    )
    return {"results": results}
