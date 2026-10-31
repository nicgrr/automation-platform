"""Private Vinted preparation: no marketplace requests or stock mutations."""
from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import datetime, UTC
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .audit import record_event
from .database import get_session
from .images import serve_image
from .models import (CatalogItem, CatalogItemType, CollectibleProduct, InventoryItem,
                     ListingPhotoPair, PlatformConfig, ShopListing)
from .ui import brand_header, page

router = APIRouter(prefix='/collectibles/vinted', tags=['vinted-preparation'])
PREFIX = 'vinted_draft:'


def rows_for_review(session):
    """An intake retake belongs to its existing stock, not a second draft."""
    pairs = session.scalars(select(ListingPhotoPair).order_by(ListingPhotoPair.created_at.desc())).all()
    figures = [p for p in pairs if (p.extracted or {}).get('item_kind') == 'collectible'
               and str((p.extracted or {}).get('graded') or '').lower() in ('', 'no', 'false', 'raw', 'ungraded')]
    result = []
    represented = set()
    owned = session.execute(select(InventoryItem, CatalogItem, CollectibleProduct)
        .join(CatalogItem, InventoryItem.catalog_item_id == CatalogItem.id)
        .outerjoin(CollectibleProduct, CollectibleProduct.catalog_item_id == CatalogItem.id)
        .where(CatalogItem.item_type == CatalogItemType.COLLECTIBLE, InventoryItem.quantity > 0)).all()
    for inv, item, product in owned:
        listings = session.scalars(select(ShopListing).where(ShopListing.inventory_item_id == inv.id)
                                    .order_by(ShopListing.updated_at.desc())).all()
        listing_ids = {l.id for l in listings}
        matches = [p for p in figures if p.matched_shop_listing_id in listing_ids
                   or p.proposed_catalog_item_id == item.id]
        represented.update(p.id for p in matches)
        pair = matches[0] if matches else None
        listing = listings[0] if listings else None
        facts = pair.extracted or {} if pair else {}
        paths = ([pair.front_image_path, pair.back_image_path] if pair else
                 ((listing.ebay_photo_paths if listing else None) or [inv.scan_image_path]))
        result.append(dict(key='i-'+inv.id, name=item.name, brand=product.brand if product else '',
            series=product.series if product else '', quantity=inv.quantity, paths=[p for p in paths if p],
            pair=pair, facts=facts, inventory=inv, listing=listing,
            recent=bool(pair), previous_price=str(listing.price) if listing else None))
    for pair in figures:
        if pair.id in represented or pair.matched_shop_listing_id:
            continue
        facts = pair.extracted or {}
        result.append(dict(key='p-'+pair.id, name=facts.get('character') or 'Unidentified figure',
            brand=facts.get('brand') or '', series=facts.get('series') or '',
            quantity=facts.get('confirmed_quantity'), paths=[p for p in [pair.front_image_path,pair.back_image_path] if p],
            pair=pair, facts=facts, inventory=None, listing=None, recent=True, previous_price=None))
    return sorted(result, key=lambda r: r['name'].casefold())


def _get_row(session, key):
    row = next((r for r in rows_for_review(session) if r['key'] == key), None)
    if row is None:
        raise HTTPException(404, 'Collectible not found; graded cards use their own review.')
    return row


def _draft(session, row):
    saved = session.get(PlatformConfig, PREFIX+row['key'])
    # Preserve a staged draft when its photo is later linked to stock.
    if saved is None and row['pair']:
        saved = session.get(PlatformConfig, PREFIX+'p-'+row['pair'].id)
    value = dict(saved.value or {}) if saved else {}
    value.setdefault('title', ' '.join(x for x in (row['brand'],row['series'],row['name']) if x))
    value.setdefault('description', f"{row['name']}. Opened/used collectible. Includes the figure shown in the photos. Figure only; box and collector card are not included unless separately confirmed. Please review the photos for condition.")
    return value


def _status(row, draft):
    issues = []
    if draft.get('hold_reason'):
        issues.append(draft['hold_reason'])
    if row['facts'].get('identity_confidence') in ('low','medium') and not draft.get('identity_confirmed'):
        issues.append('Confirm the exact identity / edition.')
    if row['quantity'] is None:
        issues.append('Confirm quantity.')
    if not row['recent']:
        issues.append('Older photo only; check that this is an original photo of your item.')
    if not draft.get('confirmed_price'):
        issues.append('Price needs your confirmation.')
    return list(dict.fromkeys(issues))


