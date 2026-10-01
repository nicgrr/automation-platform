from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from automation_control.config import Settings
from automation_control.database import Base
from automation_control.models import (
    CollectibleProduct, SealedProduct,
    CardPrice, CardVariant, CatalogItem, CatalogItemType, InventoryItem, TcgCard,
)
from scripts import import_ebay_prices as script


def _configured_settings():
    return Settings(
        ebay_env="sandbox", ebay_client_id="app-SBX-x", ebay_runame="app-SBX-runame",
        ebay_client_secret="s", ebay_token_encryption_key=Fernet.generate_key().decode(),
        app_public_base_url="https://example.test",
    )


def _seed(session, *, owned=True):
    session.add(CatalogItem(id="op13-076", item_type=CatalogItemType.TCG_CARD, name="Divine Departure"))
    session.add(TcgCard(catalog_item_id="op13-076", game="one_piece", set_code="OP13", number="076"))
    if owned:
        session.add(InventoryItem(id="inv1", card_id=None, catalog_item_id="op13-076", variant=CardVariant.NORMAL, quantity=1))
    session.commit()


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def patched(monkeypatch, engine):
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script, "get_settings", _configured_settings)
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    oauth = MagicMock()
    oauth.application_token = AsyncMock(return_value={"access_token": "app-token"})
    monkeypatch.setattr(script, "ebay_services", lambda settings: (MagicMock(), oauth))
    return engine


def _listings(*values, currency="AUD", title="Divine Departure OP13-076"):
    # titles must mention the item, or the relevance filter (correctly)
    # discards them as unrelated results
    return {"itemSummaries": [
        {"itemId": str(i), "title": title, "price": {"value": str(v), "currency": currency}}
        for i, v in enumerate(values)
    ]}


def test_build_query_prefers_set_code_and_number():
    item = CatalogItem(id="x", item_type=CatalogItemType.TCG_CARD, name="Divine Departure")
    tcg = TcgCard(catalog_item_id="x", game="one_piece", set_code="OP13", number="076")
    assert script.build_query(item, tcg) == ("Divine Departure OP13-076", "Divine Departure")


def test_build_query_uses_set_name_for_pokemon_rows_without_a_set_code():
    # every pokemon row has set_id + card_sets rather than a set_code, so
    # without this they all degraded to a bare, useless name search
    item = CatalogItem(id="xy11-1", item_type=CatalogItemType.TCG_CARD, name="Tangela")
    tcg = TcgCard(catalog_item_id="xy11-1", game="pokemon", set_id="xy11", number="1")
    assert script.build_query(item, tcg, set_name="Steam Siege") == ("Tangela Steam Siege 1", "Tangela")


def test_build_query_uses_brand_for_collectibles():
    # regression: a Sonny Angel named "Hamburger" searched eBay for
    # "Hamburger" and priced against kitchen gadgets
    item = CatalogItem(id="c1", item_type=CatalogItemType.COLLECTIBLE, name="Hamburger")
    collectible = CollectibleProduct(catalog_item_id="c1", brand="Sonny Angel", is_secret=False)
    assert script.build_query(item, None, collectible=collectible) == ("Sonny Angel Hamburger", "Sonny Angel")


def test_build_query_targets_the_secret_variant_when_flagged():
    # a secret/chase figure is worth materially more than the regular one,
    # so pricing it against regular listings understates it
    item = CatalogItem(id="c1", item_type=CatalogItemType.COLLECTIBLE, name="Hamburger")
    collectible = CollectibleProduct(catalog_item_id="c1", brand="Sonny Angel", is_secret=True)
    assert script.build_query(item, None, collectible=collectible) == ("Sonny Angel Hamburger secret", "Sonny Angel")


