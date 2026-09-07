# Architecture

This document is the Phase 0 architecture summary for evolving this repository into
the collectibles business platform (catalogue, inventory, purchasing, sales, Whatnot,
CRM, analytics). It supersedes `docs/architecture.md`, which documented only the
original Phase 1A control-plane slice and predates everything below. That file is
left in place as history rather than deleted.

## What this repository actually is today

This is **one FastAPI application** (`automation_control.api:app`) backed by **one
SQLite database** (`automation.db`), serving two lineages of work that grew here
independently and were never unified:

1. **The original control plane** (Phase 1A/1B, per `README.md` and
   `docs/phase-1a-report.md`): a deliberately sandboxed foundation for letting an AI
   process request actions — jobs, approvals, tool permissions, audit logging, a
   narrow read-only Docker metadata broker, and a read-only eBay *Sandbox* OAuth
   integration. Tables: `users`, `platform_config`, `schedules`, `job_runs`,
   `audit_events`, `tool_permissions`, `tool_executions`, `approvals`,
   `ebay_listings` (sandbox only), `market_observations`, `price_recommendations`.
   This is largely dormant — `market_observations` and `price_recommendations` are
   empty; `ebay_listings` is sandbox-only and unused by the live business.

2. **The Pokémon business, bolted on afterward**, in two separate pipelines that
   don't share code or a common "item" concept:
   - **Bulk scan-ingest** (`automation_control/scan_ingest/`): flatbed-scanner →
     detect individual cards → identify against a cached catalogue → commit to
     inventory. Tables: `card_sets`, `catalog_cards` (20,479 rows, the reference
     catalogue), `inventory_items` (995 rows, what's actually owned),
     `card_prices`, `foil_labels`. This is the system I've been operating and
     extending all of tonight's session (scanbd → scan-ingest.service → the
     dashboard pages under `/scan-ingest`, `/feed`, `/review`, `/foil-review`,
     `/inventory`).
   - **Single-card capture** (`capture.py`, `listings_review.py`, `price_review.py`):
     a separate one-card-at-a-time photo → identify → price → approve → list-on-eBay
     pipeline. Tables: `captured_cards` (579 rows), `pending_listings` (0 rows —
     built but not yet used for a real listing), `ebay_credentials`. This is a
     genuine, partially-built precursor to Module 8 (Sales Channels) — its price
     approval queue is what "process reviews" turned out to mean earlier tonight.

Both Pokémon pipelines already share the same `automation_control.scan_ingest.
pricing` module's `PriceSource` abstraction is *not* shared between them, though —
`price_review.py`'s pricing and `scan_ingest`'s pricing are separate code paths that
happen to both exist. This duplication is real architectural debt, called out below
under Proposed Changes.

## Deployment model (not Docker)

The mega-prompt that kicked off this work assumed a Dockerized, Postgres-backed
application. That assumption doesn't match reality, and I'm not forcing it to fit:

- **No Docker.** `ezbay.service`, `scan-ingest.service`, and `scanbd.service` are
  bare systemd units running a Python venv directly (`.venv/bin/uvicorn
  automation_control.api:app` and `.venv/bin/python -m automation_control.scan_ingest.
  cli scan`). There is no Dockerfile, no `docker-compose.yml`, and no container for
  this app anywhere on the host.
- **SQLite, not Postgres.** `AUTOMATION_DATABASE_URL` defaults to
  `sqlite:///./automation.db`, a 25MB file. Access is through SQLAlchemy, so the
  ORM layer doesn't care which database engine sits underneath.
- **No reverse proxy.** Nothing on this host runs nginx/Caddy/Traefik. Every
  service — this one included — binds its own port and is reachable only over
  Tailscale (this app: `127.0.0.1:8080`, exposed as `homeserver.tailefaa51.ts.net`
  via Tailscale Serve/whatever fronts it at the OS level — confirmed by the app
  being reachable at that hostname in this session's own screenshots, not by an
  nginx config, since none exists).
- **The host itself is not idle.** `docker ps` shows 15 running containers
  unrelated to this app — `personal-security-centre` (its own Postgres),
  `tcg-sniper` (its own Postgres + Redis; a card-market-sniping bot, distinct from
  this inventory system), `trading-dashboard`, `grocery-optimiser`, `up-bank-stats`,
  `lister`, `macro-mates`, `hub`, plus `portainer`, `diun`, and `uptime-kuma` for
  fleet management. None of these were touched, and none of them are "the existing
  Pokémon inventory application" — that is unambiguously this repository.

### Decision: keep this app on systemd + SQLite for now

The instructions this work started from lean Docker/Postgres by default. I'm
invoking the "unless the existing architecture provides a strong reason otherwise"
escape hatch explicitly, for reasons that should be revisited if they stop holding:

- SQLite at 25MB with one writer (this app) and a handful of readers has no
  performance or concurrency problem today. Postgres would add a new container, a
  new backup story, and a connection-pool to operate for a business need that
  doesn't exist yet.
- The existing backup system (below) already handles SQLite correctly
  (`sqlite3 .backup` before the filesystem snapshot) — Postgres would need its own
  `pg_dump` wiring added to that same script.
- Moving to Docker for *this* app specifically would mean re-solving auth,
  static-file serving, and the scanner's USB access (`scanbd`/`saned` need real
  device access) inside a container, for a service that already runs cleanly as-is.
- SQLAlchemy already isolates the app from this choice. `automation_database_url`
  is a connection string; moving to Postgres later is a config change plus a
  `pg_loader`/dump-and-restore migration, not a rewrite. Nothing about the schema
  work in Phase 1 forecloses that move.

