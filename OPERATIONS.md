# Operations

## Services (systemd, not Docker — see `ARCHITECTURE.md`)

| Unit | What it does |
|---|---|
| `ezbay.service` | The web app (`automation_control.api:app`) on `127.0.0.1:8080`, via Tailscale as `homeserver.tailefaa51.ts.net` |
| `scan-ingest.service` | Watches for scanned sheets, detects/identifies/commits cards continuously |
| `scanbd.service` | Polls the flatbed scanner's button; known to leak memory and occasionally misfire — see `scanbd-restart.timer` |

```bash
sudo systemctl status ezbay scan-ingest scanbd
sudo journalctl -u ezbay -f
```

## Timers

| Timer | Schedule | What |
|---|---|---|
| `inventory-audit.timer` | hourly, 22:00→14:00 | Photo-identity audit (read-only) |
| `foil-refresh.timer` | hourly, 22:00→14:00 | Refreshes `/foil-review` candidates (read-only) |
| `sheet-check.timer` | daily, 20:30 | Reports stuck sheets in `watch/` (read-only) |
| `scanbd-restart.timer` | daily, 14:00 | Restarts `scanbd` pre-emptively |
| `pokemonpricetracker-backfill.timer` | daily, 12:00 | Prices already-scanned inventory via the paid API — **rate-limit-sensitive, see below** |
| `restic-backup.timer` | daily, 03:00 | Whole-filesystem encrypted backup to OneDrive (pre-existing, not built by this project) |

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

## Deploying a code change

```bash
cd /home/nic/automation-platform
git pull                                      # or: apply the local commit
.venv/bin/python -m pytest -q                 # must be green before restarting
sudo systemctl restart ezbay                  # for API/dashboard changes
sudo systemctl restart scan-ingest            # for scan_ingest pipeline changes
```

Always run the test suite before restarting a live service — this has been the
working pattern all of tonight's session and caught real regressions before deploy.

## Running a migration script

```bash
.venv/bin/python -m scripts.migrate_<name> --dry-run   # review first
.venv/bin/python -m scripts.migrate_<name>              # then commit
```
Take a manual backup first (above) regardless of what the nightly restic run last did.

## Rate-limit-sensitive: pokemonpricetracker.com

Free tier: 100 credits/day, 3 credits/search, 60 requests/minute. As of
2026-09-07 the client paces every request and fails fast for the remainder of any
detected block (see the `fix(pricing)` commit) — but the daily credit ceiling is
still real. If `pokemonpricetracker-backfill.timer` needs pausing again:
```bash
sudo systemctl disable --now pokemonpricetracker-backfill.timer
sudo systemctl enable --now pokemonpricetracker-backfill.timer   # to resume
```

## Other Docker containers on this host

Not part of this project; listed here only so nobody mistakes them for it during
future audits: `personal-security-centre`, `tcg-sniper`, `trading-dashboard`,
`grocery-optimiser`, `up-bank-stats`, `lister`, `macro-mates`, `hub`, plus
`portainer`/`diun`/`uptime-kuma` for fleet management. This app is not one of them.
