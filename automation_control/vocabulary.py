"""What the collectibles in this catalogue are actually called, per brand.

`series` and `character` on a captured collectible are free text, and the
catalogue drifted accordingly: 16 of the first 47 Sonny Angel rows named a
series that doesn't exist -- `Animal Series` seven times over with four
numbered ones to choose from, `Hippers Animal Series` for a line really
called `Animal Hippers`. That isn't only untidy. The eBay pricer builds
its query from these fields, so a drifted name quietly prices a figure
against the wrong comparables.

This module is the vocabulary that was missing. Two brands so far, and
they need different shapes, which is why the loaders are separate and only
the output is common:

  Sonny Angel  -- series-level. Nobody sells "one random Animal Series 4
                  figure"; they sell a named blind box and its secret. It
                  ships as the raw spreadsheet export Nic drops in, parsed
                  rather than retyped, so refreshing it is re-exporting
                  the sheet and nothing else.
  Pop Mart     -- figure-level, because a Pop Mart set page names all
                  twelve and marks its secrets, so there's no reason to
                  stop at the series.

**Brand scoping is not optional.** Sonny Angel's Snack Series has a
Mustard, and so does Pop Mart's Twinkle Twinkle. Matching a Lens title
without knowing the brand would resolve one to the other and price it
against a different company's figure. `match()` takes a brand for exactly
this reason, and the review page knows it from the capture row.

Deliberately *not* a closed set. This drives suggestions and a datalist,
never a validator: both brands ship new series faster than the data here
gets refreshed, so an unlisted name has to stay typeable.
"""

import csv
import functools
import re
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

SONNY_ANGEL = "Sonny Angel"
POP_MART = "Pop Mart"

SERIES = "series"
CHARACTER = "character"

# Sonny Angel's own columns.
REGULAR = "regular"
LIMITED = "limited"
HIPPERS = "hippers"
GIFTS = "gifts"


@dataclass(frozen=True)
class Entry:
    """One name worth matching, and everything knowable from matching it."""

    brand: str
    name: str
    kind: str
    series: str | None = None
    category: str | None = None
    year: int | None = None
    is_secret: bool = False
    # False for names that only feed pick lists: the official Sonny Angel
    # line-ups repeat "Rabbit" and "Duck" across dozens of series, which
    # would make match() resolve a listing title to an arbitrary one.
    matchable: bool = True


