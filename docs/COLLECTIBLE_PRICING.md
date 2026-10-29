# Non-TCG collectible pricing

## Scope and safety boundary

This is an EZBay module, not a separate application. It uses the existing
catalogue, inventory, authentication, audit, pricing, marketplace-fee and sales
records. `CatalogItem.item_type` is the routing boundary. The generic service
accepts `COLLECTIBLE` only and rejects `TCG_CARD` before it resolves or calls a
provider. Existing Pokémon, One Piece and Dragon Ball importers are unchanged.

Provider access is configured in `pricing_provider_rules`, not inferred from
brand names:

- eBay: automatic active-listing comparisons through the official Browse API.
- Depop: search link and attributed manual comparable entry. Its business API is
  not public and is not treated as a competitor-search API.
- Vinted: search link and attributed manual comparable entry. Vinted Pro is
  allowlisted and does not expose general competitor search.

Verified sales, active asks and accepted offers with an unknown final price are
stored separately. Unknown accepted prices never enter sold-price calculations.
Observations older than 30 days are displayed as stale and excluded from current
valuation calculations. Original currency and the normalized AUD amount are both
retained.

## Operator workflow

Open **Research → Collectibles pricing**, search for a product, then open it.
Owned collectible tiles also link directly to the same inventory-level view.
Use **Refresh eBay** for an explicit live refresh. Depop and Vinted provide a
marketplace search link and manual comparable form until approved API access is
available. Research-only products can be added to canonical inventory from the
same page.

Purchase-lot shipping is deliberately nullable. A lot cannot be received until
shipping is entered; use `0.00` only when shipping really was free or the purchase
was local pickup. Purchase cost, allocated shipping and landed cost remain
separate on inventory.

## Scheduled refresh

The worker respects each provider rule's `refresh_seconds` cache interval and
isolates provider failures by product:

```bash
.venv/bin/python -m scripts.refresh_collectible_prices --dry-run --limit 5
```

After verification, install the supplied systemd units without opening a port:

```bash
sudo install -m 0644 docs/systemd/collectible-pricing-refresh.service /etc/systemd/system/
sudo install -m 0644 docs/systemd/collectible-pricing-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now collectible-pricing-refresh.timer
```

## Migration and rollback

Before migration, use SQLite's online backup API:

```bash
mkdir -p backups
sqlite3 automation.db ".backup 'backups/automation-before-collectible-pricing-YYYYMMDD-HHMMSS.sqlite'"
.venv/bin/alembic upgrade head
```

Verify `/health`, the targeted regression tests and the TCG timers. The preferred
rollback is to stop EZBay and every database writer, preserve the failed database,
restore the named pre-migration snapshot, restore the matching code revision, and
restart the services. Do not downgrade a live SQLite database while writers are
running.

