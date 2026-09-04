"""The interactive scanning loop: watch, detect, identify, confirm, commit.

Session state lives in the DB rather than in this process, so quitting
mid-pile and coming back tomorrow resumes the same ScanSession with its
running counts intact -- scanning several thousand cards is not a
single-sitting activity.

Everything a human has to decide is funnelled through `Prompter`, which
keeps the loop's control flow testable without a terminal: tests supply a
scripted prompter and assert on what gets committed.
"""

import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import cv2

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CardSet, CardVariant, CatalogCard, ScanSession, ScanSessionStatus
from . import catalog
from .commit import CommitRefused, commit_card, inventory_totals
from .detect import detect_cards
from .foil import assess_foil
from .pricing import (
    POKEMONPRICETRACKER_SOURCE_NAME, CachedPriceSource, PokemonPriceTrackerSource, PokemonTcgPriceSource,
    PriceSource, price_and_record,
)
from .identify import Identification, ReferenceCard, Source, best_set_by_art, detect_sheet_set_and_rotation, identify_card, identify_card_via_vision, read_set_totals

POLL_INTERVAL_SECONDS = 2.0
# Scanners write incrementally; a file that appeared mid-write would be
# truncated. Treat it as ready only once its size has stopped changing.
STABLE_CHECKS = 2

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


class Prompter:
    """Every human decision point, in one place so the loop can be driven
    by a script in tests instead of a terminal."""

    def ask(self, question: str, default: str = "") -> str:
        raise NotImplementedError

    def confirm(self, question: str, default: bool = True) -> bool:
        raise NotImplementedError

    def notify(self, message: str) -> None:
        raise NotImplementedError


