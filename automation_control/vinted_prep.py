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

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .audit import record_event
from .database import get_session
from .collectible_reference import PICKER_SCRIPT, clean
from .images import serve_image
from .models import (CatalogItem, CatalogItemType, CollectibleProduct, InventoryItem,
                     ListingPhotoPair, PlatformConfig, ShopListing)
from .ui import brand_header, page
from .vinted_copy import generate_copy

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
    for field in ('brand', 'series', 'name'):
        value.setdefault(field, clean(row[field]))
    value.setdefault('condition', 'Opened / used')
    value.setdefault('included', 'Figure only; no box or collector card included.')
    value.setdefault('condition_notes', '')
    generated = generate_copy(value['brand'], value['series'], value['name'],
                              condition=value['condition'], included=value['included'],
                              notes=value['condition_notes'])
    legacy_description = (f"{row['name']}. Opened/used collectible. Includes the figure shown in the photos. "
                          "Figure only; box and collector card are not included unless separately confirmed. "
                          "Please review the photos for condition.")
    # Earlier versions stored condition/accessory details only in freeform copy.
    # Keep those drafts custom until the owner explicitly chooses regeneration.
    value.setdefault('copy_mode', 'custom' if value.get('description') and
                     value['description'] not in (legacy_description, generated['description']) else 'generate')
    for field, text in generated.items():
        value.setdefault(field, text)
    return value


def saved_identity_for_pair(session, pair):
    """Prefill stock review from explicitly saved Vinted details, without writes."""
    keys = []
    if pair.matched_shop_listing_id:
        listing = session.get(ShopListing, pair.matched_shop_listing_id)
        if listing and listing.inventory_item_id:
            keys.append(PREFIX+'i-'+listing.inventory_item_id)
    keys.append(PREFIX+'p-'+pair.id)
    for key in keys:
        saved = session.get(PlatformConfig,key)
        if saved:
            return {field: clean(saved.value[field]) for field in ('brand','series','name')
                    if field in (saved.value or {})}
    return {}


