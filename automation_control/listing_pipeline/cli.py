import argparse
import sys
from pathlib import Path

from .config import load_config
from .errors import CardValidationError, GroupingError, SalePlanMatchError
from .export import write_bulk_upload_csv, write_image_checklist_csv, write_validation_report
from .group import group_cards
from .ingest import read_inventory, read_sale_plan
from .models import CardRecord, Listing
from .render import RenderedListing, render_all


def _ingest(args: argparse.Namespace) -> list[CardRecord]:
    cards = read_inventory(args.source)
    print(f"Ingested {len(cards)} cards from {args.source}")
    for card in cards:
        print(f"  {card.card_id}: {card.character} ({card.set_name} #{card.card_number}) [{card.bundle_tag}]")
    return cards


def _group(args: argparse.Namespace) -> tuple[list[Listing], object]:
    cards = _ingest(args)
    sale_plan = read_sale_plan(args.source)
    config = load_config(args.config)
    listings = group_cards(cards, sale_plan, config)
    print(f"Grouped into {len(listings)} listings")
    for listing in listings:
        kind = "bundle" if listing.is_bundle else "single"
        print(f"  {listing.custom_label} ({kind}): {', '.join(listing.card_ids)}")
    return listings, config


def _render(args: argparse.Namespace) -> tuple[list[RenderedListing], object]:
    listings, config = _group(args)
    rendered = render_all(listings, config)
    print(f"Rendered {len(rendered)} listings")
    for item in rendered:
        print(f"  {item.listing.custom_label}: title={item.title!r} ({len(item.title)} chars), description={len(item.description_html)} chars")
    return rendered, config


def _export(args: argparse.Namespace) -> None:
    rendered, config = _render(args)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    write_bulk_upload_csv(rendered, config, out_dir / "ebay_bulk_upload.csv", warnings)
    write_image_checklist_csv(rendered, config, out_dir / "image_naming_checklist.csv")
    write_validation_report(warnings, out_dir / "validation_report.csv")
    print(f"Wrote {len(rendered)} listings to {out_dir}")
    if warnings:
        print(f"{len(warnings)} validation warning(s) -- see validation_report.csv")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="listing-pipeline", description="eBay trading-card listing pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_p = subparsers.add_parser("ingest", help="read and validate the source inventory")
    ingest_p.add_argument("--source", required=True, help="path to the source .xlsx workbook")

    group_p = subparsers.add_parser("group", help="ingest, then apply bundling rules from config")
    group_p.add_argument("--source", required=True)
    group_p.add_argument("--config", required=True, help="path to config.toml")

    render_p = subparsers.add_parser("render", help="ingest + group, then build titles and descriptions")
    render_p.add_argument("--source", required=True)
    render_p.add_argument("--config", required=True)

    export_p = subparsers.add_parser("export", help="run the full pipeline and write output CSVs")
    export_p.add_argument("--source", required=True)
    export_p.add_argument("--config", required=True)
    export_p.add_argument("--out-dir", required=True)

    run_p = subparsers.add_parser("run", help="alias for export")
    run_p.add_argument("--source", required=True)
    run_p.add_argument("--config", required=True)
    run_p.add_argument("--out-dir", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest":
            _ingest(args)
        elif args.command == "group":
            _group(args)
        elif args.command == "render":
            _render(args)
        elif args.command in ("export", "run"):
            _export(args)
    except (CardValidationError, GroupingError, SalePlanMatchError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