def _normalise(text: str) -> str:
    """Lowercase, and punctuation down to single spaces, so a shop title
    saying "Cherry Blossom Hanami Edition" still matches the sheet's
    "Cherry Blossom: Hanami Edition"."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# --- Sonny Angel: the master list spreadsheet ------------------------------

SONNY_ANGEL_PATH = DATA_DIR / "sonny_angel_series.csv"
SONNY_ANGEL_FIGURES_PATH = DATA_DIR / "sonny_angel_figures.csv"

# Read by the sheet's own column headings rather than by position -- a
# re-export that adds a column shouldn't silently reclassify everything.
_SA_COLUMNS = {
    "regular series": REGULAR,
    "limited series": LIMITED,
    "hippers and gifts": HIPPERS,
}

# A bare caption sitting inside the "HIPPERS AND GIFTS" column, above the
# three named gifts it introduces. `Hippers` is *not* here: the original
# undecorated Hippers line is a real series, and longest-match means
# `Animal Hippers` still wins for a title naming that one.
_SA_CAPTIONS = {"gifts"}

# "(2025) Pumpkin Patch", "(LATEST - 2026) SUMMER VACATION" -- the year is
# worth keeping as data, but it must come off the name. "LATEST" rots the
# moment the next series lands, and neither belongs in a field that
# becomes an eBay search query.
_YEAR_PREFIX = re.compile(r"^\(\s*(?:LATEST\s*-\s*)?(\d{4})\s*\)\s*")


def _sa_clean_name(raw: str) -> tuple[str, int | None]:
    match = _YEAR_PREFIX.match(raw)
    year = int(match.group(1)) if match else None
    name = _YEAR_PREFIX.sub("", raw).strip()
    # Only the all-caps entries get recased -- "SUMMER VACATION" is the
    # sheet shouting about the newest series, not its real styling, and a
    # blanket title-case would mangle the rest.
    if name.isupper():
        name = name.title()
    return name, year


def _sa_category(category: str, key: str) -> str:
    """That one column holds both lines. The named gifts under its "Gifts"
    caption are a different kind of product from a hipper -- they price
    nothing like one -- so they get their own category."""
    if category == HIPPERS and "hipper" not in key:
        return GIFTS
    return category


def _load_sonny_angel() -> list[Entry]:
    """Parsed from the raw export, so: a leading blank row, a heading row
    whose last cell is a paragraph of instructions to the humans, three
    parallel columns of differing lengths, and trailing empty cells on
    every row."""
    with SONNY_ANGEL_PATH.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))

    columns: dict[int, str] = {}
    heading_index = None
    for index, row in enumerate(rows):
        found = {
            position: _SA_COLUMNS[_normalise(cell)]
            for position, cell in enumerate(row)
            if _normalise(cell) in _SA_COLUMNS
        }
        if found:
            columns, heading_index = found, index
            break
    if heading_index is None:
        raise ValueError(f"no recognisable column headings in {SONNY_ANGEL_PATH}")

    entries: list[Entry] = []
    seen: set[str] = set()
    for row in rows[heading_index + 1:]:
        for position, category in columns.items():
            raw = row[position].strip() if len(row) > position else ""
            if not raw:
                continue
            name, year = _sa_clean_name(raw)
            key = _normalise(name)
            if not key or key in seen or key in _SA_CAPTIONS:
                continue
            seen.add(key)
            entries.append(Entry(brand=SONNY_ANGEL, name=name, kind=SERIES,
                                 category=_sa_category(category, key), year=year))

    # Figures, for the series whose line-ups we have. The master list stops
    # at the series, so this is the half that lets a Sonny Angel capture
    # name the actual figure the way a Pop Mart one already does.
    known = {e.name for e in entries}
    figures = _load_figures(SONNY_ANGEL_FIGURES_PATH, SONNY_ANGEL)
    for figure in figures:
        if figure.series not in known:
            # A figure filed under a series the sheet doesn't have is a
            # typo, and it would match while reporting a series that can
            # never be selected from the datalist.
            raise ValueError(
                f"{SONNY_ANGEL_FIGURES_PATH.name}: {figure.name!r} names series "
                f"{figure.series!r}, which is not in the master list")
    entries.extend(figures)
    entries.extend(_load_sonny_angel_official(entries))
    return entries


SONNY_ANGEL_OFFICIAL_PATH = DATA_DIR / "sonny_angel_official.csv"


def _load_sonny_angel_official(existing: list[Entry]) -> list[Entry]:
    """Every series and figure on sonnyangel.com/en/products (fetched
    2026-10-31): 85 series, 652 figures, including the archived 2018/2019
    versions of the regular series, whose line-ups differ from today's.
    Run-together names were split, obvious typos fixed and gallery titles
    mapped onto the master list's series names; yearly sets that share a
    name keep their year. Secrets aren't published there, so none are here."""
    with SONNY_ANGEL_OFFICIAL_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    series_seen = {_normalise(e.name) for e in existing if e.kind == SERIES}
    figures_seen = {(_normalise(e.series or ""), _normalise(e.name)) for e in existing if e.kind == CHARACTER}
    added: list[Entry] = []
    for row in rows:
        series, name = row["series"].strip(), row["character"].strip()
        year = int(row["year"]) if row["year"].strip().isdigit() else None
        if _normalise(series) not in series_seen:
            series_seen.add(_normalise(series))
            added.append(Entry(brand=SONNY_ANGEL, name=series, kind=SERIES, category=row["category"], year=year,
                               matchable=False))
        if (_normalise(series), _normalise(name)) not in figures_seen:
            figures_seen.add((_normalise(series), _normalise(name)))
            added.append(Entry(brand=SONNY_ANGEL, name=name, kind=CHARACTER, series=series,
                               category=row["category"], year=year, matchable=False))
    return added


# --- Pop Mart: a normalised figure list ------------------------------------

POP_MART_PATH = DATA_DIR / "popmart_figures.csv"


def _load_figures(path: Path, brand: str) -> list[Entry]:
    """One row per figure, secrets included and flagged.

    Always read off a source that names the whole set rather than recalled
    -- Pop Mart's own box-set pages, and for Sonny Angel a retailer
    line-up cross-checked against a second source before it lands here. A
    figure list that is quietly missing an entry is a figure that silently
    stops matching, so partial lists are worse than absent ones.
    """
    entries: list[Entry] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            character = (row.get("character") or "").strip()
            series = (row.get("series") or "").strip()
            if not character or not series:
                continue
            entries.append(Entry(
                brand=brand, name=character, kind=CHARACTER, series=series,
                is_secret=(row.get("is_secret") or "").strip().lower() in {"yes", "true", "1"},
            ))
    return entries


