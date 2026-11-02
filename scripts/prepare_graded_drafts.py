"""Create the three reviewed graded-card drafts and confirm their approved prices.

Local database only: this never calls eBay. Snapshot the database first, then
pass it explicitly. Safe to rerun, since drafts are matched by grader + certificate.

    .venv/bin/python scripts/prepare_graded_drafts.py --database automation.db
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.audit import record_event
from automation_control.config import get_settings
from automation_control.ebay_publish import publication_preflight
from automation_control.graded_cards import confirm_price, prepare_graded_draft
from automation_control.models import ListingPhotoPair
from scripts.convert_to_ebay_template import sanitize_title

REVIEWED_BY = "Claude Code, from the slab label and certificate photos"
FANATICS = "https://www.fanaticscollect.com"

SLABS = [
    {
        "pair": "30ac06cd-eed8-44e5-bf54-de85593c9118",
        "price": "34.99",
        "facts": {
            "name": "Demon God Shroom & Demon God Salsa, Unending Nightmare", "company": "CGC", "grade": "Pristine 10",
            "certificate": "4245735015", "set_id": "dbs-3132", "set_name": "Dawn of the Z-Legends",
            "set_code": "DBS-B18", "game": "dragon_ball_super", "character": "Demon God Shroom & Demon God Salsa",
            "number": "BT18-133", "language": "English", "rarity": "Super Rare", "year": "2022",
            "title": "CGC Pristine 10 Demon God Shroom & Salsa BT18-133 SR Dragon Ball Super 2022",
        },
        "research": {
            "research_note": "Exact card, grader and grade sold for US$16.80 on 22 Dec 2025 (different certificate). "
                             "One dated comparable: moderate confidence.",
            "sources": [{"label": "Fanatics sale, 22 Dec 2025", "url": f"{FANATICS}/weekly/3dfae4c0-d63b-11f0-a505-0a58a9feac02/2022-dragon-ball-super-dawn-of-z-legends-demon-god-shroom-salsa-sr-bt18-133-cgc-10-pristine"}],
        },
    },
    {
        "pair": "1872982d-4e4b-4584-8bbc-1d43800a46b7",
        "price": "19.99",
        "facts": {
            "name": "Jiren - FB03-009 (Alternate Art)", "company": "Vault", "grade": "Mint 9",
            "certificate": "00003575", "set_id": "dbfw-23517", "set_name": "Raging Roar",
            "set_code": "FB03", "game": "dragon_ball_fusion_world", "character": "Jiren",
            "number": "FB03-009", "language": "English", "rarity": "Super Rare", "parallel": "Alternate Art", "year": "2024",
            "title": "Vault Mint 9 Jiren FB03-009 Alt Art SR Raging Roar Dragon Ball Fusion World",
        },
        "research": {
            "research_note": "No Vault 9 sale found. Exact card in PSA 10 sold for US$24 on 13 Jan 2025; raw alternate art "
                             "guide US$4.15. Different grader and grade: low confidence.",
            "sources": [
                {"label": "Fanatics PSA 10 sale, 13 Jan 2025", "url": f"{FANATICS}/weekly/4ed7c70a-c7bd-11ef-b9a9-0a58a9feac02/2024-dragon-ball-super-fusion-world-raging-roar-alt-art-jiren-fb03-009-psa-10"},
                {"label": "TCGplayer raw price guide", "url": "https://www.tcgplayer.com/categories/trading-and-collectible-card-games/dragon-ball-super-fusion-world/price-guides/raging-roar"},
            ],
        },
    },
    {
        "pair": "f0bbe565-9712-4033-b827-a6335b4c0234",
        "price": "24.99",
        "facts": {
            "name": "Venusaur V", "company": "CGC", "grade": "Mint 9",
            "certificate": "3813309008", "set_id": "pokemon-jp-sef", "set_name": "Venusaur VMAX Starter Set",
            "set_code": "sEF", "game": "pokemon_japan", "character": "Venusaur V",
            "number": "001/021", "language": "Japanese", "year": "2020",
            "title": "CGC Mint 9 Venusaur V 001/021 sEF Venusaur VMAX Starter Set Japanese Pokemon",
        },
        "research": {
            "research_note": "Exact card, grader and grade sold for US$22 on 26 Feb 2024, and for US$10.80 and US$12 in 2022. "
                             "Dated evidence: low to moderate confidence.",
            "sources": [{"label": "Fanatics sale, 26 Feb 2024", "url": f"{FANATICS}/buy-now/061ba2da-72a1-42c4-b329-e58e0cc3905e/2020-pokemon-japanese-sword-shield-starter-set-venusaur-v-1-cgc-9-mint"}],
        },
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error(f"no database at {args.database}")

    engine = create_engine(f"sqlite+pysqlite:///{args.database}", connect_args={"timeout": 30})
    settings = get_settings()
    with Session(engine) as session:
        for slab in SLABS:
            facts = {**slab["facts"], "reviewed_by": REVIEWED_BY}
            facts["title"] = sanitize_title(facts["title"])
            if len(facts["title"]) > 80:
                raise ValueError(f"title over 80 characters: {facts['title']}")
            pair = session.get(ListingPhotoPair, slab["pair"])
            if pair is None:
                raise ValueError(f"photo pair not found: {slab['pair']}")
            research = {**slab["research"], "suggested_price": slab["price"]}
            listing = prepare_graded_draft(session, pair, facts, research)
            record_event(session, actor_type="user", actor_id="nic", action="graded.prepare_draft",
                         resource_type="shop_listing", resource_id=listing.id, outcome="success",
                         correlation_id=str(uuid.uuid4()), details={"pair_id": pair.id, "certificate": facts["certificate"], "reviewed_by": REVIEWED_BY})
            confirm_price(listing.id, price=slab["price"], user="nic", session=session)
            check = publication_preflight(session, listing, settings)
            print(f"{listing.id} | {listing.status.value} | ${listing.price} | {listing.ebay_title}")
            print(f"  category={check.category_id} preflight_errors={list(check.errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
