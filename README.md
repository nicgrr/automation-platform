# Personal Automation Platform

Phase 1A foundation plus the credential-ready Phase 1B private dashboard and
read-only eBay Sandbox OAuth integration.

This repository is intentionally separate from the existing grocery application. It
does not publish ports, connect production eBay credentials, or grant an AI process
access to Docker, the host shell, or secret files.

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
