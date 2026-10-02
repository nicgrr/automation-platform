# Deprecated-feature observation register

Observation starts **2 October 2026**. The earliest removal review is
**1 December 2026**, after 60 complete days. Reaching that date does not authorize
automatic deletion; every criterion below must also pass.

## Production baseline

Read-only counts captured immediately before retirement:

| Record | Rows |
|---|---:|
| Generic goals | 0 |
| Generic approvals | 0 |
| Generic schedules | 0 |
| Tool permissions | 0 |
| Tool executions | 0 |
| Users table | 0 |
| Market observations | 0 |
| Price recommendations | 0 |
| Audit events (retained evidence) | 5,672 |

The database was at Alembic `20261002_09`. The row counts are an observation
snapshot, not an instruction to empty a table later.

## Retired now

- Generic Goals routes and analytics panel are removed. The `goals` table and ORM
  model remain so this release cannot silently propose a destructive migration.
  Retailer Goals is unrelated and remains active.
- Generic `POST /approvals` and `POST /jobs` no longer create disconnected rows.
  They return `410 Gone` and append a rejected deprecation event to the audit log.
- No audit-event schema or history is removed.

## Already in compatibility observation

- Potential Stock writes redirect into Market Watch candidate state; the old
  empty table/detail surface remains readable.
- New listing drafts use `shop_listings`; the empty `pending_listings` schema and
  historical detail handling remain.
- Inventory and price code uses canonical catalogue identity; synchronized legacy
  card-specific names/links remain temporarily.

## Required before later removal

1. Observe at least through 1 December 2026.
2. Search the repository, deployed units/timers and logs for callers.
3. Confirm production row counts and all foreign-key dependencies again.
4. Export any rows, even if they appear unused, and preserve the audit log.
5. Take and verify an online SQLite backup.
6. Implement schema removal in a separate Alembic revision and rehearse it on an
   exact production copy with integrity, foreign-key and reconciliation checks.
7. Pass the full regression suite, recovery drills and permission check.
8. Commit, tag and retain the exact pre-removal snapshot name with the release.

If any caller or real row appears, stop removal and either migrate it into the
canonical workflow or extend the observation period.