def test_build_query_strips_a_trailing_parenthetical_for_collectibles():
    """The bug this guards: "Sonny Angel Sugar (sugar pot/jar)" returned 0
    eBay results while "Sonny Angel Sugar" returned 10 -- confirmed live.
    The relevance filter stays on brand alone, so nothing is lost."""
    item = CatalogItem(id="c1", item_type=CatalogItemType.COLLECTIBLE, name="Sugar (sugar pot/jar)")
    collectible = CollectibleProduct(catalog_item_id="c1", brand="Sonny Angel", is_secret=False)
    assert script.build_query(item, None, collectible=collectible) == ("Sonny Angel Sugar", "Sonny Angel")


def test_build_query_falls_back_to_name_without_a_code():
    item = CatalogItem(id="x", item_type=CatalogItemType.TCG_CARD, name="Some Promo")
    assert script.build_query(item, None) == ("Some Promo", "Some Promo")


def test_irrelevant_listings_are_excluded_from_pricing():
    listings = [
        {"title": "Sonny Angel Hamburger Mini Figure", "price": "40.00"},
        {"title": "Hamburger Press Kitchen Gadget", "price": "5.00"},
    ]
    assert script._price_values(listings, "Sonny Angel") == [Decimal("40.00")]


def test_gender_symbol_is_ignored_since_real_listings_rarely_include_it():
    """The bug this guards: Nidoran ♀/♂ (and family) went permanently
    unpriced because must_contain required the literal unicode symbol,
    which real eBay sellers essentially never type."""
    listings = [
        {"title": "Pokemon Nidoran Female 151 Card NM", "price": "1.20"},
        {"title": "Pokemon Nidoran F 151 Card NM", "price": "1.50"},
        {"title": "Totally Unrelated Card", "price": "99.00"},
    ]
    assert script._price_values(listings, "Nidoran ♀") == [Decimal("1.20"), Decimal("1.50")]


def test_records_low_and_median_as_separate_sources(patched):
    engine = patched
    with Session(engine) as session:
        _seed(session)

    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value=_listings(10, 20, 60))):
        assert script.main([]) == 0

    with Session(engine) as session:
        prices = {p.source: p for p in session.query(CardPrice).all()}
        assert set(prices) == {"ebay_active_low", "ebay_active_median"}
        assert prices["ebay_active_low"].price == Decimal("10")
        assert prices["ebay_active_median"].price == Decimal("20")
        assert prices["ebay_active_low"].currency == "AUD"
        assert prices["ebay_active_low"].card_id == "op13-076"


def test_dry_run_queries_but_writes_nothing(patched):
    engine = patched
    with Session(engine) as session:
        _seed(session)

    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value=_listings(10, 20))):
        assert script.main(["--dry-run"]) == 0

    with Session(engine) as session:
        assert session.query(CardPrice).count() == 0


def test_no_listings_writes_no_fabricated_price(patched):
    engine = patched
    with Session(engine) as session:
        _seed(session)

    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value={"itemSummaries": []})):
        assert script.main([]) == 0

    with Session(engine) as session:
        assert session.query(CardPrice).count() == 0


def test_api_failure_on_one_card_does_not_abort_the_run(patched):
    from automation_control.adapters.ebay import EbayApiError

    engine = patched
    with Session(engine) as session:
        _seed(session)
        session.add(CatalogItem(id="op13-077", item_type=CatalogItemType.TCG_CARD, name="Second Card"))
        session.add(TcgCard(catalog_item_id="op13-077", game="one_piece", set_code="OP13", number="077"))
        session.add(InventoryItem(id="inv2", card_id=None, catalog_item_id="op13-077", variant=CardVariant.NORMAL, quantity=1))
        session.commit()

    search = AsyncMock(side_effect=[EbayApiError("boom"), _listings(30, 40, title="Second Card OP13-077")])
    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=search):
        assert script.main([]) == 0

    with Session(engine) as session:
        # the second card still got priced despite the first one failing
        assert session.query(CardPrice).count() == 2


def test_defaults_to_only_pricing_cards_you_own(patched):
    engine = patched
    with Session(engine) as session:
        _seed(session, owned=False)  # in the catalogue, but not owned

    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value=_listings(10))):
        assert script.main([]) == 0

    with Session(engine) as session:
        assert session.query(CardPrice).count() == 0


