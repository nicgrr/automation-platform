"""Printable 4x6in (100x150mm) buyer address labels for sold orders."""

import base64
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from html import escape
from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import EbayImportedOrder, ShopOrder

router = APIRouter(tags=["labels"])

_HOME_COUNTRIES = {"", "au", "aus", "australia"}
# Audit action marking an eBay order's address label as printed; the pick
# run's "Print all labels" skips any order that already has one.
LABEL_PRINTED_ACTION = "label.print_ebay"

PRINTER_QUEUE = os.environ.get("LABEL_PRINTER_QUEUE", "HotLabel")
DPI = 203
LABEL_W, LABEL_H = 800, 1200  # 100x150mm at 203 dpi; width is a multiple of 8
SENDER_NAME = "NL Vault"
WEBSITE = "www.nlvault.com.au"
_ASSETS = Path(__file__).resolve().parent / "label_assets"
_FONT_REGULAR = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
_FONT_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"


def ebay_label_lines(snapshot: dict) -> list[str]:
    steps = snapshot.get("fulfillmentStartInstructions") or []
    ship_to = ((steps[0].get("shippingStep") or {}).get("shipTo") if steps else None) or {}
    address = ship_to.get("contactAddress") or {}
    buyer = snapshot.get("buyer") or {}
    phone = ((ship_to.get("primaryPhone") or {}).get("phoneNumber") or
             (((buyer.get("buyerRegistrationAddress") or {}).get("primaryPhone") or {}).get("phoneNumber")) or "")
    locality = " ".join(
        v for v in (address.get("city"), address.get("stateOrProvince"), address.get("postalCode")) if v
    )
    country = (address.get("countryCode") or "").strip()
    lines = [
        ship_to.get("fullName") or (snapshot.get("buyer") or {}).get("username") or "",
        address.get("addressLine1") or "",
        address.get("addressLine2") or "",
        locality,
        "" if country.lower() in _HOME_COUNTRIES else country,
        phone,
    ]
    # eBay puts its own reference ("ebay:t27v2mj") in addressLine1; it isn't part of the address.
    return [line.strip() for line in lines if line and line.strip() and not line.strip().lower().startswith("ebay:")]


def shop_order_label_lines(order: ShopOrder) -> list[str]:
    country = (order.ship_to_country or "").strip()
    lines = [order.ship_to_name, *(order.ship_to_address or "").splitlines(),
             "" if country.lower() in _HOME_COUNTRIES else country,
             order.ship_to_phone or ""]
    return [line.strip() for line in lines if line and line.strip()]


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_FONT_BOLD if bold else _FONT_REGULAR, size)


def _split_long_word(draw: ImageDraw.ImageDraw, word: str, font, max_width: int) -> list[str]:
    """Break a word wider than the label, so no part of a name or address is cut off."""
    parts, part = [], ""
    for char in word:
        if part and draw.textlength(part + char, font=font) > max_width:
            parts.append(part)
            part = ""
        part += char
    return parts + [part]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    out: list[str] = []
    line = ""
    for word in text.split():
        pieces = _split_long_word(draw, word, font, max_width)
        for piece in pieces[:-1]:
            if line:
                out.append(line)
                line = ""
            out.append(piece)
        word = pieces[-1]
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            out.append(line)
            line = word
    return out + ([line] if line else [])


def _draw_header(draw: ImageDraw.ImageDraw, x: int, y: int, right: int) -> None:
    label, sender = _font(True, 30), _font(True, 36)
    draw.text((x, y + 4), "FROM:", font=label, fill=0)
    x += int(draw.textlength("FROM:", font=label)) + 14
    draw.text((x, y), SENDER_NAME, font=sender, fill=0)
    site = _font(True, 28)
    site_x = right - int(draw.textlength(WEBSITE, font=site))
    # Keep the website slightly lower than the sender name.  On the physical
    # label this gives the URL the same balanced baseline as the globe instead
    # of making the right side of the header look top-heavy.
    site_y = y + 14
    draw.text((site_x, site_y), WEBSITE, font=site, fill=0)
    # Globe, then the divider between sender and website.
    cx, cy, r = site_x - 34, y + 30, 21
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=0, width=3)
    draw.ellipse((cx - 9, cy - r, cx + 9, cy + r), outline=0, width=2)
    draw.line((cx - r, cy, cx + r, cy), fill=0, width=2)
    divider = cx - r - 22
    draw.line((divider, y - 4, divider, y + 48), fill=0, width=4)


