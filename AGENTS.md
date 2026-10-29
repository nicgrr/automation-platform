# Agent instructions (Claude Code and Codex)

This file is shared by every coding agent working on EZBay. Claude Code loads it
through `CLAUDE.md`; Codex reads it directly. Keep one copy of the rules here.

## Handoff protocol

Several agents take turns on this repo. Nobody remembers another agent's
session, so the repo is the only shared memory.

At the start of a session:

1. Read `HANDOFF.md`.
2. Run `git status` and `git log --oneline -10`, and `git diff main...` on a
   feature branch.
3. Continue from "Next steps" unless the operator asks for something else.

Before ending a session or handing over:

1. Update `HANDOFF.md`: current task, what was done, next steps, gotchas, and
   who updated it and when (absolute dates).
2. Commit, even as a WIP commit, with a message that says what changed and why.
   Do not leave uncommitted work for the next agent to guess at.

Work on one branch per task. Don't rewrite or force-push history another agent
committed.

## Project

EZBay is a private FastAPI/SQLite CRM for collectibles operations (catalogue,
intake, pricing, eBay, NL Vault, Whatnot, direct sales). See `README.md` and
`docs/ARCHITECTURE_AND_DATA_FLOW.md`.

- `automation_control`: CRM API, workflow pages, services, models
- `automation_control/scan_ingest`: bulk card scanning and intake
- `nl_vault`: storefront and checkout lifecycle
- `scripts`: jobs, integrations, drills, operational checks
- `migrations`: authoritative Alembic schema history
- `tests`: regression and transaction-safety coverage

## Commands

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Run tests before committing. Schema changes go through a new Alembic migration;
startup refuses an unexpected database revision.

## Rules

- External writes (eBay, Whatnot, payments) and destructive data corrections are
  explicit operator actions. Don't run them without being asked.
- Starting, stopping or restarting services is an operator action. The NL Vault
  storefront is a bare uvicorn process, not a systemd unit.
- Keep the CRM on `127.0.0.1`; never bind it to `0.0.0.0`.
- Never commit, print or log secrets. `.env.example` holds variable names only.
- Inventory is canonical: channel workflows reserve or consume it atomically.
