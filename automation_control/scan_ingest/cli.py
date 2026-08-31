import argparse
import getpass
import sys
from pathlib import Path

from ..adapters.pokemontcg_catalog import CatalogLookupError
from ..config import get_settings
from ..database import Base, SessionLocal, engine
from ..models import CardSet, CardVariant
from . import catalog
from .detect import detect_cards
from .hardware import ScanError, list_devices, scan_sheet
from .identify import ReferenceCard, identify_card
from .session import ConsolePrompter, process_sheet, resolve_review_queue, run_session, start_or_resume


def _detect(args: argparse.Namespace) -> int:
    result = detect_cards(
        Path(args.sheet), Path(args.out_dir),
        expected_count=args.expected_count, always_save_overlay=args.save_overlay,
        post_rotation_degrees=args.rotate,
    )
    expected = f"/{result.expected_count}" if result.expected_count is not None else ""
    print(f"Detected {result.count}{expected} card(s) on {args.sheet} ({result.rows} rows x {result.cols} cols)")
    for index, path in enumerate(result.crop_paths):
        print(f"  {index + 1}: {path} (aspect {result.cards[index].aspect_ratio:.3f})")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    if result.overlay_path:
        print(f"Overlay: {result.overlay_path}")
    return 1 if result.needs_review else 0


