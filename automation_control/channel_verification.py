"""Fresh readback and operator photo comparison. Never writes to eBay."""
from datetime import datetime, timezone
from html import escape as e
from pathlib import Path
import hashlib, json
from sqlalchemy import select
from .models import EbayListing, EbayListingLink, EbayExportItem, EbayExportItemStatus, CatalogItem


def linked_id(session, listing):
    link=session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id==listing.id))
    if link and link.ebay_listing_id:return link.ebay_listing_id
    return session.scalar(select(EbayExportItem.ebay_listing_id).where(EbayExportItem.sku==listing.id,
        EbayExportItem.kind=='single',EbayExportItem.status==EbayExportItemStatus.CONFIRMED,
        EbayExportItem.ebay_listing_id.is_not(None)).order_by(EbayExportItem.exported_at.desc(),EbayExportItem.id.desc()).limit(1))


async def refresh_one(session, listing, settings):
    from .ebay_oauth import ebay_services, valid_user_access_token
    from .adapters.ebay_trading import EbayTradingReadAdapter
    item_id=linked_id(session,listing)
    if not item_id or not str(item_id).isdigit():raise ValueError('No verified eBay item link. Link the existing item before checking.')
    cipher, oauth=ebay_services(settings)
    token=await valid_user_access_token(session,cipher,oauth)
    adapter=EbayTradingReadAdapter(token,settings.ebay_client_id,environment=settings.ebay_env,marketplace_id=settings.ebay_marketplace_id)
    try:details=await adapter.get_item_details(item_id)
    finally:await adapter.client.aclose()
    if details.title is None or details.price is None or details.currency is None or details.status is None:
        raise ValueError('Incomplete eBay response; existing observation was preserved.')
    row=session.scalar(select(EbayListing).where(EbayListing.ebay_listing_id==item_id))
    if row is None:
        row=EbayListing(ebay_listing_id=item_id);session.add(row)
    row.title=details.title;row.current_price_minor=int(details.price*100);row.currency=details.currency
    row.quantity=details.quantity;row.listing_status=details.status;row.observed_at=datetime.now(timezone.utc)
    row.raw_snapshot={**(row.raw_snapshot or {}),'source':'trading_api','image_urls':details.picture_urls,
        'description':details.description or '', 'details_source':'ebay_get_item','details_imported_at':row.observed_at.isoformat()}
    session.commit()
    return row


def photo_evidence(session,listing,row):
    from .listing_quality import listing_photo_sources
    item=session.get(CatalogItem,listing.catalog_item_id)
    sources=listing_photo_sources(session,listing,item) if item else []
    remote=(row.raw_snapshot or {}).get('image_urls') or []
    signatures=[]
    for source in sources:
        try: digest=hashlib.sha256(Path(source.value).read_bytes()).hexdigest() if not source.is_remote else source.value
        except OSError:digest='missing:'+source.value
        signatures.append(digest)
    payload={'local':signatures,'remote':remote,'item_id':row.ebay_listing_id}
    signature=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    return sources,remote,signature


def photo_panel(session,listing,row):
    from .models import AuditEvent
    sources,remote,signature=photo_evidence(session,listing,row)
    approval=session.scalar(select(AuditEvent).where(AuditEvent.action=='listing.photos_reviewed',AuditEvent.resource_id==listing.id).order_by(AuditEvent.occurred_at.desc()).limit(1))
    reviewed=bool(approval and approval.details.get('signature')==signature)
    timestamp=(row.raw_snapshot or {}).get('details_imported_at') or 'not recorded'
    body=f"<details><summary>Compare saved photos with eBay / storefront ({len(sources)} / {len(remote)})</summary><p>Photo readback: {e(timestamp)}. {'Visually confirmed by you for these photos.' if reviewed else 'Photo match needs visual confirmation.'} The storefront uses these cached eBay photos.</p><div style='display:grid;grid-template-columns:1fr 1fr;gap:12px'><div><h3>Saved photos</h3>"
    for i,source in enumerate(sources):
        url=f'/shop-listings/{listing.id}/ebay-photo/{i}'
        body+=f"<a href='{url}' target='_blank' rel='noopener'><img style='width:100%;max-height:240px;object-fit:contain' loading='lazy' src='{url}' alt='Saved photo {i+1}'></a>"
    body+='</div><div><h3>eBay / storefront photos</h3>'
    for i,url in enumerate(remote):
        if not url.startswith('https://'):continue
        body+=f"<a href='{e(url,quote=True)}' target='_blank' rel='noopener'><img style='width:100%;max-height:240px;object-fit:contain' loading='lazy' src='{e(url,quote=True)}' alt='eBay photo {i+1}'></a>"
    body+='</div></div>'
    if sources and remote:
        body+=f"<form method='post' action='/shop-listings/{listing.id}/verify-photos'><input type='hidden' name='signature' value='{signature}'><label><input type='checkbox' name='confirmed' value='yes' required>I checked every photo and their order.</label><button>Confirm photos match</button></form>"
    return body+'</details>'
