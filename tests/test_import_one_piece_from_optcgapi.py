from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import CardPrice, CardSet, CatalogItem, TcgCard

ALL_SETS = [{"set_name": "Romance Dawn", "set_id": "OP-01"}]

OP01_CARDS = [
    {"card_name": "Roronoa Zoro (001)", "set_name": "Romance Dawn", "set_id": "OP-01", "rarity": "L",
     "card_set_id": "OP01-001", "card_image": "https://example.test/OP01-001.jpg", "market_price": 2.08},
    {"card_name": "Roronoa Zoro (001) (Parallel)", "set_name": "Romance Dawn", "set_id": "OP-01", "rarity": "L",
     "card_set_id": "OP01-001", "card_image": "https://example.test/OP01-001_p1.jpg", "market_price": 568.01},
    {"card_name": "Monkey.D.Luffy (P-055) (Jolly Roger Foil)", "set_name": "Romance Dawn", "set_id": "OP-01",
     "rarity": "SEC", "card_set_id": "P-055", "card_image": "https://example.test/P-055.jpg", "market_price": None},
]


def _mock_client(all_sets=ALL_SETS, set_cards=None):
    set_cards = set_cards if set_cards is not None else OP01_CARDS
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    def get(url, **kwargs):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        if url.endswith("/allSets/"):
            response.json.return_value = all_sets
        else:
            response.json.return_value = set_cards
        return response

    client.get.side_effect = get
    return client


def _mock_client_per_set(all_sets, cards_by_set_id):
    """Like _mock_client, but a different card list per requested set_id --
    for reproducing a card_set_id that's reprinted across two sets."""
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    def get(url, **kwargs):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        if url.endswith("/allSets/"):
            response.json.return_value = all_sets
        else:
            set_id = url.rstrip("/").rsplit("/", 1)[-1]
            response.json.return_value = cards_by_set_id.get(set_id, [])
        return response

    client.get.side_effect = get
    return client


def test_a_reprint_in_a_later_set_never_overwrites_an_already_plain_card(tmp_path, monkeypatch):
    """The real bug this guards: some secret-rare cards are reprinted
    under the SAME card_set_id inside a totally different, later set's
    own card list (optcgapi genuinely does this) -- _dedupe_parallels
    can't catch that because it only sees one set's list at a time, so
    without this guard the later set's own non-plain listing silently
    overwrote the first set's correct plain photo."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    all_sets = [{"set_name": "Romance Dawn", "set_id": "OP-01"}, {"set_name": "Paramount War", "set_id": "OP-02"}]
    cards_by_set = {
        "OP-01": [{"card_name": "Roronoa Zoro (001)", "card_set_id": "OP01-001",
                   "card_image": "https://example.test/plain.jpg", "market_price": 2.0}],
        "OP-02": [{"card_name": "Roronoa Zoro (001) (SP)", "card_set_id": "OP01-001",
                   "card_image": "https://example.test/sample.jpg", "market_price": 500.0}],
    }
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client_per_set(all_sets, cards_by_set))
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    script.main([])

    with Session(engine) as session:
        row = session.query(TcgCard).filter_by(number="001").one()
        assert row.character == "Roronoa Zoro (001)"
        item = session.get(CatalogItem, row.catalog_item_id)
        assert item.image_url == "https://example.test/plain.jpg"
        # still worth keeping both price observations -- just not letting
        # the reprint's own name/image win
        assert session.query(CardPrice).filter_by(catalog_item_id=row.catalog_item_id).count() == 2


def test_a_reprint_in_a_later_set_never_overwrites_when_base_name_embeds_the_full_code(tmp_path, monkeypatch):
    """The real bug this guards: a base card_name sometimes embeds the
    full alphanumeric card code, not just a bare number -- "Roronoa Zoro
    (EB04-007)" -- and a plain-print check that only stripped a trailing
    *purely numeric* parenthetical ("(001)") wrongly flagged this as a
    non-plain name too, defeating the reprint-collision guard above for
    every card whose home set uses this naming style."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    all_sets = [{"set_name": "Adventure on Kami's Island", "set_id": "OP15-EB04"},
                {"set_name": "The World's Strongest Warriors", "set_id": "OP-17"}]
    cards_by_set = {
        "OP15-EB04": [{"card_name": "Roronoa Zoro (EB04-007)", "card_set_id": "EB04-007",
                       "card_image": "https://example.test/plain.jpg", "market_price": 3.0}],
        "OP-17": [{"card_name": "Roronoa Zoro (EB04-007) (SP)", "card_set_id": "EB04-007",
                   "card_image": "https://example.test/sample.jpg", "market_price": 400.0}],
    }
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client_per_set(all_sets, cards_by_set))
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    script.main([])

    with Session(engine) as session:
        row = session.query(TcgCard).filter_by(number="007").one()
        assert row.character == "Roronoa Zoro (EB04-007)"
        item = session.get(CatalogItem, row.catalog_item_id)
        assert item.image_url == "https://example.test/plain.jpg"