class ConsolePrompter(Prompter):
    def ask(self, question: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        answer = input(f"{question}{suffix}: ").strip()
        return answer or default

    def confirm(self, question: str, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        answer = input(f"{question} [{hint}]: ").strip().lower()
        if not answer:
            return default
        return answer.startswith("y")

    def notify(self, message: str) -> None:
        print(message)


@dataclass
class SheetOutcome:
    sheet_path: Path
    detected: int
    committed: int = 0
    skipped: int = 0
    set_aside: int = 0
    archived_to: Path | None = None
    warnings: list[str] = field(default_factory=list)


def _stable_new_images(watch_dir: Path, seen: set[Path]) -> list[Path]:
    """New, fully-written image files in the watch folder. Size stability is
    checked by the caller across polls; this just lists candidates."""
    if not watch_dir.exists():
        return []
    return sorted(
        path for path in watch_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and path not in seen
    )


def wait_for_sheet(watch_dir: Path, seen: set[Path], prompter: Prompter, poll_interval: float = POLL_INTERVAL_SECONDS, max_wait: float | None = None) -> Path | None:
    """Block until a new, fully-written image lands in the watch folder.

    Returns None if `max_wait` elapses first (tests use this; interactive
    use waits indefinitely, since the operator is off scanning).
    """
    waited = 0.0
    sizes: dict[Path, tuple[int, int]] = {}
    while True:
        for path in _stable_new_images(watch_dir, seen):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            previous_size, stable_count = sizes.get(path, (-1, 0))
            if size == previous_size and size > 0:
                stable_count += 1
                if stable_count >= STABLE_CHECKS:
                    return path
            else:
                stable_count = 0
            sizes[path] = (size, stable_count)

        if max_wait is not None and waited >= max_wait:
            return None
        time.sleep(poll_interval)
        waited += poll_interval


def _set_aside_for_review(
    crop_path: Path, media_dir: Path, sheet_name: str, index: int,
    identification: Identification | None = None, label: str | None = None,
) -> Path:
    """Where an ambiguous card goes in unattended mode instead of blocking
    on a prompt nobody's there to answer. Named after the sheet/position it
    came from so a later manual pass can trace it back to the physical
    card without needing this run's log open at the same time.

    `label` overrides the identification-derived name for a card that was
    never run through identification at all -- e.g. one set aside purely for
    failing the geometry check (see `detect.DetectionResult.misshapen`).
    """
    review_dir = media_dir / "needs_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    if label is None:
        assert identification is not None
        label = f"{identification.number}-{identification.name}" if identification.card_id else "unidentified"
    destination = review_dir / f"{Path(sheet_name).stem}-card{index}-{label}.jpg"
    # copyfile, not copy/copy2: those also set mode and timestamps on the
    # destination, which requires owning it. The service, a manual reprocess
    # and the web review all write into this shared folder as different
    # users, and only the contents need replacing.
    shutil.copyfile(crop_path, destination)
    return destination


def _archive_sheet(sheet_path: Path, archive_dir: Path, session_id: str) -> Path:
    """Move the original full sheet out of the watch folder so it isn't
    reprocessed, keeping it for re-runs if identification later improves."""
    destination_dir = archive_dir / session_id
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / sheet_path.name
    if destination.exists():
        # Local, matching the sheet filenames this suffix is appended to
        # (see hardware.scan_sheet) -- a UTC suffix on a local-time name
        # reads as a different scan session entirely.
        stamp = datetime.now().strftime("%H%M%S")
        destination = destination_dir / f"{sheet_path.stem}-{stamp}{sheet_path.suffix}"
    shutil.move(str(sheet_path), destination)
    return destination


def start_or_resume(
    session: Session, set_id: str, variant: CardVariant, prompter: Prompter,
    user: str = "scan-ingest", unattended: bool = False,
) -> ScanSession:
    """Continue the most recent unfinished session for this set, or start a
    new one. Resuming is offered rather than automatic in interactive use,
    because "same set, new pile" is a legitimate reason to want fresh
    counts -- but a background service has nobody to ask, so `unattended`
    always resumes (the safer default for something meant to run for days).
    """
    existing = session.scalars(
        select(ScanSession)
        .where(ScanSession.set_id == set_id, ScanSession.status == ScanSessionStatus.RUNNING)
        .order_by(ScanSession.started_at.desc())
    ).first()

    if existing:
        started = existing.started_at.strftime("%Y-%m-%d %H:%M") if existing.started_at else "?"
        resume = True if unattended else prompter.confirm(
            f"Resume the session started {started} ({existing.sheets_scanned} sheets, {existing.cards_committed} cards so far)?",
            default=True,
        )
        if resume:
            existing.batch_variant = variant
            session.commit()
            return existing
        existing.status = ScanSessionStatus.DONE
        existing.finished_at = datetime.now(UTC)
        session.commit()

    scan_session = ScanSession(set_id=set_id, batch_variant=variant, started_by=user)
    session.add(scan_session)
    session.commit()
    return scan_session


def _resolve_by_number(references: list[ReferenceCard], number: str) -> ReferenceCard | None:
    normalised = number.strip().lstrip("0") or "0"
    return next((r for r in references if r.number == normalised), None)


def _format_quote(quote) -> str:
    if quote is None:
        return "price unknown"
    return f"est. {quote.price} {quote.currency} ({quote.source})"


def review_card(
    identification: Identification, index: int, references: list[ReferenceCard], prompter: Prompter,
    price_quote=None,
) -> tuple[Identification | None, bool]:
    """Show one card's result and get a decision.

    Returns (identification_to_commit, was_confirmed_by_human). A None
    identification means skip this card. Confident results pass straight
    through without asking -- the whole point is that only uncertain cards
    cost the operator attention.
    """
    if not identification.needs_confirmation:
        return identification, False

    label = f"#{identification.number} {identification.name}" if identification.card_id else "(unidentified)"
    prompter.notify(f"\n  Card {index}: {label}  [{identification.source.value}, confidence {identification.confidence:.2f}, {_format_quote(price_quote)}]")
    if identification.note:
        prompter.notify(f"    {identification.note}")
    if identification.ocr_text:
        prompter.notify(f"    OCR read: {identification.ocr_text!r}")

    while True:
        answer = prompter.ask("    Enter to accept, a card number to correct, or 's' to skip", default="")
        if answer == "":
            if identification.card_id is None:
                prompter.notify("    Nothing to accept -- enter a number or 's' to skip.")
                continue
            return identification, True
        if answer.lower() in {"s", "skip"}:
            return None, True

        corrected = _resolve_by_number(references, answer)
        if corrected is None:
            prompter.notify(f"    No card #{answer} in this set -- try again.")
            continue
        # A human-typed number is authoritative: replace the identification
        # wholesale rather than patching the machine's guess.
        return (
            Identification(
                card_id=corrected.card_id, number=corrected.number, name=corrected.name,
                source=identification.source, confidence=1.0,
                phash_distance=identification.phash_distance, phash_margin=identification.phash_margin,
                ocr_text=identification.ocr_text, note="corrected by operator",
            ),
            True,
        )


@dataclass
class ReviewOutcome:
    committed: int = 0
    skipped: int = 0
    deleted: int = 0
    remaining: int = 0


def _best_across_sets(crop_path: Path, catalogs: dict[str, list[ReferenceCard]], set_totals: dict[str, int | None], settings):
    """Identify one set-aside crop against every cached set, best match wins.

    Cards reach the review queue precisely because the machine wasn't
    confident, and a crop carries no record of which set its sheet was
    routed to -- so rather than trust that earlier routing (which for the
    misrouted-sheet case was simply wrong), every cached set gets a fresh
    look. A few thousand hash comparisons per crop is nothing.
    """
    set_id = best_set_by_art(crop_path, catalogs, max_phash_distance=settings.scan_phash_max_distance)
    if set_id is None:
        return None
    identification = identify_card(
        crop_path, catalogs[set_id], set_total=set_totals.get(set_id),
        max_phash_distance=settings.scan_phash_max_distance,
    )
    return (set_id, identification) if identification.card_id else None


def resolve_review_queue(
    db: Session, settings, prompter: Prompter, variant: CardVariant = CardVariant.NORMAL,
    condition: str = "Near Mint", user: str = "scan-ingest", limit: int | None = None,
) -> ReviewOutcome:
    """Walk the set-aside crops and let a human settle each one.

    This closes the loop unattended mode deliberately leaves open: it never
    guesses on the operator's behalf, it just puts the best available
    suggestion in front of them and commits what they confirm. Crops are
    removed only once their card is committed or explicitly discarded, so
    quitting part-way is safe and re-running resumes where you stopped.
    """
    review_dir = Path(settings.scan_media_dir) / "needs_review"
    crops = sorted(review_dir.glob("*.jpg")) if review_dir.exists() else []
    outcome = ReviewOutcome(remaining=len(crops))
    if not crops:
        prompter.notify("Review queue is empty -- nothing to confirm.")
        return outcome

    catalogs = _load_all_catalogs(db)
    if not catalogs:
        raise ValueError("no sets are cached -- nothing to identify against")
    set_totals = {s.id: s.printed_total for s in catalog.cached_sets(db)}
    sessions: dict[str, ScanSession] = {}

    queue = crops[:limit] if limit else crops
    prompter.notify(f"{len(queue)} card(s) to review (of {len(crops)} in the queue).\n")

    for position, crop_path in enumerate(queue, start=1):
        best = _best_across_sets(crop_path, catalogs, set_totals, settings)
        header = f"[{position}/{len(queue)}] {crop_path.name}"

        if best is None:
            prompter.notify(f"{header}\n  No match in any cached set.")
            suggestion_set, identification = None, None
        else:
            suggestion_set, identification = best
            set_name = (db.get(CardSet, suggestion_set).name if db.get(CardSet, suggestion_set) else suggestion_set)
            prompter.notify(
                f"{header}\n  Best match: #{identification.number} {identification.name}"
                f"  [{set_name}, {identification.source.value}, confidence {identification.confidence:.2f}]"
            )
        prompter.notify(f"  Image: {crop_path}")

        answer = prompter.ask(
            "  Enter to accept, a card number to correct, 's' to skip, 'd' to discard", default=""
        ).strip()

        if answer.lower() in {"d", "discard"}:
            crop_path.unlink(missing_ok=True)
            outcome.deleted += 1
            prompter.notify("  Discarded.\n")
            continue
        if answer.lower() in {"s", "skip"} or (answer == "" and identification is None):
            outcome.skipped += 1
            prompter.notify("  Left in the queue.\n")
            continue

        target_set = suggestion_set
        decided = identification
        if answer:
            # A typed correction is scoped to the suggested set when there is
            # one; without a suggestion there's no set context, so ask.
            if target_set is None:
                target_set = prompter.ask("  Which set id?", default="").strip()
                if target_set not in catalogs:
                    prompter.notify(f"  {target_set!r} is not cached -- skipping.\n")
                    outcome.skipped += 1
                    continue
            corrected = _resolve_by_number(catalogs[target_set], answer)
            if corrected is None:
                prompter.notify(f"  No card #{answer} in that set -- left in the queue.\n")
                outcome.skipped += 1
                continue
            decided = Identification(
                card_id=corrected.card_id, number=corrected.number, name=corrected.name,
                source=Source.OCR, confidence=1.0, note="confirmed in review",
            )

        if decided is None or target_set is None:
            outcome.skipped += 1
            continue

        scan_session = sessions.get(target_set)
        if scan_session is None:
            scan_session = start_or_resume(db, target_set, variant, prompter, user=user, unattended=True)
            sessions[target_set] = scan_session

        try:
            result = commit_card(
                db, decided, crop_path, Path(settings.scan_media_dir),
                variant=variant, condition=condition, scan_session=scan_session,
                user=user, allow_unconfirmed=True,
            )
        except CommitRefused as exc:
            prompter.notify(f"  Not committed -- {exc}\n")
            outcome.skipped += 1
            continue

        crop_path.unlink(missing_ok=True)
        outcome.committed += 1
        prompter.notify(f"  {result.summary}\n")

    outcome.remaining = len(list(review_dir.glob("*.jpg"))) if review_dir.exists() else 0
    prompter.notify(
        f"Review done: {outcome.committed} committed, {outcome.skipped} skipped, "
        f"{outcome.deleted} discarded. {outcome.remaining} left in the queue."
    )
    return outcome


def process_sheet(
    db: Session, sheet_path: Path, scan_session: ScanSession, references: list[ReferenceCard],
    settings, prompter: Prompter, set_total: int | None, variant: CardVariant,
    condition: str = "Near Mint", expected_count: int | None = None,
    post_rotation_degrees: int = 0, user: str = "scan-ingest",
    price_source: PriceSource | None = None, unattended: bool = False,
) -> SheetOutcome:
    """Detect, identify, confirm, and commit every card on one sheet.

    In `unattended` mode nothing ever blocks waiting for a terminal that
    isn't there: a sheet whose *grid* looks questionable (see
    `detect.DetectionResult.structural_issue`) is skipped (left in the watch
    folder) rather than asking whether to proceed; a card needing
    identification confirmation, or one that fails the geometry check on its
    own without the grid being in doubt, is set aside individually (see
    `_set_aside_for_review`) rather than prompted for or taking its whole
    sheet down with it -- confident cards still commit exactly as in
    interactive use, since that path never asks either.
    """
    work_dir = Path(settings.scan_media_dir) / "work" / scan_session.id / sheet_path.stem
    detection = detect_cards(
        sheet_path, work_dir, expected_count=expected_count,
        max_count=settings.scan_max_cards_per_sheet,
        always_save_overlay=False, post_rotation_degrees=post_rotation_degrees,
    )

    outcome = SheetOutcome(sheet_path=sheet_path, detected=detection.count, warnings=list(detection.warnings))
    prompter.notify(f"\nSheet {sheet_path.name}: {detection.count} card(s) detected ({detection.rows}x{detection.cols})")
    for warning in detection.warnings:
        prompter.notify(f"  WARNING: {warning}")
    if detection.overlay_path:
        prompter.notify(f"  Overlay saved: {detection.overlay_path}")

    # `structural_issue` means the grid itself looks wrong -- no single card
    # position to isolate, so the whole sheet needs a human look (or a
    # rescan) before anything on it is trusted. When `needs_review` is set
    # for some *other* reason, it can only be `misshapen`: specific card
    # positions the count itself doesn't call into question. In unattended
    # mode those get set aside individually below instead of blocking the
    # sheet's other, good cards; in interactive mode the operator still gets
    # a heads-up before committing to any of these crops.
    if detection.structural_issue or (not unattended and detection.needs_review):
        proceed = False if unattended else prompter.confirm("  Detection looks off. Continue with these crops?", default=False)
        if not proceed:
            prompter.notify("  Sheet skipped -- left in the watch folder for a rescan.")
            outcome.skipped = detection.count
            return outcome

    if price_source is None:
        # Real market/eBay-sold pricing when a key is configured -- see
        # pricing.py's module docstring for why the zero-cost pokemontcg.io
        # snapshot below is a fallback, not the preferred source. Wrapped in
        # CachedPriceSource since PokemonPriceTrackerSource bills per call;
        # without it, scanning several copies of the same common would
        # re-bill the same price on every commit.
        if settings.pokemonpricetracker_api_key:
            price_source = CachedPriceSource(
                PokemonPriceTrackerSource(settings.pokemonpricetracker_api_key), db, POKEMONPRICETRACKER_SOURCE_NAME,
            )
        else:
            price_source = PokemonTcgPriceSource()

    def _quote_for(card_id: str | None):
        if card_id is None:
            return None
        card = db.get(CatalogCard, card_id)
        return price_source.get_price(card, variant) if card else None

    api_key = settings.anthropic_api_key
    misshapen = set(detection.misshapen)
    for index, crop_path in enumerate(detection.crop_paths, start=1):
        if unattended and index in misshapen:
            # A misshapen crop's own pHash is exactly the signal the shape
            # check calls into doubt, so identify_card isn't worth trying
            # here -- go straight to the one signal that doesn't depend on
            # the crop's geometry. If Claude can't place it either (or
            # there's no key configured), it falls to the same set-aside as
            # always; if it can, `identification` carries on into the
            # ordinary identify/commit path below like any other card.
            identification = identify_card_via_vision(crop_path, references, api_key) if api_key else None
            if identification is None:
                saved_to = _set_aside_for_review(
                    crop_path, Path(settings.scan_media_dir), sheet_path.name, index, label="not-card-shaped",
                )
                prompter.notify(f"  Card {index}: not card-shaped -- set aside for review: {saved_to}")
                outcome.set_aside += 1
                continue
        else:
            identification = identify_card(
                crop_path, references, set_total=set_total,
                max_phash_distance=settings.scan_phash_max_distance,
            )
            # OCR and pHash both fell short of auto-commit -- one more shot
            # before this card costs a human's attention. Only takes over
            # when it does *better* (a resolved, confirmable match); a weak
            # vision guess never overwrites a phash guess that's already as
            # good or better.
            if unattended and identification.needs_confirmation and api_key:
                vision = identify_card_via_vision(crop_path, references, api_key)
                if vision is not None and not vision.needs_confirmation:
                    identification = vision

        identified_card = db.get(CatalogCard, identification.card_id) if identification.card_id else None
        if identified_card is not None:
            summary = assess_foil(crop_path, identified_card).log_summary()
            if identification.foil_observation:
                summary += f" | vision saw: {identification.foil_observation}"
            prompter.notify(f"  Card {index}: {summary}")
        if unattended and identification.needs_confirmation:
            saved_to = _set_aside_for_review(crop_path, Path(settings.scan_media_dir), sheet_path.name, index, identification)
            label = f"#{identification.number} {identification.name}" if identification.card_id else "(unidentified)"
            prompter.notify(f"  Card {index}: {label} needs confirmation ({identification.source.value}, {identification.confidence:.2f}) -- set aside: {saved_to}")
            outcome.set_aside += 1
            continue
        decided, was_confirmed = review_card(identification, index, references, prompter, price_quote=_quote_for(identification.card_id))
        if decided is None:
            outcome.skipped += 1
            continue
        try:
            result = commit_card(
                db, decided, crop_path, Path(settings.scan_media_dir),
                variant=variant, condition=condition, scan_session=scan_session,
                user=user, allow_unconfirmed=was_confirmed,
            )
        except CommitRefused as exc:
            prompter.notify(f"  Card {index}: not committed -- {exc}")
            outcome.skipped += 1
            continue
        outcome.committed += 1
        priced_card = db.get(CatalogCard, decided.card_id)
        quote = price_and_record(db, price_source, priced_card, variant) if priced_card else None
        prompter.notify(f"  Card {index}: {result.summary} -- {_format_quote(quote)}")

    scan_session.sheets_scanned += 1
    db.commit()

    outcome.archived_to = _archive_sheet(sheet_path, Path(settings.scan_archive_dir), scan_session.id)
    return outcome


def _load_all_catalogs(db: Session) -> dict[str, list[ReferenceCard]]:
    """Every cached set's reference list, keyed by set id -- what
    `_run_session_auto` probes each new sheet against so it never has to be
    told up front which set (or which way up) a pile is."""
    catalogs: dict[str, list[ReferenceCard]] = {}
    for card_set in catalog.cached_sets(db):
        refs = [
            ReferenceCard(card_id=c.id, number=c.number, name=c.name, phash=c.phash,
                      art_phash=c.art_phash, rarity=c.rarity)
            for c in catalog.get_cached_cards(db, card_set.id) if c.phash
        ]
        if refs:
            catalogs[card_set.id] = refs
    return catalogs


# How many distinct printed totals to act on when a sheet matches nothing.
# Two covers the realistic case (the true total, plus one popular OCR
# misread) without letting a garbage read trigger a spree of downloads.
AUTOCACHE_TOTALS_TRIED = 2
# A printed total isn't unique -- a few sets share one -- so all candidates
# get cached and the art match then decides between them. Capped so an
# unlucky total can't pull down a dozen sets at once.
AUTOCACHE_MAX_SETS = 3

_ROTATIONS_FOR_OCR = (
    (0, None), (90, cv2.ROTATE_90_CLOCKWISE),
    (180, cv2.ROTATE_180), (270, cv2.ROTATE_90_COUNTERCLOCKWISE),
)


def _rotated_copies(crop_paths: list[Path], work_dir: Path) -> list[Path]:
    """Every crop at all four rotations. The sheet's rotation is unknown at
    this point (that's decided by the art match, which just failed), and
    OCR only reads an upright number -- so rather than guess, all four are
    read and pooled: a correct number is read consistently at exactly one
    rotation, while misreads at the other three rarely agree with anything.
    """
    copies: list[Path] = []
    for degrees, rotation in _ROTATIONS_FOR_OCR:
        rotation_dir = work_dir / f"rot{degrees}"
        rotation_dir.mkdir(parents=True, exist_ok=True)
        for crop_path in crop_paths:
            image = cv2.imread(str(crop_path))
            if image is None:
                continue
            destination = rotation_dir / crop_path.name
            cv2.imwrite(str(destination), image if rotation is None else cv2.rotate(image, rotation))
            copies.append(destination)
    return copies


def _autocache_from_printed_total(db: Session, crop_paths: list[Path], settings, prompter: Prompter) -> bool:
    """Identify and cache the set a sheet belongs to by reading the "/264"
    printed on its cards, when no cached set matched its art.

    This is the bootstrap the art match structurally cannot do: perceptual
    hashing needs something cached to compare against, so a set nobody has
    cached yet is invisible to it no matter how good the scan. The printed
    denominator names the set's size directly, off the card itself.

    Returns whether anything new was cached (i.e. whether re-matching is
    worth attempting).
    """
    work_dir = Path(settings.scan_media_dir) / "work" / "_probe" / "_ocr"
    totals = read_set_totals(_rotated_copies(crop_paths, work_dir))
    shutil.rmtree(work_dir, ignore_errors=True)
    if not totals:
        return False

    api_key = getattr(settings, "pokemontcg_api_key", None)
    for total in totals[:AUTOCACHE_TOTALS_TRIED]:
        try:
            candidates = catalog.sets_with_printed_total(total, api_key=api_key)
        except Exception as exc:
            prompter.notify(f"  cards print '/{total}', but the set list could not be fetched: {exc}")
            continue

        uncached = [c for c in candidates if c.get("id") and not catalog.is_cached(db, c["id"])]
        if not uncached:
            continue

        prompter.notify(
            f"  cards print '/{total}' -- caching {min(len(uncached), AUTOCACHE_MAX_SETS)} matching set(s): "
            + ", ".join(c["id"] for c in uncached[:AUTOCACHE_MAX_SETS])
        )
        cached_any = False
        for candidate in uncached[:AUTOCACHE_MAX_SETS]:
            try:
                result = catalog.cache_set(db, candidate["id"], Path(settings.scan_cache_dir), api_key=api_key)
            except Exception as exc:
                prompter.notify(f"    could not cache {candidate['id']}: {exc}")
                continue
            prompter.notify(f"    cached {candidate['id']} ({result.set_name}): {result.cards_total} cards, {result.hashed} hashed")
            cached_any = cached_any or result.ready
        if cached_any:
            return True
    return False


def _run_session_auto(
    db: Session, settings, prompter: Prompter, variant: CardVariant, condition: str,
    expected_count: int | None, user: str, max_sheets: int | None, max_wait: float | None,
    poll_interval: float, price_source: PriceSource | None, unattended: bool,
) -> ScanSession | None:
    """Like `run_session`, but instead of one fixed set for the whole run,
    every new sheet is matched against *all* cached sets (and every
    rotation) to work out where it belongs -- see
    `identify.detect_sheet_set_and_rotation`. Lets an operator cache a
    handful of sets ahead of time and then just scan through piles without
    reconfiguring anything in between.

    A sheet that doesn't confidently match any cached set is left in the
    watch folder rather than guessed at -- same "never auto-commit a
    low-confidence match" contract the rest of this module holds to, just
    applied one level up (which set, not just which card).
    """
    watch_dir = Path(settings.scan_watch_dir)
    watch_dir.mkdir(parents=True, exist_ok=True)
    prompter.notify(f"\nAuto-detecting set and rotation per sheet.\nWatching {watch_dir} -- scan a sheet to begin.")

    seen: set[Path] = set()
    sheets_done = 0
    last_session: ScanSession | None = None

    while max_sheets is None or sheets_done < max_sheets:
        sheet_path = wait_for_sheet(watch_dir, seen, prompter, poll_interval=poll_interval, max_wait=max_wait)
        if sheet_path is None:
            prompter.notify("\nNo new sheet appeared.")
            break

        probe_dir = Path(settings.scan_media_dir) / "work" / "_probe" / sheet_path.stem
        probe = detect_cards(sheet_path, probe_dir, always_save_overlay=False)
        catalogs = _load_all_catalogs(db)
        match = detect_sheet_set_and_rotation(probe.crop_paths, catalogs, max_phash_distance=settings.scan_phash_max_distance) if probe.cards else None

        if match is None and probe.cards:
            # Nothing cached matches -- most often because this pile's set
            # simply hasn't been cached yet. Read the set size off the cards
            # and fetch it, then try once more.
            prompter.notify(f"\nSheet {sheet_path.name}: no cached set matches -- reading the set number off the cards.")
            if _autocache_from_printed_total(db, probe.crop_paths, settings, prompter):
                catalogs = _load_all_catalogs(db)
                match = detect_sheet_set_and_rotation(probe.crop_paths, catalogs, max_phash_distance=settings.scan_phash_max_distance)

        if match is None:
            prompter.notify(
                f"\nSheet {sheet_path.name}: {probe.count} card(s) detected, but none confidently match any "
                "cached set. Left in the watch folder -- cache the right set (scan-ingest cache-set) or check "
                "for a bad scan, then it'll be picked up again."
            )
            seen.add(sheet_path)
            sheets_done += 1
            continue

        set_id, rotation = match
        card_set = db.get(CardSet, set_id)
        references = catalogs[set_id]
        scan_session = start_or_resume(db, set_id, variant, prompter, user=user, unattended=True)
        prompter.notify(f"\nSheet {sheet_path.name}: matched {card_set.name} ({set_id}), rotation {rotation} deg")

        outcome = process_sheet(
            db, sheet_path, scan_session, references, settings, prompter,
            set_total=card_set.printed_total, variant=variant, condition=condition,
            expected_count=expected_count, post_rotation_degrees=rotation, user=user,
            price_source=price_source, unattended=unattended,
        )
        if outcome.archived_to is None:
            seen.add(sheet_path)
        sheets_done += 1
        last_session = scan_session

        holdings, copies = inventory_totals(db, set_id)
        prompter.notify(
            f"\n  Sheet done: {outcome.committed} committed, {outcome.skipped} skipped, {outcome.set_aside} set aside for review."
            f"\n  {card_set.name} inventory: {holdings} distinct, {copies} total."
        )

        if max_sheets is not None and sheets_done >= max_sheets:
            break
        if not unattended and not prompter.confirm("\nScan another sheet?", default=True):
            break

    return last_session


def run_session(
    db: Session, set_id: str | None, settings, prompter: Prompter, variant: CardVariant = CardVariant.NORMAL,
    condition: str = "Near Mint", expected_count: int | None = None, post_rotation_degrees: int = 0,
    user: str = "scan-ingest", max_sheets: int | None = None, max_wait: float | None = None,
    poll_interval: float = POLL_INTERVAL_SECONDS, price_source: PriceSource | None = None,
    unattended: bool = False,
) -> ScanSession | None:
    """The full loop: pick up sheets as they arrive, process each, and keep
    going until the operator stops or `max_sheets` is hit.

    `set_id=None` hands the whole run to `_run_session_auto`, which
    figures out the set (and rotation) per sheet instead of requiring one
    fixed set up front -- see that function's docstring.

    `unattended` is for running as a long-lived background service (see
    scan_ingest/cli.py's `scan --unattended`): every convenience prompt
    that isn't safety-critical auto-answers instead of blocking on a
    terminal that may not be attended for hours -- resume without asking,
    keep scanning without asking, never auto-mark-finished (the service is
    meant to just keep running). Ambiguous *cards* still never auto-commit;
    see `process_sheet` for how those get handled instead of prompted for.
    """
    if set_id is None:
        return _run_session_auto(
            db, settings, prompter, variant, condition, expected_count, user,
            max_sheets, max_wait, poll_interval, price_source, unattended,
        )

    if not catalog.is_cached(db, set_id):
        raise ValueError(f"set {set_id!r} is not cached -- run: scan-ingest cache-set --set-id {set_id}")

    card_set = db.get(CardSet, set_id)
    references = [
        ReferenceCard(card_id=c.id, number=c.number, name=c.name, phash=c.phash,
                      art_phash=c.art_phash, rarity=c.rarity)
        for c in catalog.get_cached_cards(db, set_id) if c.phash
    ]
    if not references:
        raise ValueError(f"set {set_id!r} has no reference hashes -- re-run cache-set --force")

    scan_session = start_or_resume(db, set_id, variant, prompter, user=user, unattended=unattended)
    watch_dir = Path(settings.scan_watch_dir)
    watch_dir.mkdir(parents=True, exist_ok=True)

    holdings, copies = inventory_totals(db, set_id)
    prompter.notify(
        f"\nScanning {card_set.name} ({set_id}) as {variant.value}, condition {condition}."
        f"\nInventory so far: {holdings} distinct card(s), {copies} total."
        f"\nWatching {watch_dir} -- scan a sheet to begin."
    )

    # Sheets already sitting in the watch folder are fair game; anything
    # left there from an aborted run should be picked up rather than
    # silently ignored.
    seen: set[Path] = set()
    sheets_done = 0

    while max_sheets is None or sheets_done < max_sheets:
        sheet_path = wait_for_sheet(watch_dir, seen, prompter, poll_interval=poll_interval, max_wait=max_wait)
        if sheet_path is None:
            prompter.notify("\nNo new sheet appeared.")
            break

        outcome = process_sheet(
            db, sheet_path, scan_session, references, settings, prompter,
            set_total=card_set.printed_total, variant=variant, condition=condition,
            expected_count=expected_count, post_rotation_degrees=post_rotation_degrees, user=user,
            price_source=price_source, unattended=unattended,
        )
        if outcome.archived_to is None:
            # left in place for a rescan -- don't offer it again this run
            seen.add(sheet_path)
        sheets_done += 1

        holdings, copies = inventory_totals(db, set_id)
        prompter.notify(
            f"\n  Sheet done: {outcome.committed} committed, {outcome.skipped} skipped, {outcome.set_aside} set aside for review."
            f"\n  Session total: {scan_session.sheets_scanned} sheet(s), {scan_session.cards_committed} card(s)."
            f"\n  {card_set.name} inventory: {holdings} distinct, {copies} total."
        )

        if max_sheets is not None and sheets_done >= max_sheets:
            break
        if not unattended and not prompter.confirm("\nScan another sheet?", default=True):
            break

    if not unattended and prompter.confirm("Mark this session finished? (No keeps it resumable)", default=False):
        scan_session.status = ScanSessionStatus.DONE
        scan_session.finished_at = datetime.now(UTC)
    db.commit()

    prompter.notify(
        f"\nSession {'finished' if scan_session.status is ScanSessionStatus.DONE else 'left open for resume'}: "
        f"{scan_session.sheets_scanned} sheet(s), {scan_session.cards_committed} card(s)."
    )
    return scan_session
