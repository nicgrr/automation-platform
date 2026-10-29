"""Read-only storefront projection of exact, live eBay listing links.

Never writes eBay prices into seller drafts or changes physical inventory.
"""
from decimal import Decimal
from types import SimpleNamespace
from sqlalchemy import and_, func, select
from automation_control.config import get_settings
from automation_control.models import (
    EbayListing, EbayListingLink, EbayListingLinkStatus,
    EbayExportItem, EbayExportItemStatus, ShopListing, ShopListingStatus,
)


def enabled():
    return get_settings().storefront_mode == 'catalogue'


def remote_id():
    direct = (select(EbayListingLink.ebay_listing_id)
        .join(EbayListing, EbayListing.ebay_listing_id == EbayListingLink.ebay_listing_id)
        .where(EbayListingLink.shop_listing_id == ShopListing.id,
               EbayListingLink.status == EbayListingLinkStatus.ACTIVE,
               EbayListing.listing_status == 'Active')
        .correlate(ShopListing).limit(1).scalar_subquery())
    exported = (select(EbayExportItem.ebay_listing_id)
        .join(EbayListing, EbayListing.ebay_listing_id == EbayExportItem.ebay_listing_id)
        .where(EbayExportItem.sku == ShopListing.id, EbayExportItem.kind == 'single',
               EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
               EbayListing.listing_status == 'Active')
        .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc())
        .correlate(ShopListing).limit(1).scalar_subquery())
    return func.coalesce(direct, exported)


def remote_value(column):
    return (select(column).where(EbayListing.ebay_listing_id == remote_id())
            .correlate(ShopListing).scalar_subquery())


def available():
    if not enabled():
        return and_(ShopListing.status == ShopListingStatus.ACTIVE, ShopListing.quantity > 0)
    return and_(remote_id().is_not(None), remote_value(EbayListing.quantity) > 0)


def price():
    return remote_value(EbayListing.current_price_minor) / 100 if enabled() else ShopListing.price


def title():
    return remote_value(EbayListing.title) if enabled() else ShopListing.title


def remote_for(session, listing_id):
    eid = session.scalar(select(remote_id()).select_from(ShopListing).where(ShopListing.id == listing_id))
    return session.scalar(select(EbayListing).where(EbayListing.ebay_listing_id == eid)) if eid else None


def project(session, listing):
    if not enabled():
        return listing if listing and listing.status == ShopListingStatus.ACTIVE else None
    remote = remote_for(session, listing.id) if listing else None
    if not remote or remote.listing_status != 'Active' or not remote.quantity or remote.quantity <= 0:
        return None
    fields = {c.name: getattr(listing, c.name) for c in ShopListing.__table__.columns}
    fields.update(price=Decimal(remote.current_price_minor) / 100, currency=remote.currency,
                  quantity=remote.quantity, title=remote.title, status=ShopListingStatus.ACTIVE)
    snapshot = remote.raw_snapshot or {}
    fields['description'] = snapshot.get('description') or ''
    photos = published_photos(session, listing.id)
    fields['image_url'] = photos[0] if photos else None
    return SimpleNamespace(**fields)


def unlinked_rows(session, query=''):
    """Live offers without a canonical single, including bundles and graded cards."""
    if not enabled():
        return []
    linked = set(session.scalars(select(remote_id()).select_from(ShopListing).where(available())))
    rows = list(session.scalars(select(EbayListing).where(
        EbayListing.listing_status == 'Active', EbayListing.quantity > 0)))
    return [r for r in rows if r.ebay_listing_id not in linked and query.casefold() in r.title.casefold()]


def unlinked_tile(remote):
    from html import escape
    snapshot = remote.raw_snapshot or {}
    image = next((u for u in snapshot.get('image_urls', []) if isinstance(u, str) and u.startswith('https://')), None)
    art = f"<img src='{escape(image, quote=True)}' alt='' loading='lazy'>" if image else '<span>Photo on eBay</span>'
    url = 'https://www.ebay.com.au/itm/' + remote.ebay_listing_id
    return (f"<a class='card' href='{escape(url)}' target='_blank' rel='noopener noreferrer'>"
            f"<div class='card-art'>{art}</div><div class='card-body'>"
            f"<div class='card-title'>{escape(remote.title)}</div>"
            f"<div class='card-sub'>Available on eBay · {remote.quantity} available</div>"
            f"<div class='card-price'>${Decimal(remote.current_price_minor)/100:,.2f} {escape(remote.currency)}</div></div></a>")


def published_photos(session, listing_id):
    """Only photos read back from the linked live eBay offer."""
    remote = remote_for(session, listing_id)
    if not remote:
        return []
    return [u for u in (remote.raw_snapshot or {}).get('image_urls', [])
            if isinstance(u, str) and u.startswith('https://')]
