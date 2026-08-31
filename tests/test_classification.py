from decimal import Decimal

from automation_control.classification import classify_pending_cards
from automation_control.models import CapturedCard, CardCaptureStatus, PricingStatus


def _priced_card(price: str, **overrides) -> CapturedCard:
    defaults = dict(
        front_image_path="f",
        character="Flareon",
        set_name="Promo",
        card_number="167",
        status=CardCaptureStatus.REVIEWED,
        pricing_status=PricingStatus.PENDING_PRICE_REVIEW,
        suggested_price=Decimal(price),
        ai_raw_response={},
    )
    defaults.update(overrides)
    return CapturedCard(**defaults)


def test_cards_under_threshold_are_grouped_into_one_lot(session):
    cards = [_priced_card("2.00") for _ in range(3)]
    session.add_all(cards)
    session.commit()

    classify_pending_cards(session, threshold_aud=Decimal("8.00"), max_lot_size=8)

    session.expire_all()
    bundle_ids = {session.get(CapturedCard, c.id).bundle_id for c in cards}
    assert len(bundle_ids) == 1
    assert None not in bundle_ids


def test_cards_at_or_above_threshold_stay_singles(session):
    cards = [_priced_card("8.00"), _priced_card("15.00")]
    session.add_all(cards)
    session.commit()

    classify_pending_cards(session, threshold_aud=Decimal("8.00"), max_lot_size=8)

    session.expire_all()
    for card in cards:
        assert session.get(CapturedCard, card.id).bundle_id is None


def test_lots_split_at_max_lot_size(session):
    cards = [_priced_card("1.00") for _ in range(10)]
    session.add_all(cards)
    session.commit()

    classify_pending_cards(session, threshold_aud=Decimal("8.00"), max_lot_size=8)

    session.expire_all()
    bundle_ids = [session.get(CapturedCard, c.id).bundle_id for c in cards]
    first_lot = bundle_ids[:8]
    remainder = bundle_ids[8:]
    assert len(set(first_lot)) == 1 and first_lot[0] is not None
    # remainder of 2 forms its own (smaller) lot
    assert len(set(remainder)) == 1 and remainder[0] is not None
    assert first_lot[0] != remainder[0]


def test_single_leftover_card_stays_ungrouped(session):
    cards = [_priced_card("1.00") for _ in range(9)]
    session.add_all(cards)
    session.commit()

    classify_pending_cards(session, threshold_aud=Decimal("8.00"), max_lot_size=8)

    session.expire_all()
    bundle_ids = [session.get(CapturedCard, c.id).bundle_id for c in cards]
    assert bundle_ids[8] is None
    assert len(set(bundle_ids[:8])) == 1 and bundle_ids[0] is not None


def test_already_bundled_cards_are_left_alone(session):
    card = _priced_card("1.00", bundle_id="existing-bundle")
    session.add(card)
    session.commit()

    classify_pending_cards(session, threshold_aud=Decimal("8.00"), max_lot_size=8)

    session.expire_all()
    assert session.get(CapturedCard, card.id).bundle_id == "existing-bundle"


def test_unpriced_or_wrong_status_cards_are_ignored(session):
    not_priced = _priced_card("1.00", pricing_status=PricingStatus.NOT_PRICED, suggested_price=None)
    already_approved = _priced_card("1.00", pricing_status=PricingStatus.PRICE_APPROVED)
    session.add_all([not_priced, already_approved])
    session.commit()

    classify_pending_cards(session, threshold_aud=Decimal("8.00"), max_lot_size=8)

    session.expire_all()
    assert session.get(CapturedCard, not_priced.id).bundle_id is None
    assert session.get(CapturedCard, already_approved.id).bundle_id is None


def test_max_lot_size_below_two_is_a_no_op(session):
    cards = [_priced_card("1.00") for _ in range(5)]
    session.add_all(cards)
    session.commit()

    classify_pending_cards(session, threshold_aud=Decimal("8.00"), max_lot_size=1)

    session.expire_all()
    assert all(session.get(CapturedCard, c.id).bundle_id is None for c in cards)
