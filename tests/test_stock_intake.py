from decimal import Decimal
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from automation_control.database import Base
from automation_control.models import (ListingPhotoBatch,ListingPhotoPair,ListingPhotoPairStatus,StockIntakeReceipt,
    InventoryItem,ShopListing,CatalogItem,CatalogItemType,TcgCard,CardVariant,ShopListingStatus)
from automation_control.stock_intake import confirm,guard_legacy

@pytest.fixture
def db(tmp_path):
    engine=create_engine(f'sqlite:///{tmp_path / "intake.db"}')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(CatalogItem(id='card',name='Pikachu',item_type=CatalogItemType.TCG_CARD))
        s.add(TcgCard(catalog_item_id='card',game='pokemon',number='25',language='English'))
        b=ListingPhotoBatch(intake_mode='new_stock',total_pairs=1);s.add(b);s.flush()
        s.add(ListingPhotoPair(id='pair',batch_id=b.id,position=1,front_image_path='front.jpg',status=ListingPhotoPairStatus.NEEDS_REVIEW,extracted={'character':'Pikachu'}));s.commit()
        yield s

def apply(s,mode='new_stock',qty=1,target=''):
    return confirm(s,s.get(ListingPhotoPair,'pair'),mode=mode,catalog_id='card',finish='normal',quantity=qty,listing_id=target,user='owner')

def old_stock(s,qty=5):
    i=InventoryItem(catalog_item_id='card',variant=CardVariant.NORMAL,condition='Near Mint',quantity=qty,scan_image_path='old-scan.jpg');s.add(i);s.flush()
    l=ShopListing(catalog_item_id='card',inventory_item_id=i.id,title='Reviewed title',ebay_title='Reviewed title',ebay_description='Reviewed description',price=Decimal('8.99'),quantity=1,status=ShopListingStatus.ACTIVE,finish='normal',ebay_photo_paths=['old-front.jpg']);s.add(l);s.commit();return i,l

def test_new_stock_receipt_is_replay_safe(db):
    l=apply(db,qty=2);db.commit()
    again=apply(db,qty=9);db.commit()
    assert again.id==l.id and l.quantity==2
    assert db.query(InventoryItem).one().quantity==2
    r=db.get(StockIntakeReceipt,'pair');assert (r.quantity_added,r.previous_quantity)==(2,0)
    assert db.query(ShopListing).count()==1

def test_photo_update_preserves_old_scanner_quantity_and_reviewed_price(db):
    i,l=old_stock(db)
    apply(db,'photo_update',qty=999,target=l.id);db.commit()
    assert i.quantity==5 and l.quantity==1 and l.price==Decimal('8.99')
    assert l.ebay_title=='Reviewed title' and i.scan_image_path=='old-scan.jpg'
    assert l.ebay_photo_paths[0]=='front.jpg'
    assert db.get(StockIntakeReceipt,'pair').quantity_added==0

def test_additional_copies_only_add_confirmed_batch_count(db):
    i,l=old_stock(db)
    apply(db,'additional_copies',qty=2,target=l.id);db.commit()
    apply(db,'additional_copies',qty=2,target=l.id);db.commit()
    assert i.quantity==7 and l.quantity==3
    assert l.price==Decimal('8.99') and l.ebay_photo_paths==['old-front.jpg']
    r=db.get(StockIntakeReceipt,'pair');assert (r.previous_quantity,r.quantity_added)==(5,2)

@pytest.mark.parametrize('mode,target', [('new_stock',False),('photo_update',False),('additional_copies',False)])
def test_conflicts_do_not_change_inventory(db,mode,target):
    i,l=old_stock(db)
    with pytest.raises(HTTPException):apply(db,mode,qty=3)
    db.rollback()
    assert i.quantity==5 and l.quantity==1 and db.query(StockIntakeReceipt).count()==0

def test_legacy_handlers_cannot_bypass_confirmation(db):
    pair=db.get(ListingPhotoPair,'pair')
    with pytest.raises(HTTPException):guard_legacy(db,pair)
    from automation_control.ebay_photo_batch import _apply_pair
    assert 'Scan Studio' in _apply_pair(db,pair,20)
    assert db.query(InventoryItem).count()==0

def test_wrong_finish_is_rejected(db):
    i,l=old_stock(db);l.finish='masterball_pattern';db.commit()
    with pytest.raises(HTTPException):apply(db,'photo_update',target=l.id)
    db.rollback();assert i.quantity==5


def test_additive_migration_preserves_existing_batches(tmp_path):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    spec=importlib.util.spec_from_file_location('intake_migration','migrations/versions/20261108_01_stock_intake.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    with engine.begin() as c:
        c.execute(text('CREATE TABLE listing_photo_batches (id TEXT PRIMARY KEY, total_pairs INTEGER)'))
        c.execute(text("INSERT INTO listing_photo_batches VALUES ('old', 39)"))
        with Operations.context(MigrationContext.configure(c)):module.upgrade()
        assert c.execute(text('SELECT total_pairs,intake_mode FROM listing_photo_batches')).one()==(39,'legacy')
        with Operations.context(MigrationContext.configure(c)):module.downgrade()
        assert c.execute(text('SELECT total_pairs FROM listing_photo_batches')).scalar()==39


def test_http_confirmation_double_submit_is_not_double_stock(db):
    from fastapi.testclient import TestClient
    from automation_control.api import app
    from automation_control.database import get_session
    from automation_control.auth import require_dashboard_user
    engine=db.get_bind()
    def request_session():
        with Session(engine) as s:yield s
    app.dependency_overrides[get_session]=request_session
    app.dependency_overrides[require_dashboard_user]=lambda:'owner'
    try:
        client=TestClient(app)
        data={'mode':'new_stock','catalog_id':'card','finish':'normal','quantity':'2','confirmed':'yes'}
        for _ in range(2):
            r=client.post('/scan-studio/cards/pair/confirm',data=data,follow_redirects=False)
            assert r.status_code==303 and r.headers['location']=='/scan-studio/cards/pair'
        with Session(engine) as check:
            assert check.query(InventoryItem).one().quantity==2
            assert check.query(StockIntakeReceipt).count()==1
        r=client.get('/scan-studio/cards/pair')
        assert '2 copies added' in r.text
    finally:app.dependency_overrides.clear()
