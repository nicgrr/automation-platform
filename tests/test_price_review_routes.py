from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from automation_control.adapters.currency import CurrencyLookupError
from automation_control.adapters.pokemontcg import PriceLookupError, PriceNotFound
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import AuditEvent, CapturedCard, CardCaptureStatus, PricingStatus
from automation_control.price_review import _run_price_check, _try_start_price_check, _finish_price_check


@pytest.fixture
def engine(tmp_path):
    db_path = tmp_path / "test.db"
    return create_engine(f"sqlite+pysqlite:///{db_path}", connect_args={"check_same_thread": False})


@pytest.fixture
def session(engine):
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def price_check_session_local(engine):
    # _run_price_check opens its own SessionLocal() (it's designed to run as
    # a background task with its own DB session, separate from any
    # request-scoped one) -- point that at the same test engine so its
    # writes are visible to the test's `session` object. Also stub the
    # USD->AUD rate lookup (a real HTTP call) with a fixed, predictable rate.
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with patch("automation_control.price_review.SessionLocal", test_session_factory), \
         patch("automation_control.price_review.get_usd_to_aud_rate", return_value=Decimal("1.5")):
        yield


@pytest.fixture
def client(session: Session, price_check_session_local, tmp_path):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _reviewed_card(**overrides) -> CapturedCard:
    defaults = dict(front_image_path="f", character="Flareon", set_name="Promo", card_number="167", status=CardCaptureStatus.REVIEWED, ai_raw_response={})
    defaults.update(overrides)
    return CapturedCard(**defaults)


# --- _run_price_check (called directly: deterministic, no background-task timing) ---

def test_run_price_check_sets_suggested_price_on_success(session, price_check_session_local):
    card = _reviewed_card()
    session.add(card)
    session.commit()

    # price_check_session_local stubs the USD->AUD rate at 1.5
    with patch("automation_control.price_review.lookup_price", return_value=Decimal("12.50")):
        _run_price_check(api_key=None, user="testuser")

    session.expire_all()
    refreshed = session.get(CapturedCard, card.id)
    assert refreshed.suggested_price == Decimal("18.75")  # 12.50 USD * 1.5
    assert refreshed.price_source == "pokemontcg_market_usd_to_aud"
    assert refreshed.pricing_status == PricingStatus.PENDING_PRICE_REVIEW
    assert refreshed.price_lookup_error is None
    assert refreshed.price_checked_at is not None


def test_run_price_check_skips_entirely_when_currency_rate_unavailable(session, engine):
    card = _reviewed_card()
    session.add(card)
    session.commit()

    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with patch("automation_control.price_review.SessionLocal", test_session_factory), \
         patch("automation_control.price_review.get_usd_to_aud_rate", side_effect=CurrencyLookupError("frankfurter.dev down")), \
         patch("automation_control.price_review.lookup_price") as mock_lookup:
        _run_price_check(api_key=None, user="testuser")
        mock_lookup.assert_not_called()

    session.expire_all()
    refreshed = session.get(CapturedCard, card.id)
    assert refreshed.pricing_status == PricingStatus.NOT_PRICED
    assert refreshed.suggested_price is None
    events = session.query(AuditEvent).filter(AuditEvent.action == "card.price_check", AuditEvent.outcome == "failed").all()
    assert any("USD->AUD" in (e.details or {}).get("error", "") for e in events)


def test_run_price_check_records_error_without_guessing_a_price(session, price_check_session_local):
    card = _reviewed_card()
    session.add(card)
    session.commit()

    with patch("automation_control.price_review.lookup_price", side_effect=PriceNotFound("no match")):
        _run_price_check(api_key=None, user="testuser")

    session.expire_all()
    refreshed = session.get(CapturedCard, card.id)
    assert refreshed.suggested_price is None
    assert refreshed.pricing_status == PricingStatus.NOT_PRICED
    assert "no match" in refreshed.price_lookup_error


def test_run_price_check_records_error_on_api_failure(session, price_check_session_local):
    card = _reviewed_card()
    session.add(card)
    session.commit()

    with patch("automation_control.price_review.lookup_price", side_effect=PriceLookupError("pokemontcg.io unavailable")):
        _run_price_check(api_key=None, user="testuser")

    session.expire_all()
    refreshed = session.get(CapturedCard, card.id)
    assert refreshed.pricing_status == PricingStatus.NOT_PRICED
    assert "unavailable" in refreshed.price_lookup_error


def test_run_price_check_skips_cards_missing_identity_fields(session, price_check_session_local):
    card = _reviewed_card(character=None, set_name=None)
    session.add(card)
    session.commit()

    with patch("automation_control.price_review.lookup_price") as mock_lookup:
        _run_price_check(api_key=None, user="testuser")
        mock_lookup.assert_not_called()

    session.expire_all()
    refreshed = session.get(CapturedCard, card.id)
    assert "missing character or set name" in refreshed.price_lookup_error


