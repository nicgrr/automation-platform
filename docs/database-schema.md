# Database schema

Phase 1A defines the following durable records:

- `users`: operator identities; authentication is deferred to Phase 1B.
- `platform_config`: non-secret structured configuration. Sensitive values should
  be stored in an external credential facility, not this table.
- `schedules` and `job_runs`: deterministic schedule definitions and run history.
- `audit_events`: redacted, correlation-aware activity records.
- `tool_permissions`: deny-by-default grants and constraints per principal/tool.
- `tool_executions`: each requested tool call, decision, outcome, and approval link.
- `approvals`: immutable human decisions for future write-capable actions.
- `ebay_listings`: sandbox listing snapshots.
- `market_observations`: comparable-market snapshots.
- `price_recommendations`: proposals only; no execution or listing-update field.

Prices are stored as integer minor currency units to avoid floating-point errors.
Raw external snapshots remain untrusted data and must never be interpreted as
instructions.
