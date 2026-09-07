"""NL Vault -- the public storefront.

Deliberately a separate FastAPI application from automation_control, run
as its own process on its own port, rather than extra routes on the admin
app. The admin app holds inventory management, scan control, eBay
credentials and the pricing engine behind a single session cookie; a
public shop is the one part of this system that strangers are meant to
reach. Keeping them as separate apps means an auth mistake in one cannot
expose the other, and the storefront process only ever needs read access
to the catalogue plus write access to its own shop_* tables.
"""
