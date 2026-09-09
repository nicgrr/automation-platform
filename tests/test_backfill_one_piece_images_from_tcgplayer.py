from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from automation_control.database import Base
from automation_control.models import CatalogItem, CatalogItemType, TcgCard
from scripts import backfill_one_piece_images_from_tcgplayer as script

GROUPS = [{"groupId": 1, "name": "Romance Dawn", "abbreviation": "OP01"}]


def _product(name, number, product_id):
    return {"productId": product_id, "name": name, "extendedData": [{"name": "Number", "value": number}]}


def _mock_client(groups=GROUPS, products_by_group=None):
    products_by_group = products_by_group or {}
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    def get(url, **kwargs):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        if url.endswith("/groups"):
            response.json.return_value = {"results": groups}
        else:
            group_id = int(url.rstrip("/").rsplit("/", 2)[-2])
            response.json.return_value = {"results": products_by_group.get(group_id, [])}
        return response

    client.get.side_effect = get
    return client


def _seed_card(session, item_id, character, set_id="op1", number="1"):
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=character, image_url="https://old.example/watermarked.jpg"))
    session.add(TcgCard(catalog_item_id=item_id, game="one_piece", set_id=set_id, set_code="OP01", number=number, character=character))
    session.commit()


def test_updates_image_for_an_existing_plain_card(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client(
        products_by_group={1: [_product("Roronoa Zoro (001)", "OP01-001", 100)]}
    ))
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    with Session(engine) as session:
        _seed_card(session, "one-piece-op01-001", "Roronoa Zoro (001)", number="1")

    result = script.main([])
    assert result == 0

    with Session(engine) as session:
        item = session.get(CatalogItem, "one-piece-op01-001")
        assert item.image_url == "https://tcgplayer-cdn.tcgplayer.com/product/100_in_1000x1000.jpg"


def test_never_takes_a_non_plain_products_image(tmp_path, monkeypatch):
    """The bug this guards: a group can list ONLY a reprint/alternate-art
    edition of a number (a real TCGplayer cross-listing quirk, same as
    optcgapi's) -- that must never become the catalogue's reference photo,
    even when it's the only candidate a given group has for that number."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client(
        products_by_group={1: [_product("Roronoa Zoro (001) (Alternate Art)", "OP01-001", 101)]}
    ))
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    with Session(engine) as session:
        _seed_card(session, "one-piece-op01-001", "Roronoa Zoro (001)", number="1")

    script.main([])

    with Session(engine) as session:
        item = session.get(CatalogItem, "one-piece-op01-001")
        assert item.image_url == "https://old.example/watermarked.jpg"


def test_prefers_plain_over_alternate_art_within_one_group(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client(
        products_by_group={1: [
            _product("Roronoa Zoro (001) (Alternate Art)", "OP01-001", 101),
            _product("Roronoa Zoro (001)", "OP01-001", 100),
        ]}
    ))
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    with Session(engine) as session:
        _seed_card(session, "one-piece-op01-001", "Roronoa Zoro (001)", number="1")

    script.main([])

    with Session(engine) as session:
        item = session.get(CatalogItem, "one-piece-op01-001")
        assert item.image_url == "https://tcgplayer-cdn.tcgplayer.com/product/100_in_1000x1000.jpg"


def test_skips_a_number_with_no_matching_local_card(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client(
        products_by_group={1: [_product("Nobody Local (999)", "OP01-999", 200)]}
    ))
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    result = script.main([])
    assert result == 0  # doesn't blow up on an unmatched card


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client(
        products_by_group={1: [_product("Roronoa Zoro (001)", "OP01-001", 100)]}
    ))
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    with Session(engine) as session:
        _seed_card(session, "one-piece-op01-001", "Roronoa Zoro (001)", number="1")

    script.main(["--dry-run"])

    with Session(engine) as session:
        item = session.get(CatalogItem, "one-piece-op01-001")
        assert item.image_url == "https://old.example/watermarked.jpg"


def test_only_group_filters_to_that_group(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    two_groups = GROUPS + [{"groupId": 2, "name": "Paramount War", "abbreviation": "OP02"}]
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: _mock_client(groups=two_groups))
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    try:
        script.main(["--group", "OP99"])
        assert False, "expected SystemExit for an unknown group abbreviation"
    except SystemExit:
        pass
