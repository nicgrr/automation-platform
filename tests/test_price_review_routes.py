from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from automation_control.adapters.currency import CurrencyLookupError
from automation_control.adapters.pokemontcg import PriceLookupError, PriceNotFound
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import AuditEvent, CapturedCard, CardCaptureStatus, PricingStatus
from automation_control.price_review import (_run_price_check, _try_start_price_check,
                                            _finish_price_check, needs_a_person)


@pytest.fixture(autouse=True)
def _clear_price_check_guard():
    """The run-lock is module state, so a test that leaves a run "in
    progress" makes every later test's price check a silent no-op. Reset it
    around each test rather than relying on every test to unwind its own."""
    import automation_control.price_review as pr

    pr._price_check_run_id = None
    pr._price_check_started_at = None
    yield
    pr._price_check_run_id = None
    pr._price_check_started_at = None


@pytest.fixture(autouse=True)
def _bundling_enabled_for_legacy_bundle_tests():
    """Route tests are isolated from the operator's live business setting.

    Most of this file predates the singles-only switch and intentionally
    exercises the optional bundle workflow. Tests for the disabled mode
    override this patch locally.
    """
    classification = SimpleNamespace(
        bundling_enabled=True,
        bulk_price_threshold_aud=Decimal("8.00"),
        max_lot_size=8,
    )
    with patch("automation_control.price_review.load_pipeline_config",
               return_value=SimpleNamespace(classification=classification)):
        yield


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

def _held_card(session, card_id, *, variant=None):
    """An owned TCG card, shaped the way the app really writes one: a
    catalog_items row plus an inventory row carrying BOTH ids.

    The catalog_item_id is the half that matters here -- card_prices is
    keyed by it, and so are the gap counts on this page. An inventory row
    with only card_id set is the bug these tests used to encode rather
    than catch: unreachable by any price lookup.
    """
    from automation_control.models import CardVariant, CatalogItem, CatalogItemType, InventoryItem

    session.add(CatalogItem(id=card_id, name=card_id, item_type=CatalogItemType.TCG_CARD))
    item = InventoryItem(card_id=card_id, catalog_item_id=card_id,
                         variant=variant or CardVariant.NORMAL)
    session.add(item)
    return item



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
    assert "Choose printing" in resp.text


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


def test_singles_only_mode_hides_and_ignores_bundle_controls(client, session):
    main = _reviewed_card(character="Zapdos ex", pricing_status=PricingStatus.PENDING_PRICE_REVIEW,
                          bundle_id="old-auto-group")
    partner = _reviewed_card(character="Flareon", card_number="168",
                             pricing_status=PricingStatus.PENDING_PRICE_REVIEW,
                             bundle_id="old-auto-group")
    session.add_all([main, partner])
    session.commit()

    disabled = SimpleNamespace(classification=SimpleNamespace(bundling_enabled=False))
    with patch("automation_control.price_review.load_pipeline_config", return_value=disabled):
        page = client.get(f"/cards/pricing/{main.id}").text
        resp = client.post(
            f"/cards/pricing/{main.id}",
            data={"action": "approve", "price": "4.00", "bundle_with": [partner.id]},
            follow_redirects=False,
        )

    assert "Bundle with" not in page
    assert resp.status_code == 303
    session.expire_all()
    assert session.get(CapturedCard, main.id).bundle_id is None
    assert session.get(CapturedCard, partner.id).pricing_status == PricingStatus.PENDING_PRICE_REVIEW


def test_approved_cards_appear_in_their_own_section(client, session):
    """Real gap, found 2026-10-01: an approved card dropped off every list
    on the page, so there was no way back to it to change the price."""
    card = _reviewed_card(character="Snorlax", set_name="Jungle", card_number="11",
                          pricing_status=PricingStatus.PRICE_APPROVED, approved_price=Decimal("8.00"))
    session.add(card)
    session.commit()

    page = client.get("/cards/pricing").text

    assert "Approved, not yet listed (1)" in page
    assert "Snorlax" in page
    assert "AUD $8.00" in page
    assert f"/cards/pricing/{card.id}" in page


