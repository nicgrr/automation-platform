"""Graded-card price review and parcel requirements, separate from raw stock."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import (CatalogItem, CatalogItemType, CardSet, TcgCard, InventoryItem,
    InventoryStatus, ShopListing, ShopListingStatus, PlatformConfig, ListingPhotoPair,
    ListingPhotoPairStatus, ListingPhotoBatch, EbayListingLink, EbayListingLinkStatus,
    EbayExportItem, EbayExportItemStatus)
from .ui import page, brand_header

router = APIRouter(tags=['graded-cards'])
PREFIX = 'graded_listing:'
POLICY_KEY = 'graded_parcel_policy'


def review_data(session, listing):
    row = session.get(PlatformConfig, PREFIX + listing.id)
    return dict(row.value or {}) if row else {}


def save_review_data(session, listing, data):
    row = session.get(PlatformConfig, PREFIX + listing.id)
    if row is None:
        session.add(PlatformConfig(key=PREFIX + listing.id, value=data))
    else:
        row.value = data


def identity(stock):
    return {k: getattr(stock, k) for k in ('id', 'catalog_item_id', 'grading_company', 'grade', 'certification_number')}


def graded_condition(stock):
    """EBAY_AU 183454 values verified from getItemConditionPolicies (2026-11-02)."""
    if stock is None:
        raise ValueError('A graded stock record is required.')
    company = (stock.grading_company or '').strip().casefold()
    graders = {'cgc': '275015', 'vault': '2750123'}
    # These are the grades verified for this workflow; unknown labels fail closed.
    grades = {'10': '275020', 'pristine 10': '275020', 'gem mint 10': '275020',
              '9.5': '275021', 'gem mint 9.5': '275021', '9': '275022', 'mint 9': '275022'}
    grade = ' '.join((stock.grade or '').casefold().split())
    cert = stock.certification_number or ''
    if company not in graders or grade not in grades or not re.fullmatch(r'[A-Za-z0-9-]{1,20}', cert):
        raise ValueError('Review the supported grader, grade and certificate (maximum 20 characters).')
    return {'condition': 'LIKE_NEW', 'conditionDescriptors': [
        {'name': '27501', 'values': [graders[company]]},
        {'name': '27502', 'values': [grades[grade]]},
        {'name': '27503', 'additionalInfo': cert},
    ]}


def positive(value, maximum):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not 0 < number <= Decimal(str(maximum)):
            raise ValueError
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Enter a positive, finite measurement within parcel limits.') from None


def graded_package(session, listing):
    measurements = review_data(session, listing).get('package') or {}
    if not measurements.get('measured_at'):
        raise ValueError('Add the packed slab weight and dimensions in Graded cards.')
    weight = positive(measurements.get('weight_g'), 22000)
    dimensions = {key: int(positive(measurements.get(key + '_cm'), 105).to_integral_value(rounding=ROUND_CEILING))
                  for key in ('length', 'width', 'height')}
    return {'weight': {'value': float(weight / 1000), 'unit': 'KILOGRAM'},
            'dimensions': {**dimensions, 'unit': 'CENTIMETER'},
            'packageType': 'PACKAGE_THICK_ENVELOPE'}


def graded_policy(session, settings):
    row = session.get(PlatformConfig, POLICY_KEY)
    policy = row.value if row else {}
    if not (policy.get('environment') == settings.ebay_env
            and policy.get('marketplace_id') == settings.ebay_marketplace_id
            and policy.get('service') == 'AU_Regular'
            and policy.get('shipping_cost_type') == 'CALCULATED'
            and policy.get('verified_at') and policy.get('fulfillment_policy_id')):
        raise ValueError('A verified tracked-parcel policy is required for graded cards.')
    return str(policy['fulfillment_policy_id'])


def graded_preflight(session, listing, settings, effective_price):
    errors = []
    stock = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    if stock is None or stock.catalog_item_id != listing.catalog_item_id:
        return ['The slab must be linked to its own canonical stock record.']
    if stock.quantity != 1 or listing.quantity != 1 or stock.status not in (InventoryStatus.AVAILABLE, InventoryStatus.LISTED):
        errors.append('The slab must have exactly one available copy, with no reserved or sold stock.')
    if stock.condition != 'Graded' or listing.condition != 'Graded':
        errors.append('The slab condition must remain Graded.')
    duplicate = session.scalar(select(InventoryItem.id).where(
        InventoryItem.id != stock.id, InventoryItem.certification_number == stock.certification_number,
        InventoryItem.grading_company.ilike(stock.grading_company or '')))
    if duplicate:
        errors.append('Another stock record uses this grading certificate; resolve it first.')
    from .listing_quality import listing_photo_sources
    from .representative_backs import is_representative
    photos = listing_photo_sources(session, listing, session.get(CatalogItem, listing.catalog_item_id))
    if len({p.value for p in photos if p.is_own_photo and not is_representative(p.value)}) < 2 or any(is_representative(p.value) for p in photos):
        errors.append('Use the actual slab front and back photos; representative backs are not suitable.')
    data = review_data(session, listing)
    try:
        reviewed = Decimal(str(data.get('confirmed_price', 'NaN')))
        approved = reviewed.is_finite() and reviewed == effective_price and data.get('price_confirmed_at') and data.get('reviewed_identity') == identity(stock)
    except InvalidOperation:
        approved = False
    if not approved:
        errors.append('Confirm this exact price in Graded cards before publishing.')
    for check in (lambda: graded_condition(stock), lambda: graded_package(session, listing), lambda: graded_policy(session, settings)):
        try:
            check()
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def _get(session, listing_id):
    listing = session.get(ShopListing, listing_id)
    item = session.get(CatalogItem, listing.catalog_item_id) if listing else None
    if item is None or item.item_type != CatalogItemType.GRADED_CARD:
        raise HTTPException(404, 'Graded listing not found')
    return listing


def _draft_only(session, listing):
    """Never make a local-only price change to a live or uncertain eBay offer."""
    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing.id))
    export = session.scalar(select(EbayExportItem).where(EbayExportItem.sku == listing.id,
        EbayExportItem.status.in_([EbayExportItemStatus.PENDING, EbayExportItemStatus.CONFIRMED])))
    if listing.status != ShopListingStatus.DRAFT or (link and link.status != EbayListingLinkStatus.ENDED) or export:
        raise HTTPException(409, 'This item may already have an eBay offer. Review it in Listing Studio before changing its price or postage.')


@router.get('/graded-cards', response_class=HTMLResponse)
def graded_review(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    from .listing_quality import australian_sold_listings_url, generated_ebay_title
    rows = session.scalars(select(ShopListing).join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
        .where(CatalogItem.item_type == CatalogItemType.GRADED_CARD,
               ShopListing.status.in_([ShopListingStatus.DRAFT, ShopListingStatus.ACTIVE]))
        .order_by(ShopListing.created_at)).all()
    body = brand_header('Graded cards') + '<p><a href="/ebay">Back to eBay workspace</a></p><h1>Graded cards — price review</h1><p>Suggested prices are AUD, excluding postage. Confirm each price, then add measured parcel details. Publishing is a separate step in Listing Studio.</p>'
    for listing in rows:
        stock = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
        if not stock:
            continue
        item = session.get(CatalogItem, listing.catalog_item_id)
        card = session.get(TcgCard, item.id)
        card_set = session.get(CardSet, card.set_id) if card and card.set_id else None
        data = review_data(session, listing)
        suggested = data.get('suggested_price', '')
        confirmed = data.get('confirmed_price') if data.get('reviewed_identity') == identity(stock) and str(listing.price) == data.get('confirmed_price') else None
        price = confirmed or suggested
        query = f'{card.character or item.name} {card.number or ""} {stock.grading_company} {stock.grade} {card.language}' if card else item.name
        title = listing.ebay_title or (generated_ebay_title(listing, item, card, card_set, stock) if card else listing.title)
        photos = ''.join(f'<a href="/shop-listings/{listing.id}/ebay-photo/{i}" target="_blank"><img src="/shop-listings/{listing.id}/ebay-photo/{i}" alt="Actual slab {side}" loading="lazy"></a>' for i, side in enumerate(('front', 'back')) if i < len(listing.ebay_photo_paths or []))
        sources = ''.join(f'<li><a href="{escape(s["url"], quote=True)}" target="_blank" rel="noopener">{escape(s["label"])}</a></li>' for s in data.get('sources', []) if s.get('url', '').startswith('https://'))
        package = data.get('package') or {}
        fields = ''.join(f'<label>{label}<input type="number" step="0.1" min="0.1" max="{maximum}" name="{key}" value="{escape(str(package.get(key, "")))}" required></label>' for key, label, maximum in [('weight_g', 'Packed weight (grams)', 22000), ('length_cm', 'Length (cm)', 105), ('width_cm', 'Width (cm)', 105), ('height_cm', 'Height (cm)', 105)])
        body += f'''<article class="graded-card" id="{listing.id}"><h2>{escape(item.name)}</h2>
        <p><strong>{escape(stock.grading_company or '')} {escape(stock.grade or '')}</strong> · Certificate {escape(stock.certification_number or '')} · Qty {listing.quantity}</p>
        <p>{escape(card_set.name if card_set else 'Set pending')} · {escape(card.language if card else '')} · {escape(card.number or '' if card else '')}</p>
        <div class="slab-photos">{photos}</div><p>{'Confirmed asking price' if confirmed else 'Suggested asking price'}: <strong>{'AU $' + escape(price) if price else 'Research pending'}</strong>{'' if confirmed else ' · Needs your review'}</p>
        <p>{escape(data.get('research_note', 'Pricing research pending.'))}</p><ul>{sources}</ul>
        <p><a href="{escape(australian_sold_listings_url(query), quote=True)}" target="_blank" rel="noopener">Sold listings · Australia</a></p>
        <form method="post" action="/graded-cards/{listing.id}/price"><label>Your asking price (AUD)<input type="number" min="1" max="999999.99" step="0.01" name="price" value="{escape(price)}" required></label><button class="btn">Confirm price</button></form>
        <details><summary>Postage: {'measurements saved' if package.get('measured_at') else 'packed measurements needed'}</summary><p>Buyer-paid calculated tracked parcel. Measure the slab inside its protective packaging. Decimal centimetres round up for eBay.</p><form method="post" action="/graded-cards/{listing.id}/parcel">{fields}<button class="btn">Save packed measurements</button></form></details>
        <p><strong>Listing title:</strong> {escape(title)}</p><a class="btn" href="/shop-listings/{listing.id}/ebay-preview">Open Listing Studio</a></article>'''
    if not rows:
        body += '<p>No graded drafts yet. Review the slab photos from your upload first.</p>'
    body += '<style>.graded-card{background:var(--surface,#fff);border:1px solid #ddd;border-radius:16px;padding:20px;margin:24px 0;overflow-wrap:anywhere}.slab-photos{display:flex;gap:12px}.slab-photos a{width:50%;min-width:0}.slab-photos img{width:100%;max-height:360px;object-fit:contain}form{display:grid;gap:12px;max-width:500px;margin:16px 0}label{display:grid;gap:6px}input{min-width:0;max-width:100%;padding:10px}summary{padding:14px 0;cursor:pointer}h2{font-size:1.2rem}</style>'
    return HTMLResponse(page('EzBay — Graded cards', body))


@router.post('/graded-cards/{listing_id}/price')
def confirm_price(listing_id: str, price: str = Form(...), user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    listing = _get(session, listing_id)
    _draft_only(session, listing)
    try:
        amount = positive(price, '999999.99')
        if amount < 1 or amount != amount.quantize(Decimal('.01')):
            raise ValueError('Enter an AUD price of at least $1.00 with no more than two decimal places.')
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    stock = session.get(InventoryItem, listing.inventory_item_id)
    if not stock:
        raise HTTPException(409, 'Link the slab to stock first')
    data = review_data(session, listing)
    listing.price = amount
    data.update(confirmed_price=f'{amount:.2f}', price_confirmed_at=datetime.now(UTC).isoformat(),
                price_confirmed_by=user, reviewed_identity=identity(stock))
    save_review_data(session, listing, data)
    record_event(session, actor_type='user', actor_id=user, action='graded.price_confirm', resource_type='shop_listing', resource_id=listing.id,
                 outcome='success', correlation_id=str(uuid.uuid4()), details={'price': f'{amount:.2f}', 'currency': 'AUD'})
    return RedirectResponse('/graded-cards#' + listing.id, 303)


@router.post('/graded-cards/{listing_id}/parcel')
def save_parcel(listing_id: str, weight_g: str = Form(...), length_cm: str = Form(...), width_cm: str = Form(...), height_cm: str = Form(...),
                user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    listing = _get(session, listing_id)
    _draft_only(session, listing)
    try:
        package = {k: str(positive(v, 22000 if k == 'weight_g' else 105)) for k, v in
                   {'weight_g': weight_g, 'length_cm': length_cm, 'width_cm': width_cm, 'height_cm': height_cm}.items()}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    data = review_data(session, listing)
    data['package'] = {**package, 'measured_at': datetime.now(UTC).isoformat(), 'measured_by': user}
    save_review_data(session, listing, data)
    record_event(session, actor_type='user', actor_id=user, action='graded.parcel_confirm', resource_type='shop_listing', resource_id=listing.id,
                 outcome='success', correlation_id=str(uuid.uuid4()), details=package)
    return RedirectResponse('/graded-cards#' + listing.id, 303)


def prepare_graded_draft(session, pair, facts, research):
    """Stage visually reviewed intake in one transaction; caller snapshots/audits/commits.

    Idempotent by grader/certificate. Never replaces an existing live listing,
    quantity or confirmed price, and never combines a slab with raw stock.
    """
    from .representative_backs import is_representative
    if not pair.front_image_path or not pair.back_image_path or pair.front_image_path == pair.back_image_path or any(is_representative(p) for p in (pair.front_image_path, pair.back_image_path)):
        raise ValueError('Both actual slab photos are required')
    matches = list(session.scalars(select(InventoryItem).where(
        InventoryItem.grading_company.ilike(facts['company']), InventoryItem.certification_number == facts['certificate'])))
    if matches:
        if len(matches) != 1 or matches[0].grade != facts['grade']:
            raise ValueError('Conflicting grading certificate')
        listings = list(session.scalars(select(ShopListing).where(ShopListing.inventory_item_id == matches[0].id)))
        if len(listings) != 1 or pair.matched_shop_listing_id != listings[0].id:
            raise ValueError('Certificate already exists; review the retake against its existing listing')
        return listings[0]
    if pair.matched_shop_listing_id or pair.status in (ListingPhotoPairStatus.APPLIED, ListingPhotoPairStatus.PUBLISHED):
        raise ValueError('This photo pair has already been applied')
    cid = 'photo-product-' + hashlib.sha256('|'.join(['graded', facts['company'].casefold(), facts['certificate'].casefold()]).encode()).hexdigest()[:32]
    if session.get(CatalogItem, cid):
        raise ValueError('Existing graded catalogue record needs manual reconciliation')
    set_id = facts['set_id']
    card_set = session.get(CardSet, set_id)
    if not card_set:
        session.add(CardSet(id=set_id, name=facts['set_name'], code=facts['set_code'], game=facts['game']))
    elif card_set.game != facts['game'] or card_set.name != facts['set_name']:
        raise ValueError('Card set conflicts with the reviewed metadata')
    session.add(CatalogItem(id=cid, name=facts['name'], item_type=CatalogItemType.GRADED_CARD,
        pricing_policy='manual_only', pricing_policy_reason='Separate slab identity; owner must confirm researched price'))
    session.flush()
    session.add(TcgCard(catalog_item_id=cid, game=facts['game'], set_id=set_id, set_code=facts['set_code'],
        character=facts['character'], number=facts['number'], language=facts['language'], rarity=facts.get('rarity'),
        parallel=facts.get('parallel'), release_date=facts['year']))
    stock = InventoryItem(catalog_item_id=cid, condition='Graded', quantity=1, status=InventoryStatus.AVAILABLE,
        scan_image_path=pair.front_image_path, grading_company=facts['company'], grade=facts['grade'], certification_number=facts['certificate'])
    graded_condition(stock)
    session.add(stock); session.flush()
    listing = ShopListing(catalog_item_id=cid, inventory_item_id=stock.id, title=facts['title'], ebay_title=facts['title'],
        condition='Graded', quantity=1, price=Decimal('0'), currency='AUD', status=ShopListingStatus.DRAFT,
        ebay_photo_paths=[pair.front_image_path, pair.back_image_path])
    session.add(listing); session.flush()
    save_review_data(session, listing, {**research, 'prepared_at': datetime.now(UTC).isoformat(), 'pair_id': pair.id})
    pair.extracted = {**(pair.extracted or {}), 'product_review': {'kind': 'graded', **facts, 'reviewed_by': 'Codex', 'verification': 'Photos of slab labels; not online certificate authentication'}}
    pair.proposed_catalog_item_id=cid; pair.matched_shop_listing_id=listing.id; pair.status=ListingPhotoPairStatus.APPLIED; pair.issue=None
    from .ebay_photo_batch import _refresh_batch_counts
    batch=session.get(ListingPhotoBatch,pair.batch_id)
    if batch: _refresh_batch_counts(session,batch)
    return listing
