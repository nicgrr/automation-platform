"""Shared "a listing just sold N units" stock math -- used by both
storefront/checkout.py's own sale path and scripts/import_ebay_orders.py
(Phase 5), so the max(0, ...)/SOLD_OUT-flip logic exists in exactly one
place rather than being re-implemented identically in both.
"""

from .models import ShopListing, ShopListingStatus


def decrement_listing_quantity(listing: ShopListing, sold_quantity: int) -> int | None:
    """Reduce `listing`'s quantity by `sold_quantity`, flipping to SOLD_OUT
    at zero. Returns the new quantity if it actually changed, or None if
    the listing was already at zero (nothing left to decrement -- callers
    use this to know whether an eBay sync push is even needed)."""
    if listing.quantity <= 0:
        return None
    listing.quantity = max(0, listing.quantity - sold_quantity)
    if listing.quantity == 0:
        listing.status = ShopListingStatus.SOLD_OUT
    return listing.quantity
