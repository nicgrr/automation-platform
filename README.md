# Personal Automation Platform

Started as a Phase 1A control-plane foundation plus a Phase 1B private dashboard
and read-only eBay Sandbox OAuth integration. Has since grown into the operational
system for a real Pokémon TCG bulk-scan-to-inventory business (see `/scan-ingest`,
`/feed`, `/review`, `/foil-review`, `/inventory`) and a separate single-card
capture-to-listing pipeline (`/cards/*`).

**This is now also the foundation for a broader collectibles business platform**
(Pokémon, One Piece, Sonny Angel, Smiski, sealed product, Whatnot, CRM — see
`ARCHITECTURE.md` and `DATABASE.md` for the current audit and the proposed schema).
Evolving in place rather than as a separate app — `ARCHITECTURE.md` documents why.

This repository is intentionally separate from the existing grocery application. It
does not publish ports, connect production eBay credentials, or grant an AI process
access to Docker, the host shell, or secret files.

See `ARCHITECTURE.md`, `DATABASE.md`, `OPERATIONS.md`, and `CHANGELOG.md` for
current state; `docs/scan-ingest.md` for the day-to-day scanning workflow.

## Components

- `automation_control`: control-plane API, persistence models, permissions, and jobs
- `automation_control.adapters`: read-only grocery and eBay clients
- `automation_control.docker_broker`: narrow Docker metadata/log broker
- `tests`: policy, persistence, adapter, and API tests

## Safety properties

- Tools are denied unless explicitly registered and granted.
- Approvals are immutable decisions associated with a proposed action.
- Audit events record tool/job activity without storing credentials.
- The Docker contract includes only allowlist, status, health, and bounded logs.
- eBay uses sandbox endpoints and read-only methods only.
- No Compose file or public port is included in Phase 1A.

## Local development

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Running the API is an explicit operator action. Keep it on localhost:

```bash
.venv/bin/uvicorn automation_control.api:app --host 127.0.0.1 --port 8080
```

For private Tailscale access and Sandbox OAuth setup, see
`docs/phase-1b-configuration.md`. Do not bind this service to `0.0.0.0`.

## Configuration

See `.env.example` for variable names only. Never commit real values.
