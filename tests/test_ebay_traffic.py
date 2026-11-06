"""eBay listing traffic: report parsing, batching, refresh and the
/analytics panel. eBay itself is never called -- httpx.MockTransport."""
import asyncio
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control import ebay_traffic
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.ebay_oauth import ANALYTICS_SCOPE, SELLER_SCOPE, OAuthError
from automation_control.ebay_traffic import fetch_traffic, parse_traffic_report, refresh_traffic, report_window
from automation_control.models import EbayCredential, EbayListing, EbayListingTraffic


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def report(*records):
    return {
        "header": {"dimensionKeys": [{"key": "LISTING_ID"}],
                   "metrics": [{"key": "LISTING_IMPRESSION_TOTAL"}, {"key": "LISTING_VIEWS_TOTAL"}, {"key": "TRANSACTION"}]},
        "records": [
            {"dimensionValues": [{"value": listing_id, "applicable": True}],
             "metricValues": [{"value": value, "applicable": True} for value in values]}
            for listing_id, values in records
        ],
    }


def add_listing(session, listing_id, title, status="Active"):
    session.add(EbayListing(ebay_listing_id=listing_id, title=title, currency="AUD",
                            current_price_minor=999, listing_status=status))


def test_scope_requests_analytics_read_only():
    assert ANALYTICS_SCOPE in SELLER_SCOPE
    assert ANALYTICS_SCOPE.endswith("sell.analytics.readonly")


def test_window_is_thirty_days_ending_before_unsettled_days():
    assert report_window(date(2026, 11, 6)) == (date(2026, 10, 6), date(2026, 11, 4))


def test_parse_maps_values_by_header_position_and_zeroes_inapplicable():
    payload = report(("111", [500, 20, 1]))
    payload["records"][0]["metricValues"][2] = {"value": None, "applicable": False}
    assert parse_traffic_report(payload) == {
        "111": {"LISTING_IMPRESSION_TOTAL": 500, "LISTING_VIEWS_TOTAL": 20, "TRANSACTION": 0}}
    assert parse_traffic_report({}) == {}


def test_fetch_batches_listing_ids_and_builds_the_filter():
    seen = []

    def handler(request):
        params = parse_qs(urlsplit(str(request.url)).query)
        seen.append(params)
        ids = params["filter"][0].split("listing_ids:{")[1].rstrip("}").split("|")
        return httpx.Response(200, json=report(*((i, [1, 1, 0]) for i in ids)))

    ids = [str(n) for n in range(250)]

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_traffic(client, "tok", ids, date(2026, 10, 6), date(2026, 11, 4),
                                       environment="production", marketplace_id="EBAY_AU")

    result = asyncio.run(run())
    assert len(seen) == 2 and len(result) == 250
    assert seen[0]["dimension"] == ["LISTING"]
    assert seen[0]["filter"][0].startswith("marketplace_ids:{EBAY_AU},date_range:[20261006..20261104],listing_ids:{0|1|")


def test_fetch_raises_on_ebay_error():
    async def run():
        transport = httpx.MockTransport(lambda r: httpx.Response(403, json={"errors": []}))
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_traffic(client, "tok", ["1"], date(2026, 10, 6), date(2026, 11, 4),
                                environment="production", marketplace_id="EBAY_AU")

    with pytest.raises(ebay_traffic.EbayApiError):
        asyncio.run(run())


def test_refresh_without_analytics_scope_asks_for_reconnect(session):
    session.add(EbayCredential(id=1, environment="production", encrypted_access_token=b"x",
                               encrypted_refresh_token=b"x", access_token_expires_at=datetime.now(UTC),
                               refresh_token_expires_at=datetime.now(UTC), scopes="https://api.ebay.com/oauth/api_scope/sell.inventory"))
    session.commit()
    with pytest.raises(OAuthError, match="reconnect"):
        asyncio.run(refresh_traffic(session, object(), "tester"))


def test_refresh_replaces_window_for_active_listings_only(session, monkeypatch):
    session.add(EbayCredential(id=1, environment="production", encrypted_access_token=b"x",
                               encrypted_refresh_token=b"x", access_token_expires_at=datetime.now(UTC),
                               refresh_token_expires_at=datetime.now(UTC), scopes=SELLER_SCOPE))
    add_listing(session, "111", "Pikachu")
    add_listing(session, "222", "Eevee")
    add_listing(session, "333", "Old", status="Ended")
    session.add(EbayListingTraffic(ebay_listing_id="stale", period_start=date(2026, 1, 1),
                                   period_end=date(2026, 1, 30), impressions=1, views=1, transactions=0))
    session.commit()

    async def token(*args, **kwargs):
        return "tok"

    monkeypatch.setattr(ebay_traffic, "ebay_services", lambda settings: (None, None))
    monkeypatch.setattr(ebay_traffic, "valid_user_access_token", token)
    settings = type("S", (), {"ebay_env": "production", "ebay_marketplace_id": "EBAY_AU"})()

    async def run():
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=report(("111", [900, 30, 2]))))
        async with httpx.AsyncClient(transport=transport) as client:
            return await refresh_traffic(session, settings, "tester", today=date(2026, 11, 6), client=client)

    assert asyncio.run(run()) == 2
    session.commit()
    stored = {t.ebay_listing_id: (t.impressions, t.views, t.transactions) for t in session.scalars(select(EbayListingTraffic))}
    assert stored == {"111": (900, 30, 2), "222": (0, 0, 0)}


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_panel_prompts_reconnect_when_no_traffic(client):
    response = client.get("/analytics")
    assert response.status_code == 200
    assert "eBay performance" in response.text and "/auth/ebay/start" in response.text


def test_panel_groups_listings(client, session):
    for listing_id, title, values in (
        ("1", "Looked at <Charizard>", (800, 40, 0)),
        ("2", "Seller Eevee", (600, 30, 3)),
        ("3", "Hidden Nymble", (2, 0, 0)),
    ):
        add_listing(session, listing_id, title)
        impressions, views, sold = values
        session.add(EbayListingTraffic(ebay_listing_id=listing_id, period_start=date(2026, 10, 6),
                                       period_end=date(2026, 11, 4), impressions=impressions, views=views,
                                       transactions=sold, fetched_at=datetime(2026, 11, 6, tzinfo=UTC)))
    session.commit()
    text = client.get("/analytics").text
    assert "1,402" in text and "70" in text and "4.3%" in text
    assert "Looked at &lt;Charizard&gt;" in text
    not_selling = text.split("Viewed but not selling")[1].split("Rarely seen")[0]
    assert "Charizard" in not_selling and "Eevee" not in not_selling
    assert text.split("Rarely seen")[1].split("Best sellers")[0].index("Nymble") < text.split("Rarely seen")[1].index("Eevee")
    assert "3 sold from 30 views" in text.split("Best sellers")[1]