def test_run_price_check_ignores_cards_not_yet_identity_reviewed(session, price_check_session_local):
    card = _reviewed_card(status=CardCaptureStatus.PENDING_REVIEW)
    session.add(card)
    session.commit()

    with patch("automation_control.price_review.lookup_price") as mock_lookup:
        _run_price_check(api_key=None, user="testuser")
        mock_lookup.assert_not_called()


def test_run_price_check_ignores_already_priced_cards(session, price_check_session_local):
    card = _reviewed_card(pricing_status=PricingStatus.PENDING_PRICE_REVIEW, suggested_price=Decimal("5.00"))
    session.add(card)
    session.commit()

    with patch("automation_control.price_review.lookup_price") as mock_lookup:
        _run_price_check(api_key=None, user="testuser")
        mock_lookup.assert_not_called()


def test_run_price_check_records_one_audit_event_per_card(session, price_check_session_local):
    # regression test: record_event used to sit outside the per-card loop,
    # so a batch of N cards produced exactly one event (for whichever card
    # happened to be last), not N.
    first = _reviewed_card(character="Flareon", id="first")
    second = _reviewed_card(character="Vaporeon", card_number="168", id="second")
    session.add_all([first, second])
    session.commit()

    with patch("automation_control.price_review.lookup_price", return_value=Decimal("5.00")), \
         patch("automation_control.price_review.time.sleep"):
        _run_price_check(api_key=None, user="testuser")

    session.expire_all()
    events = session.query(AuditEvent).filter(AuditEvent.action == "card.price_check").all()
    assert {e.resource_id for e in events} == {"first", "second"}


def test_run_price_check_paces_between_cards_but_not_after_the_last_one(session, price_check_session_local):
    first = _reviewed_card(character="Flareon", id="first")
    second = _reviewed_card(character="Vaporeon", card_number="168", id="second")
    session.add_all([first, second])
    session.commit()

    with patch("automation_control.price_review.lookup_price", return_value=Decimal("5.00")), \
         patch("automation_control.price_review.time.sleep") as mock_sleep:
        _run_price_check(api_key=None, user="testuser")

    assert mock_sleep.call_count == 1  # once between the two cards, not after the second


def test_run_price_check_is_a_noop_if_another_check_is_already_running(session, price_check_session_local):
    card = _reviewed_card()
    session.add(card)
    session.commit()

    run_id = _try_start_price_check()
    assert run_id is not None
    try:
        with patch("automation_control.price_review.lookup_price") as mock_lookup:
            _run_price_check(api_key=None, user="testuser")
            mock_lookup.assert_not_called()
    finally:
        _finish_price_check(run_id)


def test_run_price_check_proceeds_if_previous_run_is_stale(session, price_check_session_local):
    card = _reviewed_card()
    session.add(card)
    session.commit()

    run_id = _try_start_price_check()
    assert run_id is not None
    # simulate that run having started long enough ago to count as wedged,
    # without sleeping for real in the test
    import automation_control.price_review as price_review_module
    price_review_module._price_check_started_at -= price_review_module.STALE_RUN_SECONDS + 1

    try:
        with patch("automation_control.price_review.lookup_price", return_value=Decimal("5.00")):
            _run_price_check(api_key=None, user="testuser")

        session.expire_all()
        refreshed = session.get(CapturedCard, card.id)
        assert refreshed.pricing_status == PricingStatus.PENDING_PRICE_REVIEW
    finally:
        _finish_price_check(run_id)


def test_try_start_price_check_returns_none_while_a_run_is_active():
    run_id = _try_start_price_check()
    assert run_id is not None
    try:
        assert _try_start_price_check() is None
    finally:
        _finish_price_check(run_id)


def test_finish_price_check_ignores_a_stale_run_id_after_a_newer_run_started():
    # a wedged run that eventually wakes up and finishes must not clear a
    # newer, still-active run's state out from under it.
    first_run_id = _try_start_price_check()
    import automation_control.price_review as price_review_module
    price_review_module._price_check_started_at -= price_review_module.STALE_RUN_SECONDS + 1

    second_run_id = _try_start_price_check()
    assert second_run_id is not None
    assert second_run_id != first_run_id

    _finish_price_check(first_run_id)  # the stale run finishing late
    assert _try_start_price_check() is None  # second run should still be considered active

    _finish_price_check(second_run_id)
    assert _try_start_price_check() is not None  # now it's actually free


# --- routes ---