def test_reopening_an_approved_card_shows_its_current_price(client, session):
    """Previously fell through to suggested_price (None for a manually
    approved card), so the field showed blank instead of what was set."""
    card = _reviewed_card(pricing_status=PricingStatus.PRICE_APPROVED, approved_price=Decimal("8.00"),
                          suggested_price=None)
    session.add(card)
    session.commit()

    page = client.get(f"/cards/pricing/{card.id}").text

    assert "value='8.00'" in page


def test_reopening_a_bundled_card_still_shows_its_bundle_mate_as_checked(client, session):
    """Previously the bundle-candidates query only looked at NOT_PRICED/
    PENDING_PRICE_REVIEW cards, so a PRICE_APPROVED bundle mate's checkbox
    never rendered -- re-approving with the (empty) form would leave the
    mate on its old price while this card's price changed underneath it."""
    bundle_id = "shared-bundle-1"
    main = _reviewed_card(character="Chikorita", pricing_status=PricingStatus.PRICE_APPROVED,
                          approved_price=Decimal("2.50"), bundle_id=bundle_id)
    partner = _reviewed_card(character="Totodile", card_number="41", pricing_status=PricingStatus.PRICE_APPROVED,
                             approved_price=Decimal("2.50"), bundle_id=bundle_id)
    session.add_all([main, partner])
    session.commit()

    page = client.get(f"/cards/pricing/{main.id}").text

    assert f"value='{partner.id}' checked" in page


def test_editing_a_bundled_card_with_its_mate_checked_keeps_prices_in_sync(client, session):
    bundle_id = "shared-bundle-2"
    main = _reviewed_card(character="Chikorita", pricing_status=PricingStatus.PRICE_APPROVED,
                          approved_price=Decimal("2.50"), bundle_id=bundle_id)
    partner = _reviewed_card(character="Totodile", card_number="41", pricing_status=PricingStatus.PRICE_APPROVED,
                             approved_price=Decimal("2.50"), bundle_id=bundle_id)
    session.add_all([main, partner])
    session.commit()

    client.post(f"/cards/pricing/{main.id}", data={"action": "approve", "price": "3.00", "bundle_with": [partner.id]})

    session.expire_all()
    assert session.get(CapturedCard, main.id).approved_price == Decimal("3.00")
    assert session.get(CapturedCard, partner.id).approved_price == Decimal("3.00")


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


# --- separating a retry from a decision ------------------------------------

@pytest.mark.parametrize("error,expected", [
    ("pokemontcg.io unavailable after 3 attempts: pokemontcg.io returned 500", False),
    ("pokemontcg.io unavailable after 3 attempts: pokemontcg.io returned 502", False),
    ("ambiguous match for 'Tyrunt' (query='name:\"Tyrunt\"' #'070'): 2 candidates", True),
    ("no priced match found for 'Mega Greninja ex' after 2 search variant(s)", True),
    ("missing character or set name, needs manual price", True),
    (None, False),
    ("something nobody has seen before", False),
])
def test_needs_a_person_reads_the_error(error, expected):
    """The real strings from twelve cards that sat unpriced for a month. An
    unrecognised error defaults to retryable: guessing "transient" costs one
    API call, guessing "permanent" strands the card in a list nobody
    revisits."""
    assert needs_a_person(error) is expected


def test_a_retry_run_skips_the_cards_a_retry_cannot_help(session, price_check_session_local):
    """Re-asking about an ambiguous card returns the same ambiguity and
    spends a rate-limited call to do it."""
    outage = _reviewed_card(character="Tepig", price_lookup_error="pokemontcg.io unavailable after 3 attempts")
    ambiguous = _reviewed_card(character="Tyrunt", price_lookup_error="ambiguous match for 'Tyrunt': 2 candidates")
    session.add_all([outage, ambiguous])
    session.commit()

    with patch("automation_control.price_review.lookup_price", return_value=Decimal("2.00")) as lookup:
        _run_price_check(api_key=None, user="testuser")

    assert [c.args[0] for c in lookup.call_args_list] == ["Tepig"]
    session.expire_all()
    assert session.get(CapturedCard, ambiguous.id).pricing_status == PricingStatus.NOT_PRICED