def render_landscape_label(lines: list[str]) -> Image.Image:
    """The branded address label, 1200x800 and greyscale, before it is thresholded."""
    name, rest = (lines[0], lines[1:]) if lines else ("", [])
    panel = Image.open(_ASSETS / "panel.png").convert("L")
    swoosh = Image.open(_ASSETS / "swoosh.png").convert("L")
    left = panel.width + 36
    right = LABEL_H - 30
    width = right - left
    for scale in (1.0, 0.85, 0.72, 0.6, 0.5, 0.42):
        image = Image.new("L", (LABEL_H, LABEL_W), 255)  # landscape: 1200 x 800
        image.paste(panel, (0, 0))
        draw = ImageDraw.Draw(image)
        _draw_header(draw, left, 40, right)
        draw.line((left, 112, right, 112), fill=0, width=4)
        draw.text((left, 134), "TO:", font=_font(True, 34), fill=0)
        y = 186
        rows, sources = [], []
        for text, bold, size in [(name, True, int(72 * scale)), *[(line, False, int(56 * scale)) for line in rest]]:
            font = _font(bold, size)
            sources.append((text, font))
            for row in _wrap(draw, text, font, width):
                rows.append((row, font, y))
                y += int(size * 1.28)
            if bold:
                y += 12
        words_fit = all(draw.textlength(word, font=font) <= width
                        for text, font in sources for word in text.split())
        if y <= LABEL_W - 24 and words_fit:
            break
    # The swoosh is decoration, so it only appears where it can't touch the address.
    swoosh_top = LABEL_W - swoosh.height - 6
    if y <= swoosh_top:
        # Dithered on its own so it prints as a tone while the address stays crisp.
        image.paste(swoosh.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L"),
                    (right - swoosh.width, swoosh_top))
    for row, font, row_y in rows:
        draw.text((left, row_y), row, font=font, fill=0)
    return image


def render_label_image(lines: list[str]) -> Image.Image:
    """The label as a 1-bit 800x1200 bitmap, black on white.

    The label is laid out landscape (150mm wide x 100mm tall) and then turned
    90 degrees, because the stock feeds through the printer 100mm wide.
    """
    image = render_landscape_label(lines).rotate(90, expand=True)
    return image.point(lambda p: 255 if p >= 128 else 0).convert("1", dither=Image.Dither.NONE)


def tspl_for_image(image: Image.Image) -> bytes:
    """TSPL2 job (HotLabel HL-M9 speaks TSPL2) printing the image as one 100x150mm label."""
    mono = image.convert("1")
    width_bytes = (mono.width + 7) // 8
    header = (
        f"SIZE 100 mm,150 mm\r\nGAP 3 mm,0 mm\r\nDIRECTION 0\r\nCLS\r\n"
        f"BITMAP 0,0,{width_bytes},{mono.height},0,"
    ).encode()
    return header + mono.tobytes() + b"\r\nPRINT 1,1\r\n"


MAX_UPLOAD = 8 * 1024 * 1024


