# EZBay CRM and collectibles operations

EZBay is the private operating platform for catalogue, stock intake, pricing,
purchasing, eBay, NL Vault, Whatnot, direct sales, customers and recovery health.
It is one Alembic-versioned FastAPI/SQLite system with a separate NL Vault web
process over the same business data.

Current documentation:

- [Operator guide](docs/OPERATOR_GUIDE.md)
- [Architecture and data flow](docs/ARCHITECTURE_AND_DATA_FLOW.md)
- [Security, releases and recovery](docs/SECURITY_AND_RECOVERY.md)
- [Cleanup roadmap](docs/EZBAY_IMPLEMENTATION_ROADMAP.md)
- [Deprecation observation register](docs/DEPRECATION_OBSERVATION_2026-10-02.md)
- [Changelog](CHANGELOG.md)

## Main components

- `automation_control`: CRM API, workflow pages, business services and models
- `automation_control/scan_ingest`: bulk card scanning and inventory intake
- `nl_vault`: private storefront and checkout lifecycle
- `scripts`: jobs, integrations, drills and operational checks
- `migrations`: authoritative Alembic schema history
- `tests`: regression and transaction-safety coverage

## Safety properties

- Inventory is canonical and channel workflows reserve or consume it atomically.
- External writes and destructive corrections require explicit operator actions.
- Audit events preserve redacted evidence of business transitions.
- Startup refuses an unexpected database revision.
- Backups, restore drills and release rollback are separately verified.
- The CRM stays private over Tailscale; NL Vault public launch has its own gate.

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

Do not bind the CRM to `0.0.0.0`; use the established private Tailscale route.

## Configuration

See `.env.example` for variable names only. Never commit or print real values.
