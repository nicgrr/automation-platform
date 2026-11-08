from tests.test_stock_intake import db,apply,old_stock
from automation_control.stock_sources import balances
from automation_control.models import InventoryItem
from sqlalchemy import update

def test_sql_and_orm_reductions_keep_batch_separate(db):
    inventory,listing=old_stock(db,5)
    apply(db,'additional_copies',qty=2,target=listing.id);db.commit()
    lots=balances(db,inventory.id)
    assert [(x['source'],x['remaining']) for x in lots]==[('unassigned',5),('photo_batch',2)]
    db.execute(update(InventoryItem).where(InventoryItem.id==inventory.id).values(quantity=InventoryItem.quantity-1));db.commit()
    assert [x['remaining'] for x in balances(db,inventory.id)]==[5,1]
    inventory.quantity-=2;db.commit()
    assert [x['remaining'] for x in balances(db,inventory.id)]==[4,0]
    inventory.quantity+=1;db.commit()
    assert sum(x['remaining'] for x in balances(db,inventory.id))==5
    assert balances(db,inventory.id)[-1]['source']=='unassigned'

def test_rollback_and_duplicate_confirmation_leave_sources_unchanged(db):
    listing=apply(db,qty=2);db.commit()
    apply(db,qty=8);db.commit()
    inventory=db.get(InventoryItem,listing.inventory_item_id)
    assert len(balances(db,inventory.id))==1
    db.execute(update(InventoryItem).where(InventoryItem.id==inventory.id).values(quantity=0));db.rollback()
    assert balances(db,inventory.id)[0]['remaining']==2

def test_migration_seeds_existing_stock_and_captures_sql(tmp_path):
    import importlib.util
    from sqlalchemy import create_engine,text
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec=importlib.util.spec_from_file_location('source_migration','migrations/versions/20261109_01_inventory_sources.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    with engine.begin() as c:
        c.execute(text('CREATE TABLE inventory_items (id TEXT PRIMARY KEY, quantity INTEGER NOT NULL)'))
        c.execute(text("INSERT INTO inventory_items VALUES ('old',5)"))
        with Operations.context(MigrationContext.configure(c)):module.upgrade()
        c.execute(text("UPDATE inventory_items SET quantity=3 WHERE id='old'"))
        assert c.execute(text('SELECT delta,source FROM inventory_quantity_events ORDER BY id')).all()==[(5,'baseline'),(-2,'unassigned')]
        with Operations.context(MigrationContext.configure(c)):module.downgrade()
        assert c.execute(text('SELECT quantity FROM inventory_items')).scalar()==3
