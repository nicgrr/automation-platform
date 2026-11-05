"""Route supported parcel products to their own review and publishing rules."""
from . import sealed_studio, card_lot_studio, sleeve_studio


def is_supported(session, listing):
    return (sealed_studio.is_sealed(session, listing) or card_lot_studio.is_lot(session, listing)
            or sleeve_studio.is_sleeve(session, listing))


def handler(session, listing):
    if sleeve_studio.is_sleeve(session, listing):
        return sleeve_studio
    return card_lot_studio if card_lot_studio.is_lot(session, listing) else sealed_studio


def category_id(session, listing):
    return handler(session, listing).CATEGORY_ID


def condition_error(session, listing, condition):
    if sleeve_studio.is_sleeve(session, listing):
        return None if condition == "New" else "Sealed sleeve packs require New condition."
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


def fulfillment_policy_id(session, listing, settings):
    if sleeve_studio.is_sleeve(session, listing):
        return settings.ebay_fulfillment_policy_id
    return settings.ebay_sealed_fulfillment_policy_id
