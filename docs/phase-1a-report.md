# RBAY Project — Phase 1A Report

**Status:** Complete  
**Verification date:** 8 August 2026  
**Test result:** 10 passed, 0 failed

## Executive summary

Phase 1A establishes the foundation of a private, auditable automation control
plane for RBAY. It provides the data model, policy boundaries, approval and audit
structures, read-only external adapters, and a narrow Docker observation broker
needed for later automation without granting an AI process unrestricted access to
the host, credentials, containers, or external write operations.

The phase is intentionally non-operational: it does not deploy services, publish
ports, authenticate end users, use production eBay credentials, mutate eBay
listings, restart containers, execute shell commands, or perform arbitrary HTTP or
filesystem access.

## Delivered scope

- FastAPI control-plane foundation for recording job requests.
- Durable models for users, configuration, schedules, job runs, audit events,
  tool permissions, tool executions, approvals, eBay listing snapshots, market
  observations, and price recommendations.
- Deny-by-default tool permission evaluation with explicit grants and constraints.
- Immutable human approval decisions linked to proposed actions.
- Redacted, correlation-aware audit recording that avoids credential persistence.
- Read-only grocery and eBay adapter contracts.
- eBay sandbox-only configuration and read-only marketplace operations.
- Recommendation-only eBay pricing workflow; no listing-update path exists.
- Narrow Docker broker contract limited to allowlisted container names, status,
  health, and bounded log retrieval of at most 500 lines.
- Automated tests covering policy, persistence, adapters, auditing, API behavior,
  and Docker broker boundaries.

## Architecture and trust boundaries

The control plane owns job, approval, permission, and audit records. External
systems are reached only through typed adapters. AI-facing code calls the control
plane and does not receive credentials or import the Docker gateway directly.

The Docker broker is a separate trust boundary. It exposes observation only and
has no restart, exec, create, update, delete, or generic Docker capability.
External listings, logs, and raw snapshots are treated as untrusted data and are
never interpreted as instructions.

## Security controls

| Control | Phase 1A implementation |
|---|---|
| Tool access | Denied unless a tool is registered and explicitly granted |
| Secrets | Kept outside the database and AI-facing process |
| eBay access | Sandbox endpoints and read methods only |
| Docker access | Exact allowlist; metadata, health, and bounded logs only |
| Audit data | Credential-shaped fields redacted before persistence |
| External writes | Not available in the Phase 1A tool registry |
| Irreversible actions | Reserved for a later phase with human approval |
| Network exposure | No deployment configuration or published ports |

## Data design

Prices are stored as integer minor currency units to avoid floating-point errors.
The schema separates observed marketplace data from generated recommendations.
Recommendations are proposals only and contain no execution or listing-mutation
field. Permission, execution, approval, and audit records provide the basis for a
traceable decision chain.

## Verification

The Phase 1A suite was executed locally with:

```bash
PYTHONPATH=.deps python3 -m pytest -q
```

Result:

```text
..........                                                               [100%]
10 passed in 0.38s
```

The passing suite verifies model persistence, eBay observations and pricing
recommendations, deny-by-default permissions, audit redaction, grocery and eBay
adapter behavior, API job recording, and Docker broker restrictions.

## Known limitations and deferred work

- User authentication is deferred to Phase 1B.
- No production deployment or Compose configuration is supplied.
- No public port is published.
- Real eBay credentials and production endpoints are not enabled.
- No eBay create, revise, publish, or delete operation exists.
- No autonomous execution of recorded jobs exists.
- No container restart, exec, or lifecycle mutation exists.
- No arbitrary shell, filesystem, HTTP, or Docker tool exists.

These are deliberate scope boundaries, not Phase 1A defects.

## Phase 1B handoff recommendations

1. Add operator authentication and identity-to-principal mapping.
2. Add controlled service configuration and private deployment packaging.
3. Implement deterministic job execution with idempotency and run-state handling.
4. Connect secrets through an external credential facility.
5. Add an explicit approval workflow before introducing any write-capable tool.
6. Expand integration and failure-path tests before enabling production systems.
7. Preserve the deny-by-default registry and the separation between observation,
   recommendation, approval, and execution.

## Conclusion

Phase 1A meets its goal: RBAY now has a tested control-plane foundation with clear
trust boundaries and no unrestricted or production write capability. It is ready
to proceed to Phase 1B once authentication, deployment, secrets management, and
the next execution scope are agreed.