def _status(row, draft):
    issues = []
    for field, label in (('brand', 'brand'), ('series', 'series / set'), ('name', 'figure name')):
        if not clean(draft.get(field, row.get(field))):
            issues.append(f'Confirm the {label}.')
    if draft.get('hold_reason'):
        issues.append(draft['hold_reason'])
    reviewed = row['facts'].get('product_review') or {}
    identity_checked = draft.get('identity_confirmed') or (reviewed.get('kind') == 'collectible' and reviewed.get('reviewed_by'))
    if row['facts'].get('identity_confidence') in ('low','medium') and not identity_checked:
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
        query = ' '.join(draft[field] for field in ('brand', 'series', 'name') if draft[field])
        sold = 'https://www.ebay.com.au/sch/i.html?'+urlencode({'_nkw':query,'LH_Sold':'1','LH_Complete':'1','LH_PrefLoc':'1'})
        previous = f"Previous EzBay price: AU${escape(row['previous_price'])}. " if row['previous_price'] else ''
        pairlink = f"<a href='/product-photo-review/{row['pair'].id}?kind=collectible'>Review identity / stock</a>" if row['pair'] else ''
        value = draft.get('confirmed_price') or draft.get('suggested_price') or ''
        post_link = f"<p><a class='btn' href='/collectibles/vinted/{key}/post'>Copy listing &amp; photos</a></p>"
        cards.append(f"""<article class='panel' id='{key}'>
<h2>{escape(draft['name'])}</h2><p>Vinted first · Qty {row['quantity'] if row['quantity'] is not None else 'unconfirmed'} · {'Recent upload' if row['recent'] else 'Older photo'}</p>
<div class='vinted-photos'>{photos}</div>
<form method='post' action='/collectibles/vinted/{key}/save' class='vinted-draft-form'>
<fieldset class='vinted-identity'><legend>Item details</legend>
<label>Brand<input name='brand' maxlength='128' value='{escape(draft['brand'],quote=True)}' placeholder='Brand needs identification'></label>
<label>Series / Set<input name='series' maxlength='128' value='{escape(draft['series'],quote=True)}' placeholder='Series / set needs identification'></label>
<label>Figure name<input name='name' data-character maxlength='128' required value='{escape(draft['name'],quote=True)}'></label>
<p class='muted'>Choose a brand to see its series, then choose the figure. Uncertain details remain flagged for review.</p>
</fieldset>
<p><strong>Suggested asking price: {suggestions}</strong></p><p>{escape(draft.get('price_note') or 'Research pending; this is not a sold valuation.')}</p>
<p class='muted'>{previous}Saved Vinted price: {('AU$'+escape(draft['confirmed_price'])) if draft.get('confirmed_price') else 'not confirmed'}.</p>
<p class='vinted-status'>{escape(' '.join(issues)) if issues else 'Draft details saved — review in Vinted before publishing.'}</p>
<details><summary>Price references</summary><ul>{sources}</ul><a href='{escape(sold,quote=True)}' target='_blank' rel='noopener'>Sold listings · Australia (research only)</a></details>
<label>Condition<input name='condition' maxlength='256' required value='{escape(draft['condition'],quote=True)}'></label>
<label>Included in the sale<input name='included' maxlength='1000' required value='{escape(draft['included'],quote=True)}'></label>
<label>Condition notes / other details<textarea name='condition_notes' rows='3' maxlength='2000' placeholder='Add any marks, damage or other details the buyer should know.'>{escape(draft['condition_notes'])}</textarea></label>
<input type='hidden' name='copy_mode' value='custom'>
<label class='copy-option'><input type='checkbox' name='copy_mode' value='generate' {'checked' if draft.get('copy_mode','generate') == 'generate' else ''}>Write the title and description from these details when saving</label>
<details {'open' if draft['copy_mode']=='custom' else ''}><summary>Listing wording — untick above to keep your own edits</summary>
<p class='muted'>Before rewriting custom wording, carry any condition and accessory details into the fields above.</p>
<label>Listing title<input name='title' maxlength='256' value='{escape(draft['title'],quote=True)}'></label>
<label>Description<textarea name='description' rows='5' maxlength='5000'>{escape(draft['description'])}</textarea></label>
</details>
<label>Your Vinted price (AUD, postage extra)<input type='number' name='price' step='0.01' min='0.01' max='99999' value='{escape(str(value),quote=True)}' placeholder='Leave blank to keep unpriced'></label>
<p class='muted'>Save opens your copy-and-paste listing and photos. Save details keeps your price unchanged. Confirm price uses the entered amount, rounded up to .99.</p>
<div class='vinted-save-actions'><button name='intent' value='details'>Save details</button><button name='intent' value='price'>Save draft &amp; confirm price</button></div></form><p>{pairlink}</p>
{post_link}</article>""")
    body = brand_header('Collectibles — Vinted preparation') + (
        f"<div class='panel'><p><strong>{len(rows)} figurines · Vinted first · Nothing published by this page</strong></p>"
        "<p>Graded cards stay in their separate workflow. Suggestions are AUD asking prices for opened figures, postage extra; they are not verified sold values. Review titles, condition, quantities and included accessories before posting.</p>"
        "<p><a class='btn' href='/collectibles/vinted/live'>Live on Vinted</a> "
        "<a class='btn ghost' href='/collectibles/vinted/download'>Download drafts and photos</a> "
        "<a class='btn ghost' href='/collectibles'>Collectibles catalogue</a> "
        "<a class='btn ghost' href='/shop-listings'>Back to Listing Studio</a></p></div>"
        + "<div class='vinted-grid'>"+''.join(cards)+"</div>"
        + PICKER_SCRIPT
        + "<style>.vinted-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:20px}.vinted-grid article{min-width:0}.vinted-photos{display:flex;gap:8px;overflow:auto}.vinted-photos img{height:230px;max-width:200px;object-fit:contain}.vinted-grid form,.vinted-grid label{display:grid;gap:8px}.vinted-grid form{gap:16px;margin-top:18px}.vinted-grid input,.vinted-grid textarea{width:100%;min-width:0;box-sizing:border-box;padding:10px}.vinted-grid label.copy-option{display:flex;align-items:start}.vinted-grid .copy-option input{width:auto;margin-top:4px}.vinted-status{color:#9b501b}.vinted-identity{display:grid;gap:12px;border:1px solid #ddd;border-radius:10px;min-width:0;padding:14px;margin:12px 0}.vinted-identity legend{font-weight:700}.vinted-save-actions{display:flex;flex-wrap:wrap;gap:8px}</style>"
    )
    return HTMLResponse(page('EzBay — Vinted preparation',body))


