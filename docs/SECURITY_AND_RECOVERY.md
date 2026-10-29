# EZBay security, release and recovery guide

## Security boundary

- Keep both web processes bound to localhost. The CRM (`ezbay.service`, port 8080)
  is private: reach it through Tailscale only, never expose it directly and never
  enable Tailscale Funnel.
- NL Vault (`nl-vault.service`, port 8090) is deliberately public at
  `nlvault.com.au`/`www.nlvault.com.au` through the Cloudflare tunnel defined in
  `~/server/cloudflared/config.yml`. It currently runs in catalogue mode (no
  cart or checkout). Never add a tunnel route that points at port 8080.
- Dashboard sessions are signed, time-limited and protected by login throttling
  and CSRF checks on browser forms. JSON requests remain subject to the same
  private-network and authentication boundary.
- Store real credentials only in the protected environment file. Never print,
  commit, paste into documentation or expose them to frontend code.
- Treat listings, uploads, logs and marketplace responses as untrusted data.
- Audit recording redacts keys whose names look like passwords, secrets, tokens,
  API keys, authorization values or codes.
- NL Vault's running service is not a public-launch approval. Stripe configuration,
  verified business/legal facts and explicit legal-text approval are separate
  hard gates.

## Normal health checks

Use Dashboard first. For command-line verification:

```bash
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8090/health
.venv/bin/python -m scripts.crm_health_check
sudo systemctl list-timers --all
```

Investigate persistent dashboard warnings before clearing anything. Do not edit a
status file merely to make a warning disappear.

## Release gate

From the repository root:

```bash
git status --short
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q automation_control scripts
.venv/bin/alembic current
.venv/bin/alembic check
.venv/bin/python -m scripts.check_release_permissions
```

Before a migration or release, create an online SQLite backup with `.backup`;
never copy a live database file as if it were an inert document. Rehearse every
migration against an exact production copy, checking `integrity_check`, foreign
keys, Alembic head and important row counts. Deploy only a committed, tagged
revision. Restart only affected services and confirm local and Tailscale health.

## Backup layers

1. **Pre-release snapshots** provide a fast, named rollback point.
2. **Daily encrypted restic backup** protects the wider filesystem and includes a
   consistent SQLite snapshot.
3. **Monthly repository check** verifies backup storage.
4. **Monthly restore drill** proves that a snapshot can be restored, migrated and
   booted—not merely that files were uploaded.

Dashboard recovery evidence becomes attention-worthy when the newest successful
backup or restore drill is stale.

## Non-destructive recovery drills

```bash
.venv/bin/python -m scripts.restore_drill
.venv/bin/python -m scripts.release_rollback_drill \
  --tag <previous-release-tag> \
  --snapshot backups/<matching-pre-release-snapshot>.sqlite
```

Both work in private temporary storage. The restore drill validates SQLite
integrity and foreign keys before and after migration, preserves core counts and
boots the app health endpoint. The rollback drill pairs historical code with its
matching snapshot without checking files out over the live worktree.

## Real rollback

- **Code-only release:** return to the previous tag and restart affected services;
  do not replace the database.
- **Release with a migration:** stop every process that can write, restore the
  named pre-release snapshot, return to the matching previous tag, restart and run
  reconciliation/health checks.
- Prefer the rehearsed snapshot pair over a production Alembic downgrade.

Never restore while web, scanner or scheduled writers are active. Preserve the
failed database and logs for investigation instead of overwriting the only copy.

## Channel drills

The following use temporary databases and no real money, publication or external
write:

```bash
.venv/bin/python -m scripts.storefront_transaction_drill
.venv/bin/python -m scripts.whatnot_show_drill
.venv/bin/python -m scripts.retailer_purchase_drill
.venv/bin/python -m scripts.direct_sale_drill
.venv/bin/python -m scripts.supplier_purchase_drill
```

Run the relevant drill after changing a channel's lifecycle in addition to its
unit tests.
