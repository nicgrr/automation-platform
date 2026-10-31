"""A remembered list of collectible brands, series and characters.

Onboarding a figure means typing its brand, series and character. This
keeps every combination ever seen (seeded from the catalogue and researched
series lists in vocabulary.py, then added to whenever the operator saves
one), so each form's
Brand / Series / Character boxes become linked search lists: choose a brand
and the Series box offers that brand's series; choose a series and the
Character box offers its characters. Matching ignores case and stray spaces
and returns the remembered spelling, so "sonny angel " and "Sonny Angel" are
one brand and the catalogue stops growing near-duplicates.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from . import vocabulary
from .models import CollectibleReference

router = APIRouter(prefix="/collectibles/reference", tags=["collectible-reference"])


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def key(value: str | None) -> str:
    return clean(value).casefold()


def canonical(session: Session, brand: str, series: str = "", character: str = "") -> tuple[str, str, str]:
    """The remembered spelling of each part, or the cleaned input when new."""
    out = [clean(brand), clean(series), clean(character)]
    if out[0]:
        hit = session.scalar(select(CollectibleReference.brand).where(CollectibleReference.brand_key == key(brand)).limit(1))
        out[0] = hit or out[0]
    if out[0] and out[1]:
        hit = session.scalar(select(CollectibleReference.series).where(
            CollectibleReference.brand_key == key(brand), CollectibleReference.series_key == key(series)).limit(1))
        out[1] = hit or out[1]
    if out[0] and out[1] and out[2]:
        hit = session.scalar(select(CollectibleReference.character).where(
            CollectibleReference.brand_key == key(brand), CollectibleReference.series_key == key(series),
            CollectibleReference.character_key == key(character)).limit(1))
        out[2] = hit or out[2]
    return out[0], out[1], out[2]


def remember(session: Session, brand: str, series: str = "", character: str = "", *,
             is_secret: bool = False, source: str = "operator") -> None:
    """Add this combination to the list if it's new (flushes; caller commits)."""
    brand, series, character = canonical(session, brand, series, character)
    if not brand:
        return
    existing = session.scalar(select(CollectibleReference).where(
        CollectibleReference.brand_key == key(brand), CollectibleReference.series_key == key(series),
        CollectibleReference.character_key == key(character)))
    if existing is not None:
        if is_secret and not existing.is_secret:
            existing.is_secret = True
        return
    session.add(CollectibleReference(
        id=str(uuid.uuid4()), brand=brand, series=series, character=character,
        brand_key=key(brand), series_key=key(series), character_key=key(character),
        is_secret=is_secret, source=source))
    session.flush()


def _vocabulary_brand(brand: str) -> str | None:
    return next((b for b in vocabulary.BRANDS if key(b) == key(brand)), None)


def _merge(remembered: list[str], curated: list[str]) -> list[str]:
    """One entry per name, keeping the remembered (operator's) spelling."""
    out: dict[str, str] = {}
    for name in [*remembered, *curated]:
        if name and key(name) not in out:
            out[key(name)] = clean(name)
    return list(out.values())


@router.get("/brands")
def brands(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    rows = list(session.execute(select(CollectibleReference.brand).distinct()).scalars())
    return JSONResponse(sorted(_merge(rows, list(vocabulary.BRANDS)), key=str.casefold))


@router.get("/series")
def series(brand: str = Query(""), user: str = Depends(require_dashboard_user),
           session: Session = Depends(get_session)):
    rows = list(session.execute(select(CollectibleReference.series).where(
        CollectibleReference.brand_key == key(brand)).distinct()).scalars())
    known = _vocabulary_brand(brand)
    curated = vocabulary.series_names(known) if known else []
    return JSONResponse(sorted(_merge(rows, curated), key=_natural))


@router.get("/characters")
def characters(brand: str = Query(""), series: str = Query(""), user: str = Depends(require_dashboard_user),
               session: Session = Depends(get_session)):
    rows = session.execute(select(CollectibleReference.character, CollectibleReference.is_secret).where(
        CollectibleReference.brand_key == key(brand), CollectibleReference.series_key == key(series),
        CollectibleReference.character_key != "")).all()
    secret = {key(c): s for c, s in rows}
    known = _vocabulary_brand(brand)
    curated = [e for e in (vocabulary.entries_for(known) if known else ())
               if e.kind == vocabulary.CHARACTER and key(e.series) == key(series)]
    for e in curated:
        secret[key(e.name)] = secret.get(key(e.name), False) or e.is_secret
    names = _merge([c for c, _ in rows], [e.name for e in curated])
    return JSONResponse(sorted(({"name": n, "secret": secret.get(key(n), False)} for n in names),
                               key=lambda r: (r["secret"], _natural(r["name"]))))


def _natural(value: str):
    """'Animal Series 2' before 'Animal Series 10'."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


PICKER_SCRIPT = r"""<script>
(function(){
  // Linked Brand -> Series -> Character search lists (collectible_reference.py).
  // Works on any form with inputs named brand, series and character.
  function list(input, id){
    var dl = document.getElementById(id);
    if (!dl) { dl = document.createElement('datalist'); dl.id = id; document.body.appendChild(dl); }
    input.setAttribute('list', id); input.setAttribute('autocomplete', 'off');
    return dl;
  }
  function fill(dl, values){
    dl.innerHTML = '';
    values.forEach(function(v){
      var o = document.createElement('option');
      o.value = typeof v === 'string' ? v : v.name;
      if (v.secret) o.label = v.name + ' (secret)';
      dl.appendChild(o);
    });
  }
  function get(url){ return fetch(url, {credentials: 'same-origin'}).then(function(r){ return r.ok ? r.json() : []; }).catch(function(){ return []; }); }
  document.querySelectorAll('form').forEach(function(form, n){
    var b = form.querySelector('input[name=brand]'), s = form.querySelector('input[name=series]'),
        c = form.querySelector('input[name=character]') || form.querySelector('input[name=name][data-character]');
    if (!b || !s) return;
    var bl = list(b, 'ref-brands-' + n), sl = list(s, 'ref-series-' + n), cl = c ? list(c, 'ref-chars-' + n) : null;
    get('/collectibles/reference/brands').then(function(v){ fill(bl, v); });
    function loadSeries(){
      if (!b.value.trim()) { fill(sl, []); return; }
      get('/collectibles/reference/series?brand=' + encodeURIComponent(b.value)).then(function(v){ fill(sl, v); });
    }
    function loadChars(){
      if (!cl) return;
      if (!b.value.trim() || !s.value.trim()) { fill(cl, []); return; }
      get('/collectibles/reference/characters?brand=' + encodeURIComponent(b.value) + '&series=' + encodeURIComponent(s.value))
        .then(function(v){ fill(cl, v); });
    }
    b.addEventListener('change', function(){ loadSeries(); loadChars(); });
    s.addEventListener('change', loadChars);
    s.addEventListener('focus', loadSeries);
    if (c) c.addEventListener('focus', loadChars);
    loadSeries(); loadChars();
  });
})();
</script>"""