def _load_pop_mart() -> list[Entry]:
    entries = _load_figures(POP_MART_PATH, POP_MART)
    # The series itself is worth matching too: plenty of listings name the
    # set without naming which figure is in it. Sonny Angel needs no
    # equivalent -- its series come from the master list spreadsheet.
    for series in dict.fromkeys(entry.series for entry in entries):
        entries.append(Entry(brand=POP_MART, name=series, kind=SERIES, series=series))
    return entries


# --- aliases ---------------------------------------------------------------

# Names a line is sold under that its own data doesn't record. Not
# spelling variants -- normalisation already handles punctuation and case
# -- but different names for the same thing, resolving to the canonical
# entry so the alias never reaches the database.
#
# Matching is word-order sensitive (a substring of the normalised text),
# which the first two exist to cover: this app's own seed script invented
# "Hippers Animal Series" for the line the sheet calls "Animal Hippers",
# and without an alias the bare "Hippers" needle wins and an Animal Hipper
# is stored as a plain one, which prices differently.
#
# Confirmed by Nic, not inferred: the catalogue's `Fast Food Series` rows
# are Snack Series figures, and that's the name sellers use -- so matching
# canonical names alone missed every title for the Hamburger secret, the
# most valuable figure in the catalogue. Only add an entry that's actually
# been confirmed; a wrong one mislabels silently.
_ALIASES: dict[str, tuple[str, str]] = {
    # normalised alias -> (brand, canonical name)
    "hippers animal series": (SONNY_ANGEL, "Animal Hippers"),
    "hippers harvest series": (SONNY_ANGEL, "Harvest Hippers"),
    "fast food series": (SONNY_ANGEL, "Snack Series"),
    "fast food": (SONNY_ANGEL, "Snack Series"),
    "twinkle twinkle": (POP_MART, "Twinkle Twinkle Create Your Taste Series"),
    # Listings routinely drop the CRYBABY line prefix and just name the
    # series, which is the form the canonical name can't match on its own.
    "crying again series": (POP_MART, "CRYBABY Crying Again Series"),
    "crying again": (POP_MART, "CRYBABY Crying Again Series"),
    "crying for love series": (POP_MART, "CRYBABY Crying For Love Series"),
    "crying for love": (POP_MART, "CRYBABY Crying For Love Series"),
    # The sheet calls the 2026 release "SUMMER VACATION"; everyone selling
    # it uses the edition name, which is how it reached the catalogue as
    # "Summer Vacation Series - Sun-Kissed Style".
    "sun kissed style": (SONNY_ANGEL, "Summer Vacation"),
    "summer vacation series sun kissed style": (SONNY_ANGEL, "Summer Vacation"),
    # Retailers split on the plural. Matching is exact on word boundaries,
    # so "Cherry Blossoms" would otherwise miss the singular entry entirely.
    "cherry blossoms": (SONNY_ANGEL, "Cherry Blossom"),
    # Sonny Angel's Robby secrets are sold under both the short name and the
    # full "... Robby Angel" -- the Summer Vacation line-up on the brand's
    # own release uses "Flamingo Robby Angel", so the long form is the
    # official one and the short form is what most listings actually say.
    "pink robby angel": (SONNY_ANGEL, "Pink Robby"),
    "flower robby angel": (SONNY_ANGEL, "Flower Robby"),
    # The master list's raw export shortens this to "Cat Life"; everyone
    # selling it -- the brand's own product pages included -- calls it
    # "Cat Life Series".
    "cat life series": (SONNY_ANGEL, "Cat Life"),
}

_LOADERS = {SONNY_ANGEL: _load_sonny_angel, POP_MART: _load_pop_mart}

BRANDS = tuple(_LOADERS)


@functools.lru_cache(maxsize=1)
def all_entries() -> tuple[Entry, ...]:
    return tuple(entry for load in _LOADERS.values() for entry in load())


def entries_for(brand: str | None = None) -> tuple[Entry, ...]:
    if brand is None:
        return all_entries()
    wanted = _normalise(brand)
    return tuple(e for e in all_entries() if _normalise(e.brand) == wanted)