def round_up_99(value):
    amount = Decimal(value)
    if not amount.is_finite() or amount <= 0 or amount > 99999:
        raise ValueError('Enter a positive price up to AUD 99,999.')
    rounded = amount.to_integral_value(rounding=ROUND_FLOOR) + Decimal('.99')
    return rounded if rounded >= amount else rounded + 1


@router.get('', response_class=HTMLResponse)
def review(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    rows = rows_for_review(session)
    cards = []
    for row in rows:
        draft = _draft(session,row)
        key = row['key']
        issues = _status(row,draft)
        photos = ''.join(f"<a href='/collectibles/vinted/{key}/photo/{i}' target='_blank'><img loading='lazy' src='/collectibles/vinted/{key}/photo/{i}?v={hashlib.sha256(p.encode()).hexdigest()[:12]}' alt='Item photo {i+1}'></a>" for i,p in enumerate(row['paths']))
        suggestions = f"AU${escape(str(draft['suggested_price']))}" if draft.get('suggested_price') else 'Held for identification'
        sources = ''.join(f"<li><a href='{escape(s['url'],quote=True)}' target='_blank' rel='noopener'>{escape(s['label'])}</a></li>" for s in draft.get('sources',[]) if s.get('url','').startswith('https://'))
        query = ' '.join(x for x in (row['brand'],row['name']) if x)
        sold = 'https://www.ebay.com.au/sch/i.html?'+urlencode({'_nkw':query,'LH_Sold':'1','LH_Complete':'1','LH_PrefLoc':'1'})
        previous = f"Previous EzBay price: AU${escape(row['previous_price'])}. " if row['previous_price'] else ''
        pairlink = f"<a href='/product-photo-review/{row['pair'].id}?kind=collectible'>Review identity / stock</a>" if row['pair'] else ''
        value = draft.get('confirmed_price') or draft.get('suggested_price') or ''
        post_link = (f"<p><a class='btn' href='/collectibles/vinted/{key}/post'>Post to Vinted</a></p>"
                     if row['inventory'] is not None else '')
        cards.append(f"""<article class='panel' id='{key}'>
<h2>{escape(row['name'])}</h2><p>Vinted only · Qty {row['quantity'] if row['quantity'] is not None else 'unconfirmed'} · {'Recent upload' if row['recent'] else 'Older photo'}</p>
<div class='vinted-photos'>{photos}</div>
<p><strong>Suggested asking price: {suggestions}</strong></p><p>{escape(draft.get('price_note') or 'Research pending; this is not a sold valuation.')}</p>
<p class='muted'>{previous}Saved Vinted price: {('AU$'+escape(draft['confirmed_price'])) if draft.get('confirmed_price') else 'not confirmed'}.</p>
<p class='vinted-status'>{escape(' '.join(issues)) if issues else 'Draft details saved — review in Vinted before publishing.'}</p>
<details><summary>Price references</summary><ul>{sources}</ul><a href='{escape(sold,quote=True)}' target='_blank' rel='noopener'>Sold listings · Australia (research only)</a></details>
<form method='post' action='/collectibles/vinted/{key}/save'>
<label>Listing title<input name='title' maxlength='256' required value='{escape(draft['title'],quote=True)}'></label>
<label>Description<textarea name='description' rows='5' maxlength='5000'>{escape(draft['description'])}</textarea></label>
<label>Your Vinted price (AUD, postage extra)<input type='number' name='price' step='0.01' min='0.01' max='99999' value='{escape(str(value),quote=True)}' placeholder='Leave blank to keep unpriced'></label>
<p class='muted'>Saving confirms your entered price and rounds it up to .99. It does not publish or change existing listing prices.</p>
<button>Save Vinted draft</button></form><p>{pairlink}</p>
{post_link}</article>""")
    body = brand_header('Collectibles — Vinted preparation') + (
        f"<div class='panel'><p><strong>{len(rows)} figurines · Vinted only · Nothing published by this page</strong></p>"
        "<p>Graded cards stay in their separate workflow. Suggestions are AUD asking prices for opened figures, postage extra; they are not verified sold values. Review titles, condition, quantities and included accessories before posting.</p>"
        "<p><a class='btn' href='/collectibles/vinted/live'>Live on Vinted</a> "
        "<a class='btn ghost' href='/collectibles/vinted/download'>Download drafts and photos</a> "
        "<a class='btn ghost' href='/collectibles'>Collectibles catalogue</a> "
        "<a class='btn ghost' href='/shop-listings'>Back to Listing Studio</a></p></div>"
        + "<div class='vinted-grid'>"+''.join(cards)+"</div>"
        + "<style>.vinted-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:20px}.vinted-grid article{min-width:0}.vinted-photos{display:flex;gap:8px;overflow:auto}.vinted-photos img{height:230px;max-width:200px;object-fit:contain}.vinted-grid form,.vinted-grid label{display:grid;gap:8px}.vinted-grid form{gap:16px;margin-top:18px}.vinted-grid input,.vinted-grid textarea{width:100%;min-width:0;box-sizing:border-box;padding:10px}.vinted-status{color:#9b501b}</style>"
    )
    return HTMLResponse(page('EzBay — Vinted preparation',body))


@router.post('/{key}/save')
def save(key: str, title: str = Form(...), description: str = Form(''), price: str = Form(''),
         user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    row = _get_row(session,key)
    if not title.strip() or len(title)>256 or len(description)>5000:
        raise HTTPException(400,'Check the title and description lengths.')
    try:
        amount = str(round_up_99(price)) if price.strip() else None
    except (InvalidOperation, ValueError):
        raise HTTPException(400,'Enter a valid positive AUD price, or leave blank.')
    draft = _draft(session,row)
    draft.update(title=title.strip(),description=description.strip(),confirmed_price=amount,
                 currency='AUD',channel='vinted',reviewed_by=user,reviewed_at=datetime.now(UTC).isoformat())
    config = session.get(PlatformConfig,PREFIX+key)
    if config is None:
        config = PlatformConfig(key=PREFIX+key);session.add(config)
    config.value=draft
    record_event(session,actor_type='user',actor_id=user,action='vinted.draft.save',
                 resource_type='collectible',resource_id=key,outcome='success',
                 correlation_id=str(uuid.uuid4()),details={'price':amount,'published':False})
    return RedirectResponse('/collectibles/vinted#'+key,status_code=303)


@router.get('/{key}/photo/{index}')
def photo(key: str,index: int,user: str = Depends(require_dashboard_user),session: Session = Depends(get_session)):
    row = _get_row(session,key)
    if index<0 or index>=len(row['paths']):
        raise HTTPException(404,'Photo not found')
    return serve_image(row['paths'][index], max_age=0)


@router.get('/download')
def download(user: str = Depends(require_dashboard_user),session: Session = Depends(get_session)):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('READ-ME.txt','Vinted preparation only. Nothing published. Prices are AUD, postage extra. Suggested prices are unconfirmed asking prices, not sold valuations. Read REVIEW NOTES in each draft. Use original photos of the actual item. Graded cards are excluded. Do not list these figurines on eBay.\n')
        for row in rows_for_review(session):
            draft = _draft(session,row)
            folder = re.sub(r'[^a-zA-Z0-9_-]+','-',row['name']).strip('-')+'-'+row['key'][-8:]
            notes = _status(row,draft)
            text = '\n'.join([draft['title'],'',draft['description'],'',
                 'CONFIRMED PRICE (AUD): '+str(draft.get('confirmed_price') or 'Not confirmed'),
                 'SUGGESTED PRICE (AUD): '+str(draft.get('suggested_price') or 'Held'),
                 'QUANTITY: '+str(row['quantity'] or 'Confirm'),
                 'REVIEW NOTES: '+' '.join(notes),
                 'PRICE BASIS: '+str(draft.get('price_note') or 'Research pending')])
            z.writestr(folder+'/draft.txt',text)
            z.writestr(folder+'/references.json',json.dumps(draft.get('sources',[]),indent=2))
            for i,raw in enumerate(row['paths']):
                path = Path(raw)
                if path.is_file():
                    z.write(path,folder+f'/photo-{i+1}'+path.suffix)
                else:
                    z.writestr(folder+f'/MISSING-PHOTO-{i+1}.txt','Photo missing; resolve before listing.')
    return Response(buffer.getvalue(),media_type='application/zip',headers={
        'Content-Disposition':'attachment; filename="vinted-collectibles-drafts.zip"','Cache-Control':'no-store'})