def _sets(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.cached:
        with SessionLocal() as session:
            rows = catalog.cached_sets(session)
            if not rows:
                print("No sets cached yet. Run: scan-ingest cache-set --set-id <id>")
                return 0
            print(f"{len(rows)} set(s) cached locally:")
            for card_set in rows:
                cards = catalog.get_cached_cards(session, card_set.id)
                hashed = sum(1 for c in cards if c.phash)
                print(f"  {card_set.id:<10} {card_set.name:<34} {card_set.release_date or '?':<11} {len(cards):>4} cards, {hashed:>4} hashed")
        return 0

    matches = catalog.search_sets(args.query or "", api_key=settings.pokemontcg_api_key)
    if not matches:
        print(f"No sets matched {args.query!r}")
        return 1
    print(f"{len(matches)} set(s):")
    for card_set in matches:
        print(f"  {card_set['id']:<10} {card_set.get('name', ''):<34} {card_set.get('releaseDate') or '?':<11} {card_set.get('printedTotal') or '?':>4} printed  [{card_set.get('ptcgoCode') or '-'}]")
    return 0


def _cache_set(args: argparse.Namespace) -> int:
    settings = get_settings()
    Base.metadata.create_all(engine)
    cache_dir = Path(args.cache_dir or settings.scan_cache_dir)

    def progress(done: int, total: int, name: str) -> None:
        # Caching a set means a few hundred image downloads against a flaky
        # API, so show live progress rather than appearing to hang.
        print(f"\r  [{done}/{total}] {name[:40]:<40}", end="", flush=True)

    with SessionLocal() as session:
        try:
            result = catalog.cache_set(
                session, args.set_id, cache_dir,
                api_key=settings.pokemontcg_api_key, force=args.force, progress=progress,
            )
        except CatalogLookupError as exc:
            print(f"\nerror: {exc}", file=sys.stderr)
            return 1

    print()
    if result.from_cache:
        print(f"{result.set_id} ({result.set_name}) already cached: {result.cards_total} cards, {result.hashed} hashed.")
        print("Re-run with --force to refresh.")
        return 0

    print(f"Cached {result.set_id} ({result.set_name}): {result.cards_total} cards, {result.images_downloaded} images downloaded, {result.hashed} hashed.")
    if result.images_failed:
        print(f"WARNING: {result.images_failed} image(s) failed -- re-run to retry just those.")
    if not result.ready:
        print("WARNING: no usable perceptual hashes -- identification would fall back to OCR only.")
        return 1
    return 0


def _identify(args: argparse.Namespace) -> int:
    settings = get_settings()
    Base.metadata.create_all(engine)

    with SessionLocal() as session:
        if not catalog.is_cached(session, args.set_id):
            print(f"error: set {args.set_id!r} is not cached. Run: scan-ingest cache-set --set-id {args.set_id}", file=sys.stderr)
            return 1
        card_set = session.get(catalog.CardSet, args.set_id)
        references = [
            ReferenceCard(card_id=c.id, number=c.number, name=c.name, phash=c.phash)
            for c in catalog.get_cached_cards(session, args.set_id) if c.phash
        ]
        set_total = card_set.printed_total

    sheet_dir = Path(args.crops_dir)
    crops = sorted(sheet_dir.glob("card*.jpg"), key=lambda p: int("".join(ch for ch in p.stem if ch.isdigit()) or 0))
    if not crops:
        print(f"error: no card*.jpg crops found in {sheet_dir}", file=sys.stderr)
        return 1

    print(f"Identifying {len(crops)} crop(s) against {args.set_id} ({len(references)} reference cards)\n")
    header = f"{'#':<4} {'card':<30} {'source':<9} {'conf':<6} {'dist':<5} {'margin':<7} {'ocr':<10} flag"
    print(header)
    print("-" * len(header))

    needs_review = 0
    for index, crop in enumerate(crops, start=1):
        result = identify_card(crop, references, set_total=set_total, max_phash_distance=settings.scan_phash_max_distance)
        label = f"{result.number} {result.name}" if result.card_id else "(unidentified)"
        flag = "CONFIRM" if result.needs_confirmation else ""
        needs_review += bool(flag)
        print(
            f"{index:<4} {label[:30]:<30} {result.source.value:<9} {result.confidence:<6.2f} "
            f"{str(result.phash_distance or '-'):<5} {str(result.phash_margin or '-'):<7} "
            f"{(result.ocr_text or '-')[:10]:<10} {flag}"
        )
        if result.note:
            print(f"     note: {result.note}")

    print(f"\n{len(crops) - needs_review}/{len(crops)} identified confidently; {needs_review} need confirmation.")
    return 0


def _scan(args: argparse.Namespace) -> int:
    settings = get_settings()
    Base.metadata.create_all(engine)
    prompter = ConsolePrompter()

    with SessionLocal() as session:
        try:
            run_session(
                session, args.set_id, settings, prompter,
                variant=CardVariant(args.variant), condition=args.condition,
                expected_count=args.expected_count, post_rotation_degrees=args.rotate,
                user=getpass.getuser(), unattended=args.unattended,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            # Counts are committed per sheet, so an interrupt loses at most
            # the sheet in progress and the session stays resumable.
            print("\nInterrupted -- session left open, resume with the same command.")
            return 130
    return 0


def _reprocess(args: argparse.Namespace) -> int:
    """Re-run detect/identify/commit on one already-scanned sheet -- for
    recovering a sheet that was scanned while the wrong set (or no set) was
    cached, without having to physically rescan it. The sheet can be
    anywhere on disk (e.g. still sitting in the watch folder after a
    detection warning, or already moved into scan_archive_dir)."""
    settings = get_settings()
    Base.metadata.create_all(engine)
    prompter = ConsolePrompter()
    sheet_path = Path(args.sheet)
    if not sheet_path.exists():
        print(f"error: sheet not found: {sheet_path}", file=sys.stderr)
        return 1

    with SessionLocal() as session:
        if not catalog.is_cached(session, args.set_id):
            print(f"error: set {args.set_id!r} is not cached. Run: scan-ingest cache-set --set-id {args.set_id}", file=sys.stderr)
            return 1
        card_set = session.get(CardSet, args.set_id)
        references = [
            ReferenceCard(card_id=c.id, number=c.number, name=c.name, phash=c.phash)
            for c in catalog.get_cached_cards(session, args.set_id) if c.phash
        ]
        if not references:
            print(f"error: set {args.set_id!r} has no reference hashes -- re-run cache-set --force", file=sys.stderr)
            return 1

        variant = CardVariant(args.variant)
        scan_session = start_or_resume(session, args.set_id, variant, prompter, user=getpass.getuser(), unattended=True)
        outcome = process_sheet(
            session, sheet_path, scan_session, references, settings, prompter,
            set_total=card_set.printed_total, variant=variant, condition=args.condition,
            expected_count=args.expected_count, post_rotation_degrees=args.rotate,
            user=getpass.getuser(), unattended=args.unattended,
        )
    print(f"\n{outcome.committed} committed, {outcome.skipped} skipped, {outcome.set_aside} set aside for review.")
    return 0


def _review(args: argparse.Namespace) -> int:
    """Settle the cards unattended scanning set aside rather than guessing."""
    settings = get_settings()
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        try:
            resolve_review_queue(
                session, settings, ConsolePrompter(),
                variant=CardVariant(args.variant), condition=args.condition,
                user=getpass.getuser(), limit=args.limit,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            # Each card commits as it's confirmed, so quitting loses nothing
            # and the queue picks up where it left off.
            print("\nStopped -- the rest of the queue is untouched.")
            return 130
    return 0


def _capture(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        if args.list_devices:
            devices = list_devices()
            if not devices:
                print("No scanners detected.")
                return 1
            print("Detected scanner(s):")
            for device in devices:
                print(f"  {device}")
            return 0

        path = scan_sheet(Path(settings.scan_watch_dir), device=args.device, resolution=args.resolution, mode=args.mode)
    except ScanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Scanned sheet saved: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scan-ingest", description="EzBay bulk card scan ingest")
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect_p = subparsers.add_parser("detect", help="detect and crop cards from one scanned sheet")
    detect_p.add_argument("--sheet", required=True, help="path to the scanned sheet image")
    detect_p.add_argument("--out-dir", required=True, help="directory to write card crops (and overlay, if any) into")
    detect_p.add_argument("--expected-count", type=int, default=None, help="assert this many cards were found; omit to accept whatever grid the sheet actually has")
    detect_p.add_argument("--save-overlay", action="store_true", help="always save the annotated overlay, even when the count matches")
    detect_p.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="degrees clockwise to rotate every crop upright, if cards are placed sideways on the bed (default 0)")

    sets_p = subparsers.add_parser("sets", help="find sets to scan, or list what's already cached")
    sets_p.add_argument("query", nargs="?", help="substring of a set name, code, or id (e.g. 'steam siege', 'STS', 'xy11')")
    sets_p.add_argument("--cached", action="store_true", help="list locally cached sets instead of searching upstream")

    cache_p = subparsers.add_parser("cache-set", help="download and cache one set's card list, images, and hashes")
    cache_p.add_argument("--set-id", required=True, help="pokemontcg.io set id, e.g. xy11")
    cache_p.add_argument("--cache-dir", default=None, help="override the configured cache directory")
    cache_p.add_argument("--force", action="store_true", help="re-fetch even if already cached")

    id_p = subparsers.add_parser("identify", help="identify already-cropped cards against a cached set (prints results, writes nothing)")
    id_p.add_argument("--crops-dir", required=True, help="directory of card*.jpg crops produced by `detect`")
    id_p.add_argument("--set-id", required=True, help="the cached set these cards come from, e.g. xy11")

    scan_p = subparsers.add_parser("scan", help="interactive scanning session: watch the folder, identify, confirm, commit to inventory")
    scan_p.add_argument("--set-id", default=None, help="the cached set you're scanning, e.g. xy11. Omit to auto-detect the set (and rotation) per sheet from every cached set -- see 'reprocess' for recovering a sheet after cache-set'ing a set you hadn't cached yet")
    scan_p.add_argument("--variant", default=CardVariant.NORMAL.value, choices=[v.value for v in CardVariant], help="printing for this batch; can be corrected per card (default normal)")
    scan_p.add_argument("--condition", default="Near Mint", help="condition for this batch (default 'Near Mint')")
    scan_p.add_argument("--expected-count", type=int, default=None, help="cards per sheet, to catch mis-detections (e.g. 8)")
    scan_p.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="degrees clockwise to make crops upright (90 for cards laid sideways). Ignored when --set-id is omitted -- rotation is auto-detected per sheet")
    scan_p.add_argument("--unattended", action="store_true", help="never block on a prompt (for running as a background service) -- ambiguous cards are set aside for later review instead of asked about")

    reprocess_p = subparsers.add_parser("reprocess", help="re-run detect/identify/commit on one already-scanned sheet (e.g. after fixing the active set or a detection bug), without rescanning")
    reprocess_p.add_argument("--sheet", required=True, help="path to the sheet image, wherever it currently sits (watch or archive folder)")
    reprocess_p.add_argument("--set-id", required=True, help="the cached set to identify against, e.g. sm10")
    reprocess_p.add_argument("--variant", default=CardVariant.NORMAL.value, choices=[v.value for v in CardVariant], help="printing for this batch (default normal)")
    reprocess_p.add_argument("--condition", default="Near Mint", help="condition for this batch (default 'Near Mint')")
    reprocess_p.add_argument("--expected-count", type=int, default=None, help="assert this many cards were found")
    reprocess_p.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="degrees clockwise to make crops upright")
    reprocess_p.add_argument("--unattended", action="store_true", help="set aside ambiguous cards instead of prompting")

    review_p = subparsers.add_parser("review", help="confirm the cards unattended scanning set aside, one at a time, and commit them")
    review_p.add_argument("--variant", default=CardVariant.NORMAL.value, choices=[v.value for v in CardVariant], help="printing to commit these as (default normal)")
    review_p.add_argument("--condition", default="Near Mint", help="condition to commit these as (default 'Near Mint')")
    review_p.add_argument("--limit", type=int, default=None, help="only work through this many, so a big queue can be done in sittings")

    capture_p = subparsers.add_parser("capture", help="scan one sheet on the attached flatbed straight into the watch folder")
    capture_p.add_argument("--device", default=None, help="scanimage device id (see --list-devices); omit to auto-pick the only connected scanner")
    capture_p.add_argument("--resolution", type=int, default=600, help="DPI (default 600)")
    capture_p.add_argument("--mode", default="Color", help="scan mode (default Color)")
    capture_p.add_argument("--list-devices", action="store_true", help="list detected scanners and exit")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "detect":
        return _detect(args)
    if args.command == "sets":
        return _sets(args)
    if args.command == "cache-set":
        return _cache_set(args)
    if args.command == "identify":
        return _identify(args)
    if args.command == "scan":
        return _scan(args)
    if args.command == "reprocess":
        return _reprocess(args)
    if args.command == "review":
        return _review(args)
    if args.command == "capture":
        return _capture(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
