"""A live feed of sheets as they're scanned.

The gap this fills is the pause between sheets: you put nine cards on the
glass, press the button, and then have no idea whether it worked or whether
it's safe to load the next nine. The log answers that, eventually, if you
go looking. This shows it -- the scan itself, what came off it, and one
plain statement of whether the scanner is free.

Read-only. Sheet outcomes are parsed from the same log the status page
reads; images come from the archived sheet and the per-sheet crops the
detector already wrote.
"""

import time
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from .auth import require_dashboard_user
from .config import get_settings
from .scan_ingest_status import _parse_sheets, _service_state, _tail_log
from .ui import brand_header, page, pill

router = APIRouter(prefix="/feed", tags=["feed"])

# Enough recent sheets to cover a sitting without turning the page into a
# whole session's history.
SHEETS_SHOWN = 8

# A sheet still in the watch folder after this long isn't being worked on --
# it's one the service looked at and left behind (an unmatched set, a blank
# scan). Counting those as "busy" would pin the feed to "wait" forever, which
# is exactly the question it exists to answer. Processing a sheet takes well
# under a minute.
STUCK_AFTER_SECONDS = 240
SHEET_THUMB_WIDTH = 420   # an archived sheet is ~70MB; this is ~40KB
CROP_THUMB_WIDTH = 130
THUMB_QUALITY = 78


def _thumb(source: Path, cache_name: str, width: int) -> FileResponse:
    """Resize once and cache. An archived sheet is a ~70MB PNG -- serving
    those raw would make the feed unusable on a phone, which is the one
    place it's actually read."""
    cache_dir = Path(get_settings().scan_media_dir) / "thumbs" / "feed"
    cache_dir.mkdir(parents=True, exist_ok=True)
    thumb = cache_dir / cache_name
    if not thumb.exists() or thumb.stat().st_mtime < source.stat().st_mtime:
        try:
            from PIL import Image

            with Image.open(source) as image:
                image = image.convert("RGB")
                height = round(image.height * (width / image.width))
                image.resize((width, height), Image.LANCZOS).save(
                    thumb, "JPEG", quality=THUMB_QUALITY, optimize=True
                )
        except Exception:
            return FileResponse(source, headers={"Cache-Control": "public, max-age=3600"})
    return FileResponse(thumb, headers={"Cache-Control": "public, max-age=604800"})


def _safe_stem(stem: str) -> str:
    """Sheet names come from the URL, so anything that isn't one is refused
    rather than used to walk the filesystem."""
    if not stem.startswith("sheet-") or "/" in stem or ".." in stem:
        raise HTTPException(status_code=404, detail="unknown sheet")
    return stem


def _archived_sheet(stem: str) -> Path | None:
    archive = Path(get_settings().scan_archive_dir)
    return next(archive.rglob(f"{stem}*.png"), None)


def _sheet_crops(stem: str) -> list[Path]:
    """The card crops the detector wrote for this sheet, in reading order."""
    work = Path(get_settings().scan_media_dir) / "work"
    directory = next((d for d in work.glob(f"*/{stem}") if d.is_dir()), None)
    if directory is None:
        return []
    return sorted(
        directory.glob("card*.jpg"),
        key=lambda p: int("".join(c for c in p.stem if c.isdigit()) or 0),
    )


def _watch_state() -> tuple[int, int]:
    """(being processed, stuck) sheets in the watch folder."""
    watch = Path(get_settings().scan_watch_dir)
    if not watch.exists():
        return 0, 0
    now = time.time()
    processing = stuck = 0
    for sheet in watch.glob("*.png"):
        try:
            age = now - sheet.stat().st_mtime
        except OSError:
            continue
        if age > STUCK_AFTER_SECONDS:
            stuck += 1
        else:
            processing += 1
    return processing, stuck


def _readiness() -> tuple[str, str, str]:
    """(pill kind, headline, detail) -- the answer to "can I scan now?"."""
    if _service_state() != "active":
        return "crit", "Scanner service is not running", \
            "Nothing will be processed until it's started again."

    processing, stuck = _watch_state()
    stuck_note = (
        f" {stuck} earlier sheet{'s' if stuck != 1 else ''} couldn't be matched and "
        f"{'are' if stuck != 1 else 'is'} waiting on you -- that doesn't block scanning."
        if stuck else ""
    )
    if processing:
        return "warn", f"Working through {processing} sheet{'s' if processing != 1 else ''}", \
            "Wait for this to finish before scanning the next nine." + stuck_note
    return "ok", "Ready — scan the next nine", \
        "Nothing being processed. Put the next cards on the glass and press the button." + stuck_note


