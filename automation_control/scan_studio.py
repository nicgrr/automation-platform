"""Responsive scan workspace; read-only summaries share the existing intake pipeline."""
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from .auth import require_dashboard_user
from .database import get_session
from .models import ListingPhotoBatch, ListingPhotoPair, InventoryPlacement, StorageLocation
from .ui import page

router = APIRouter(tags=['scan-studio'])

@router.get('/scan-studio', response_class=HTMLResponse)
def studio(user: str = Depends(require_dashboard_user)):
    body = (Path(__file__).parent / 'templates' / 'scan_studio.html').read_text()
    return HTMLResponse(page('EzBay — Scan Studio', body,
        head_extra="<link rel='stylesheet' href='/static/scan-studio.css?v=1'><script defer src='/static/scan-studio.js?v=1'></script>"),
        headers={'Cache-Control': 'no-store'})

@router.get('/scan-studio/data')
def data(batch_id: str = '', user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    batches = list(session.scalars(select(ListingPhotoBatch).order_by(ListingPhotoBatch.started_at.desc()).limit(20)))
    selected = session.get(ListingPhotoBatch, batch_id) if batch_id else (batches[0] if batches else None)
    if batch_id and selected is None:
        raise HTTPException(404, 'Batch not found')
    if selected and all(b.id != selected.id for b in batches):
        batches.append(selected)
    cards = []
    if selected:
        rows = session.execute(select(ListingPhotoPair, InventoryPlacement, StorageLocation)
            .outerjoin(InventoryPlacement, InventoryPlacement.listing_photo_pair_id == ListingPhotoPair.id)
            .outerjoin(StorageLocation, StorageLocation.id == InventoryPlacement.storage_location_id)
            .where(ListingPhotoPair.batch_id == selected.id).order_by(ListingPhotoPair.position))
        for pair, placement, location in rows:
            facts = pair.extracted or {}
            cards.append({'id': pair.id, 'position': pair.position,
                'name': str(facts.get('character') or 'Identifying card'),
                'number': str(facts.get('card_number') or ''), 'status': pair.status.value,
                'issue': pair.issue or '', 'slot': location.name if location else '',
                'stored': bool(placement and placement.confirmed_at),
                'image': f'/ebay-listing-bulk-photos/{selected.id}/image/{pair.id}/front?thumb=true',
                'review_url': f'/photo-review/{pair.id}',
                'listing_url': f'/shop-listings/{pair.matched_shop_listing_id}/ebay-preview' if pair.matched_shop_listing_id else ''})
    return JSONResponse({'selected': selected.id if selected else None, 'cards': cards,
        'batches': [{'id': b.id, 'date': b.started_at.isoformat(), 'total': b.total_pairs,
                     'processed': b.processed_pairs, 'status': b.status.value,
                     'review': b.review_pairs, 'failed': b.failed_pairs} for b in batches]},
        headers={'Cache-Control': 'no-store'})
