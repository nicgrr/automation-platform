"""Shared "a listing just sold N units" stock math -- used by both
storefront/checkout.py's own sale path and scripts/import_ebay_orders.py
(Phase 5), so the max(0, ...)/SOLD_OUT-flip logic exists in exactly one
place rather than being re-implemented identically in both.
"""

from .models import BulkLot, ShopListing, ShopListingStatus


def decrement_listing_quantity(listing: ShopListing | BulkLot, sold_quantity: int) -> int | None:
    """Reduce `listing`'s quantity by `sold_quantity`, flipping to SOLD_OUT
    at zero. Returns the new quantity if it actually changed, or None if
    the listing was already at zero (nothing left to decrement -- callers
    use this to know whether an eBay sync push is even needed).

    Takes a BulkLot too, not just a ShopListing -- the two share the exact
    same quantity/status shape (BulkLot.status is the same ShopListingStatus
    enum), and a Bulk Master lot sold via a manually-uploaded eBay CSV needs
    this same decrement, not a second copy of it."""
    if listing.quantity <= 0:
        return None
    listing.quantity = max(0, listing.quantity - sold_quantity)
    if listing.quantity == 0:
        listing.status = ShopListingStatus.SOLD_OUT
    return listing.quantity