@router.get("", response_class=HTMLResponse)
def scan_feed(
    live: str = Query("1"),
    user: str = Depends(require_dashboard_user),
) -> HTMLResponse:
    settings = get_settings()
    kind, headline, detail = _readiness()
    _, sheets = _parse_sheets(_tail_log(Path(settings.scan_log_file)))

    cards = []
    for sheet in sheets[:SHEETS_SHOWN]:
        stem = Path(sheet.name).stem
        has_sheet = _archived_sheet(stem) is not None
        crops = _sheet_crops(stem)

        status_kind = "ok" if sheet.committed else ("crit" if sheet.status.startswith("skipped") else "warn")
        strip = "".join(
            f"<img class='crop' src='/feed/crop/{escape(stem)}/{index}' alt='' loading='lazy' decoding='async'>"
            for index in range(1, len(crops) + 1)
        )
        card_kind_class = {"ok": "", "warn": "card-warn", "bad": "card-bad"}
        card_list = "".join(
            f"<li class='{card_kind_class[c.kind]}'>{escape(str(c.index))}. {escape(c.label)}</li>"
            for c in sheet.cards
        )
        cards.append(
            "<div class='sheet'>"
            + (f"<a class='sheet-img' href='/feed/sheet/{escape(stem)}' target='_blank' rel='noopener'>"
               f"<img src='/feed/sheet/{escape(stem)}' alt='' loading='lazy' decoding='async'></a>"
               if has_sheet else "<div class='sheet-img missing'>sheet not archived</div>")
            + "<div class='sheet-body'>"
            + f"<div class='sheet-when'>{escape(sheet.when or stem)}</div>"
            + f"<div class='sheet-status'>{pill(escape(sheet.status), status_kind)}</div>"
            + f"<div class='sheet-detail'>{sheet.detected} card(s) detected ({escape(sheet.grid)})</div>"
            + (f"<ul class='card-list'>{card_list}</ul>" if card_list else "")
            + (f"<div class='crops'>{strip}</div>" if strip else "")
            + "</div></div>"
        )

    is_live = live != "0"
    toggle = f"/feed?live={'0' if is_live else '1'}"
    head_extra = "<meta http-equiv='refresh' content='5'>" if is_live else ""

    body = (
        brand_header("Scan feed")
        + f"<div class='ready {kind}'><div class='ready-head'>{pill(escape(headline), kind)}</div>"
          f"<div class='ready-detail'>{escape(detail)}</div></div>"
        + "<div class='toolbar'>"
        + f"<a class='chip' href='{toggle}'>{'Pause' if is_live else 'Resume'} auto-refresh</a>"
        + "<a class='chip' href='/review'>Review queue</a>"
        + "<a class='chip' href='/inventory'>Inventory</a>"
        + "</div>"
        + (f"<div class='sheets'>{''.join(cards)}</div>" if cards
           else "<div class='panel'><p>No sheets scanned yet in this run.</p></div>")
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Scan feed", body, head_extra=head_extra))


@router.get("/sheet/{stem}")
def feed_sheet(stem: str, user: str = Depends(require_dashboard_user)):
    source = _archived_sheet(_safe_stem(stem))
    if source is None:
        raise HTTPException(status_code=404, detail="sheet not archived")
    return _thumb(source, f"{stem}-sheet-{SHEET_THUMB_WIDTH}.jpg", SHEET_THUMB_WIDTH)


@router.get("/crop/{stem}/{index}")
def feed_crop(stem: str, index: int, user: str = Depends(require_dashboard_user)):
    crops = _sheet_crops(_safe_stem(stem))
    if index < 1 or index > len(crops):
        raise HTTPException(status_code=404, detail="no such card on that sheet")
    return _thumb(crops[index - 1], f"{stem}-card{index}-{CROP_THUMB_WIDTH}.jpg", CROP_THUMB_WIDTH)


_STYLE = """<style>
.ready{border-radius:14px;padding:16px 18px;margin:0 0 18px;border:1px solid var(--panel-border);
  background:var(--panel)}
.ready.ok{border-color:var(--success)}
.ready.warn{border-color:rgba(212,165,39,.35)}
.ready.crit{border-color:var(--danger)}
.ready-head{font-size:16px}
.ready-detail{font-size:13px;color:var(--text-dim);margin-top:7px}

.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 18px}
.chip{display:inline-flex;align-items:center;padding:9px 15px;border-radius:999px;font-size:13.5px;
  border:1px solid var(--panel-border);color:var(--text-dim);text-decoration:none;background:var(--surface-sunken)}
.chip:hover{border-color:var(--accent);color:var(--accent)}

.sheets{display:flex;flex-direction:column;gap:14px}
.sheet{display:flex;gap:14px;background:var(--panel);border:1px solid var(--panel-border);
  border-radius:14px;padding:12px;min-width:0}
.sheet>*{min-width:0}
.sheet-img{flex:0 0 132px;display:block;border-radius:9px;overflow:hidden;background:var(--surface-sunken)}
.sheet-img img{width:100%;display:block}
.sheet-img.missing{display:grid;place-items:center;color:var(--text-dim);font-size:11px;
  height:120px;text-align:center;padding:6px}
.sheet-body{flex:1 1 auto;min-width:0}
.sheet-when{font-size:14px;font-weight:600}
.sheet-status{margin:6px 0}
.sheet-detail{font-size:12.5px;color:var(--text-dim)}
.card-list{list-style:none;margin:8px 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));
  gap:2px 12px;font-size:12.5px;color:var(--text)}
.card-list li{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card-list li.card-warn{color:var(--accent)}
.card-list li.card-bad{color:var(--danger)}
.crops{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.crops .crop{width:52px;border-radius:5px;display:block;background:var(--surface-sunken)}

@media (max-width:560px){
  .sheet{flex-direction:column}
  .sheet-img{flex:none;max-width:220px}
  .crops .crop{width:46px}
}
</style>"""
