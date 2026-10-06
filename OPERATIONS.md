# Detailed operations reference

> Use [`docs/OPERATOR_GUIDE.md`](docs/OPERATOR_GUIDE.md) for current workflows and
> [`docs/SECURITY_AND_RECOVERY.md`](docs/SECURITY_AND_RECOVERY.md) for the current
> release/recovery contract. This older long-form reference retains host history
> and troubleshooting detail; point-in-time revisions and readiness notes below
> may be historical.

## Services (systemd, not Docker — see `ARCHITECTURE.md`)

| Unit | What it does |
|---|---|
| `ezbay.service` | The web app (`automation_control.api:app`) on `127.0.0.1:8080`, via Tailscale as `homeserver.tailefaa51.ts.net` |
| `nl-vault.service` | Private NL Vault storefront on `127.0.0.1:8090`, via Tailscale port `8444`; public launch is separately gated in the dashboard |
| `scan-ingest.service` | Watches for scanned sheets, detects/identifies/commits cards continuously |
| `scanbd.service` | Polls the flatbed scanner's button; known to leak memory and occasionally misfire — see `scanbd-restart.timer` |

```bash
sudo systemctl status ezbay nl-vault scan-ingest scanbd
sudo journalctl -u ezbay -f
```

## Timers

| Timer | Schedule | What |
|---|---|---|
| `tcgcsv-pricing-sync.timer` | daily, approximately 08:17 | Imports the free TCGCSV catalogue/prices once when its daily build changes |
| `inventory-audit.timer` | hourly, 22:00→14:00 | Photo-identity audit (read-only) |
| `scanbd-restart.timer` | daily, 00:00 | Restarts `scanbd` pre-emptively |
| `pokemonpricetracker-backfill.timer` | daily, 12:00 | Prices already-scanned inventory via the paid API — **rate-limit-sensitive, see below** |
| `ebay-order-import.timer` | every 10 min | Polls eBay orders, reflects a sale onto the matching `ShopListing` — see below |
| `ebay-listings-refresh.timer` | hourly | Refreshes the mirrored eBay listing cache |
| `ebay-price-import.timer` | daily, approximately 03:17 | Refreshes eBay market prices |
| `market-watch-scan.timer` | twice daily | Refreshes AU/JP collectible watches |
| `restic-backup.timer` | daily, 03:00 | Whole-filesystem encrypted backup to OneDrive (pre-existing, not built by this project) |
| `restic-check-monthly.timer` | monthly | Checks backup-repository integrity |
| `restic-maintenance.timer` | weekly | Applies retention and repository maintenance |
| `ezbay-restore-drill.timer` | monthly | Restores the newest manual SQLite snapshot into a temporary database, migrates and boots it |
| `ezbay-reconciliation-audit.timer` | daily, 03:17 | Inventory-vs-listings drift audit (read-only) — shown on the dashboard and in logs |

```bash
sudo systemctl list-timers --all
sudo systemctl start <name>.service   # run one immediately, off-schedule
```

## Backups

**Automatic:** `restic-backup.timer` already covers `automation.db` correctly (a
`sqlite3 .backup` consistency snapshot is taken before the filesystem backup runs),
nightly at 03:00 Sydney time, encrypted, to OneDrive. Verify it's healthy:

```bash
sudo systemctl status restic-backup.service
cat /var/lib/restic-status/* | tail -20   # look for "status": "success"
```

**Manual, before a migration or risky change:**
```bash
cd /home/nic/automation-platform
sqlite3 automation.db ".backup '/home/nic/backups/automation-$(date +%Y%m%d-%H%M%S).db'"
```

**Restore** (from a manual snapshot):
```bash
sudo systemctl stop ezbay scan-ingest        # stop writers first
cp /home/nic/backups/automation-<timestamp>.db /home/nic/automation-platform/automation.db
sudo systemctl start ezbay scan-ingest
```

