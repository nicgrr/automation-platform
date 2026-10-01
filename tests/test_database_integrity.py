from sqlalchemy import text

from automation_control.database import Base, make_engine


def test_sqlite_connections_enforce_foreign_keys(tmp_path):
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path / 'fk.db'}")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_current_metadata_uses_live_generic_foreign_key_targets(tmp_path):
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path / 'schema.db'}")
    Base.metadata.create_all(engine)

    with engine.connect() as connection:
        targets = {
            table: {
                (row["from"], row["table"], row["to"], row["on_delete"])
                for row in connection.execute(text(f"PRAGMA foreign_key_list({table})")).mappings()
            }
            for table in ("card_prices", "foil_labels", "sale_items", "whatnot_show_items", "shop_listings")
        }

    assert ("card_id", "catalog_items", "id", "CASCADE") in targets["card_prices"]
    assert ("inventory_item_id", "inventory_items", "id", "SET NULL") in targets["foil_labels"]
    assert ("inventory_item_id", "inventory_items", "id", "SET NULL") in targets["sale_items"]
    assert ("inventory_item_id", "inventory_items", "id", "SET NULL") in targets["whatnot_show_items"]
    assert ("inventory_item_id", "inventory_items", "id", "CASCADE") in targets["shop_listings"]