Revisit this if: multiple processes need to write concurrently (e.g. a second
person's device, a background worker separate from the web process), or reporting
queries against a much larger dataset start showing up as real latency.

## Authentication & security posture

- Dashboard auth: username + `scrypt`-hashed password (`n=2^14, r=8, p=1` — a
  reasonable memory-hard KDF choice), session token = HMAC-SHA256 over
  `username:expiry`, signed with `SESSION_SIGNING_KEY`, 8-hour expiry, verified with
  `hmac.compare_digest` (constant-time, correctly avoids timing attacks). No
  CSRF tokens on the POST forms — acceptable *today* only because the app is
  single-operator and reachable exclusively over Tailscale (not the open internet),
  not because it's actually CSRF-safe. Flagged as a real gap below.
- No rate limiting on the login endpoint or any POST route.
- Credentials live in `.env.local` (git-ignored; confirmed present) and
  `scan_ingest_data/session.env`. I verified *which* variables are configured by
  name only — `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD_HASH`, `SESSION_SIGNING_KEY`,
  `EBAY_*`, `ANTHROPIC_API_KEY`, `POKEMONPRICETRACKER_API_KEY`, `R2_*` — without
  reading or printing any value. `.env.example` documents the same names for
  reference; real values are never in git (`git log` for `.env.local` shows nothing
  — it's untracked, per `.gitignore`).
- eBay integration exists but is sandbox-only and OAuth-token-encrypted at rest
  (`EBAY_TOKEN_ENCRYPTION_KEY`) — no production eBay credential is wired in yet.
  Update (2026-09-07): `ebay_env` now accepts `"production"` at the config
  layer (production OAuth URLs, matching credential validators, adapter
  `base_url`) so the code path exists, but three separate things all still
  have to happen deliberately before a production listing is possible: (1)
  `scripts/start-control-plane.sh` still hard-refuses to start the service
  unless `EBAY_ENV=sandbox` exactly — this was deliberately left in place,
  not loosened; (2) `EbaySandboxReadAdapter` is read-only in both
  environments, no mutation methods exist; (3)
  `listing_pipeline/publish.py` (the only place that would ever write a
  real listing) remains an intentional, unimplemented, unwired placeholder
  with its own documented requirement (an approved `Approval` record before
  any write). Built on explicit instruction to add the plumbing without
  turning any of this on — see `CHANGELOG.md`.
- The existing `docs/security-model.md` principles (deny tools by default, sandbox
  eBay, human approval before irreversible writes, redact credential-shaped audit
  fields) still hold and should keep holding as this grows.

## Backups (already exist, already working)

A restic-to-OneDrive backup already runs nightly (`restic-backup.timer`, 03:00
Sydney time) and covers the **entire filesystem**, not just this app — including a
`sqlite3 .backup` consistency snapshot of every `.db`/`.sqlite` file found under
`/home /root /srv /opt /usr/local /var/www /var/lib/docker/volumes` before the
restic run, which already picks up `automation.db` automatically with no
special-casing needed. Verified: last night's run (2026-09-07 03:05–03:34)
completed with `"status": "success", "exit_code": 0`, 11 retained snapshots,
23.8GB repository (compressed). `restic-check-monthly.timer` and
`restic-maintenance.timer` also run automatically.

In addition, before any of tonight's work, I took an explicit manual snapshot:
`~/backups/automation-pre-collectibles-platform-<timestamp>.db`, as a
project-start reference point distinct from the rolling nightly set.

**This satisfies "back up before migrating."** No schema migration should proceed
without either relying on tonight's nightly restic run or taking a fresh manual
snapshot first — see `OPERATIONS.md` for the exact restore procedure.

## Proposed direction (Phase 1 and beyond)

Evolve this app in place rather than building a second system next to it:

1. **Unify the "what is it" concept.** Both Pokémon pipelines already have their
   own idea of a card (`catalog_cards` for bulk scan, `captured_cards`'s free-text
   fields for single-capture). Module 1's generic catalogue
   (`catalog_items` + type-specific detail tables) should absorb `catalog_cards`
   as the `tcg_card` item type, not replace it — existing rows keep their ids.
2. **Unify the "what do I own" concept.** `inventory_items` (995 rows, well-modeled
   already: variant, condition, quantity, scan photo, cost) is very close to
   Module 2's target shape already. Extend it rather than replace it: add
   `catalog_item_id` (nullable during migration, backed to point at the new generic
   table), storage location, and status fields; keep `card_id` and the scan-specific
   fields working until every reader is migrated.
3. **One pricing engine, not two.** Retire the dormant `market_observations` /
   `price_recommendations` pair in favor of `scan_ingest.pricing`'s
   `PriceSource`/`CachedPriceSource` abstraction (already provider-pluggable,
   already rate-limit-safe as of tonight's fix), extended to serve
   `price_review.py` too.
4. **New modules are new tables, not a new app.** Purchase lots, potential stock,
   customers, suppliers, marketplaces/fee rules, Whatnot shows, sales, goals, and
   the release calendar are all genuinely new concepts with no existing analog —
   they get new tables per the proposal in `DATABASE.md`, added via additive
   migrations (new tables, new nullable columns) so nothing existing breaks mid-way.

See `DATABASE.md` for the concrete schema proposal and migration sequencing, and
the Phase 0 report delivered in-session for the full module-by-module implementation
plan mapped onto the requested Phase 1–7 structure.
