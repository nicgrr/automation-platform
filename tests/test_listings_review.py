from dataclasses import dataclass
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.listing_pipeline.config import EbayFieldsConfig, ImagesConfig, PipelineConfig, PricingConfig, TitleConfig
from automation_control.listings_review import _run_build_listings
from automation_control.models import Approval, ApprovalStatus, CapturedCard, CardCaptureStatus, ListingBuildStatus, PendingListing, PricingStatus

TERM_ORDER = ["game", "character", "card_number", "rarity", "set_name"]


def _pipeline_config():
    return PipelineConfig(
        ebay=EbayFieldsConfig(
            category_id="183454", condition_id="4000", item_location="x",
            shipping_profile_name="x", return_profile_name="x", payment_profile_name="x",
            custom_label_prefix="NIC",
        ),
        images=ImagesConfig(base_url="https://pub.example.com/cards", filename_pattern="{custom_label}_{index}.jpg"),
        pricing=PricingConfig(auto_accept_pct="0.9", floor_price_pct=Decimal("0.70")),
        title=TitleConfig(term_order=TERM_ORDER),
    )


@dataclass
class FakeSettings:
    r2_endpoint_url: str | None = "https://r2.example.com"
    r2_access_key_id: str | None = "key"
    r2_secret_access_key: str | None = "secret"
    r2_bucket_name: str | None = "bucket"


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
def listings_session_local(engine):
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with patch("automation_control.listings_review.SessionLocal", test_session_factory), \
         patch("automation_control.listings_review.load_pipeline_config", return_value=_pipeline_config()), \
         patch("automation_control.listings_review.upload_card_image", side_effect=lambda local_path, key, **kw: f"https://pub.example.com/cards/{key}"):
        yield


@pytest.fixture
def client(session: Session, tmp_path):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _approved_card(**overrides) -> CapturedCard:
    defaults = dict(
        front_image_path="front.jpg", back_image_path=None,
        character="Flareon", set_name="Promo", card_number="167", rarity="Rare", language="English", graded="Raw",
        status=CardCaptureStatus.REVIEWED, pricing_status=PricingStatus.PRICE_APPROVED, approved_price=Decimal("10.00"),
        ai_raw_response={},
    )
    defaults.update(overrides)
    return CapturedCard(**defaults)


# --- _run_build_listings ---

def test_build_listings_creates_a_draft_pending_listing(session, listings_session_local):
    card = _approved_card()
    session.add(card)
    session.commit()

    _run_build_listings(user="testuser", settings=FakeSettings())

    session.expire_all()
    listings = session.scalars(select(PendingListing)).all()
    assert len(listings) == 1
    listing = listings[0]
    assert listing.status == ListingBuildStatus.DRAFT
    assert listing.list_price == Decimal("10.00")
    assert listing.floor_price == Decimal("7.00")
    assert listing.card_ids == [card.id]
    assert listing.image_urls == [f"https://pub.example.com/cards/captured/{card.id}-front.jpg"]


def test_build_listings_skips_cards_already_linked_to_a_listing(session, listings_session_local):
    card = _approved_card()
    session.add(card)
    session.commit()
    session.add(PendingListing(custom_label="NIC-01", title="x", description_html="x", list_price=Decimal("1"), floor_price=Decimal("1"), category_id="1", condition_id="1", card_ids=[card.id]))
    session.commit()

    _run_build_listings(user="testuser", settings=FakeSettings())

    session.expire_all()
    listings = session.scalars(select(PendingListing)).all()
    assert len(listings) == 1  # the pre-existing one only -- no duplicate built for the already-linked card


def test_build_listings_is_a_noop_when_r2_is_not_configured(session, listings_session_local):
    card = _approved_card()
    session.add(card)
    session.commit()

    _run_build_listings(user="testuser", settings=FakeSettings(r2_bucket_name=None))

    session.expire_all()
    assert session.scalars(select(PendingListing)).all() == []


def test_build_listings_groups_bundled_cards_into_one_listing(session, listings_session_local):
    members = [_approved_card(character="Flareon", bundle_id="b1"), _approved_card(character="Vaporeon", card_number="168", bundle_id="b1")]
    session.add_all(members)
    session.commit()

    _run_build_listings(user="testuser", settings=FakeSettings())

    session.expire_all()
    listings = session.scalars(select(PendingListing)).all()
    assert len(listings) == 1
    assert sorted(listings[0].card_ids) == sorted(c.id for c in members)


# --- routes ---

def test_listings_build_schedules_background_task_and_redirects(client):
    with patch("fastapi.BackgroundTasks.add_task") as mock_add_task:
        resp = client.post("/listings/build", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/listings/review?building=1"
    assert mock_add_task.call_args.args[0] is _run_build_listings


def test_listings_review_requires_auth():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/listings/review")
    assert resp.status_code in (401, 403, 307, 302)


def test_review_form_shows_draft_listing(session, client):
    listing = PendingListing(custom_label="NIC-01", title="Flareon Promo", description_html="<p>hi</p>", list_price=Decimal("10.00"), floor_price=Decimal("7.00"), category_id="1", condition_id="1", card_ids=[])
    session.add(listing)
    session.commit()

    resp = client.get(f"/listings/review/{listing.id}")
    assert resp.status_code == 200
    assert "Flareon Promo" in resp.text


def test_review_form_404s_for_unknown_listing(client):
    resp = client.get("/listings/review/does-not-exist")
    assert resp.status_code == 404


def test_approve_creates_approved_approval_and_marks_listing_approved(session, client):
    listing = PendingListing(custom_label="NIC-01", title="Flareon Promo", description_html="<p>hi</p>", list_price=Decimal("10.00"), floor_price=Decimal("7.00"), category_id="1", condition_id="1", card_ids=[])
    session.add(listing)
    session.commit()

    resp = client.post(f"/listings/review/{listing.id}", data={"action": "approve", "title": "Flareon Promo", "list_price": "12.00", "floor_price": "8.00"}, follow_redirects=False)
    assert resp.status_code == 303

    session.expire_all()
    refreshed = session.get(PendingListing, listing.id)
    assert refreshed.status == ListingBuildStatus.APPROVED
    assert refreshed.list_price == Decimal("12.00")
    assert refreshed.floor_price == Decimal("8.00")
    assert refreshed.approval_id is not None

    approval = session.get(Approval, refreshed.approval_id)
    assert approval.status == ApprovalStatus.APPROVED
    assert approval.action == "ebay.publish_listing"


def test_reject_marks_listing_rejected_without_creating_an_approval(session, client):
    listing = PendingListing(custom_label="NIC-01", title="x", description_html="x", list_price=Decimal("10.00"), floor_price=Decimal("7.00"), category_id="1", condition_id="1", card_ids=[])
    session.add(listing)
    session.commit()

    resp = client.post(f"/listings/review/{listing.id}", data={"action": "reject"}, follow_redirects=False)
    assert resp.status_code == 303

    session.expire_all()
    refreshed = session.get(PendingListing, listing.id)
    assert refreshed.status == ListingBuildStatus.REJECTED
    assert refreshed.approval_id is None
    assert session.scalars(select(Approval)).all() == []


def test_approve_rejects_non_positive_price(session, client):
    listing = PendingListing(custom_label="NIC-01", title="x", description_html="x", list_price=Decimal("10.00"), floor_price=Decimal("7.00"), category_id="1", condition_id="1", card_ids=[])
    session.add(listing)
    session.commit()

    resp = client.post(f"/listings/review/{listing.id}", data={"action": "approve", "title": "x", "list_price": "0", "floor_price": "1"})
    assert resp.status_code == 400
