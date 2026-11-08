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
