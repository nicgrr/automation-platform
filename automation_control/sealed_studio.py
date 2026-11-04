"""Sealed starter-deck review and Inventory API payloads.

Uses the existing Studio save/confirm workflow. Parcel data belongs to each
listing; raw-card condition descriptors and letter postage never apply.
"""
from decimal import Decimal, InvalidOperation
from html import escape

from .models import CatalogItem, CatalogItemType, SealedProduct, PlatformConfig, InventoryItem, InventoryStatus
from .listing_quality import QualityCheck, ListingQuality
from scripts.convert_to_ebay_template import sanitize_title

CATEGORY_ID = '183457'  # CCG Sealed Decks & Kits
PREFIX = 'sealed_studio:'
GAMES = {'Dragon Ball Super Fusion World': 'Dragon Ball Super Card Game',
         'One Piece': 'One Piece CCG', 'Pokemon': 'Pokémon TCG',
         'pokemon': 'Pokémon TCG', 'one_piece': 'One Piece CCG'}


def is_sealed(session, listing):
    item = session.get(CatalogItem, listing.catalog_item_id)
    return bool(item and item.item_type == CatalogItemType.SEALED_PRODUCT)


def details(session, listing):
    row = session.get(PlatformConfig, PREFIX + listing.id)
    return dict(row.value or {}) if row else {}


def positive(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Enter a valid packed weight and dimensions.')
    if not result.is_finite() or result <= 0:
        raise ValueError('Packed weight and dimensions must be greater than zero.')
    return result


def parcel_payload(data):
    if not all(data.get(k) for k in ('weight_g', 'length_cm', 'width_cm', 'height_cm')):
        raise ValueError('Add the packed weight and dimensions in Sealed product details.')
    return {'weight': {'value': float(positive(data['weight_g']) / 1000), 'unit': 'KILOGRAM'},
            'dimensions': {**{k: float(positive(data[k + '_cm'])) for k in ('length', 'width', 'height')}, 'unit': 'CENTIMETER'},
            'packageType': 'PACKAGE_THICK_ENVELOPE'}


def reviewed_data(language, complete, weight_g, length_cm, width_cm, height_cm):
    if language not in ('English', 'Japanese', 'Chinese'):
        raise ValueError('Choose the product language.')
    values = dict(language=language, complete=complete, weight_g=weight_g.strip(),
                  length_cm=length_cm.strip(), width_cm=width_cm.strip(), height_cm=height_cm.strip())
    # Partial drafts are allowed; malformed measurements are not.
    for key in ('weight_g', 'length_cm', 'width_cm', 'height_cm'):
        if values[key]: positive(values[key])
    return values


def save_details(session, listing, values):
    row = session.get(PlatformConfig, PREFIX + listing.id)
    if row: row.value = values
    else: session.add(PlatformConfig(key=PREFIX + listing.id, value=values))


def copy_for(listing):
    return sanitize_title(listing.ebay_title or listing.title).strip(), (listing.ebay_description or listing.description or '').strip()


def quality(session, listing, photos):
    product = session.get(SealedProduct, listing.catalog_item_id)
    data = details(session, listing)
    title, description = copy_for(listing)
    identity = bool(product and product.brand and product.game in GAMES and product.set_id
                    and product.product_type in ('Starter Deck', 'Starter Deck EX') and data.get('language'))
    try:
        parcel_payload(data); parcel_ok = True
    except ValueError: parcel_ok = False
    checks = (
        QualityCheck('Title', bool(title) and len(title) <= 80, True, 'A clear product title of up to 80 characters.'),
        QualityCheck('Description', bool(description), True, 'Describe the sealed deck and included contents.'),
        QualityCheck('Product identity', identity, True, 'Brand, game, product code, starter-deck type and language are required.'),
        QualityCheck('Sealed and complete', listing.condition == 'New' and data.get('complete') is True, True, 'Confirm the deck is unopened with all original contents.'),
        QualityCheck('Actual-product photos', any(p.is_own_photo for p in photos), True, 'Photographs of the actual sealed product are required.'),
        QualityCheck('Price and quantity', listing.price is not None and listing.price.is_finite() and listing.price >= 1 and listing.quantity > 0, True, 'Confirm the AUD asking price and available quantity.'),
        QualityCheck('Parcel details', parcel_ok, True, 'Enter packed weight in grams and dimensions in centimetres.'),
    )
    return ListingQuality(round(100 * sum(c.passed for c in checks) / len(checks)), checks)


def preflight(session, listing, settings):
    errors = []
    if not settings.ebay_sealed_fulfillment_policy_id:
        errors.append('Configure a tracked parcel postage policy for sealed products.')
    stock = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    if not stock or stock.catalog_item_id != listing.catalog_item_id or listing.quantity > stock.quantity:
        errors.append('The sealed listing must be backed by enough matching inventory.')
    if stock and stock.status not in (InventoryStatus.AVAILABLE, InventoryStatus.LISTED):
        errors.append("The sealed inventory is not available for sale.")
    return errors


def fields_html(session, listing):
    product = session.get(SealedProduct, listing.catalog_item_id)
    data = details(session, listing)
    out = "<fieldset class='studio-field wide'><legend>Sealed product details</legend>"
    for label, value in [('Brand', getattr(product, 'brand', '')), ('Game', getattr(product, 'game', '')),
                         ('Product code / set', getattr(product, 'set_id', '')), ('Product type', getattr(product, 'product_type', ''))]:
        out += f"<p><strong>{escape(label)}:</strong> {escape(value or 'Missing')}</p>"
    out += "<label class='studio-field'>Language<select name='sealed_language'><option value=''>Choose language</option>"
    for language in ('English', 'Japanese', 'Chinese'):
        selected = ' selected' if data.get('language') == language else ''
        out += f"<option{selected}>{language}</option>"
    out += "</select></label>"
    checked = ' checked' if data.get('complete') else ''
    out += f"<label><input type='checkbox' name='sealed_complete' value='yes'{checked}> Unopened and complete with all original contents, including any bonus pack</label>"
    out += "<h3>Tracked parcel postage</h3><p>Buyer-paid Australia Post parcel postage, calculated by eBay from the packed weight, size and destination. Include your shipping box and padding.</p>"
    for key, label in [('weight_g','Packed weight (g)'),('length_cm','Length (cm)'),('width_cm','Width (cm)'),('height_cm','Height (cm)')]:
        out += f"<label class='studio-field'>{label}<input type='number' name='sealed_{key}' min='0.01' step='0.01' value='{escape(str(data.get(key, '')), quote=True)}'></label>"
    return out + '</fieldset>'


def build_inventory_payload(session, listing, images):
    product = session.get(SealedProduct, listing.catalog_item_id)
    data = details(session, listing)
    title, description = copy_for(listing)
    return {'condition': 'NEW', 'packageWeightAndSize': parcel_payload(data),
            'product': {'title': title, 'description': description, 'imageUrls': images,
                        'aspects': {'Game': [GAMES[product.game]], 'Manufacturer': [product.brand],
                                    'Set': [product.set_id], 'Configuration': ['Deck'],
                                    'Number of Decks or Kits': ['1'], 'Language': [data['language']]}},
            'availability': {'shipToLocationAvailability': {'quantity': listing.quantity}}}
