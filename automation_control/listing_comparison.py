"""Read-only comparison against explicitly linked eBay observations."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from html import escape
from sqlalchemy import select
from .models import EbayListing, EbayListingLink, EbayExportItem, EbayExportItemStatus


def comparison_html(session, listing, title=None):
    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing.id))
    item_id = link.ebay_listing_id if link else None
    if not item_id:
        export = session.scalar(select(EbayExportItem).where(
            EbayExportItem.sku == listing.id, EbayExportItem.kind == 'single',
            EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
            EbayExportItem.ebay_listing_id.is_not(None))
            .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc()).limit(1))
        item_id = export.ebay_listing_id if export else None
    prefix = "<section class='panel'><h2>Saved details vs eBay</h2>"
    if not item_id:
        return prefix + '<p>No verified eBay link recorded. This does not prove the item is absent from eBay.</p></section>'
    remote = session.scalar(select(EbayListing).where(EbayListing.ebay_listing_id == item_id))
    url = f"https://www.ebay.com.au/itm/{item_id}" if str(item_id).isdigit() else None
    link_html = f"<p><a href='{url}' target='_blank' rel='noopener'>Open linked eBay listing</a></p>" if url else ''
    if remote is None:
        return prefix + '<p>Linked listing has no cached eBay observation. Live details unverified.</p>' + link_html + '</section>'
    observed = remote.observed_at
    if observed and observed.tzinfo is None: observed = observed.replace(tzinfo=timezone.utc)
    stale = not observed or datetime.now(timezone.utc) - observed > timedelta(hours=24)
    time_label = observed.strftime('%Y-%m-%d %H:%M UTC') if observed else 'unknown'
    body = prefix + f"<p>eBay status at last check: <strong>{escape(remote.listing_status)}</strong>. Last checked: {time_label}. {'Older than 24 hours — current details need checking.' if stale else 'Cached observation, not a live check.'}</p>"
    pairs = [('Price', f'{listing.currency} {listing.price:.2f}', f'{remote.currency} {Decimal(remote.current_price_minor)/100:.2f}', listing.currency == remote.currency and listing.price == Decimal(remote.current_price_minor)/100),
             ('Quantity offered', str(listing.quantity), str(remote.quantity) if remote.quantity is not None else 'Unknown', None if remote.quantity is None else listing.quantity == remote.quantity)]
    if title is not None:
        pairs.append(('Title', title, remote.title, title == remote.title))
    body += "<div style='overflow-x:auto'><table><thead><tr><th>Field</th><th>Saved</th><th>Last seen on eBay</th><th>Comparison</th></tr></thead><tbody>"
    for label, local, actual, equal in pairs:
        verdict = 'Unverified' if equal is None else 'Matches cached value' if equal else 'Different'
        body += f'<tr><th>{label}</th><td>{escape(local)}</td><td>{escape(actual)}</td><td>{verdict}</td></tr>'
    body += '</tbody></table></div><p>Photos and description are not verified by this comparison. Saving a draft does not update eBay.</p>'
    return body + link_html + '</section>'