**Restore from restic** (whole-filesystem, use only if the manual snapshots are
also gone):
```bash
restic -r <repo> snapshots                    # list available points in time
restic -r <repo> restore <snapshot-id> --target /tmp/restore --include /home/nic/automation-platform
```
Repository location and password are in `/etc/restic-onedrive/` — not printed here
per the "never print secrets" rule; confirmed to exist, not read.

### Restore drill

The last non-destructive drill passed on 2026-10-01 using
`backups/automation-pre-pricing-policy-20261001T1643.sqlite`. The snapshot was
copied beneath `/tmp`, passed `PRAGMA integrity_check` and
`PRAGMA foreign_key_check`, upgraded from `20261001_01` to `20261001_02`, and
passed those checks again. Catalogue, inventory, price and shop-listing counts
were preserved (`64911`, `2140`, `97131`, `2127`), and a FastAPI health request
against the restored copy returned HTTP 200.

Repeat without touching production:

```bash
cd /home/nic/automation-platform
.venv/bin/python -m scripts.restore_drill
# or select an exact snapshot:
.venv/bin/python -m scripts.restore_drill --snapshot backups/<snapshot>.sqlite
```

The command copies the snapshot beneath a private temporary directory, verifies
integrity and foreign keys, migrates it to the current Alembic head, proves key
row counts were preserved, and boots the application health endpoint against the
restored copy. Temporary business data is removed when the command exits. Its
atomic status record drives the dashboard's restore-age warning.

Install the tracked monthly unit with:

```bash
sudo install -d -o nic -g ezbay -m 2775 /home/nic/automation-platform/scan_ingest_data/status
sudo install -m 0644 docs/systemd/ezbay-restore-drill.service /etc/systemd/system/
sudo install -m 0644 docs/systemd/ezbay-restore-drill.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ezbay-restore-drill.timer
```

## Deploying a release

```bash
cd /home/nic/automation-platform
git status --short                            # must be clean
git describe --tags --always --dirty          # record exactly what will run
.venv/bin/python -m pytest -q                 # must be green
find automation_control docs migrations scripts static tests -type d -exec chmod g+rx {} +
chmod -R g+rX .venv                          # restrictive umasks must not hide installed packages from ezbay
.venv/bin/python -m scripts.check_release_permissions
.venv/bin/alembic current                     # must match head
.venv/bin/alembic check                       # must report no new operations
sqlite3 automation.db ".backup '/home/nic/backups/automation-pre-release-$(date +%Y%m%d-%H%M%S).db'"
sudo systemctl restart ezbay                  # for API/dashboard changes
sudo systemctl restart scan-ingest            # for scan_ingest pipeline changes
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8090/health
```

Deploy only committed, tagged revisions. Settings shows both the Git revision and
database revision currently in use. If smoke checks fail, stop writers, restore the
pre-release database only when the release included a data migration, return to the
previous tag and restart both services.

### Rollback procedure

For a code-only release, check out the previous tag and restart the affected
services; leave the database in place. For a release containing a data migration,
stop every writer first, restore the named pre-release SQLite snapshot, check out
the previous tag, then restart and run both health probes. Do not run an Alembic
downgrade on production when a pre-release snapshot exists: restoring the exact
code/database pair is the safer and already rehearsed path.

Prove the pair non-destructively before relying on it:

```bash
.venv/bin/python -m scripts.release_rollback_drill \
  --tag <previous-release-tag> \
  --snapshot backups/<matching-pre-release-snapshot>.sqlite
```

The drill exports the tag and copies the snapshot beneath temporary storage. It
starts the historical application through its real schema gate and health endpoint,
checks integrity and foreign keys, and confirms key row counts did not change. It
never checks out files over the live worktree or opens the production database.

The tracked storefront unit is installed with:

```bash
sudo install -m 0644 docs/systemd/nl-vault.service /etc/systemd/system/nl-vault.service
sudo systemctl daemon-reload
sudo systemctl enable --now nl-vault.service
```