def series_names(brand: str | None = None) -> list[str]:
    """Canonical series names for the review form's datalist. Sonny
    Angel's own ordering puts hippers and gifts last -- a figure in hand is
    far more often a regular or limited one."""
    order = {REGULAR: 0, LIMITED: 1, HIPPERS: 2, GIFTS: 3, None: 1}
    series = [e for e in entries_for(brand) if e.kind == SERIES]
    return [e.name for e in sorted(series, key=lambda e: (order.get(e.category, 1), -(e.year or 0), e.name))]


def character_names(brand: str | None = None) -> list[str]:
    return [e.name for e in entries_for(brand) if e.kind == CHARACTER]


@functools.lru_cache(maxsize=8)
def _needles(brand: str | None) -> tuple[tuple[str, Entry], ...]:
    """Names and their aliases in match order: characters before series,
    then longest-first so "Animal Series 4" beats "Animal Series"-as-a-
    prefix and "Cherry Blossom Hippers" beats "Cherry Blossom Series". An
    alias maps to the canonical Entry, so matching one still yields the
    real name."""
    entries = tuple(e for e in entries_for(brand) if e.matchable)
    by_key = {(e.brand, e.name): e for e in entries}
    pairs = [(_normalise(e.name), e) for e in entries]
    for alias, (alias_brand, canonical) in _ALIASES.items():
        entry = by_key.get((alias_brand, canonical))
        if entry is None:
            if brand is None:
                raise ValueError(f"alias {alias!r} points at unknown {alias_brand} entry {canonical!r}")
            continue  # the alias belongs to a brand we're not looking at
        pairs.append((_normalise(alias), entry))
    # A character beats a series regardless of length: naming the figure
    # tells you its series as well, so it is strictly the better answer.
    # "Twinkle Twinkle Chili Sauce" should be Chili Sauce, not the set.
    kind_rank = {CHARACTER: 0, SERIES: 1}
    return tuple(sorted(pairs, key=lambda pair: (kind_rank.get(pair[1].kind, 1), -len(pair[0]))))


def match(text: str, brand: str | None = None) -> Entry | None:
    """The most specific thing named in a free-text string, or None.

    Built for Google Lens result titles ("Sonny Angel Flower Series Lily
    Bell Figure- US Selller"), which is the one place a series reliably
    appears -- it's typically nowhere on the figure itself, which is why
    reviewing a shelf photo leaves the field blank.

    Pass `brand` whenever the capture knows it. Sonny Angel and Pop Mart
    both have a Mustard, and an unscoped match on that name is a coin
    toss between two companies' figures.

    Conservative by design: the whole canonical name has to be present, so
    a title saying only "Animal Series" matches nothing rather than
    guessing one of the four. A wrong name is worse than a blank one,
    because the blank gets typed and the wrong one gets priced.
    """
    if not text:
        return None
    haystack = _normalise(text)
    best_character = best_series = None
    for needle, entry in _needles(brand):
        if not needle or not re.search(rf"\b{re.escape(needle)}\b", haystack):
            continue
        if entry.kind == CHARACTER:
            best_character = best_character or entry
        else:
            best_series = best_series or entry
        if best_character and best_series:
            break

    # A character only wins if the text doesn't name a *different* series.
    # Figure lists are per-series and arrive one series at a time, so a
    # generic name -- Rabbit, Shark, Cherry -- exists in series we haven't
    # loaded yet. Without this, "Animal Series 4 Rabbit" matches the one
    # Rabbit we happen to know and reports the wrong series, which is
    # worse than reporting only the series the title actually named.
    if best_character and best_series and best_character.series != best_series.name:
        return best_series
    return best_character or best_series


def suggest(titles: list[str], brand: str | None = None) -> list[tuple[Entry, int]]:
    """What a set of titles agree this is, commonest first.

    Consensus is the real signal in a Lens run: one listing calling a
    figure Flower Series is a guess, thirty agreeing is the answer.
    Returns counts too, so the UI can show its evidence instead of
    asserting an identification.
    """
    counts: dict[tuple[str, str], tuple[Entry, int]] = {}
    for title in titles:
        found = match(title or "", brand)
        if found is None:
            continue
        key = (found.brand, found.name)
        _, count = counts.get(key, (found, 0))
        counts[key] = (found, count + 1)
    return sorted(counts.values(), key=lambda pair: (-pair[1], pair[0].name))
