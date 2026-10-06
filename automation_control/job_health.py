"""Whether the background jobs are actually running -- Module 26.

This exists because of a specific, expensive silence. The nightly
pokemonpricetracker price backfill failed at noon every day from 8
September to 13 September on a TypeError. It was written to
`scan_ingest_data/logs/pokemonpricetracker-backfill.log` **six times, once
per day**, and nothing in the app ever read that file. Six days of prices
went missing and the only symptom was a number on a page that nobody had
reason to distrust.

So this is deliberately not a log viewer -- `/scan-ingest` already tails
one. It answers a narrower question on the page you land on: is anything
that should be running not running?

Two states matter and only one of them is "failed":

  * **failed** -- it ran and exited non-zero.
  * **overdue** -- its timer's next firing is in the past, or there is no
    next firing at all. A timer that has been stopped, masked or never
    enabled reports perfectly healthy on every other measure, because
    nothing failed; nothing happened.

`Result` is not sufficient on its own. `systemctl reset-failed` clears it
back to "success" while `ExecMainStatus` still holds the non-zero exit --
which is exactly the state this module was first written against, and
would have reported a green tick over a job that failed hours earlier.
Both are checked.

Read-only throughout, the same way scan_ingest_status.py already queries
`systemctl is-active`. Nothing here can start, stop or restart anything.
"""

import json
import subprocess
from urllib import request as urllib_request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

OK = "ok"
FAILED = "failed"
OVERDUE = "overdue"
RUNNING = "running"
UNKNOWN = "unknown"

# How late a timer may be before it counts as overdue. Generous, because
# OnCalendar firings drift with RandomizedDelaySec and a machine that was
# asleep catches up via Persistent=true rather than being broken.
OVERDUE_GRACE = timedelta(hours=6)

QUERY_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class Job:
    unit: str          # the .service unit
    timer: str | None  # its .timer, when it's scheduled rather than always-on
    label: str
    what: str          # what breaks, in the reader's terms, when this stops
    health_url: str | None = None


JOBS = (
    Job("ebay-price-import.service", "ebay-price-import.timer", "eBay market prices",
        "listing prices are built from these, so they go stale silently"),
    Job("pokemonpricetracker-backfill.service", "pokemonpricetracker-backfill.timer",
        "Card price backfill", "held cards stop getting a market value"),
    Job("tcgcsv-pricing-sync.service", "tcgcsv-pricing-sync.timer", "TCGCSV bulk prices",
        "the free local pricing baseline stops refreshing and external quota use rises"),
    Job("ebay-listings-refresh.service", "ebay-listings-refresh.timer", "eBay listings cache",
        "the dashboard widget and /settings' listing count go stale"),
    Job("market-watch-scan.service", "market-watch-scan.timer", "Collectible market watches",
        "new Australia/Japan marketplace finds and alert emails stop arriving"),
    Job("ebay-order-import.service", "ebay-order-import.timer", "eBay orders",
        "sales stop coming back into stock"),
    Job("scan-ingest.service", None, "Scan ingest",
        "scanned sheets pile up in the watch folder"),
    Job("scanbd.service", None, "Scanner button",
        "pressing the physical scanner button no longer starts a scan"),
    Job("scanbd-restart.service", "scanbd-restart.timer", "Scanner recovery",
        "scanbd's known memory-leak and duplicate-trigger failure is no longer cleared daily"),
    Job("inventory-audit.service", "inventory-audit.timer", "Inventory photo audit",
        "wrong-card photos stop being detected before stock is listed"),
    Job("ezbay-reconciliation-audit.service", "ezbay-reconciliation-audit.timer",
        "Inventory-vs-listings audit", "inventory/listing drift (a card sold but still "
        "listed, or vice versa) goes unnoticed until someone spots it by hand"),
    Job("restic-backup.service", "restic-backup.timer", "Encrypted backup",
        "the platform and its database are no longer being copied to off-host storage"),
    Job("restic-check-monthly.service", "restic-check-monthly.timer", "Backup integrity check",
        "backup repository corruption can remain undetected"),
    Job("restic-maintenance.service", "restic-maintenance.timer", "Backup maintenance",
        "backup retention and repository maintenance stop running"),
    Job("ezbay-restore-drill.service", "ezbay-restore-drill.timer", "Restore drill",
        "backup snapshots stop being proven readable and bootable"),
    Job("nl-vault.service", None, "NL Vault storefront",
        "the shop is unavailable even if its Tailscale proxy is still listening",
        "http://127.0.0.1:8090/health"),
)