Running the private service does not mean the shop is ready for public launch. The
dashboard launch gate remains visible until all Stripe credentials, verified legal
business facts and explicit legal-review approval are configured.

## Running a schema migration

```bash
.venv/bin/alembic current
.venv/bin/alembic upgrade head
.venv/bin/alembic check
```

Take a manual backup first regardless of what the nightly restic run last did. The
scripts under `scripts/migrations/` are pre-Alembic history and must not be used for
new schema work. Production was baselined at `20261001_01` on 1 October 2026.
Revision `20261002_03` makes inventory catalogue identity mandatory and unique;
the legacy `card_id` link remains temporarily for scan-reference compatibility.
Revision `20261002_04` adds purchase-candidate status and decision fields directly
to Market Watch listings; it preserves the empty legacy potential-purchases table.

## Rate-limit-sensitive: pokemonpricetracker.com

Free tier: 100 credits/day, 3 credits/search, 60 requests/minute. As of
2026-09-07 the client paces every request and fails fast for the remainder of any
detected block (see the `fix(pricing)` commit) — but the daily credit ceiling is
still real. If `pokemonpricetracker-backfill.timer` needs pausing again:
```bash
sudo systemctl disable --now pokemonpricetracker-backfill.timer
sudo systemctl enable --now pokemonpricetracker-backfill.timer   # to resume
```

## eBay two-way stock sync

`scripts/import_ebay_orders.py` (run via `scripts/run-ebay-order-import.sh`)
polls eBay for orders and reflects a sale onto the matching `ShopListing`;
a website sale pushes the other direction automatically already (see
`automation_control/ebay_sync.py`), no timer needed for that half.

This is enabled in production through `ebay-order-import.timer` and polls every
ten minutes. Order import requires only the connected OAuth account; it does not
depend on Seller Hub listing policies. Direct publication is separately gated
on the payment, fulfillment and return policy IDs plus the merchant location in
`.env.local`. The owner-approved production canary passed on 2026-10-03.

If the timer is ever disabled, restore it the same way as any other timer:
```bash
sudo systemctl enable --now ebay-order-import.timer
```

See `docs/EBAY_OPERATIONS.md` for policy/location configuration, publication,
verification and safe-retry procedures.

## Inventory-vs-listings reconciliation audit

`scripts/audit_inventory_vs_listings.py` (run nightly via
`ezbay-reconciliation-audit.timer` / `scripts/run-reconciliation-audit.sh`)
formalizes four checks found and fixed by hand on 2026-09-28/29 after a
real overselling bug (a sold card was never marked sold in inventory —
see `automation_control/stock.py`'s `decrement_listing_quantity`): sold
orders whose inventory is still AVAILABLE, ACTIVE listings whose
inventory is gone/sold, the same inventory item behind two ACTIVE
listings, and variation listings mixing languages among their children.

Read-only — it only reports. Per the owner decision on 1 October 2026, operational
alerts are dashboard-only; Discord/email should not be configured as the incident
channel. Findings remain visible through the dashboard health state and logs.

```bash
sudo systemctl status ezbay-reconciliation-audit.timer
sudo journalctl -u ezbay-reconciliation-audit -f
.venv/bin/python -m scripts.audit_inventory_vs_listings   # run on demand
```

If a finding turns out to be real drift rather than noise, the same
one-off repair scripts from the original incident still apply:
`scripts/repair_inventory_after_ebay_sales.py` (inventory left
AVAILABLE after a real sale) and
`scripts/repair_active_listings_with_sold_inventory.py` (a listing left
ACTIVE after its own inventory sold) — both idempotent, both take
`--dry-run`.

## Other Docker containers on this host

Not part of this project; listed here only so nobody mistakes them for it during
future audits: `personal-security-centre`, `tcg-sniper`, `trading-dashboard`,
`grocery-optimiser`, `up-bank-stats`, `lister`, `macro-mates`, `hub`, plus
`portainer`/`diun`/`uptime-kuma` for fleet management. This app is not one of them.
