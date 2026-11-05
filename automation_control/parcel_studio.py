"""Route supported parcel products to their own review and publishing rules."""
from . import sealed_studio, card_lot_studio


def is_supported(session, listing):
    return sealed_studio.is_sealed(session, listing) or card_lot_studio.is_lot(session, listing)


def handler(session, listing):
    return card_lot_studio if card_lot_studio.is_lot(session, listing) else sealed_studio


def category_id(session, listing):
    return handler(session, listing).CATEGORY_ID


def condition_error(session, listing, condition):
    if card_lot_studio.is_lot(session, listing):
        return None if condition == 'Near Mint' else 'This reviewed card lot requires Near Mint condition; review other conditions separately.'
    return None if condition == 'New' else 'Sealed decks must use New condition; opened decks require separate review.'


copy_for = sealed_studio.copy_for
reviewed_data = sealed_studio.reviewed_data


def details(session, listing):
    return handler(session, listing).details(session, listing)


def save_details(session, listing, values):
    return handler(session, listing).save_details(session, listing, values)


def preflight(session, listing, settings):
    return handler(session, listing).preflight(session, listing, settings)


def quality(session, listing, photos):
    return handler(session, listing).quality(session, listing, photos)


def fields_html(session, listing):
    return handler(session, listing).fields_html(session, listing)


def build_inventory_payload(session, listing, images):
    return handler(session, listing).build_inventory_payload(session, listing, images)
