"""Human catalogue matching for photo proposals; never publishes to eBay."""
import hashlib
import threading
import uuid
from decimal import Decimal
from pathlib import Path
from html import escape
from types import SimpleNamespace
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session
from .auth import require_dashboard_user
from .database import get_session
from .models import (ListingPhotoPair, ListingPhotoPairStatus, ListingPhotoBatch, CatalogItem,
    CatalogItemType, TcgCard, CardSet, ShopListing, ShopListingStatus, InventoryItem, InventoryStatus, CardVariant)
from .listing_finishes import FINISHES, finish_field, save_finish
from .representative_backs import attach_to_pair, with_notice
from .listing_quality import generated_ebay_title, generated_ebay_description
from .audit import record_event
from .ui import page, brand_header

router = APIRouter(tags=['photo-identification-review'])
_REVIEW_LOCK = threading.Lock()
GAMES = ('pokemon', 'pokemon_japan', 'one_piece', 'dragon_ball_super', 'dragon_ball_fusion_world', 'super_dragon_ball_heroes', 'dragon_ball_super_divers')


def _pair(session, pair_id):
    pair = session.get(ListingPhotoPair, pair_id)
    if not pair: raise HTTPException(404, 'Photo not found')
    return pair


def _url(pair_id, **values):
    return f'/photo-review/{pair_id}?' + urlencode(values)


def _input(label, name, value='', required=False):
    return f"<label>{escape(label)}<input name='{name}' value='{escape(str(value or ''), quote=True)}' {'required' if required else ''} maxlength='256'></label>"


