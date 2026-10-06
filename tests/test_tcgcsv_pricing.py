from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select

from automation_control.models import (
    PricingProviderCategory,
    PricingProviderGroup,
    PricingProviderProduct,
    PricingProviderQuote,
    PricingSyncRun,
    PricingSyncStatus,
)
from automation_control.pricing.providers.tcgcsv import (
    TcgCsvClient,
    parse_timestamp,
    select_relevant_categories,
)
from automation_control.pricing.tcgcsv_sync import sync_tcgcsv


def _payload(results):
    return {"success": True, "errors": [], "results": results}


def _transport(request: httpx.Request) -> httpx.Response:
    assert request.headers["user-agent"].startswith("EzBay/")
    routes = {
        "/last-updated.txt": "2026-10-02T20:00:00Z",
        "/tcgplayer/categories": _payload([
            {"categoryId": 3, "name": "Pokemon", "displayName": "Pokemon", "modifiedOn": "2026-10-02T01:00:00Z"},
            {"categoryId": 80, "name": "Dragon Ball Super: Fusion World", "displayName": "Dragon Ball Super: Fusion World"},
            {"categoryId": 27, "name": "Dragon Ball Super CCG", "displayName": "Dragon Ball Super CCG"},
        ]),
        "/tcgplayer/3/groups": _payload([{
            "groupId": 3170,
            "name": "SWSH12: Silver Tempest",
            "abbreviation": "SWSH12",
            "publishedOn": "2022-11-11T00:00:00Z",
            "modifiedOn": "2026-10-01T00:00:00Z",
        }]),
        "/tcgplayer/3/3170/products": _payload([
            {
                "productId": 451396,
                "name": "Lugia VSTAR",
                "cleanName": "Lugia VSTAR",
                "imageUrl": "https://example.test/lugia.jpg",
                "url": "https://example.test/lugia",
                "modifiedOn": "2026-10-01T02:00:00Z",
                "extendedData": [
                    {"name": "Number", "value": "139/195"},
                    {"name": "Rarity", "value": "Ultra Rare"},
                ],
            },
            {
                "productId": 900001,
                "name": "Silver Tempest Booster Box",
                "cleanName": "Silver Tempest Booster Box",
                "extendedData": [],
            },
        ]),
        "/tcgplayer/3/3170/prices": _payload([
            {
                "productId": 451396,
                "subTypeName": "Holofoil",
                "lowPrice": 9.5,
                "midPrice": 11.0,
                "highPrice": 20.0,
                "marketPrice": 10.25,
                "directLowPrice": 10.0,
            },
            {
                "productId": 451396,
                "subTypeName": "Reverse Holofoil",
                "lowPrice": 8.5,
                "midPrice": 9.0,
                "highPrice": 15.0,
                "marketPrice": 8.75,
                "directLowPrice": None,
            },
            {
                "productId": 900001,
                "subTypeName": "Normal",
                "lowPrice": 120,
                "midPrice": 140,
                "highPrice": 180,
                "marketPrice": 135,
                "directLowPrice": None,
            },
        ]),
    }
    value = routes.get(request.url.path)
    if value is None:
        return httpx.Response(404, json={"error": request.url.path})
    if isinstance(value, str):
        return httpx.Response(200, text=value)
    return httpx.Response(200, json=value)


def _client():
    return TcgCsvClient(
        client=httpx.Client(transport=httpx.MockTransport(_transport)),
        request_interval=0,
    )


def _two_group_transport(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/tcgplayer/3/groups":
        return httpx.Response(200, json=_payload([
            {"groupId": 3170, "name": "First", "abbreviation": "ONE"},
            {"groupId": 3171, "name": "Second", "abbreviation": "TWO"},
        ]))
    return _transport(request)


def test_parse_timestamp_normalizes_to_utc():
    assert parse_timestamp("2026-10-02T20:00:00Z") == datetime(2026, 10, 2, 20, tzinfo=UTC)
    assert parse_timestamp("not-a-date") is None


def test_category_discovery_does_not_confuse_fusion_world_with_super():
    categories = _payload([
        {"categoryId": 80, "displayName": "Dragon Ball Super: Fusion World"},
        {"categoryId": 27, "displayName": "Dragon Ball Super CCG"},
    ])["results"]
    selected = select_relevant_categories(
        categories, {"dragon_ball_super", "dragon_ball_fusion_world"},
    )
    assert {item["categoryId"] for item in selected} == {27, 80}


def test_daily_bulk_sync_persists_catalog_and_append_only_quotes(session):
    client = _client()
    summary = sync_tcgcsv(session, client, game_keys={"pokemon"})
    session.commit()

    assert summary.skipped is False
    assert summary.categories == 1
    assert summary.groups == 1
    assert summary.products == 2
    assert summary.quotes_added == 3
    assert summary.requests == 5
    assert summary.failures == 0
    assert session.scalar(select(func.count()).select_from(PricingProviderCategory)) == 1
    assert session.scalar(select(func.count()).select_from(PricingProviderGroup)) == 1
    assert session.scalar(select(func.count()).select_from(PricingProviderProduct)) == 2
    assert session.scalar(select(func.count()).select_from(PricingProviderQuote)) == 3

    lugia = session.scalar(select(PricingProviderProduct).where(
        PricingProviderProduct.provider_product_id == "451396",
    ))
    assert lugia is not None
    assert lugia.product_type == "single"
    assert lugia.card_number == "139/195"
    sealed = session.scalar(select(PricingProviderProduct).where(
        PricingProviderProduct.provider_product_id == "900001",
    ))
    assert sealed is not None and sealed.product_type == "sealed_or_other"
    quote = session.scalar(select(PricingProviderQuote).where(
        PricingProviderQuote.provider_product_id == "451396",
        PricingProviderQuote.subtype == "Holofoil",
    ))
    assert quote is not None
    assert str(quote.market_price) == "10.2500"
    assert quote.currency == "USD"

    forced = sync_tcgcsv(session, client, game_keys={"pokemon"}, force=True)
    assert forced.quotes_added == 0
    assert session.scalar(select(func.count()).select_from(PricingProviderQuote)) == 3
    session.commit()

    skipped = sync_tcgcsv(session, client, game_keys={"pokemon"})
    assert skipped.skipped is True
    assert skipped.requests == 1


def test_limited_smoke_sync_is_partial_and_does_not_mark_daily_build_complete(session):
    client = TcgCsvClient(
        client=httpx.Client(transport=httpx.MockTransport(_two_group_transport)),
        request_interval=0,
    )
    summary = sync_tcgcsv(
        session, client, game_keys={"pokemon"}, max_groups=1,
    )
    run = session.scalar(select(PricingSyncRun).order_by(PricingSyncRun.started_at.desc()))
    assert summary.groups == 1
    assert run is not None
    assert run.status is PricingSyncStatus.PARTIAL
    assert run.details["limited"] is True