def test_include_blocked_is_the_escape_hatch(session, price_check_session_local):
    """The classifier reads error strings, so a wrongly-classified card would
    otherwise never be retried by anything again."""
    ambiguous = _reviewed_card(character="Tyrunt", price_lookup_error="ambiguous match for 'Tyrunt'")
    session.add(ambiguous)
    session.commit()

    with patch("automation_control.price_review.lookup_price", return_value=Decimal("2.00")) as lookup:
        _run_price_check(api_key=None, user="testuser", include_blocked=True)

    assert [c.args[0] for c in lookup.call_args_list] == ["Tyrunt"]


def test_the_page_splits_retryable_from_blocked(client, session):
    """One list is why twelve cards went unnoticed for a month -- ten behind
    nothing worse than an API that was briefly down."""
    session.add_all([
        _reviewed_card(character="Tepig", price_lookup_error="pokemontcg.io unavailable after 3 attempts: returned 500"),
        _reviewed_card(character="Totodile", price_lookup_error="pokemontcg.io unavailable after 3 attempts: returned 502"),
        _reviewed_card(character="Tyrunt", price_lookup_error="ambiguous match for 'Tyrunt': 2 candidates"),
    ])
    session.commit()

    page = client.get("/cards/pricing").text

    assert "Ready to price (2)" in page
    assert "Needs your decision (1)" in page
    assert "Retry 2 lookups" in page
    assert "Try these anyway" in page
    # The reason belongs next to the card only where it's a decision to make,
    # grouped by what kind of decision -- not the raw technical error text.
    assert page.index("Needs your decision") < page.index("Ambiguous match")
    assert "2 candidates" in page
    assert "ambiguous match for 'Tyrunt'" not in page


def test_a_card_never_checked_is_ready_not_blocked(client, session):
    session.add(_reviewed_card(character="Snivy"))
    session.commit()

    page = client.get("/cards/pricing").text

    assert "Ready to price (1)" in page
    assert "Needs your decision (0)" in page
    assert "Check prices" in page  # not "Retry", nothing has failed yet


def test_the_decision_list_is_grouped_by_what_kind_of_decision(client, session):
    """Real feedback, 2026-09-30: the flat list of raw technical error
    strings was hard to scan on a phone. Grouping by reason means the
    section heading carries the meaning instead of every row repeating it."""
    session.add_all([
        _reviewed_card(character="Mega Starmie ex", set_name="POR", card_number="021",
                       price_lookup_error="no priced match found for 'Mega Starmie ex' "
                                          "('POR' #'021/088') after 2 search variant(s)"),
        _reviewed_card(character="Tyrunt", set_name="MEP", card_number="070",
                       price_lookup_error="ambiguous match for 'Tyrunt' (query='name:\"Tyrunt\"' "
                                          "#'070', promo_filter=False): 2 candidates with different prices"),
        _reviewed_card(character=None, set_name=None,
                       price_lookup_error="missing character or set name, needs manual price"),
    ])
    session.commit()

    page = client.get("/cards/pricing").text

    assert "No match found (1)" in page
    assert "Ambiguous match (1)" in page
    assert "Incomplete capture (1)" in page
    assert "2 variant(s) tried" in page
    assert "2 candidates" in page
    # the raw technical strings themselves no longer print
    assert "search variant(s)" not in page.replace("2 variant(s) tried", "")
    assert "promo_filter" not in page


