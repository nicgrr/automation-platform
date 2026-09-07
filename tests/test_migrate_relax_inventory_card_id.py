from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import CardVariant, InventoryItem, InventoryStatus


def _make_legacy_db(tmp_path):
    """Build a DB with the OLD (NOT NULL card_id) schema, the way production
    looked before this migration -- so the test exercises the actual rebuild,
    not just a no-op against an already-nullable column."""
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE catalog_cards (id VARCHAR(64) PRIMARY KEY)
        """))
        conn.execute(text("""
            CREATE TABLE inventory_items (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                card_id VARCHAR(64) NOT NULL,
                variant VARCHAR(12) NOT NULL,
                condition VARCHAR(64) NOT NULL,
                quantity INTEGER NOT NULL,
                scan_image_path VARCHAR(512),
                session_id VARCHAR(36),
                notes TEXT,
                added_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                catalog_item_id VARCHAR(64),
                storage_location_id VARCHAR(36),
                status VARCHAR(19) NOT NULL,
                allocated_cost_basis NUMERIC(10, 2),
                grading_company VARCHAR(64),
                certification_number VARCHAR(64),
                CONSTRAINT uq_inventory_card_variant_condition UNIQUE (card_id, variant, condition)
            )
        """))
        conn.execute(text("CREATE INDEX ix_inventory_items_status ON inventory_items (status)"))
        conn.execute(text("CREATE INDEX ix_inventory_items_card_id ON inventory_items (card_id)"))
        conn.execute(text(
            "INSERT INTO inventory_items (id, card_id, variant, condition, quantity, added_at, updated_at, status) "
            "VALUES ('item1', 'card1', 'NORMAL', 'Near Mint', 3, '2026-01-01', '2026-01-01', 'AVAILABLE')"
        ))
    return engine, db_path


def test_migration_rebuilds_table_with_nullable_card_id_and_preserves_data(tmp_path, monkeypatch):
    engine, db_path = _make_legacy_db(tmp_path)
    import automation_control.database as database_module
    monkeypatch.setattr(database_module, "engine", engine)

    from scripts import migrate_relax_inventory_card_id
    monkeypatch.setattr(migrate_relax_inventory_card_id, "engine", engine)

    result = migrate_relax_inventory_card_id.main([])
    assert result == 0

    with Session(engine) as session:
        item = session.get(InventoryItem, "item1")
        assert item.card_id == "card1"
        assert item.quantity == 3
        assert item.status == InventoryStatus.AVAILABLE

        # the whole point: a collectible row with no card_id now works
        collectible_item = InventoryItem(id="item2", card_id=None, catalog_item_id=None, variant=CardVariant.NORMAL, condition="New", quantity=1)
        session.add(collectible_item)
        session.commit()

    with Session(engine) as session:
        assert session.get(InventoryItem, "item2").card_id is None


def test_migration_is_idempotent_when_already_nullable(tmp_path, monkeypatch):
    db_path = tmp_path / "already_done.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(engine)  # current models.py already has card_id nullable

    from scripts import migrate_relax_inventory_card_id
    monkeypatch.setattr(migrate_relax_inventory_card_id, "engine", engine)

    result = migrate_relax_inventory_card_id.main([])
    assert result == 0  # no-op, doesn't try to rebuild an already-correct table
