"""Responsive scan workspace; read-only summaries share the existing intake pipeline."""
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from .auth import require_dashboard_user
from .database import get_session
from .models import ListingPhotoBatch, ListingPhotoPair, InventoryPlacement, StorageLocation, StockIntakeReceipt
from .ui import page

router = APIRouter(tags=['scan-studio'])

@router.get('/scan-studio', response_class=HTMLResponse)
def studio(user: str = Depends(require_dashboard_user)):
    body = (Path(__file__).parent / 'templates' / 'scan_studio.html').read_text()
    return HTMLResponse(page('EzBay — Scan Studio', body,
        head_extra="<link rel='stylesheet' href='/static/scan-studio.css?v=1'><script defer src='/static/scan-studio.js?v=2'></script>"),
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
        rows = session.execute(select(ListingPhotoPair, InventoryPlacement, StorageLocation, StockIntakeReceipt)
            .outerjoin(InventoryPlacement, InventoryPlacement.listing_photo_pair_id == ListingPhotoPair.id)
            .outerjoin(StorageLocation, StorageLocation.id == InventoryPlacement.storage_location_id)
            .outerjoin(StockIntakeReceipt, StockIntakeReceipt.pair_id == ListingPhotoPair.id)
            .where(ListingPhotoPair.batch_id == selected.id).order_by(ListingPhotoPair.position))
        for pair, placement, location, receipt in rows:
            facts = pair.extracted or {}
            cards.append({'id': pair.id, 'position': pair.position,
                'name': str(facts.get('character') or 'Identifying card'),
                'number': str(facts.get('card_number') or ''), 'status': pair.status.value,
                'issue': pair.issue or '', 'slot': location.name if location else '',
                'stored': bool(placement and placement.confirmed_at),
                'receipt': {'mode':receipt.mode,'added':receipt.quantity_added,'previous':receipt.previous_quantity,'url':f'/scan-studio/cards/{pair.id}'} if receipt else None,
                'image': f'/ebay-listing-bulk-photos/{selected.id}/image/{pair.id}/front?thumb=true',
                'review_url': (f'/scan-studio/cards/{pair.id}' if selected.intake_mode != 'legacy' else f'/photo-review/{pair.id}'),
                'listing_url': f'/shop-listings/{pair.matched_shop_listing_id}/ebay-preview' if pair.matched_shop_listing_id else ''})
    return JSONResponse({'selected': selected.id if selected else None, 'cards': cards,
        'batches': [{'id': b.id, 'date': b.started_at.isoformat(), 'total': b.total_pairs,
                     'processed': b.processed_pairs, 'status': b.status.value,
                     'intake_mode': b.intake_mode, 'review': b.review_pairs, 'failed': b.failed_pairs} for b in batches]},
        headers={'Cache-Control': 'no-store'})


def review_progress(session: Session, pair: ListingPhotoPair):
    """Read-only queue; receipts, not navigation, determine confirmation."""
    from .models import ListingPhotoPairStatus
    rows = session.execute(select(ListingPhotoPair, StockIntakeReceipt)
        .outerjoin(StockIntakeReceipt, StockIntakeReceipt.pair_id == ListingPhotoPair.id)
        .where(ListingPhotoPair.batch_id == pair.batch_id)
        .order_by(ListingPhotoPair.position, ListingPhotoPair.id)).all()
    eligible = [p for p, receipt in rows if not receipt and p.id != pair.id
                and p.status in (ListingPhotoPairStatus.READY, ListingPhotoPairStatus.NEEDS_REVIEW)]
    later = [p for p in eligible if (p.position, p.id) > (pair.position, pair.id)]
    next_pair = (later or eligible or [None])[0]
    return len(rows), sum(receipt is not None for _, receipt in rows), next_pair


@router.get('/scan-studio/cards/{pair_id}', response_class=HTMLResponse)
def card_review(pair_id: str, q: str = '', catalog: str = '', error: str = '',
                user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    from html import escape as e
    from sqlalchemy import or_
    from .models import CatalogItem, TcgCard, CardSet, ShopListing, ShopListingStatus, StockIntakeReceipt, InventoryItem
    from .listing_finishes import FINISHES
    from .stock_intake import MODES, is_studio
    pair = session.get(ListingPhotoPair,pair_id)
    if not pair: raise HTTPException(404,'Photo not found')
    if not is_studio(session,pair): raise HTTPException(400,'Use the existing batch review for this older upload.')
    batch=session.get(ListingPhotoBatch,pair.batch_id)
    receipt=session.get(StockIntakeReceipt,pair.id)
    back=f"<p><a href='/scan-studio?batch={batch.id}'>← Back to this batch</a></p>"
    total, confirmed_count, next_pair = review_progress(session, pair)
    back += f"<p><a class='btn' href='/scan-studio/batches/{batch.id}/pricing'>Review batch prices</a></p>"
    back += f"<nav class='review-progress' aria-label='Batch review'><strong>Card {pair.position} · {confirmed_count} of {total} confirmed</strong>"
    if next_pair:
        label = 'Next card' if receipt else 'Review later — next card'
        back += f"<a class='btn' href='/scan-studio/cards/{next_pair.id}'>{label}</a>"
    elif confirmed_count == total:
        back += "<span>Batch intake complete · listings still need review.</span>"
    else:
        back += "<span>No other cards ready to review. Check the batch for processing or failed photos.</span>"
    back += "</nav>"
    if receipt:
        body=back+f"<h1>Intake confirmed</h1><p>{e(MODES[receipt.mode])}: {receipt.quantity_added} copies added. Stock before this intake: {receipt.previous_quantity}.</p><p>This receipt records this batch only; it is not a current available-stock count.</p><a class='btn' href='/shop-listings/{receipt.shop_listing_id}/ebay-preview'>Review listing</a>"
        return HTMLResponse(page('EzBay — Intake receipt',body,head_extra="<link rel='stylesheet' href='/static/printing-review.css?v=2'>"),headers={'Cache-Control':'no-store'})
    facts=pair.extracted or {}
    term=q.strip() or str(facts.get('character') or '')
    selected=catalog or pair.proposed_catalog_item_id or ''
    results=[]
    if term:
        results=session.execute(select(CatalogItem,TcgCard).join(TcgCard,TcgCard.catalog_item_id==CatalogItem.id)
            .where(or_(CatalogItem.name.ilike(f'%{term}%'),TcgCard.number.ilike(f'%{term}%'))).limit(40)).all()
    selected_item=session.get(CatalogItem,selected) if selected else None
    selected_card=session.get(TcgCard,selected) if selected else None
    if selected_item and selected_card and all(item.id!=selected for item,_ in results):results.insert(0,(selected_item,selected_card))
    from urllib.parse import urlencode
    def reference(item, card):
        cardset = session.get(CardSet, card.set_id) if card.set_id else None
        fields = [('Set', cardset.name if cardset else card.set_code or card.set_id),
                  ('Number', card.number), ('Language', card.language), ('Rarity', card.rarity),
                  ('Variant', card.variant), ('Parallel', card.parallel)]
        details = ''.join(f"<dt>{label}</dt><dd>{e(str(value or 'Not recorded'))}</dd>" for label, value in fields)
        photo = (f"<a href='/photo-review-reference/{e(item.id,quote=True)}' target='_blank' rel='noopener'><img loading='lazy' src='/photo-review-reference/{e(item.id,quote=True)}' alt='Catalogue reference: {e(item.name,quote=True)}'></a>"
                 if item.image_url else "<p class='reference-missing'>Catalogue image unavailable — compare the printed details.</p>")
        return f"<h3>{e(item.name)}</h3>{photo}<p>Catalogue reference · not your actual card</p><dl>{details}</dl>"

    candidates = ''
    for item, card in results:
        url = '?' + urlencode({'q': term, 'catalog': item.id})
        chosen = item.id == selected
        candidates += f"<article class='printing-candidate {'chosen' if chosen else ''}'>{reference(item,card)}<a class='btn' href='{e(url,quote=True)}'>{'Selected printing' if chosen else 'Compare this printing'}</a></article>"
    options="<option value=''>Choose exact catalogue printing</option>"
    for item,card in results:
        cardset=session.get(CardSet,card.set_id) if card.set_id else None
        label=f'{item.name} · {card.number or "?"} · {cardset.name if cardset else card.set_id or "?"} · {card.language or "?"} · {card.rarity or ""}'
        options+=f"<option value='{e(item.id,quote=True)}' {'selected' if item.id==selected else ''}>{e(label)}</option>"
    targets="<option value=''>No existing listing selected</option>"
    listings=list(session.scalars(select(ShopListing).where(ShopListing.catalog_item_id==selected,ShopListing.status.in_([ShopListingStatus.ACTIVE,ShopListingStatus.DRAFT])))) if selected else []
    for l in listings:
        inv=session.get(InventoryItem,l.inventory_item_id) if l.inventory_item_id else None
        targets+=f"<option value='{e(l.id)}'>{e(l.title)} · {e(l.finish or 'finish unconfirmed')} · offered {l.quantity}, owned {inv.quantity if inv else '?'}</option>"
    finishes=''.join(f"<option value='{k}'>{e(v)}</option>" for k,v in FINISHES.items())
    modes=''.join(f"<option value='{k}' {'selected' if k==batch.intake_mode else ''}>{e(v)}</option>" for k,v in MODES.items())
    body=back+"<h1>Confirm this card</h1>"+(f"<p role='alert' style='color:#b42318'>{e(error)}</p>" if error else '')
    actual=f'/ebay-listing-bulk-photos/{batch.id}/image/{pair.id}/front'
    body+=f"<div class='printing-comparison'><section><h2>Your actual photo</h2><a href='{actual}' target='_blank' rel='noopener'><img src='{actual}' alt='Your actual card photo'></a><p>Tap either picture to inspect it at full size.</p></section>"
    body+=(f"<section><h2>Selected catalogue printing</h2>{reference(selected_item,selected_card)}</section>" if selected_item and selected_card else "<section><h2>Choose a printing below</h2><p>Match the artwork, printed number, set and language with your photo.</p></section>")
    body+="</div><p>Check special finishes on the physical card: Poké Ball, Master Ball, Cosmos and gold borders can affect value. A catalogue image alone does not confirm the finish.</p><div class='intake-grid'><div>"
    body+=f"<form method='get'><label>Find card by name or number<input name='q' value='{e(term,quote=True)}'></label><button>Search catalogue</button></form>"
    body+=f"<details><summary>Choose from text list</summary><form method='get'><label>Exact printing<select name='catalog' required>{options}</select></label><button>Choose printing</button></form></details>"
    body+=f"<h2>Catalogue candidates</h2><div class='printing-candidates'>{candidates or '<p>No candidates found. Try the printed number or a shorter name.</p>'}</div></div><div>"
    if selected_card:
        body+="<h2>Confirm stock action</h2>"
        body+=f"""<form method='post' action='/scan-studio/cards/{pair.id}/confirm'>
<input type='hidden' name='catalog_id' value='{e(selected,quote=True)}'>
<label>Action<select name='mode'>{modes}</select></label>
<p>New stock creates a draft only when this printing has no existing stock. Additional copies adds only the physical copies entered below. Photo update leaves all quantities and prices unchanged.</p>
<label>Existing listing<select name='listing_id'>{targets}</select></label>
<label>Finish<select name='finish' required><option value=''>Confirm finish</option>{finishes}</select></label>
<label>New physical copies (ignored for photo updates)<input name='quantity' type='number' min='1' max='999' value='1' required></label>
<label><input type='checkbox' name='confirmed' value='yes' required> I checked the printing and action against the physical card.</label>
<button class='btn' name='after' value='next'>Confirm &amp; next</button> <button name='after' value='receipt'>Confirm &amp; view receipt</button><p>This saves stock and a CRM draft. It does not publish or revise eBay.</p></form>"""
    body+="</div></div>"
    body+="<link rel='stylesheet' href='/static/printing-review.css?v=2'>"
    return HTMLResponse(page('EzBay — Confirm intake',body),headers={'Cache-Control':'no-store'})


from fastapi import Form
from fastapi.responses import RedirectResponse
@router.post('/scan-studio/cards/{pair_id}/confirm')
def confirm_card(pair_id: str, mode: str = Form(...), catalog_id: str = Form(...), finish: str = Form(...),
                 quantity: int = Form(1), listing_id: str = Form(''), confirmed: str = Form(''), after: str = Form('receipt'),
                 user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    from sqlalchemy import text
    from urllib.parse import urlencode
    from .stock_intake import confirm
    if confirmed!='yes':raise HTTPException(400,'Confirm the physical card and intake action.')
    # SQLite writer lock serializes read/modify/write across workers and retries.
    session.execute(text('BEGIN IMMEDIATE'))
    try:
        pair=session.get(ListingPhotoPair,pair_id)
        if not pair:raise HTTPException(404,'Photo not found')
        confirm(session,pair,mode=mode,catalog_id=catalog_id,finish=finish,quantity=quantity,listing_id=listing_id,user=user)
        session.commit()
    except HTTPException as exc:
        session.rollback()
        return RedirectResponse(f'/scan-studio/cards/{pair_id}?'+urlencode({'catalog':catalog_id,'error':exc.detail}),303)
    except Exception:
        session.rollback();raise
    if after == 'next':
        _, _, next_pair = review_progress(session, pair)
        if next_pair:
            return RedirectResponse(f'/scan-studio/cards/{next_pair.id}',303)
    return RedirectResponse(f'/scan-studio/cards/{pair_id}',303)


@router.get('/scan-studio/batches/{batch_id}/pricing', response_class=HTMLResponse)
def batch_pricing(batch_id: str, missing: bool = False,
                  user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    from html import escape as e
    from .models import ShopListing, CatalogItem, TcgCard
    from .listing_quality import australian_sold_listings_url, australian_active_listings_url
    from .listing_finishes import FINISHES
    batch = session.get(ListingPhotoBatch, batch_id)
    if not batch: raise HTTPException(404, 'Batch not found')
    if batch.intake_mode == 'legacy': raise HTTPException(400, 'Use Listing Studio for this older batch.')
    rows = session.execute(select(StockIntakeReceipt, ListingPhotoPair, ShopListing)
        .join(ListingPhotoPair, ListingPhotoPair.id == StockIntakeReceipt.pair_id)
        .join(ShopListing, ShopListing.id == StockIntakeReceipt.shop_listing_id)
        .where(ListingPhotoPair.batch_id == batch_id).order_by(ListingPhotoPair.position)).all()
    grouped = {}
    for receipt, pair, listing in rows:
        entry = grouped.setdefault(listing.id, {'listing': listing, 'pair': pair, 'added': 0})
        entry['added'] += receipt.quantity_added
    unpriced = sum(entry['listing'].price <= 0 for entry in grouped.values())
    body = f"<p><a href='/scan-studio?batch={e(batch_id)}'>← Back to batch</a></p><h1>Review batch prices</h1><p>{len(grouped)} listings · {unpriced} missing prices</p>"
    body += "<p>A price set is not approval to publish. Open Listing Studio to edit and review the full listing. Sold searches are research links, not a valuation; match the printing and condition and check postage.</p>"
    body += f"<nav><a class='btn' href='?missing=false'>All confirmed listings</a> <a class='btn' href='?missing=true'>Missing prices</a></nav><div class='printing-candidates'>"
    shown = 0
    for entry in grouped.values():
        listing, pair = entry['listing'], entry['pair']
        if missing and listing.price > 0: continue
        shown += 1
        item = session.get(CatalogItem, listing.catalog_item_id)
        card = session.get(TcgCard, listing.catalog_item_id)
        finish = FINISHES.get(listing.finish, listing.finish or 'Finish not recorded')
        identity = ' '.join(str(v) for v in [item.name if item else listing.title,
            card.set_code or card.set_id if card else '', card.number if card else '',
            card.language if card else '', card.parallel if card else '', finish] if v)
        price_label = f"{e(listing.currency)} ${listing.price:.2f} · price set" if listing.price > 0 else 'Price needed'
        body += f"<article class='printing-candidate'><img loading='lazy' src='/ebay-listing-bulk-photos/{e(batch_id)}/image/{e(pair.id)}/front?thumb=true' alt='Your photo'><h2>{e(listing.title)}</h2><p>{e(identity)}</p><strong>{price_label}</strong><p>{listing.quantity} offered · +{entry['added']} copies from this batch</p>"
        body += f"<p><a href='{e(australian_sold_listings_url(identity),quote=True)}' target='_blank' rel='noopener'>Sold listings · Australia</a></p><p><a href='{e(australian_active_listings_url(identity),quote=True)}' target='_blank' rel='noopener'>Active listings · Australia</a></p><a class='btn' href='/shop-listings/{e(listing.id)}/ebay-preview' target='_blank' rel='noopener'>Review price &amp; listing</a></article>"
    if not shown:
        body += '<p>No missing prices in confirmed listings.</p>' if grouped else '<p>Confirm cards in this batch first. They will appear here after intake is saved.</p>'
    body += '</div>'
    return HTMLResponse(page('EzBay — Batch pricing', body, head_extra="<link rel='stylesheet' href='/static/printing-review.css?v=2'>"),headers={'Cache-Control':'no-store'})