def test_all_flag_prices_unowned_catalogue_cards_too(patched):
    engine = patched
    with Session(engine) as session:
        _seed(session, owned=False)

    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value=_listings(10, 12))):
        assert script.main(["--all"]) == 0

    with Session(engine) as session:
        assert session.query(CardPrice).count() == 2


def test_build_query_uses_the_catalog_name_directly_for_sealed_products():
    item = CatalogItem(id="etb1", item_type=CatalogItemType.SEALED_PRODUCT, name="Pokemon TCG 151 Elite Trainer Box")
    sealed = SealedProduct(catalog_item_id="etb1", game="pokemon", product_type="etb")
    assert script.build_query(item, None, sealed=sealed) == (
        "Pokemon TCG 151 Elite Trainer Box", "Pokemon TCG 151 Elite Trainer Box",
    )


def test_sealed_flag_prices_catalogue_sealed_products_even_if_unowned(patched):
    """Sealed products are reference data, not owned stock the normal
    inventory-only default would find -- same reasoning as --collectibles."""
    engine = patched
    with Session(engine) as session:
        session.add(CatalogItem(id="etb1", item_type=CatalogItemType.SEALED_PRODUCT,
                                name="Pokemon TCG 151 Elite Trainer Box"))
        session.add(SealedProduct(catalog_item_id="etb1", game="pokemon", product_type="etb"))
        session.commit()

    listings = _listings(80, 90, title="Pokemon TCG 151 Elite Trainer Box")
    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value=listings)):
        assert script.main(["--sealed"]) == 0

    with Session(engine) as session:
        prices = {p.source: p for p in session.query(CardPrice).all()}
        assert prices["ebay_active_low"].card_id == "etb1"
        assert prices["ebay_active_low"].price == Decimal("80")


def test_sealed_flag_does_not_also_price_regular_cards(patched):
    engine = patched
    with Session(engine) as session:
        _seed(session, owned=False)
        session.add(CatalogItem(id="etb1", item_type=CatalogItemType.SEALED_PRODUCT,
                                name="Pokemon TCG 151 Elite Trainer Box"))
        session.add(SealedProduct(catalog_item_id="etb1", game="pokemon", product_type="etb"))
        session.commit()

    listings = _listings(80, title="Pokemon TCG 151 Elite Trainer Box")
    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value=listings)):
        assert script.main(["--sealed"]) == 0

    with Session(engine) as session:
        priced_ids = {p.card_id for p in session.query(CardPrice).all()}
        assert priced_ids == {"etb1"}


def test_multi_box_bundles_are_excluded_from_sealed_product_pricing():
    """Confirmed live (2026-09-11): a real, single-box Scarlet & Violet
    Base ETB listing sits around $205-330 AUD now that the set is out of
    print -- genuine appreciation, not noise. What does skew the number
    is a seller bundling multiple boxes into one listing at roughly
    multiple-unit price ("ETB PAIR", "Both Miraidon & Koraidon")."""
    listings = [
        {"title": "Pokemon TCG Scarlet & Violet Base Elite Trainer Box Sealed", "price": "230.00"},
        {"title": "Pokemon Scarlet and Violet Base Elite Trainer Box PAIR (NEW & SEALED)", "price": "589.00"},
        {"title": "Scarlet & Violet Base Set Elite Trainer Box Both Miraidon & Koraidon NEW", "price": "563.13"},
        {"title": "Pokemon TCG Scarlet & Violet Base Elite Trainer Box Lot of 3", "price": "820.00"},
    ]
    values = script._price_values(listings, "Pokemon TCG Scarlet & Violet Base Elite Trainer Box", exclude_multiples=True)
    assert values == [Decimal("230.00")]


def test_multi_box_exclusion_is_off_by_default_for_cards():
    """Cards don't have this problem -- a listing is one card or a lot,
    and lots read differently -- so the filter is opt-in, not global."""
    listings = [{"title": "Divine Departure OP13-076 Lot of 3", "price": "50.00"}]
    assert script._price_values(listings, "Divine Departure") == [Decimal("50.00")]