def test_each_blocked_card_gets_an_ebay_sold_listings_search_link(client, session):
    session.add(_reviewed_card(character="Mega Lucario ex", set_name="Mega Ascension", card_number="113",
                               price_lookup_error="no priced match found for 'Mega Lucario ex'"))
    session.commit()

    page = client.get("/cards/pricing").text

    assert "ebay.com.au/sch/i.html" in page
    assert "Mega+Lucario+ex" in page
    assert "LH_Sold=1" in page
    assert "target='_blank'" in page


def test_an_empty_category_prints_no_heading(client, session):
    session.add(_reviewed_card(character="Tyrunt", price_lookup_error="ambiguous match for 'Tyrunt': 2 candidates"))
    session.commit()

    page = client.get("/cards/pricing").text

    assert "No match found" not in page
    assert "Incomplete capture" not in page


# --- inventory prices, the "check everything" button ------------------------

def test_the_page_shows_the_inventory_price_backlog(client, session):
    """The honest form of "check prices for everything": the upstream free
    tier allows ~25 cards a day, so the button says how far through you are
    instead of implying the job ends when the page reloads."""
    _held_card(session, "xy11-1")
    _held_card(session, "xy11-2")
    session.commit()

    page = client.get("/cards/pricing").text

    assert "Inventory prices" in page
    assert "2 held card/variant pairs still have no market price" in page
    assert "Price the next 25" in page


def test_a_collectible_is_not_counted_in_the_backlog(client, session):
    """Collectibles carry card_id NULL and are not this source's job -- the
    same NULL that crashed the nightly timer for six days."""
    from automation_control.models import CardVariant, CatalogItem, CatalogItemType, InventoryItem

    session.add(CatalogItem(id="a-figure", name="Sonny Angel", item_type=CatalogItemType.COLLECTIBLE))
    session.add(InventoryItem(card_id=None, catalog_item_id="a-figure", variant=CardVariant.NORMAL))
    session.commit()

    assert "Every held card has a market price" in client.get("/cards/pricing").text


def test_refresh_inventory_runs_the_same_script_the_timer_runs(client, session):
    """Not a second implementation: the timer spent six days failing on a
    TypeError nobody saw, and two copies would double the odds of a repeat."""
    with patch("scripts.backfill_pokemonpricetracker_prices.main", return_value=0) as backfill:
        resp = client.post("/cards/pricing/refresh-inventory", follow_redirects=False)

    assert resp.status_code == 303
    backfill.assert_called_once_with([])


def test_a_crash_in_the_refresh_is_recorded_not_swallowed(client, session):
    """The failure mode being fixed is a background job dying quietly."""
    from automation_control.models import AuditEvent

    with patch("scripts.backfill_pokemonpricetracker_prices.main", side_effect=TypeError("boom")):
        client.post("/cards/pricing/refresh-inventory")

    events = session.scalars(select(AuditEvent).where(AuditEvent.action == "inventory.price_refresh")).all()
    assert [e.outcome for e in events] == ["failed"]
    assert "TypeError: boom" in events[0].details["error"]


def test_the_page_shows_the_ebay_signal_gap(client, session):
    """ebay_active_low is what list_for_sale prices from, so a held card
    without one is priced off a weaker source."""
    from automation_control.models import CardPrice, CardVariant

    _held_card(session, "xy11-1")
    _held_card(session, "xy11-2")
    session.add(CardPrice(catalog_item_id="xy11-1", variant=CardVariant.NORMAL,
                          source="ebay_active_low", price=Decimal("5.00"), currency="AUD"))
    session.commit()

    page = client.get("/cards/pricing").text

    assert "eBay market signal" in page
    assert "1 held card has no eBay asking-price signal" in page
    assert "Refresh eBay prices" in page


def test_refresh_ebay_runs_the_import_script(client, session):
    with patch("scripts.import_ebay_prices.main", return_value=0) as import_prices:
        resp = client.post("/cards/pricing/refresh-ebay", follow_redirects=False)

    assert resp.status_code == 303
    import_prices.assert_called_once_with([])


# --- unpriced inventory: the actual list behind the dashboard's % figure ----