@dataclass
class JobStatus:
    job: Job
    state: str
    last_run: datetime | None = None
    next_run: datetime | None = None
    exit_code: int | None = None
    detail: str = ""

    @property
    def needs_attention(self) -> bool:
        # UNKNOWN must not collapse into a green "all healthy" banner. It
        # means the dashboard could not establish whether the job is healthy
        # (missing/renamed unit, unavailable systemd, or a timed-out query).
        return self.state in (FAILED, OVERDUE, UNKNOWN)


def _show(unit: str, properties: list[str]) -> dict[str, str]:
    """`systemctl show` for one unit. Missing systemd, a missing unit and a
    hung query all come back as "nothing known" rather than an exception --
    a status panel must never be the thing that takes the page down."""
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, *(f"-p{name}" for name in properties)],
            capture_output=True, text=True, timeout=QUERY_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    values = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key and value:
            values[key] = value
    return values


def _parse_timestamp(value: str | None) -> datetime | None:
    """systemd prints "Mon 2026-09-14 03:17:22 AEST". The zone abbreviation
    is not parseable across machines, so it's dropped and the result is
    treated as local time -- which is what the reader is comparing against
    anyway."""
    if not value or value in ("n/a", "0"):
        return None
    parts = value.split()
    if len(parts) < 3:
        return None
    try:
        return datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def status_for(job: Job, now: datetime | None = None) -> JobStatus:
    now = now or datetime.now()
    service = _show(job.unit, ["Result", "ActiveState", "ExecMainStatus", "ExecMainExitTimestamp"])
    if not service:
        return JobStatus(job, UNKNOWN, detail="could not read this unit's state")

    exit_code = service.get("ExecMainStatus")
    exit_code = int(exit_code) if (exit_code or "").lstrip("-").isdigit() else None
    last_run = _parse_timestamp(service.get("ExecMainExitTimestamp"))
    active = service.get("ActiveState", "")

    next_run = None
    if job.timer:
        timer = _show(job.timer, ["NextElapseUSecRealtime", "LastTriggerUSec"])
        next_run = _parse_timestamp(timer.get("NextElapseUSecRealtime"))
        last_run = last_run or _parse_timestamp(timer.get("LastTriggerUSec"))

    # Order matters. A job that is mid-run reports its *previous* exit code,
    # so checking "running" first stops a long job that failed yesterday
    # from being reported as failing right now while it's busy succeeding.
    if active in ("activating", "active") and job.timer:
        return JobStatus(job, RUNNING, last_run, next_run, exit_code, "running now")

    # Both, because reset-failed clears Result while ExecMainStatus keeps
    # the non-zero exit.
    if exit_code not in (None, 0) or service.get("Result") not in (None, "success"):
        result = service.get("Result")
        if exit_code not in (None, 0):
            # Don't print Result here: after a reset-failed it says
            # "success" next to a non-zero exit, which reads as a bug in
            # this panel rather than a job that failed.
            detail = f"last run exited {exit_code}"
        else:
            detail = f"last run failed ({result})"
        return JobStatus(job, FAILED, last_run, next_run, exit_code, detail)

    if job.timer:
        if next_run is None:
            return JobStatus(job, OVERDUE, last_run, None, exit_code,
                             "no next run scheduled -- the timer is stopped or disabled")
        if next_run < now - OVERDUE_GRACE:
            late = now - next_run
            return JobStatus(job, OVERDUE, last_run, next_run, exit_code,
                             f"should have run {_ago(late)} ago and hasn't")
    elif active != "active":
        return JobStatus(job, FAILED, last_run, None, exit_code, f"not running ({active or 'unknown'})")

    if job.health_url:
        problem = _http_problem(job.health_url)
        if problem:
            return JobStatus(job, FAILED, last_run, next_run, exit_code, problem)

    return JobStatus(job, OK, last_run, next_run, exit_code)


def _http_problem(url: str) -> str | None:
    """Return a dashboard-safe problem, or None for a healthy JSON probe."""
    try:
        with urllib_request.urlopen(url, timeout=QUERY_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return f"health check returned HTTP {response.status}"
            payload = json.loads(response.read(1024))
            if payload.get("status") != "ok":
                return "health check did not report ok"
    except Exception:
        return "health check could not reach the service"
    return None


def _ago(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    if hours >= 48:
        return f"{hours // 24} days"
    return f"{hours} hours" if hours else "under an hour"


def all_statuses(now: datetime | None = None) -> list[JobStatus]:
    return [status_for(job, now) for job in JOBS]