@router.get('/photo-review/{pair_id}', response_class=HTMLResponse)
def review(pair_id: str, q: str = '', number: str = '', language: str = '', catalog: str = '',
           user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    pair = _pair(session, pair_id)
    facts = pair.extracted or {}
    from .product_photo_review import is_graded
    if facts.get('item_kind') == 'collectible' or is_graded(facts):
        return RedirectResponse(f'/product-photo-review/{pair.id}', 303)
    query = q.strip() or str(facts.get('character') or '')
    statement = select(CatalogItem, TcgCard).join(TcgCard, TcgCard.catalog_item_id == CatalogItem.id)
    if query:
        statement = statement.where(or_(CatalogItem.name.ilike(f'%{query}%'), TcgCard.number.ilike(f'%{query}%')))
    if number: statement = statement.where(TcgCard.number.ilike(f'%{number.strip()}%'))
    if language: statement = statement.where(func.lower(TcgCard.language) == language.lower().strip())
    results = session.execute(statement.order_by(CatalogItem.name, TcgCard.number).limit(40)).all() if query or number else []
    cards = []
    for item, card in results:
        card_set = session.get(CardSet, card.set_id) if card.set_id else None
        image_url = item.image_url if item.image_url and item.image_url.startswith('https://') else (f'/photo-review-reference/{item.id}' if item.image_url else None)
        image = f"<img src='{escape(image_url, quote=True)}' alt='Catalogue reference' loading='lazy'>" if image_url else '<div>Reference image unavailable</div>'
        existing = list(session.scalars(select(ShopListing).where(ShopListing.catalog_item_id == item.id, ShopListing.status.in_([ShopListingStatus.ACTIVE,ShopListingStatus.DRAFT]),ShopListing.quantity > 0)))
        cards.append(f"<article class='panel'>{image}<strong>{escape(item.name)}</strong>"
                     f"<p>{escape(card_set.name if card_set else card.set_id or 'Unknown set')} · {escape(card.number or '')}<br>"
                     f"{escape(card.language or '')} · {escape(card.rarity or '')} · {escape(card.variant or '')}</p>"
                     f"<small>{len(existing)} existing current listing(s)</small><p><a class='btn' href='{escape(_url(pair_id,q=query,catalog=item.id),quote=True)}'>Choose this printing</a></p></article>")
    selection = ''
    if catalog:
        item = session.get(CatalogItem, catalog);card = session.get(TcgCard, catalog)
        if not item or not card: raise HTTPException(404, 'Catalogue card not found')
        existing = list(session.scalars(select(ShopListing).where(ShopListing.catalog_item_id == catalog).order_by(ShopListing.created_at.desc())))
        choices = ''.join(f"<option value='{escape(l.id)}'>{escape(l.title)} — {l.status.value}, qty {l.quantity}</option>" for l in existing)
        selection = (f"<section class='panel'><h2>Confirm {escape(item.name)} #{escape(card.number or '')}</h2>"
                     f"<p>{escape(card.language or '')} · {escape(card.rarity or '')}</p>"
                     f"<form method='post' action='/photo-review/{pair_id}/save'>"
                     f"<input type='hidden' name='catalog' value='{escape(catalog)}'>"
                     "<label>Use these photos for<select name='mode'>" +
                     ("<option value='update'>Update an existing listing — quantity stays unchanged</option>" if existing else '') +
                     "<option value='new'>Create a draft for this printing</option></select></label>" +
                     (f"<label>Existing record<select name='listing_id'>{choices}</select></label>" if existing else '') +
                     finish_field(SimpleNamespace(finish=None)) +
                     "<label>Quantity for NEW stock only<input name='quantity' type='number' min='1' max='999' placeholder='Confirm physical count'></label>"
                     "<p>Existing stock is reused. New stock requires a confirmed count. Existing listings keep their price and quantity.</p>"
                     "<label><input type='checkbox' name='confirmed' value='yes' required> I checked the name, set, number, language, rarity and finish against my photo.</label>"
                     "<button class='btn'>Save and open Listing Studio</button></form></section>")
    game_options = ''.join(f"<option value='{g}'>{escape(g.replace('_',' ').title())}</option>" for g in GAMES)
    manual = ("<details class='panel'><summary>Correct card missing? Add a local catalogue entry</summary>"
              "<p>Enter the details visible on the card. This creates a local catalogue entry, not inventory or an eBay listing.</p>"
              f"<form method='post' action='/photo-review/{pair_id}/catalogue'>"
              f"<label>Game<select name='game'>{game_options}</select></label>" +
              _input('Card name','name',facts.get('character'),True)+_input('Set name','set_name',facts.get('set_name'),True)+
              _input('Printed number','number',facts.get('card_number'),True)+_input('Language','language',facts.get('language'),True)+
              _input('Rarity (IR, SR, etc.)','rarity',facts.get('rarity'),True)+
              "<label><input type='checkbox' name='confirmed' value='yes' required> I verified these details from the card.</label>"
              "<button class='btn'>Save catalogue identity and review</button></form></details>")
    body = (brand_header('Identify your card')+f"<p><a href='/ebay-listing-bulk-photos/{pair.batch_id}'>Back to upload review</a></p>"
            "<div class='identify-layout'><aside class='panel'><h2>Your photo</h2>"
            f"<img class='own-front' src='/ebay-listing-bulk-photos/{pair.batch_id}/image/{pair.id}/front' alt='Your uploaded card'>"
            "<p>Compare the set, printed number and language. Reference artwork may not show the correct foil treatment.</p></aside><section>"
            f"<form method='get'>{_input('Search name or number','q',query)}{_input('Printed number filter','number',number)}"
            f"{_input('Language filter','language',language)}<button class='btn'>Search catalogue</button></form>"+
            selection+f"<div class='catalogue-grid'>{''.join(cards) or '<p>No matches. Refine the search or add a local entry below.</p>'}</div>"+
            manual+"</section></div><style>.identify-layout{display:grid;grid-template-columns:minmax(220px,1fr) 2fr;gap:20px}.own-front{width:100%;max-height:600px;object-fit:contain}.catalogue-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}.catalogue-grid img{width:100%;height:230px;object-fit:contain}.catalogue-grid strong{display:block}form{display:grid;gap:12px;margin:16px 0}form label{display:grid;gap:5px}input,select{padding:10px;min-width:0}input[type=checkbox]{width:auto} @media(max-width:680px){.identify-layout{grid-template-columns:1fr}.own-front{max-height:330px}}</style>")
    return HTMLResponse(page('EZBay — Identify card',body))


@router.post('/photo-review/{pair_id}/catalogue')
def add_catalogue(pair_id: str, game: str = Form(...), name: str = Form(...), set_name: str = Form(...),
                  number: str = Form(...), language: str = Form(...), rarity: str = Form(...), confirmed: str = Form(''),
                  user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    pair = _pair(session,pair_id)
    fields=[game,name,set_name,number,language,rarity]
    if confirmed!='yes' or game not in GAMES or any(not v.strip() or len(v)>256 for v in fields):
        raise HTTPException(400,'Confirm complete card details first')
    if len(number)>32 or len(language)>32 or len(rarity)>128: raise HTTPException(400,'Card details are too long')
    # Reuse an existing exact identity, including a prior manual entry.
    found = session.execute(select(TcgCard).join(CatalogItem, CatalogItem.id==TcgCard.catalog_item_id).join(CardSet, CardSet.id==TcgCard.set_id).where(
        TcgCard.game==game, func.lower(CatalogItem.name)==name.strip().lower(), func.lower(CardSet.name)==set_name.strip().lower(),
        func.lower(TcgCard.number)==number.strip().lower(),func.lower(TcgCard.language)==language.strip().lower(),func.lower(TcgCard.rarity)==rarity.strip().lower())).scalars().first()
    cid=found.catalog_item_id if found else 'local-'+hashlib.sha256('|'.join(v.strip().lower() for v in fields).encode()).hexdigest()[:24]
    with _REVIEW_LOCK:
        if not session.get(CatalogItem,cid):
            sid='local-'+hashlib.sha256((game+'|'+set_name.strip().lower()).encode()).hexdigest()[:24]
            if not session.get(CardSet,sid):session.add(CardSet(id=sid,name=set_name.strip(),game=game));session.flush()
            session.add(CatalogItem(id=cid,item_type=CatalogItemType.TCG_CARD,name=name.strip()));session.flush()
            session.add(TcgCard(catalog_item_id=cid,game=game,set_id=sid,number=number.strip(),language=language.strip(),rarity=rarity.strip()))
        pair.extracted={**(pair.extracted or {}),'manual_catalogue_review':{'catalog_item_id':cid,'reviewed_by':user}}
        session.commit()
    return RedirectResponse(_url(pair_id,catalog=cid,q=name),303)


@router.post('/photo-review/{pair_id}/save')
def save_review(pair_id: str, catalog: str = Form(...), mode: str = Form(...), listing_id: str = Form(''),
                finish: str = Form(''), quantity: str = Form(''), confirmed: str = Form(''),
                user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    with _REVIEW_LOCK:
        pair=_pair(session,pair_id)
        from .product_photo_review import is_graded
        if (pair.extracted or {}).get('item_kind') == 'collectible':
            raise HTTPException(409,'Use the collectible review form for this item.')
        if is_graded(pair.extracted):
            raise HTTPException(409,"Use Review collectible / graded card to confirm the slab certificate; do not merge it with raw stock.")
        if confirmed!='yes' or mode not in ('update','new') or (finish and finish not in FINISHES):raise HTTPException(400,'Confirm a valid printing and finish')
        item=session.get(CatalogItem,catalog);card=session.get(TcgCard,catalog)
        if not item or not card:raise HTTPException(404,'Catalogue card not found')
        if pair.status in (ListingPhotoPairStatus.APPLIED,ListingPhotoPairStatus.PUBLISHED) and pair.matched_shop_listing_id:
            return RedirectResponse(f'/shop-listings/{pair.matched_shop_listing_id}/ebay-preview',303)
        listings=list(session.scalars(select(ShopListing).where(ShopListing.catalog_item_id==catalog)))
        if mode=='update':
            listing=session.get(ShopListing,listing_id)
            if not listing or listing.catalog_item_id!=catalog:raise HTTPException(400,'Choose an existing record for this catalogue card')
        else:
            matching=[l for l in listings if l.status in (ShopListingStatus.DRAFT,ShopListingStatus.ACTIVE) and l.quantity>0 and (not l.finish or (l.finish or '')==finish)]
            if matching:raise HTTPException(409,'This printing already has a listing. Select Update existing listing; no duplicate was created.')
            if not finish:raise HTTPException(400,'Choose the finish before creating a new draft')
            try:count=int(quantity)
            except ValueError:raise HTTPException(400,'Confirm the physical quantity for new stock')
            if not 1<=count<=999:raise HTTPException(400,'Quantity must be between 1 and 999')
            variant=CardVariant.HOLO if finish=='cosmos_holo' else CardVariant(finish or 'normal')
            stock=list(session.scalars(select(InventoryItem).where(InventoryItem.catalog_item_id==catalog,InventoryItem.variant==variant,InventoryItem.condition=='Near Mint')))
            if len(stock)>1:raise HTTPException(409,'Multiple stock records need review before creating a draft')
            inventory=stock[0] if stock else None
            if inventory and any(l.inventory_item_id==inventory.id and l.quantity>0 and l.status in (ShopListingStatus.ACTIVE,ShopListingStatus.DRAFT) for l in listings):raise HTTPException(409,'This stock already has a listing. Update the existing listing.')
            if inventory and inventory.quantity<=0:raise HTTPException(409,'Existing stock is sold out. Confirm replenishment separately.')
            if inventory is None:
                inventory=InventoryItem(catalog_item_id=catalog,variant=variant,condition='Near Mint',quantity=count,status=InventoryStatus.AVAILABLE,scan_image_path=pair.front_image_path)
                session.add(inventory);session.flush()
            listing=ShopListing(catalog_item_id=catalog,inventory_item_id=inventory.id,title=f'{item.name} #{card.number or ""}',price=Decimal('0'),currency='AUD',quantity=inventory.quantity,status=ShopListingStatus.DRAFT,condition='Near Mint')
            session.add(listing);session.flush()
        # Only photos/finish/copy are changed for an existing record: never stock or price.
        if finish:save_finish(listing,finish)
        attach_to_pair(pair,card.game,card.language,card.rarity)
        listing.ebay_photo_paths=[pair.front_image_path]+([pair.back_image_path] if pair.back_image_path else [])
        card_set=session.get(CardSet,card.set_id) if card.set_id else None
        inventory=session.get(InventoryItem,listing.inventory_item_id) if listing.inventory_item_id else None
        if not listing.ebay_title:listing.ebay_title=generated_ebay_title(listing,item,card,card_set,inventory)
        if not listing.ebay_description:listing.ebay_description=generated_ebay_description(listing,item,card,card_set,inventory,has_own_photo=True)
        listing.ebay_description=with_notice(listing.ebay_description,listing.ebay_photo_paths)
        pair.proposed_catalog_item_id=catalog;pair.matched_shop_listing_id=listing.id
        pair.proposed_title=listing.ebay_title;pair.proposed_description=listing.ebay_description
        pair.proposed_price=listing.price;pair.status=ListingPhotoPairStatus.APPLIED;pair.issue=None
        from .ebay_photo_batch import _refresh_batch_counts,_link_pair_inventory
        _link_pair_inventory(session,pair,inventory)
        batch=session.get(ListingPhotoBatch,pair.batch_id)
        if batch:_refresh_batch_counts(session,batch)
        session.commit()
        record_event(session,actor_type='user',actor_id=user,action='photo_review.confirm',resource_type='listing_photo_pair',resource_id=pair.id,outcome='success',correlation_id=str(uuid.uuid4()),details={'catalog_item_id':catalog,'listing_id':listing.id,'mode':mode,'finish':finish})
    return RedirectResponse(f'/shop-listings/{listing.id}/ebay-preview',303)


@router.get('/photo-review-reference/{catalog_id}')
def catalogue_reference(catalog_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    from .images import serve_image
    item=session.get(CatalogItem,catalog_id)
    if not item or not item.image_url:raise HTTPException(404,'Reference image unavailable')
    if item.image_url.startswith('https://'):return RedirectResponse(item.image_url)
    path=Path(item.image_url).resolve()
    root=Path(__file__).resolve().parents[1]/'scan_ingest_data'
    if not path.is_relative_to(root) or not path.is_file():raise HTTPException(404,'Reference image unavailable')
    return serve_image(path)