def test_check_all_schedules_background_task_and_redirects(client):
    with patch("fastapi.BackgroundTasks.add_task") as mock_add_task:
        resp = client.post("/cards/pricing/check-all", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/cards/pricing?checking=1"
    assert mock_add_task.call_args.args[0] is _run_price_check


def test_pricing_list_requires_auth():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/cards/pricing")
    assert resp.status_code == 401


def test_pricing_list_shows_pending_and_unpriced_cards(client, session):
    priced = _reviewed_card(character="Flareon", pricing_status=PricingStatus.PENDING_PRICE_REVIEW, suggested_price=Decimal("12.50"))
    unpriced = _reviewed_card(character="Vaporeon", card_number="168")
    session.add_all([priced, unpriced])
    session.commit()

    resp = client.get("/cards/pricing")
    assert resp.status_code == 200
    assert "Flareon" in resp.text
    assert "Vaporeon" in resp.text


def test_price_review_form_shows_suggested_price(client, session):
    card = _reviewed_card(pricing_status=PricingStatus.PENDING_PRICE_REVIEW, suggested_price=Decimal("12.50"), price_source="pokemontcg_market_usd_to_aud")
    session.add(card)
    session.commit()

    resp = client.get(f"/cards/pricing/{card.id}")
    assert resp.status_code == 200
    assert "12.50" in resp.text
    assert "pokemontcg_market_usd_to_aud" in resp.text


def test_price_review_form_404_for_unknown_card(client):
    resp = client.get("/cards/pricing/does-not-exist")
    assert resp.status_code == 404


def test_price_review_approve_sets_approved_price(client, session):
    card = _reviewed_card(pricing_status=PricingStatus.PENDING_PRICE_REVIEW, suggested_price=Decimal("12.50"))
    session.add(card)
    session.commit()

    resp = client.post(f"/cards/pricing/{card.id}", data={"action": "approve", "price": "15.00"}, follow_redirects=False)
    assert resp.status_code == 303

    session.expire_all()
    refreshed = session.get(CapturedCard, card.id)
    assert refreshed.approved_price == Decimal("15.00")
    assert refreshed.pricing_status == PricingStatus.PRICE_APPROVED
    assert refreshed.bundle_id is None  # no bundle members selected


def test_price_review_reject_does_not_set_a_price(client, session):
    card = _reviewed_card(pricing_status=PricingStatus.PENDING_PRICE_REVIEW, suggested_price=Decimal("12.50"))
    session.add(card)
    session.commit()

    resp = client.post(f"/cards/pricing/{card.id}", data={"action": "reject"}, follow_redirects=False)
    assert resp.status_code == 303

    session.expire_all()
    refreshed = session.get(CapturedCard, card.id)
    assert refreshed.approved_price is None
    assert refreshed.pricing_status == PricingStatus.PRICE_REJECTED


def test_price_review_approve_rejects_invalid_price(client, session):
    card = _reviewed_card(pricing_status=PricingStatus.PENDING_PRICE_REVIEW)
    session.add(card)
    session.commit()

    resp = client.post(f"/cards/pricing/{card.id}", data={"action": "approve", "price": "not-a-number"})
    assert resp.status_code == 400


def test_price_review_approve_rejects_zero_or_negative_price(client, session):
    card = _reviewed_card(pricing_status=PricingStatus.PENDING_PRICE_REVIEW)
    session.add(card)
    session.commit()

    resp = client.post(f"/cards/pricing/{card.id}", data={"action": "approve", "price": "0"})
    assert resp.status_code == 400


def test_price_review_approve_with_bundle_shares_bundle_id_and_price(client, session):
    main = _reviewed_card(character="Zapdos ex", pricing_status=PricingStatus.PENDING_PRICE_REVIEW)
    partner = _reviewed_card(character="Flareon", card_number="168", pricing_status=PricingStatus.PENDING_PRICE_REVIEW)
    session.add_all([main, partner])
    session.commit()

    resp = client.post(f"/cards/pricing/{main.id}", data={"action": "approve", "price": "40.00", "bundle_with": [partner.id]}, follow_redirects=False)
    assert resp.status_code == 303

    session.expire_all()
    refreshed_main = session.get(CapturedCard, main.id)
    refreshed_partner = session.get(CapturedCard, partner.id)
    assert refreshed_main.bundle_id is not None
    assert refreshed_main.bundle_id == refreshed_partner.bundle_id
    assert refreshed_main.approved_price == refreshed_partner.approved_price == Decimal("40.00")
    assert refreshed_partner.pricing_status == PricingStatus.PRICE_APPROVED


def test_price_review_auto_advances_to_next_pending_card(client, session):
    from datetime import UTC, datetime

    first = _reviewed_card(character="Flareon", pricing_status=PricingStatus.PENDING_PRICE_REVIEW, suggested_price=Decimal("5"), price_checked_at=datetime(2026, 1, 1, tzinfo=UTC))
    second = _reviewed_card(character="Vaporeon", card_number="168", pricing_status=PricingStatus.PENDING_PRICE_REVIEW, suggested_price=Decimal("6"), price_checked_at=datetime(2026, 1, 2, tzinfo=UTC))
    session.add_all([first, second])
    session.commit()

    resp = client.post(f"/cards/pricing/{first.id}", data={"action": "approve", "price": "5.00"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/cards/pricing/{second.id}"


def test_price_review_redirects_to_list_when_no_more_pending(client, session):
    only = _reviewed_card(pricing_status=PricingStatus.PENDING_PRICE_REVIEW, suggested_price=Decimal("5"))
    session.add(only)
    session.commit()

    resp = client.post(f"/cards/pricing/{only.id}", data={"action": "approve", "price": "5.00"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/cards/pricing"