def test_import_creates_one_row_per_physical_card_preferring_non_parallel(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client())
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    result = script.main([])
    assert result == 0

    with Session(engine) as session:
        rows = session.query(TcgCard).filter_by(game="one_piece").all()
        by_number = {r.number: r for r in rows}
        assert set(by_number) == {"001", "055"}
        # the parallel duplicate of OP01-001 was not imported as a second row
        assert by_number["001"].character == "Roronoa Zoro (001)"
        assert "Parallel" not in by_number["001"].character
        # promo code "P-055" parsed as set_code="P", number="055"
        assert by_number["055"].set_code == "P"

        item = session.get(CatalogItem, by_number["001"].catalog_item_id)
        assert item.image_url == "https://example.test/OP01-001.jpg"


def test_prefers_plain_print_over_alternate_art_not_just_literal_parallel(tmp_path, monkeypatch):
    """The real bug this guards: optcgapi never actually labels a card
    "(Parallel)" -- alternate/chase prints are named "(Alternate Art)"
    (sometimes "(SP)"), so a check that only recognized the literal word
    "(Parallel)" never fired and just kept whichever entry the API
    listed first, occasionally landing on a SAMPLE-watermarked preview
    image instead of the plain card's real photo."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    # Alternate Art listed FIRST -- a naive "keep whichever comes first"
    # dedupe would wrongly keep this one.
    cards = [
        {"card_name": "Ms. All Sunday (Alternate Art)", "set_name": "Romance Dawn", "set_id": "OP-01",
         "rarity": "SR", "card_set_id": "OP01-084", "card_image": "https://example.test/alt.jpg", "market_price": 55.0},
        {"card_name": "Ms. All Sunday", "set_name": "Romance Dawn", "set_id": "OP-01", "rarity": "SR",
         "card_set_id": "OP01-084", "card_image": "https://example.test/plain.jpg", "market_price": 1.5},
    ]
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client(set_cards=cards))
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    script.main([])

    with Session(engine) as session:
        row = session.query(TcgCard).filter_by(number="084").one()
        assert row.character == "Ms. All Sunday"
        item = session.get(CatalogItem, row.catalog_item_id)
        assert item.image_url == "https://example.test/plain.jpg"


def test_import_records_market_price_preferring_the_non_parallel_value(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client())
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    script.main([])

    with Session(engine) as session:
        zoro = session.query(TcgCard).filter_by(number="001").one()
        prices = session.query(CardPrice).filter_by(catalog_item_id=zoro.catalog_item_id).all()
        assert len(prices) == 1
        assert float(prices[0].price) == 2.08  # the non-parallel printing's price, not the parallel's 568.01
        assert prices[0].source == "optcgapi_market"
        assert prices[0].currency == "USD"

        # P-055 had market_price=None -- no fabricated price row
        luffy = session.query(TcgCard).filter_by(number="055").one()
        assert session.query(CardPrice).filter_by(catalog_item_id=luffy.catalog_item_id).count() == 0


def test_reimporting_appends_a_new_price_observation_each_time(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client())
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    script.main([])
    script.main([])

    with Session(engine) as session:
        zoro = session.query(TcgCard).filter_by(number="001").one()
        # append-only, same convention as the rest of card_prices -- two
        # runs means two observations, not one row overwritten
        assert session.query(CardPrice).filter_by(catalog_item_id=zoro.catalog_item_id).count() == 2


def test_no_prices_flag_skips_writing_card_prices(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client())
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    script.main(["--no-prices"])

    with Session(engine) as session:
        assert session.query(CardPrice).count() == 0
        assert session.query(TcgCard).filter_by(game="one_piece").count() == 2


def test_reimporting_updates_instead_of_duplicating(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client())
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    script.main([])
    script.main([])

    with Session(engine) as session:
        assert session.query(TcgCard).filter_by(game="one_piece").count() == 2


def test_imported_cards_reference_a_named_card_set(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client())
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    script.main(["--no-prices"])

    with Session(engine) as session:
        card = session.query(TcgCard).filter_by(game="one_piece").first()
        card_set = session.get(CardSet, card.set_id)
        assert card_set.name == "Romance Dawn"
        assert card_set.code == "OP-01"


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client())
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    script.main(["--dry-run"])

    with Session(engine) as session:
        assert session.query(TcgCard).filter_by(game="one_piece").count() == 0


def test_only_set_filters_to_that_set(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    two_sets = ALL_SETS + [{"set_name": "Paramount War", "set_id": "OP-02"}]
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client(all_sets=two_sets))
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    try:
        script.main(["--set", "OP-99", "--dry-run"])
        assert False, "expected SystemExit for an unknown set"
    except SystemExit:
        pass


def test_unparseable_card_set_id_is_skipped_not_fatal(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_one_piece_from_optcgapi as script
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    weird_cards = [{"card_name": "No dash here", "set_name": "Romance Dawn", "set_id": "OP-01", "rarity": "C", "card_set_id": "NODASH", "card_image": ""}]
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client(set_cards=weird_cards))
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    result = script.main([])
    assert result == 0
    with Session(engine) as session:
        assert session.query(TcgCard).filter_by(game="one_piece").count() == 0
