from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.config import Settings
from automation_control.database import Base
from automation_control.market_scanner import live_public_search, scan_market_watches
from automation_control.models import MarketListing, MarketListingMatch, MarketWatchTerm


@pytest.fixture
def session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'scanner.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _settings(**overrides):
    values = dict(
        ebay_env="production",
        market_allowed_countries="AU,JP",
        brave_search_api_key=None,
        brave_search_storage_permitted=False,
        market_public_search_domains="gumtree.com.au,depop.com,buyee.jp,zenmarket.jp,fromjapan.co.jp",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_generic_ebay_scanner_uses_exact_au_and_jp_locations(session):
    watch = MarketWatchTerm(
        name="Dragon Ball Carddass", query="Dragon Ball Carddass",
        countries=["AU", "JP"], sources=["ebay"], active=True,
    )
    session.add(watch)
    session.commit()

    oauth = AsyncMock()
    oauth.application_token.return_value = {"access_token": "token"}

    async def search(_self, query, *, limit, marketplace, item_location_country):
        rows = [{
            "itemId": f"{item_location_country}-1",
            "title": f"Dragon Ball Carddass {item_location_country}",
            "price": {"value": "25", "currency": "AUD"},
            "itemLocation": {"country": item_location_country},
            "itemWebUrl": f"https://example.test/{item_location_country}",
        }]
        if item_location_country == "AU":
            rows.append({
                "itemId": "US-leak", "title": "Wrong country",
                "price": {"value": "5", "currency": "AUD"},
                "itemLocation": {"country": "US"}, "itemWebUrl": "https://example.test/us",
            })
        return {"itemSummaries": rows}

    with patch("automation_control.market_scanner.configured", return_value=True), \
         patch("automation_control.market_scanner.ebay_services", return_value=(None, oauth)), \
         patch("automation_control.market_scanner.EbaySandboxReadAdapter.search_market", new=search):
        result = await scan_market_watches(session, _settings(), limit=10)

    session.commit()
    assert result.found == 2
    assert result.created == 2
    assert result.skipped == 1
    assert {row.country for row in session.scalars(select(MarketListing)).all()} == {"AU", "JP"}
    assert session.query(MarketListingMatch).count() == 2
    session.refresh(watch)
    assert watch.last_scan_summary["found"] == 2


@pytest.mark.asyncio
async def test_public_web_discovery_requires_storage_permission(session):
    watch = MarketWatchTerm(
        name="Pokemon cards", query="Pokemon cards",
        countries=["AU"], sources=["public_web"], active=True,
    )
    session.add(watch)
    session.commit()

    with patch("automation_control.market_scanner._brave_results", new=AsyncMock()) as brave:
        result = await scan_market_watches(
            session,
            _settings(brave_search_api_key="key", brave_search_storage_permitted=False),
        )

    brave.assert_not_awaited()
    assert result.found == 0
    assert "storage is not enabled" in result.errors[0]


@pytest.mark.asyncio
async def test_live_public_search_is_transient_and_allow_listed(session):
    rows = [
        {
            "title": "Dragon Ball card A$25",
            "url": "https://www.gumtree.com.au/s-ad/example/1",
            "description": "Sydney seller",
        },
        {
            "title": "Unapproved result $1",
            "url": "https://example.test/listing",
            "description": "not allow-listed",
        },
    ]
    with patch(
        "automation_control.market_scanner._brave_results",
        new=AsyncMock(return_value=rows),
    ):
        results = await live_public_search(
            _settings(brave_search_api_key="key"), "Dragon Ball", "AU",
        )

    assert len(results) == 1
    assert results[0]["source"] == "gumtree"
    assert results[0]["price"] == Decimal("25")
    assert session.query(MarketListing).count() == 0


@pytest.mark.asyncio
async def test_live_public_search_rejects_country_outside_au_jp():
    with pytest.raises(ValueError, match="Country is not enabled"):
        await live_public_search(
            _settings(brave_search_api_key="key"), "Dragon Ball", "US",
        )


@pytest.mark.asyncio
async def test_public_web_results_normalize_known_au_and_japan_sources(session):
    watch = MarketWatchTerm(
        name="Sonny Angel", query="Sonny Angel",
        countries=["AU", "JP"], sources=["public_web"], active=True,
    )
    session.add(watch)
    session.commit()

    async def results(_settings_obj, _query, country, *, limit):
        if country == "AU":
            return [
                {"title": "Sonny Angel Peach A$45", "url": "https://www.gumtree.com.au/s-ad/x/1", "description": "collectible"},
                {"title": "Untrusted result", "url": "https://example.test/x", "description": "$1"},
            ]
        return [{
            "title": "Sonny Angel Robby JPY 3,000",
            "url": "https://zenmarket.jp/en/auction.aspx?itemCode=abc",
            "description": "Japanese listing",
        }]

    with patch("automation_control.market_scanner._brave_results", new=results), \
         patch("automation_control.market_scanner.exchange_rate_to_aud", new=AsyncMock(return_value=Decimal("0.01"))):
        result = await scan_market_watches(
            session,
            _settings(brave_search_api_key="key", brave_search_storage_permitted=True),
        )

    session.commit()
    assert result.found == 2
    assert result.skipped == 1
    listings = session.scalars(select(MarketListing).order_by(MarketListing.source)).all()
    assert [(row.source, row.country) for row in listings] == [("gumtree", "AU"), ("zenmarket", "JP")]
    assert listings[0].landed_aud == Decimal("45.00")
    assert listings[1].landed_aud == Decimal("30.00")
