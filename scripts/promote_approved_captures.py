"""Hand approved photo captures to Inventory and the Bulk Upload queue.

The same service runs automatically when a price is approved in the UI.
This command is the backfill for approvals made before that handoff existed.
Nothing is silently forced: unsafe rows become visible NEEDS_REVIEW records.

    .venv/bin/python -m scripts.promote_approved_captures --apply
"""

import argparse
import uuid
from collections import Counter

from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.capture_promotion import promote_approved_card
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import (
    CapturePromotion, CapturePromotionStatus, CapturedCard, PricingStatus,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform handoffs; without this, report only")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        already_recorded = set(session.scalars(
            select(CapturePromotion.captured_card_id).where(
                CapturePromotion.status.in_((
                    CapturePromotionStatus.PROMOTED,
                    CapturePromotionStatus.REVISION_REQUIRED,
                ))
            )
        ))
        cards = [
            card for card in session.scalars(
                select(CapturedCard).where(CapturedCard.pricing_status == PricingStatus.PRICE_APPROVED)
                .order_by(CapturedCard.captured_at)
            )
            if card.id not in already_recorded
        ][:args.limit]
        print(f"approved captures awaiting handoff: {len(cards)}")
        if not args.apply:
            print("report only; add --apply to process them")
            return 0

        counts: Counter[str] = Counter()
        settings = get_settings()
        for card in cards:
            result = promote_approved_card(session, card, settings)
            session.commit()
            outcome = "promoted" if result.promoted else "needs_review"
            counts[outcome] += 1
            print(f"{outcome}: {card.character} #{card.card_number}"
                  + (f" — {result.issue}" if result.issue else ""))
            record_event(
                session, actor_type="system", actor_id="capture-promotion-backfill",
                action="capture.promote", resource_type="captured_card",
                resource_id=card.id, outcome="success" if result.promoted else "blocked",
                correlation_id=str(uuid.uuid4()),
                details={"shop_listing_id": result.shop_listing_id, "issue": result.issue},
            )

    print(f"promoted={counts['promoted']} needs_review={counts['needs_review']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