def test_unpriced_inventory_lists_a_held_card_with_no_price_at_all(client, session):
    """Real feedback, 2026-10-01: a bare percentage on the dashboard gave no
    way to act on it -- this is the list the percentage is counting."""
    _held_card(session, "xy11-1")
    session.commit()

    page = client.get("/cards/pricing").text

    assert "Unpriced inventory (1)" in page
    assert "xy11-1" in page
    assert "eBay search" in page


def test_unpriced_inventory_shows_the_real_set_name_not_the_raw_set_id(client, session):
    """Real gap, found 2026-10-01: this used to show (and eBay-search for)
    the raw set_id code like 'cn-csv10c' directly -- not a string any real
    eBay listing title would ever contain."""
    from automation_control.models import CardSet, TcgCard

    _held_card(session, "card-1")
    session.add(CardSet(id="cn-csv10c", name="Chasing Glory Together", game="pokemon"))
    session.add(TcgCard(catalog_item_id="card-1", game="pokemon", set_id="cn-csv10c", number="104/222"))
    session.commit()

    page = client.get("/cards/pricing").text

    assert "Chasing Glory Together" in page
    assert "cn-csv10c" not in page


def test_unpriced_inventory_falls_back_to_the_raw_set_id_when_the_set_is_not_cached(client, session):
    from automation_control.models import TcgCard

    _held_card(session, "card-1")
    session.add(TcgCard(catalog_item_id="card-1", game="pokemon", set_id="xy11", number="1"))
    session.commit()

    page = client.get("/cards/pricing").text

    assert "xy11" in page


def test_a_card_priced_by_either_source_is_not_in_the_unpriced_list(client, session):
    from automation_control.models import CardVariant, CardPrice

    _held_card(session, "xy11-1")
    _held_card(session, "xy11-2")
    session.add(CardPrice(catalog_item_id="xy11-1", variant=CardVariant.NORMAL,
                          source="ebay_active_low", price=Decimal("5.00"), currency="AUD"))
    session.commit()

    page = client.get("/cards/pricing").text

    assert "Unpriced inventory (1)" in page
    assert "xy11-2" in page


def test_every_held_card_priced_shows_the_all_clear_message(client, session):
    _held_card(session, "xy11-1")
    session.commit()

    from automation_control.models import CardVariant, CardPrice
    session.add(CardPrice(catalog_item_id="xy11-1", variant=CardVariant.NORMAL,
                          source="ebay_active_low", price=Decimal("5.00"), currency="AUD"))
    session.commit()

    page = client.get("/cards/pricing").text

    assert "Unpriced inventory (0)" in page
    assert "Every held card has a price from at least one source." in page


def test_the_pricing_jobs_last_run_time_shows_when_known(client, session):
    """Answers "do they even run?" on the page itself instead of needing a
    terminal -- real question asked 2026-10-01."""
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    fake_status = MagicMock(state="ok", last_run=datetime(2026, 10, 1, 3, 55, tzinfo=UTC), detail="")
    with patch("automation_control.job_health.status_for", return_value=fake_status):
        page = client.get("/cards/pricing").text

    assert "Last ran Thu 03:55" in page


def test_a_failed_pricing_job_shows_a_warning_on_the_page(client, session):
    from unittest.mock import MagicMock

    fake_status = MagicMock(state="failed", last_run=None, detail="TypeError: boom")
    with patch("automation_control.job_health.status_for", return_value=fake_status):
        page = client.get("/cards/pricing").text

    assert "Last run failed -- TypeError: boom" in page


def test_a_crash_in_the_ebay_refresh_is_recorded(client, session):
    from automation_control.models import AuditEvent

    with patch("scripts.import_ebay_prices.main", side_effect=RuntimeError("no token")):
        client.post("/cards/pricing/refresh-ebay")

    events = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "inventory.ebay_price_refresh")).all()
    assert [e.outcome for e in events] == ["failed"]
    assert "RuntimeError: no token" in events[0].details["error"]
