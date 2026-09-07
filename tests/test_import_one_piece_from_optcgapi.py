from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import CatalogItem, TcgCard

ALL_SETS = [{"set_name": "Romance Dawn", "set_id": "OP-01"}]

OP01_CARDS = [
    {"card_name": "Roronoa Zoro (001)", "set_name": "Romance Dawn", "set_id": "OP-01", "rarity": "L",
     "card_set_id": "OP01-001", "card_image": "https://example.test/OP01-001.jpg"},
    {"card_name": "Roronoa Zoro (001) (Parallel)", "set_name": "Romance Dawn", "set_id": "OP-01", "rarity": "L",
     "card_set_id": "OP01-001", "card_image": "https://example.test/OP01-001_p1.jpg"},
    {"card_name": "Monkey.D.Luffy (P-055) (Jolly Roger Foil)", "set_name": "Romance Dawn", "set_id": "OP-01",
     "rarity": "SEC", "card_set_id": "P-055", "card_image": "https://example.test/P-055.jpg"},
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