def test_unconfigured_ebay_exits_with_a_clear_message(monkeypatch, engine):
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script, "get_settings", lambda: Settings(ebay_env="sandbox"))

    with pytest.raises(SystemExit) as exc:
        script.main([])
    assert "not configured" in str(exc.value)


def test_targets_are_ordered_never_priced_first_then_stalest(engine):
    """Without an order, --limit re-priced whatever the DB returned first,
    every run -- so a limit below the held count silently starved every
    card past it and refreshed the same rows forever. That also made the
    review page's "Refresh eBay prices" button a 200-card no-op."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    with sessionmaker(bind=engine)() as session:
        for cid, name in [("a", "Fresh"), ("b", "Stale"), ("c", "Never")]:
            session.add(CatalogItem(id=cid, item_type=CatalogItemType.TCG_CARD, name=name))
            session.add(TcgCard(catalog_item_id=cid, game="one_piece", set_code="OP13", number=cid))
            session.add(InventoryItem(card_id=None, catalog_item_id=cid,
                                      variant=CardVariant.NORMAL, quantity=1))
        session.add(CardPrice(catalog_item_id="a", variant=CardVariant.NORMAL, source="ebay_active_low",
                              price=Decimal("1"), currency="AUD", fetched_at=now))
        session.add(CardPrice(catalog_item_id="b", variant=CardVariant.NORMAL, source="ebay_active_low",
                              price=Decimal("1"), currency="AUD", fetched_at=now - timedelta(days=9)))
        session.commit()

        targets = script._targets(session, price_all=False, game=None, limit=10)

    assert [row[0].id for row in targets] == ["c", "b", "a"]


def test_a_limit_takes_the_stalest_not_an_arbitrary_slice(engine):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    with sessionmaker(bind=engine)() as session:
        for cid in ("a", "b"):
            session.add(CatalogItem(id=cid, item_type=CatalogItemType.TCG_CARD, name=cid))
            session.add(TcgCard(catalog_item_id=cid, game="one_piece", set_code="OP13", number=cid))
            session.add(InventoryItem(card_id=None, catalog_item_id=cid,
                                      variant=CardVariant.NORMAL, quantity=1))
        session.add(CardPrice(catalog_item_id="a", variant=CardVariant.NORMAL, source="ebay_active_low",
                              price=Decimal("1"), currency="AUD", fetched_at=now))
        session.add(CardPrice(catalog_item_id="b", variant=CardVariant.NORMAL, source="ebay_active_low",
                              price=Decimal("1"), currency="AUD", fetched_at=now - timedelta(days=30)))
        session.commit()

        targets = script._targets(session, price_all=False, game=None, limit=1)

    assert [row[0].id for row in targets] == ["b"]


def test_inventory_without_a_catalog_item_id_is_invisible_to_pricing(engine):
    """Not a hypothetical: 185 inventory rows carrying only the older
    card_id column were invisible to this script, which is why 163 held
    cards had no ebay_active_low row no matter how often it ran. They were
    invisible to list_for_sale on the same filter, so 473 copies could not
    be listed either."""
    with sessionmaker(bind=engine)() as session:
        session.add(CatalogItem(id="op13-076", item_type=CatalogItemType.TCG_CARD, name="Divine Departure"))
        session.add(TcgCard(catalog_item_id="op13-076", game="one_piece", set_code="OP13", number="076"))
        # Old-shaped row: card_id only, no catalog_item_id.
        session.add(InventoryItem(card_id="op13-076", catalog_item_id=None,
                                  variant=CardVariant.NORMAL, quantity=1))
        session.commit()

        assert script._targets(session, price_all=False, game=None, limit=10) == []

        # Filling the newer column is all it takes to make it visible.
        row = session.scalars(select(InventoryItem)).one()
        row.catalog_item_id = row.card_id
        session.commit()

        assert [r[0].id for r in script._targets(session, price_all=False, game=None, limit=10)] == ["op13-076"]
