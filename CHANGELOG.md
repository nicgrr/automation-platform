# Changelog

Entries from the point this file was created (2026-09-07) onward. Earlier history
lives in `git log`.

## 2026-09-07 — Phase 0: collectibles platform audit

- Audited the host: confirmed this repo (not a separate app) is "the existing
  Pokémon inventory application," running as bare systemd (not Docker) against
  SQLite (not Postgres); catalogued the 22 existing tables across its two
  previously-separate Pokémon pipelines (bulk scan-ingest, single-card capture);
  confirmed the pre-existing restic-to-OneDrive backup already covers
  `automation.db` correctly and is running successfully.
- Took a manual pre-project backup snapshot
  (`~/backups/automation-pre-collectibles-platform-20260907-081207.db`) in
  addition to the automatic nightly one.
- Added `ARCHITECTURE.md`, `DATABASE.md`, `OPERATIONS.md`, this file — Phase 0
  deliverables. `docs/architecture.md` and `docs/database-schema.md` are left in
  place as historical Phase 1A record.
- Decision: evolve this app in place for the collectibles platform rather than
  building a parallel Docker/Postgres system — see `ARCHITECTURE.md` for the full
  reasoning.
- No schema changes yet. Phase 1 (unified catalogue, One Piece support, inventory
  extension) is proposed in `DATABASE.md`, pending review before execution.

### Also this session, ahead of the platform work
- `feat(foil)`: human-in-the-loop foil/reverse-holo review at `/foil-review`,
  backed by a new `foil_labels` table, plus several scan-recovery scripts
  (gapless-sheet recovery, held-sheet checks, variant correction) written while
  clearing a night's backlog of stuck scans.
- `fix(pricing)`: pokemonpricetracker.com calls are now paced and circuit-broken
  at the shared `_get()` call site, after live per-scan pricing (not the backfill
  job) fired 50+ requests in under 5 minutes and got the API key blocked three
  times.
- `inventory-audit.timer` moved from a single midnight run to hourly, 22:00–14:00.
