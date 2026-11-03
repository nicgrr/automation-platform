"""Operator channel policy shared by publication and export entry points."""

from sqlalchemy.orm import Session

from .models import CatalogItem, CatalogItemType, InventoryItem, PlatformConfig, ShopListing


COLLECTIBLE_EBAY_BLOCK_REASON = (
    "Collectibles are reserved for Vinted initially, and only the automatic move after 14 days unsold "
    "can list them on eBay. "
    "eBay transfer and figurine parcel postage are not configured yet; publication, revisions and exports remain disabled."
)


def ebay_channel_block_reason(session: Session, listing: ShopListing) -> str | None:
    """Check both identities so an old mismatched listing cannot bypass policy.

    This policy covers figurines/collectibles only. Graded cards keep their
    separate workflow. Callers may still withdraw or zero existing eBay offers.
    """
    if session.get(PlatformConfig, f"vinted_fallback:{listing.id}") is not None:
        return None  # the owner's automatic move after 14 unsold days on Vinted
    identities = {listing.catalog_item_id}
    if listing.inventory_item_id:
        stock = session.get(InventoryItem, listing.inventory_item_id)
        if stock is not None:
            identities.add(stock.catalog_item_id)
    for item_id in identities:
        item = session.get(CatalogItem, item_id) if item_id else None
        if item is not None and item.item_type == CatalogItemType.COLLECTIBLE:
            return COLLECTIBLE_EBAY_BLOCK_REASON
    return None
