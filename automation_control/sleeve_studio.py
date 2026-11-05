"""Reviewed sealed sleeve packs, sold as accessories rather than cards."""
from html import escape
from . import sealed_studio
from .models import CatalogItem, CatalogItemType, AccessoryProduct, PlatformConfig, InventoryItem, InventoryStatus
from .listing_quality import QualityCheck, ListingQuality

CATEGORY_ID = '183461'
PREFIX = 'sleeve_studio:'


def details(session, listing):
    row = session.get(PlatformConfig, PREFIX + listing.id)
    return dict(row.value or {}) if row else {}


def is_sleeve(session, listing):
    item = session.get(CatalogItem, listing.catalog_item_id)
    data = details(session, listing)
    return bool(item and item.item_type == CatalogItemType.ACCESSORY
                and data.get('catalog_item_id') == listing.catalog_item_id and data.get('reviewed') is True)


def save_details(session, listing, values):
    row = session.get(PlatformConfig, PREFIX + listing.id)
    data = {**details(session, listing), **values}
    if row:
        row.value = data
    else:
        session.add(PlatformConfig(key=PREFIX + listing.id, value=data))


def preflight(session, listing, settings):
    errors = []
    if not settings.ebay_fulfillment_policy_id:
        errors.append('Configure the sleeve postage policy.')
    stock = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    if not stock or stock.catalog_item_id != listing.catalog_item_id or listing.quantity > stock.quantity:
        errors.append('The sleeve listing must have enough matching inventory.')
    if stock and stock.status not in (InventoryStatus.AVAILABLE, InventoryStatus.LISTED):
        errors.append('The sleeve inventory is not available for sale.')
    return errors


def quality(session, listing, photos):
    data = details(session, listing)
    product = session.get(AccessoryProduct, listing.catalog_item_id)
    title, description = sealed_studio.copy_for(listing)
    identity = bool(is_sleeve(session, listing) and product and product.brand
        and product.product_type == 'Card sleeves' and product.pack_size == '65 sleeves'
        and data.get('set_name') and data.get('character') and data.get('game') == 'pokemon')
    checks = (
        QualityCheck('Title', bool(title) and len(title) <= 80, True, 'Describe the sleeve pack in up to 80 characters.'),
        QualityCheck('Description', bool(description), True, 'Describe the actual sleeves and pack count.'),
        QualityCheck('Sleeve identity', identity, True, 'Review the set, character and sealed 65-sleeve pack.'),
        QualityCheck('Sealed pack', listing.condition == 'New' and data.get('complete') is True, True, 'Confirm an unopened, complete pack.'),
        QualityCheck('Actual-product photos', any(p.is_own_photo for p in photos), True, 'Show the actual sleeve pack.'),
        QualityCheck('Price and quantity', listing.price is not None and listing.price.is_finite() and listing.price >= 1 and listing.quantity > 0, True, 'Confirm the price and number of packs.'),
    )
    return ListingQuality(round(100 * sum(c.passed for c in checks) / len(checks)), checks)


def fields_html(session, listing):
    data = details(session, listing)
    out = "<fieldset class='studio-field wide'><legend>Sleeve pack details</legend>"
    for label, value in [('Set', data.get('set_name')), ('Character', data.get('character')), ('Pack size', '65 sleeves')]:
        out += f'<p><strong>{label}:</strong> {escape(value or "Missing")}</p>'
    checked = ' checked' if data.get('complete') else ''
    out += "<input type='hidden' name='sealed_language' value='English'>"
    out += f"<label><input type='checkbox' name='sealed_complete' value='yes'{checked}> Sealed and complete pack of 65 sleeves</label>"
    return out + '<p>Quantity counts packs. Uses your standard letter postage policy.</p></fieldset>'


def build_inventory_payload(session, listing, images):
    data = details(session, listing)
    product = session.get(AccessoryProduct, listing.catalog_item_id)
    title, description = sealed_studio.copy_for(listing)
    return {'condition': 'NEW', 'product': {'title': title, 'description': description, 'imageUrls': images,
        'aspects': {'Brand': [product.brand], 'Type': ['Card Sleeves'], 'Game': ['Pokémon TCG'],
                    'Set': [data['set_name']], 'Character': [data['character']],
                    'Number of Sleeves': ['65'], 'Compatible Card Size': ['Standard']}},
        'availability': {'shipToLocationAvailability': {'quantity': listing.quantity}}}