@router.post('/{key}/save')
async def save(key: str, request: Request, title: str = Form(''), description: str = Form(''), price: str = Form(''),
         brand: str | None = Form(None), series: str | None = Form(None), name: str | None = Form(None),
         condition: str | None = Form(None), included: str | None = Form(None), condition_notes: str | None = Form(None),
         copy_mode: str | None = Form(None), intent: str = Form('details'),
         user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    row = _get_row(session,key)
    if intent not in ('details', 'price'):
        raise HTTPException(400,'Choose Save details or Confirm price.')
    draft = _draft(session,row)
    form = await request.form()
    copy_mode = copy_mode if 'copy_mode' in form else draft['copy_mode']
    fields = {field: clean((value or '') if field in form else draft[field])
              for field, value in (('brand',brand), ('series',series), ('name',name))}
    if not fields['name'] or any(len(value)>128 for value in fields.values()):
        raise HTTPException(400,'Enter a figure name and keep each item detail within 128 characters.')
    contents = {field: ((value or '').strip() if field in form else draft[field])
                for field, value in (('condition', condition), ('included', included), ('condition_notes', condition_notes))}
    if (not contents['condition'] or not contents['included'] or len(contents['condition'])>256
            or len(contents['included'])>1000 or len(contents['condition_notes'])>2000):
        raise HTTPException(400, 'Enter the condition and included contents; keep notes within 2,000 characters.')
    if copy_mode == 'generate':
        copy = generate_copy(**fields, condition=contents['condition'], included=contents['included'],
                             notes=contents['condition_notes'])
    elif copy_mode == 'custom' and title.strip() and len(title)<=256 and len(description)<=5000:
        copy = {'title': title.strip(), 'description': description.strip()}
    else:
        raise HTTPException(400, 'Choose generated wording, or enter a title up to 256 and description up to 5,000 characters.')
    if draft.get('title') != copy['title'] or draft.get('description') != copy['description']:
        draft['previous_copy'] = {field: draft.get(field, '') for field in ('title', 'description')}
    if intent == 'price':
        try:
            amount = str(round_up_99(price)) if price.strip() else None
        except (InvalidOperation, ValueError):
            raise HTTPException(400,'Enter a valid positive AUD price, or leave blank.')
        draft.update(confirmed_price=amount,reviewed_by=user,reviewed_at=datetime.now(UTC).isoformat())
    draft.update(fields)
    draft.update(contents)
    draft.update(copy)
    draft.update(copy_mode=copy_mode,currency='AUD',channel='vinted',
                 details_updated_by=user,details_updated_at=datetime.now(UTC).isoformat())
    config = session.get(PlatformConfig,PREFIX+key)
    if config is None:
        config = PlatformConfig(key=PREFIX+key);session.add(config)
    config.value=draft
    record_event(session,actor_type='user',actor_id=user,action='vinted.draft.save',
                 resource_type='collectible',resource_id=key,outcome='success',
                 correlation_id=str(uuid.uuid4()),details={'price':draft.get('confirmed_price'),'save_intent':intent,'identity':fields,'published':False})
    return RedirectResponse('/collectibles/vinted/'+key+'/post',status_code=303)


@router.get('/{key}/photo/{index}')
def photo(key: str,index: int,user: str = Depends(require_dashboard_user),session: Session = Depends(get_session)):
    row = _get_row(session,key)
    if index<0 or index>=len(row['paths']):
        raise HTTPException(404,'Photo not found')
    return serve_image(row['paths'][index], max_age=0)


@router.get('/{key}/photo/{index}/download')
def photo_download(key: str,index: int,user: str = Depends(require_dashboard_user),session: Session = Depends(get_session)):
    row = _get_row(session,key)
    if index<0 or index>=len(row['paths']):
        raise HTTPException(404,'Photo not found')
    path = Path(row['paths'][index])
    if not path.is_file():
        raise HTTPException(404,'Photo not found')
    name = re.sub(r'[^a-zA-Z0-9_-]+','-',_draft(session,row)['name']).strip('-') or 'figure'
    return FileResponse(path, filename=f'{name}-{index+1}{path.suffix}', headers={'Cache-Control':'no-store'})


@router.get('/download')
def download(user: str = Depends(require_dashboard_user),session: Session = Depends(get_session)):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('READ-ME.txt','Vinted preparation only. Nothing published. Prices are AUD, postage extra. Suggested prices are unconfirmed asking prices, not sold valuations. Read REVIEW NOTES in each draft. Use original photos of the actual item. Graded cards are excluded. Vinted first; after 14 days unsold, review for eBay. Record the Vinted link to start tracking.\n')
        for row in rows_for_review(session):
            draft = _draft(session,row)
            folder = re.sub(r'[^a-zA-Z0-9_-]+','-',draft['name']).strip('-')+'-'+row['key'][-8:]
            notes = _status(row,draft)
            text = '\n'.join([draft['title'],'',
                 'BRAND: '+(draft['brand'] or 'Needs identification'),
                 'SERIES / SET: '+(draft['series'] or 'Needs identification'),
                 'FIGURE NAME: '+draft['name'],'',draft['description'],'',
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
