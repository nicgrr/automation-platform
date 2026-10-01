"""Shared "a listing just sold N units" stock math -- used by both
storefront/checkout.py's own sale path and scripts/import_ebay_orders.py
(Phase 5), so the max(0, ...)/SOLD_OUT-flip logic exists in exactly one
place rather than being re-implemented identically in both.
"""

from sqlalchemy.orm import Session

from .models import BulkLot, InventoryItem, InventoryStatus, ShopListing, ShopListingStatus


def decrement_listing_quantity(session: Session, listing: ShopListing | BulkLot, sold_quantity: int) -> int | None:
    """Reduce `listing`'s quantity by `sold_quantity`, flipping to SOLD_OUT
    at zero. Returns the new quantity if it actually changed, or None if
    the listing was already at zero (nothing left to decrement -- callers
    use this to know whether an eBay sync push is even needed).

    Takes a BulkLot too, not just a ShopListing -- the two share the exact
    same quantity/status shape (BulkLot.status is the same ShopListingStatus
    enum), and a Bulk Master lot sold via a manually-uploaded eBay CSV needs
    this same decrement, not a second copy of it. A BulkLot has no single
    InventoryItem behind it (it's a bundle), so the inventory side below
    only ever applies to a ShopListing.

    Real incident, confirmed 2026-09-28: this function only ever touched
    the listing, never the InventoryItem behind it -- every sale through
    either checkout.py or import_ebay_orders.py left the physical card
    marked AVAILABLE forever, regardless of how many times it actually
    sold. Nothing reading "available inventory" (the dashboard count,
    a future re-scan, a second listing built from stock) had any way to
    know the card was gone. `session` is now required so this can also
    reach and correct the real InventoryItem row in the same place."""
    if listing.quantity <= 0:
        return None
    listing.quantity = max(0, listing.quantity - sold_quantity)
    if listing.quantity == 0:
        listing.status = ShopListingStatus.SOLD_OUT
    if isinstance(listing, ShopListing) and listing.inventory_item_id:
        item = session.get(InventoryItem, listing.inventory_item_id)
        if item is not None and item.status != InventoryStatus.SOLD:
            item.quantity = max(0, item.quantity - sold_quantity)
            if item.quantity == 0:
                item.status = InventoryStatus.SOLD
    return listing.quantity
