# Applied migrations

Every migration in this directory has already been run against production
data. They're kept because each one documents the shape of a schema change
that `Base.metadata.create_all` can't express -- it only ever *creates*
tables, never alters existing ones (see `DATABASE.md`).

They live here rather than in `scripts/` so that the handful of scripts
that are actually run day to day -- `run-*.sh` and `start-*.sh` -- aren't
buried among one-shot migrations. `migrate_add_retailer_goals.py` was applied
on 2026-09-24 after the SQLite-consistent backup
`backups/automation-pre-retailer-goals-20260924.db` was verified.

`migrate_add_market_watch.py` was applied on 2026-09-24 after the
SQLite-consistent backup `backups/automation-pre-market-watch-20260924.db`.

`migrate_add_market_discovery.py` was applied on 2026-09-24 after the
SQLite-consistent backup
`backups/automation-pre-market-discovery-20260924.db`. It created/seeded the
generic watch-term, listing-match and alert-import records and linked existing
Sonny Angel listings without deleting or rewriting them.

Re-running one is not expected to be necessary and is not guaranteed safe.
Read the script before you do.

`migrate_add_capture_promotions.py` was applied on 2026-10-01 after the
SQLite-consistent backup
`backups/automation-pre-capture-promotions-20261001.sqlite` was verified.
It adds the idempotent handoff record between approved photo captures,
Inventory, ShopListing and Bulk Upload; no existing stock was rewritten.

`migrate_add_captured_card_variant.py` was applied on 2026-10-01 after the
same verified backup. It adds the nullable printing choice used to prevent
Normal/Holo/Reverse-Holo stock from being merged during that handoff.
