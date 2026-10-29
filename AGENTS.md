# Agent instructions (Claude Code and Codex)

This file is shared by every coding agent working on EZBay. Claude Code loads it
through `CLAUDE.md`; Codex reads it directly. Keep one copy of the rules here.

## Handoff protocol

Several agents take turns on this repo. Nobody remembers another agent's
session, so the repo is the only shared memory. Keep it ready for an unexpected
usage limit or interruption; do not wait for the session to end.

At the start of a session:

1. Read `HANDOFF.md`.
2. Run `git status` and `git log --oneline -10`, and `git diff main...` on a
   feature branch.
3. Read "In progress" and "Next steps", including the operator's constraints.
   Compare the handoff with the actual working tree and recent commits before
   resuming. An interrupted step may be partly applied even if the note still
   says "planned"; do not blindly repeat writes or discard uncommitted work.

Before each small batch of edits or other state-changing work:

1. Update `HANDOFF.md` first. Record the goal, active agent, branch and base
   commit, relevant files, planned next action, and operator constraints.
   Separate completed work from work in progress. Include unresolved decisions,
   known failures and the exact next step another agent should take.
2. Record the existing dirty files and who owns them where known. Never
   overwrite, stage or commit another agent's or the operator's unfinished work
   just to create a checkpoint. If ownership is unclear, preserve the changes
   and record the uncertainty.
3. Save and commit the handoff checkpoint before starting the edit batch,
   staging only the handoff changes you own. A checkpoint that already describes
   the same batch need not be repeated. Editing the handoff itself does not
   require a recursive checkpoint.
4. Keep each batch small enough to describe and resume as one implementation
   step. Record intent before any newly authorized external action too; this
   protocol does not grant permission to change services or external systems.

After each batch, before a lengthy operation, and before ending or handing over:

1. Refresh `HANDOFF.md` with actual results, files changed, remaining work and
   the next concrete action. Record validation as passed, failed, running or
   not run, with the relevant command/result or reason. Never report validation
   as passed unless it actually ran and passed.
2. Commit your changes together with the handoff, even as a clearly labelled
   WIP commit if validation or implementation is unfinished. Stage explicit
   paths or hunks you own. Do not wait for a usage warning to checkpoint.
3. If a commit cannot be made, save the handoff on disk with the reason and
   the uncommitted paths. Never reset or stash someone else's work to force a
   clean tree. On takeover, the next agent must inspect both the note and diff.
4. Update "Last updated" with the agent and absolute date (and time when useful).
   Maintain only your own work-history subsection at meaningful checkpoints,
   tying summaries to commits; keep uncertain historical work unattributed.

Include an explicit `Agent: Codex` or `Agent: Claude Code` trailer in new
agent-created commits, alongside any existing co-author convention. Do not
retroactively label uncertain commits or rewrite another agent's history.

This is a shared instruction-based workflow. No timer or hook is installed by
these files, and an abrupt cutoff may leave the current batch partly applied.
The saved plan and frequent checkpoints are the recovery path; another agent
does not start automatically when a usage limit is reached.

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

Run appropriate tests before marking a code change complete. Handoff-only and
WIP checkpoints may be committed before validation; record what remains unrun
in `HANDOFF.md`. Follow an explicit operator instruction not to run tests.
Schema changes go through a new Alembic migration; startup refuses an unexpected
database revision.

## Rules

- External writes (eBay, Whatnot, payments) and destructive data corrections are
  explicit operator actions. Don't run them without being asked.
- Starting, stopping or restarting services is an operator action. The NL Vault
  storefront is a bare uvicorn process, not a systemd unit.
- Keep the CRM on `127.0.0.1`; never bind it to `0.0.0.0`.
- Never commit, print or log secrets. `.env.example` holds variable names only.
- Inventory is canonical: channel workflows reserve or consume it atomically.
