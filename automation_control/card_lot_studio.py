"""Explicitly reviewed card lots; never infer lot contents from a title."""
from html import escape
from . import sealed_studio as sealed
from .models import CatalogItem, CatalogItemType, PlatformConfig
from .listing_quality import QualityCheck, ListingQuality

CATEGORY_ID = '183455'
PREFIX = 'card_lot_studio:'


def details(session, listing):
    row = session.get(PlatformConfig, PREFIX + listing.id)
    return dict(row.value or {}) if row else {}


def is_lot(session, listing):
    item = session.get(CatalogItem, listing.catalog_item_id)
    data = details(session, listing)
    return bool(item and item.item_type == CatalogItemType.OTHER
                and data.get('catalog_item_id') == listing.catalog_item_id
                and data.get('reviewed') is True)


def save_details(session, listing, values):
    # Preserve reviewed contents while the operator edits shipping or language.
    data = {**details(session, listing), **values}
    row = session.get(PlatformConfig, PREFIX + listing.id)
    if row:
        row.value = data
    else:
        session.add(PlatformConfig(key=PREFIX + listing.id, value=data))


copy_for = sealed.copy_for
reviewed_data = sealed.reviewed_data


def preflight(session, listing, settings):
    return [e.replace('sealed', 'card-lot') for e in sealed.preflight(session, listing, settings)]


def quality(session, listing, photos):
    data = details(session, listing)
    cards = data.get('cards', [])
    identity = (is_lot(session, listing) and data.get('game') in sealed.GAMES
                and data.get('language') in ('English', 'Japanese', 'Chinese')
                and len(cards) >= 2 and all(c.get('name') and c.get('number') for c in cards))
    title, description = copy_for(listing)
    try:
        sealed.parcel_payload(data)
        parcel_ok = True
    except ValueError:
        parcel_ok = False
    checks = (
        QualityCheck('Title', bool(title) and len(title) <= 80, True, 'Describe the complete card lot in up to 80 characters.'),
        QualityCheck('Description', bool(description), True, 'Describe the included cards and holder.'),
        QualityCheck('Lot identity', bool(identity), True, 'Review the exact cards, game and language in this lot.'),
        QualityCheck('Complete lot and condition', data.get('complete') is True and listing.condition == 'Near Mint', True, 'Confirm all listed cards are included and Near Mint.'),
        QualityCheck('Actual-product photos', any(p.is_own_photo for p in photos), True, 'Show the actual lot offered.'),
        QualityCheck('Price and quantity', listing.price is not None and listing.price.is_finite() and listing.price >= 1 and listing.quantity > 0, True, 'Confirm the price and number of complete lots.'),
        QualityCheck('Parcel details', parcel_ok, True, 'Enter packed weight and dimensions.'),
    )
    return ListingQuality(round(100 * sum(c.passed for c in checks) / len(checks)), checks)


def fields_html(session, listing):
    data = details(session, listing)
    out = "<fieldset class='studio-field wide'><legend>Card lot details</legend><p>Quantity counts complete lots, not individual cards.</p><ul>"
    for card in data.get('cards', []):
        out += f"<li>{escape(card['name'])} — {escape(card['number'])}</li>"
    out += '</ul>'
    if data.get('holder_included'):
        out += '<p>Display holder included; cards are removable.</p>'
    out += "<label class='studio-field'>Language<select name='sealed_language'>"
    for language in ('English', 'Japanese', 'Chinese'):
        selected = ' selected' if data.get('language') == language else ''
        out += f'<option{selected}>{language}</option>'
    checked = ' checked' if data.get('complete') else ''
    out += f"</select></label><label><input type='checkbox' name='sealed_complete' value='yes'{checked}> All listed cards and holder are included</label>"
    out += '<h3>Tracked parcel postage</h3><p>Buyer-paid Australia Post parcel postage calculated from the packed weight, dimensions and destination.</p>'
    for key, label in [('weight_g','Packed weight (g)'),('length_cm','Length (cm)'),('width_cm','Width (cm)'),('height_cm','Height (cm)')]:
        out += f"<label class='studio-field'>{label}<input type='number' name='sealed_{key}' min='0.01' step='0.01' value='{escape(str(data.get(key, '')), quote=True)}'></label>"
    return out + '</fieldset>'


def build_inventory_payload(session, listing, images):
    data = details(session, listing)
    title, description = copy_for(listing)
    return {'condition': 'USED_EXCELLENT', 'conditionDescription': 'Near Mint cards. Actual lot and holder pictured.',
            'packageWeightAndSize': sealed.parcel_payload(data),
            'product': {'title': title, 'description': description, 'imageUrls': images,
                        'aspects': {'Game': [sealed.GAMES[data['game']]],
                                    'Language': [data['language']], 'Number of Cards': [str(len(data['cards']))]}},
            'availability': {'shipToLocationAvailability': {'quantity': listing.quantity}}}
