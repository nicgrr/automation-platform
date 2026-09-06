"""Read-only view of the background scan-ingest service: what it's doing
right now and its recent log output, so checking on it doesn't require SSH.
This never controls the service (see automation_control/scan_ingest/session.py
for the actual processing) -- it only reads the DB and tails a log file.
"""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .config import get_settings
from .database import get_session
from .models import CardSet, ScanSession, ScanSessionStatus
from .ui import brand_header, page, pill

router = APIRouter(prefix="/scan-ingest", tags=["scan-ingest"])

SERVICE_NAME = "scan-ingest.service"
LOG_TAIL_LINES = 200
LOG_TAIL_BYTES = 200_000  # read at most this much from the end of the log


def _service_state() -> str:
    """`systemctl is-active` is a read-only status query -- no special
    privileges needed, this app has none for controlling the unit."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _tail_log(path: Path) -> str:
    if not path.exists():
        return "(no log yet)"
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - LOG_TAIL_BYTES))
        chunk = f.read().decode("utf-8", errors="replace")
    lines = chunk.splitlines()[-LOG_TAIL_LINES:]
    return "\n".join(lines) or "(log is empty)"


SHEET_HEADER_RE = re.compile(r"^Sheet (\S+): (\d+) card\(s\) detected \((\d+)x(\d+)\)$")
SHEET_DONE_RE = re.compile(r"^Sheet done: (\d+) committed, (\d+) skipped, (\d+) set aside for review\.$")
SHEET_TIMESTAMP_RE = re.compile(r"(\d{8})-(\d{6})")

# Each committed/reviewed card logs two lines under the same "Card N:"
# prefix -- a foil-shadow assessment, then the actual identification --
# distinguished only by what follows the prefix, so the committed pattern
# excludes both that and the geometry-flagged variant, which shares the
# prefix but never carries a bracketed variant.
CARD_COMMITTED_RE = re.compile(r"^Card (\d+): (?!foil shadow:)(.+?) \[(\w+)\] (added|incremented to x\d+)")
CARD_REVIEW_RE = re.compile(r"^Card (\d+): (.+?) needs confirmation")
CARD_NOT_SHAPED_RE = re.compile(r"^Card (\d+): not card-shaped")


@dataclass
class CardLogEntry:
    index: int
    label: str
    kind: str  # "ok" | "warn" | "bad"


@dataclass
class SheetLogEntry:
    name: str
    detected: int
    grid: str
    committed: int | None = None
    skipped: int | None = None
    set_aside: int | None = None
    lines: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.committed is None:
            return "processing / interrupted"
        if self.committed == 0 and self.skipped and not self.set_aside:
            return "skipped -- needs rescan"
        return f"{self.committed} committed, {self.skipped} skipped, {self.set_aside} for review"

    @property
    def when(self) -> str:
        match = SHEET_TIMESTAMP_RE.search(self.name)
        if not match:
            return ""
        try:
            return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return ""

    @property
    def cards(self) -> list[CardLogEntry]:
        """One entry per card actually named in the log for this sheet, in
        reading order -- what the feed shows instead of just a bare count."""
        entries: dict[int, CardLogEntry] = {}
        for line in self.lines:
            committed = CARD_COMMITTED_RE.match(line)
            if committed:
                index = int(committed.group(1))
                entries[index] = CardLogEntry(index, f"{committed.group(2)} ({committed.group(3)})", "ok")
                continue
            not_shaped = CARD_NOT_SHAPED_RE.match(line)
            if not_shaped:
                index = int(not_shaped.group(1))
                entries.setdefault(index, CardLogEntry(index, "not card-shaped", "bad"))
                continue
            review = CARD_REVIEW_RE.match(line)
            if review:
                index = int(review.group(1))
                entries[index] = CardLogEntry(index, review.group(2), "warn")
        return [entries[i] for i in sorted(entries)]


def _parse_sheets(log_text: str) -> tuple[list[str], list[SheetLogEntry]]:
    """Group the raw log into one block per sheet, newest first, so the
    status page can show a scannable one-line summary per sheet instead of
    a wall of per-card text. Falls back gracefully -- a line that doesn't
    match the expected shape just gets kept as body text on the current
    sheet (or the preamble, before any sheet has started)."""
    preamble: list[str] = []
    sheets: list[SheetLogEntry] = []
    current: SheetLogEntry | None = None

    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        header = SHEET_HEADER_RE.match(line)
        if header:
            current = SheetLogEntry(name=header.group(1), detected=int(header.group(2)), grid=f"{header.group(3)}x{header.group(4)}")
            sheets.append(current)
            continue
        if current is None:
            if line:
                preamble.append(line)
            continue
        done = SHEET_DONE_RE.match(line)
        if done:
            current.committed, current.skipped, current.set_aside = int(done.group(1)), int(done.group(2)), int(done.group(3))
        if line:
            current.lines.append(line)

    sheets.reverse()
    return preamble, sheets


def _needs_review_count(media_dir: Path) -> int:
    review_dir = media_dir / "needs_review"
    if not review_dir.exists():
        return 0
    return sum(1 for p in review_dir.iterdir() if p.is_file())


@router.get("", response_class=HTMLResponse)
def scan_ingest_status(
    request: Request,
    live: str = Query("1"),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    settings = get_settings()
    state = _service_state()
    state_kind = "ok" if state == "active" else "bad"

    running_sessions = list(
        session.scalars(
            select(ScanSession)
            .where(ScanSession.status == ScanSessionStatus.RUNNING)
            .order_by(ScanSession.started_at.desc())
        )
    )
    set_names = {
        s.id: s.name for s in session.scalars(
            select(CardSet).where(CardSet.id.in_({rs.set_id for rs in running_sessions}))
        )
    } if running_sessions else {}

    review_count = _needs_review_count(Path(settings.scan_media_dir))

    stat_grid = (
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>Service</div><div class='value'>{pill(escape(state), state_kind)}</div></div>"
        f"<div class='stat-card'><div class='label'>Running sessions</div><div class='value'>{len(running_sessions)}</div></div>"
        f"<div class='stat-card'><div class='label'>Needs review</div><div class='value'>{review_count}</div></div>"
        "</div>"
    )

    if running_sessions:
        rows = "".join(
            "<tr>"
            f"<td>{escape(set_names.get(rs.set_id, rs.set_id))}</td>"
            f"<td>{escape(rs.batch_variant.value)}</td>"
            f"<td>{rs.sheets_scanned}</td>"
            f"<td>{rs.cards_committed}</td>"
            f"<td>{rs.started_at.strftime('%Y-%m-%d %H:%M') if rs.started_at else '?'}</td>"
            "</tr>"
            for rs in running_sessions
        )
        sessions_table = (
            "<div class='table-wrap'><table><thead><tr>"
            "<th>Set</th><th>Variant</th><th>Sheets</th><th>Cards committed</th><th>Started</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        )
    else:
        sessions_table = "<p>No session currently running.</p>"

    review_note = (
        f"<p>{review_count} card(s) need a manual look: "
        f"<code>{escape(settings.scan_media_dir)}/needs_review/</code></p>"
        if review_count else ""
    )

    log_text = _tail_log(Path(settings.scan_log_file))
    preamble, sheets = _parse_sheets(log_text)
    is_live = live != "0"
    toggle_href = f"/scan-ingest?live={'0' if is_live else '1'}"
    toggle_label = "Pause auto-refresh" if is_live else "Resume auto-refresh"
    head_extra = "<meta http-equiv='refresh' content='5'>" if is_live else ""

    preamble_html = (
        f"<p style='color:var(--text-dim);font-size:13px'>{escape(' &middot; '.join(preamble))}</p>" if preamble else ""
    )

    sheet_blocks = []
    for index, sheet in enumerate(sheets):
        status_kind = "ok" if sheet.committed else ("bad" if sheet.status.startswith("skipped") else "neutral")
        summary = (
            f"{escape(sheet.when or sheet.name)} &mdash; {sheet.detected} detected ({escape(sheet.grid)}) &mdash; "
            f"{pill(escape(sheet.status), status_kind)}"
        )
        detail = "\n".join(sheet.lines)
        sheet_blocks.append(
            f"<details{' open' if index == 0 else ''}><summary>{summary}</summary>"
            f"<pre style='white-space:pre-wrap;font-size:12px;color:var(--text-dim);margin:10px 0 0'>{escape(detail)}</pre></details>"
        )
    sheets_html = "".join(sheet_blocks) or "<p>No sheets processed yet this run.</p>"

    body = (
        brand_header("Scan ingest status")
        + "<p class='subtitle'>Live view of the background card-scanning service -- no SSH needed.</p>"
        + stat_grid
        + f"<div class='panel'><h2>Active sessions</h2>{sessions_table}{review_note}</div>"
        + "<div class='panel'><h2>Sheets processed</h2>"
        + f"<p><a class='btn' href='{toggle_href}'>{toggle_label}</a></p>"
        + preamble_html
        + f"<div style='display:flex;flex-direction:column;gap:8px'>{sheets_html}</div>"
        + "</div>"
        + "<div class='panel'><h3>Raw log</h3>"
        + f"<pre style='white-space:pre-wrap;max-height:400px;overflow-y:auto;font-size:12px;color:var(--text-dim);margin:0'>{escape(log_text)}</pre>"
        + "</div>"
    )
    return HTMLResponse(page("EzBay — Scan Ingest", body, head_extra=head_extra))
