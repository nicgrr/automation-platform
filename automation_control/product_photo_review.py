"""Explicit review of collectibles and slabs uploaded as photo pairs."""
import hashlib
import threading
import uuid
from decimal import Decimal
from html import escape
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from .auth import require_dashboard_user
from .database import get_session
from .models import (CatalogItem, CatalogItemType, CollectibleProduct, InventoryItem,
    CardVariant, ShopListing, ShopListingStatus, ListingPhotoPair, ListingPhotoPairStatus, ListingPhotoBatch)
from .audit import record_event
from .ui import brand_header, page

router = APIRouter(tags=['product-photo-review'])
_LOCK = threading.Lock()

def is_graded(facts):
    value = str((facts or {}).get('graded') or '').strip().casefold()
    return value not in ('', 'no', 'raw', 'ungraded', 'none', 'false', 'not graded')

def _get(session, pair_id):
    pair = session.get(ListingPhotoPair, pair_id)
    if pair is None: raise HTTPException(404, 'Photo pair not found')
    return pair

@router.get('/product-photo-review/{pair_id}', response_class=HTMLResponse)
def review(pair_id: str, kind: str = '', user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    pair = _get(session, pair_id)
    facts = pair.extracted or {}
    selected_kind = kind if kind in ('collectible','graded') else ('graded' if is_graded(facts) else 'collectible')
    slab = selected_kind == 'graded'
    candidates = session.scalars(select(CatalogItem).where(CatalogItem.item_type == (CatalogItemType.GRADED_CARD if slab else CatalogItemType.COLLECTIBLE)).order_by(CatalogItem.name)).all()
    options = "<option value=''>New item — check existing records first</option>" + ''.join(f"<option value='{escape(c.id)}' {'selected' if c.id==pair.proposed_catalog_item_id else ''}>{escape(c.name)}</option>" for c in candidates)
    def field(label, key, value='', required=False):
        return f"<label>{label}<input name='{key}' value='{escape(str(value),quote=True)}' maxlength='128' {'required' if required else ''}></label>"
    photos = ''.join(f"<img src='/ebay-listing-bulk-photos/{pair.batch_id}/image/{pair.id}/{side}' alt='{side} photo'>" for side in ('front','back') if getattr(pair, side+'_image_path'))
    body = brand_header('Review graded card' if slab else 'Review collectible') + f"<a href='/ebay-listing-bulk-photos/{pair.batch_id}'>Back to upload</a><div class='product-photos'>{photos}</div>"
    body += ("<p>Existing stock keeps its quantity and price. Save the reviewed photos to Listing Studio.</p>"
             f"<p>Review as: <a href='/product-photo-review/{pair_id}?kind=collectible'>Collectible</a> · <a href='/product-photo-review/{pair_id}?kind=graded'>Graded card</a></p>"
             + (f"<p>Detected grading: {escape(str(facts.get('graded') or 'Not detected'))}</p>" if slab else '')
             + f"<form method='post' action='/product-photo-review/{pair_id}'><input type='hidden' name='kind' value='{selected_kind}'>"
             + f"<label>Existing {'slab' if slab else 'collectible'}<select name='existing'>{options}</select></label>"
             + field('Card name and number' if slab else 'Character / figure name','name',facts.get('character') or '',True)
             + field('Card game' if slab else 'Brand','brand',facts.get('brand') or '')
             + field('Set' if slab else 'Series','series',facts.get('series') or facts.get('set_name') or '')
             + field('Language / printing' if slab else 'Variant / colourway','variant',facts.get('variant') or (facts.get('language') if slab else '') or '')
             + ("<input type='hidden' name='condition' value='Graded'>" if slab else "<label>Condition<select name='condition'><option>Used</option><option>New</option></select></label>")
             + (field('Grading company','company',required=True) + field('Grade','grade',required=True) + field('Certificate number','certificate',required=True)
                + "<input type='hidden' name='quantity' value='1'><p>One slab per certificate. Use its actual front and back photos.</p>" if slab else
                "<label>Quantity for new stock<input name='quantity' type='number' min='1' max='999' value='1'></label>")
             + "<label><input type='checkbox' name='confirmed' value='yes' required>I checked the identity and condition.</label>"
             "<button class='btn'>Save and open Listing Studio</button></form>"
             "<style>.product-photos{display:flex;flex-wrap:wrap;gap:12px}.product-photos img{width:min(100%,340px);max-height:440px;object-fit:contain}form{display:grid;gap:14px;max-width:700px}label{display:grid;gap:5px}input,select{min-width:0;padding:10px}</style>")
    return HTMLResponse(page('EzBay — Product review', body))

@router.post('/product-photo-review/{pair_id}')
def save(pair_id: str, kind: str = Form(...), name: str = Form(...), existing: str = Form(''),
         brand: str = Form(''), series: str = Form(''), variant: str = Form(''), condition: str = Form('Used'),
         company: str = Form(''), grade: str = Form(''), certificate: str = Form(''), quantity: int = Form(1),
         confirmed: str = Form(''), user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    with _LOCK:
        pair = _get(session,pair_id)
        if pair.status in (ListingPhotoPairStatus.APPLIED,ListingPhotoPairStatus.PUBLISHED) and pair.matched_shop_listing_id:
            return RedirectResponse(f'/shop-listings/{pair.matched_shop_listing_id}/ebay-preview',303)
        if confirmed != 'yes' or kind not in ('graded','collectible') or not name.strip() or not 1 <= quantity <= 999:
            raise HTTPException(400,'Confirm the identity and quantity')
        if any(len(v)>128 for v in (name,brand,series,variant)) or condition not in ('Used','New','Graded'):
            raise HTTPException(400,'Invalid item details')
        company,grade,certificate = company.strip(),grade.strip(),certificate.strip()
        if kind=='graded' and (not all((company,grade,certificate)) or len(company)>64 or len(grade)>32 or len(certificate)>64 or not pair.back_image_path or 'representative-backs' in pair.back_image_path):
            raise HTTPException(400,'Confirm company, grade, certificate and actual slab back photo')
        if kind=='collectible':
            company=grade=certificate=''
        expected = CatalogItemType.GRADED_CARD if kind=='graded' else CatalogItemType.COLLECTIBLE
        inventory = None
        if kind=='graded':
            matches=list(session.scalars(select(InventoryItem).where(InventoryItem.certification_number==certificate,InventoryItem.grading_company.ilike(company))))
            if len(matches)>1: raise HTTPException(409,'Multiple records have this certificate; resolve them before saving')
            inventory=matches[0] if matches else None
            if inventory and (inventory.grade != grade or (existing and inventory.catalog_item_id!=existing)):
                raise HTTPException(409,'Certificate conflicts with the selected record or grade')
        item=session.get(CatalogItem,existing) if existing else (session.get(CatalogItem,inventory.catalog_item_id) if inventory else None)
        if existing and (not item or item.item_type!=expected): raise HTTPException(400,'Choose an existing item of the correct type')
        if kind=='graded' and item and not inventory:
            raise HTTPException(409,'The selected slab does not match this company and certificate')
        if item is None:
            identity=[kind,company,certificate] if kind=='graded' else [kind,name,brand,series,variant]
            cid='photo-product-'+hashlib.sha256('|'.join(v.strip().casefold() for v in identity).encode()).hexdigest()[:32]
            item=session.get(CatalogItem,cid)
            if item is None:
                # Avoid quietly creating a second catalogue entry for a known collectible.
                possible=session.scalar(select(CatalogItem).where(CatalogItem.item_type==expected,CatalogItem.name.ilike(name.strip())))
                if possible and kind=='collectible':raise HTTPException(409,'An item with this name already exists. Select it above to update its photos.')
                item=CatalogItem(id=cid,name=name.strip(),item_type=expected,pricing_policy='manual_only',pricing_policy_reason='Photo intake requires reviewed pricing')
                session.add(item);session.flush()
                if kind=='collectible':session.add(CollectibleProduct(catalog_item_id=cid,brand=brand,series=series,character=name.strip(),variant=variant))
        stocks=list(session.scalars(select(InventoryItem).where(InventoryItem.catalog_item_id==item.id)))
        if len(stocks)>1:raise HTTPException(409,'Multiple stock records exist; resolve them before saving')
        inventory=inventory or (stocks[0] if stocks else None)
        if inventory and inventory.quantity<=0:raise HTTPException(409,'Existing stock is sold out; confirm replenishment separately')
        if inventory is None:
            inventory=InventoryItem(catalog_item_id=item.id,variant=CardVariant.NORMAL,condition='Graded' if kind=='graded' else condition,
                quantity=1 if kind=='graded' else quantity,scan_image_path=pair.front_image_path,
                grading_company=company or None,grade=grade or None,certification_number=certificate or None,
                notes=f'{brand} | {series} | {variant}')
            session.add(inventory);session.flush()
        listings=list(session.scalars(select(ShopListing).where(ShopListing.inventory_item_id==inventory.id,ShopListing.status.in_([ShopListingStatus.ACTIVE,ShopListingStatus.DRAFT]))))
        if len(listings)>1:raise HTTPException(409,'Multiple listings exist for this stock; resolve them before saving')
        listing=listings[0] if listings else None
        if listing is None:
            title=f'{name} {company} {grade}' if kind=='graded' else name
            listing=ShopListing(catalog_item_id=item.id,inventory_item_id=inventory.id,title=title[:256],condition=inventory.condition,quantity=inventory.quantity,price=Decimal('0'),currency='AUD',status=ShopListingStatus.DRAFT)
            session.add(listing);session.flush()
        inventory.scan_image_path=pair.front_image_path
        listing.ebay_photo_paths=[pair.front_image_path]+([pair.back_image_path] if pair.back_image_path else [])
        pair.extracted={**(pair.extracted or {}),'product_review':{'kind':kind,'company':company,'grade':grade,'certificate':certificate,'reviewed_by':user}}
        pair.matched_shop_listing_id=listing.id;pair.proposed_catalog_item_id=item.id;pair.status=ListingPhotoPairStatus.APPLIED;pair.issue=None
        from .ebay_photo_batch import _refresh_batch_counts
        batch=session.get(ListingPhotoBatch,pair.batch_id)
        if batch:_refresh_batch_counts(session,batch)
        session.commit()
        record_event(session,actor_type='user',actor_id=user,action='product_photo.confirm',resource_type='listing_photo_pair',resource_id=pair.id,outcome='success',correlation_id=str(uuid.uuid4()),details={'listing_id':listing.id,'kind':kind})
        return RedirectResponse(f'/shop-listings/{listing.id}/ebay-preview',303)