def postage_label_image(data: bytes) -> Image.Image:
    """An uploaded eBay/carrier postage label (PDF or image) fitted onto the 100x150mm stock.

    Only the first PDF page is used. Surrounding white space (an A4 sheet with
    the label in one corner) is cropped away, a landscape label is turned to
    run along the feed, and the result is scaled to fill the sticker.
    """
    if data[:5] == b"%PDF-":
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.pdf")
            with open(src, "wb") as handle:
                handle.write(data)
            try:
                run = subprocess.run(
                    ["gs", "-q", "-dSAFER", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pnggray", f"-r{DPI}",
                     "-dFirstPage=1", "-dLastPage=1", "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
                     "-sOutputFile=-", src],
                    capture_output=True, timeout=60,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("Ghostscript is not installed on the server") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("the PDF took too long to convert") from exc
        if run.returncode != 0 or not run.stdout:
            raise RuntimeError("that PDF could not be read")
        data = run.stdout
    try:
        page = Image.open(BytesIO(data))
        page.load()
    except Exception as exc:
        raise RuntimeError("that file is not a PDF or an image") from exc
    gray = page.convert("L")
    box = gray.point(lambda p: 255 if p < 245 else 0).getbbox()
    if box is None:
        raise RuntimeError("the label is blank")
    gray = gray.crop(box)
    if gray.width > gray.height:
        gray = gray.rotate(90, expand=True)
    scale = min(LABEL_W / gray.width, LABEL_H / gray.height, 2.0)
    gray = gray.resize((max(1, round(gray.width * scale)), max(1, round(gray.height * scale))), Image.LANCZOS)
    canvas = Image.new("L", (LABEL_W, LABEL_H), 255)
    canvas.paste(gray, ((LABEL_W - gray.width) // 2, (LABEL_H - gray.height) // 2))
    return canvas.point(lambda p: 255 if p >= 160 else 0).convert("1", dither=Image.Dither.NONE)


def send_job(job: bytes) -> None:
    """Send a TSPL job to the CUPS queue as a raw job; raises RuntimeError with a readable reason."""
    try:
        result = subprocess.run(
            ["lp", "-d", PRINTER_QUEUE, "-o", "raw", "-t", "EzBay label"],
            input=job, capture_output=True, timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("CUPS is not installed on the server") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("the print service did not respond") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "the printer queue rejected the job")


def print_label(lines: list[str]) -> None:
    send_job(tspl_for_image(render_label_image(lines)))


LABEL_PRINT_SCRIPT = """<script>
(function(){
  if (window.__labelPrintBound) return; window.__labelPrintBound = true;
  document.addEventListener('click', async function(e){
    var b = e.target.closest('[data-label-print]'); if (!b) return;
    e.preventDefault(); var was = b.textContent; b.disabled = true; b.textContent = 'Printing…';
    try {
      var r = await fetch(b.dataset.labelPrint, {method:'POST', body:new FormData()});
      var j = await r.json().catch(function(){return {};});
      b.textContent = r.ok ? 'Sent to printer' : ('Failed: ' + (j.detail || r.status));
    } catch (err) { b.textContent = 'Failed: ' + err; }
    setTimeout(function(){ b.disabled = false; b.textContent = was; }, 4000);
  });
})();
</script>"""


def label_buttons_html(kind: str, order_id: str, btn_class: str) -> str:
    """Print-to-printer button plus a preview link, for an order row."""
    oid = escape(order_id)
    return (
        f"<button type='button' class='{btn_class}' data-label-print='/labels/{kind}/{oid}/print'>Print label</button>"
        f"<a class='{btn_class}' href='/labels/{kind}/{oid}' target='_blank'>Label preview</a>"
        f"<a class='{btn_class}' href='/labels/postage' target='_blank'>Postage PDF</a>"
        + LABEL_PRINT_SCRIPT
    )


def label_page(lines: list[str], *, auto_print: bool) -> str:
    # The same picture the thermal printer gets, so a browser print can't drift from it.
    buffer = BytesIO()
    render_label_image(lines).rotate(-90, expand=True).save(buffer, "PNG")
    picture = base64.b64encode(buffer.getvalue()).decode()
    script = "<script>window.addEventListener('load',()=>setTimeout(()=>window.print(),250));</script>" if auto_print else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'><title>Postage label</title>"
        "<style>"
        "@page{size:150mm 100mm;margin:0}"
        "html,body{margin:0;background:#fff;color:#000;font-family:Arial,Helvetica,sans-serif}"
        ".label{width:150mm;height:100mm;overflow:hidden;background:#fff}"
        ".label img{display:block;width:150mm;height:100mm}"
        ".bar{padding:8px;background:#eee;text-align:center}"
        ".bar button{font-size:16px;padding:10px 22px}"
        "@media print{.bar{display:none}}"
        "@media screen{.label{margin:12px auto;border:1px dashed #999}}"
        "</style></head><body>"
        "<div class='bar'><button onclick='window.print()'>Print label</button></div>"
        f"<div class='label'><img src='data:image/png;base64,{picture}' alt='{escape(' '.join(lines), quote=True)}'></div>"
        f"{script}</body></html>"
    )


@router.get("/labels/ebay/{ebay_order_id}", response_class=HTMLResponse)
def ebay_label(
    ebay_order_id: str, print: bool = False,
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    row = session.scalars(
        select(EbayImportedOrder).where(EbayImportedOrder.ebay_order_id == ebay_order_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="order not found")
    return label_page(ebay_label_lines(row.raw_snapshot or {}), auto_print=print)


@router.get("/labels/shop/{order_id}", response_class=HTMLResponse)
def shop_label(
    order_id: str, print: bool = False,
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    order = session.get(ShopOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return label_page(shop_order_label_lines(order), auto_print=print)


@router.post("/labels/ebay/{ebay_order_id}/print")
def print_ebay_label(
    ebay_order_id: str,
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    row = session.scalars(
        select(EbayImportedOrder).where(EbayImportedOrder.ebay_order_id == ebay_order_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="order not found")
    response = _send(ebay_label_lines(row.raw_snapshot or {}))
    record_event(session, actor_type="user", actor_id=user, action=LABEL_PRINTED_ACTION,
                 resource_type="ebay_order", resource_id=ebay_order_id, outcome="success",
                 correlation_id=str(uuid.uuid4()), details={"via": "order"})
    return response


@router.post("/labels/shop/{order_id}/print")
def print_shop_label(
    order_id: str,
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    order = session.get(ShopOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return _send(shop_order_label_lines(order))


def _send(lines: list[str]) -> JSONResponse:
    try:
        print_label(lines)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse({"ok": True})


_POSTAGE_PAGE = (
    "<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>Print postage label</title>"
    "<style>body{font:16px system-ui;max-width:520px;margin:24px auto;padding:0 16px}"
    "input,button{font:inherit;margin:8px 0}button{padding:10px 18px}#msg{margin-top:12px}</style>"
    "<h2>Print postage label</h2>"
    "<p>Upload the label PDF (or a screenshot) from eBay. It is cropped to the label and printed "
    "on the 4x6 printer. Only the first page is printed.</p>"
    "<form id='f'><input type='file' name='file' accept='application/pdf,image/*' required><br>"
    "<button class='btn'>Print</button></form><div id='msg'></div>"
    "<script>document.getElementById('f').addEventListener('submit',async e=>{e.preventDefault();"
    "const m=document.getElementById('msg');m.textContent='Printing...';"
    "try{const r=await fetch('/labels/postage/print',{method:'POST',body:new FormData(e.target)});"
    "const j=await r.json().catch(()=>({}));"
    "m.textContent=r.ok?'Printed.':(j.detail||'Print failed');}catch(x){m.textContent='Print failed';}});</script>"
)


@router.get("/labels/postage", response_class=HTMLResponse)
def postage_label_page(user: str = Depends(require_dashboard_user)):
    return HTMLResponse(_POSTAGE_PAGE)


@router.post("/labels/postage/print")
async def print_postage_label(file: UploadFile = File(...), user: str = Depends(require_dashboard_user)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="file is too large")
    try:
        send_job(tspl_for_image(postage_label_image(data)))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse({"ok": True})
