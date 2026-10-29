from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import Session
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base,get_session
from automation_control.models import (ListingPhotoBatch,ListingPhotoPair,ListingPhotoPairStatus,CatalogItem,CatalogItemType,TcgCard,CardSet,ShopListing,ShopListingStatus,InventoryItem)

@pytest.fixture
def context(tmp_path):
    engine=create_engine(f'sqlite:///{tmp_path}/review.db',connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(CardSet(id='set',name='Test set',game='pokemon'))
        s.add(CatalogItem(id='card',name='Pikachu',item_type=CatalogItemType.TCG_CARD,image_url='https://example.com/reference.jpg'))
        s.flush();s.add(TcgCard(catalog_item_id='card',game='pokemon',set_id='set',number='025',language='English',rarity='IR'))
        s.add(ListingPhotoBatch(id='batch',total_pairs=1));s.flush()
        s.add(ListingPhotoPair(id='pair',batch_id='batch',position=1,front_image_path='/front.jpg',status=ListingPhotoPairStatus.NEEDS_REVIEW,extracted={'character':'Pikachu'}));s.commit()
        app.dependency_overrides[get_session]=lambda:s
        app.dependency_overrides[require_dashboard_user]=lambda:'test'
        # No `with`: entering TestClient runs the app lifespan, which checks
        # the schema revision of the configured (production) database.
        yield s, TestClient(app)
        app.dependency_overrides.clear()

def test_catalogue_search_and_selection(context):
    s,c=context
    body=c.get('/photo-review/pair').text
    assert 'reference.jpg' in body and 'Your photo' in body and 'Choose this printing' in body
    body=c.get('/photo-review/pair?catalog=card').text
    assert 'Master Ball' in body and 'Save and open Listing Studio' in body

def test_update_retakes_preserve_stock_and_price(context):
    s,c=context
    s.add(ShopListing(id='existing',catalog_item_id='card',title='Pikachu',price=Decimal('19.99'),quantity=3,status=ShopListingStatus.ACTIVE));s.commit()
    r=c.post('/photo-review/pair/save',data={'catalog':'card','mode':'update','listing_id':'existing','finish':'pokeball_pattern','quantity':'99','confirmed':'yes'},follow_redirects=False)
    assert r.status_code==303 and r.headers['location'].endswith('/existing/ebay-preview')
    l=s.get(ShopListing,'existing');assert l.quantity==3 and l.price==Decimal('19.99') and l.finish=='pokeball_pattern'
    assert s.scalar(select(func.count()).select_from(InventoryItem))==0
    assert l.ebay_photo_paths[0]=='/front.jpg'

def test_duplicate_and_repeat_submissions_do_not_create_stock(context):
    s,c=context
    data={'catalog':'card','mode':'new','finish':'holo','quantity':'2','confirmed':'yes'}
    r=c.post('/photo-review/pair/save',data=data,follow_redirects=False);assert r.status_code==303
    assert c.post('/photo-review/pair/save',data=data,follow_redirects=False).headers['location']==r.headers['location']
    assert s.scalar(select(func.count()).select_from(ShopListing))==1
    assert s.scalar(select(InventoryItem.quantity))==2
    s.add(ListingPhotoPair(id='retake',batch_id='batch',position=2,front_image_path='/retake.jpg',status=ListingPhotoPairStatus.NEEDS_REVIEW));s.commit()
    assert c.post('/photo-review/retake/save',data=data).status_code==409
    assert s.scalar(select(InventoryItem.quantity))==2

def test_local_catalogue_entry_is_idempotent_and_creates_no_stock(context):
    s,c=context
    data={'game':'pokemon','name':'New card','set_name':'New set','number':'101/100','language':'English','rarity':'IR','confirmed':'yes'}
    a=c.post('/photo-review/pair/catalogue',data=data,follow_redirects=False)
    b=c.post('/photo-review/pair/catalogue',data=data,follow_redirects=False)
    assert a.status_code==303 and a.headers['location']==b.headers['location']
    assert s.scalar(select(func.count()).select_from(InventoryItem))==0
    assert s.scalar(select(func.count()).select_from(ShopListing))==0

def test_cannot_update_a_different_catalogue_record(context):
    s,c=context
    s.add(CatalogItem(id='other',name='Other card',item_type=CatalogItemType.TCG_CARD));s.flush()
    s.add(ShopListing(id='wrong',catalog_item_id='other',title='Other',price=Decimal('1'),quantity=1,status=ShopListingStatus.ACTIVE));s.commit()
    assert c.post('/photo-review/pair/save',data={'catalog':'card','mode':'update','listing_id':'wrong','confirmed':'yes'}).status_code==400
    assert s.get(ShopListing,'wrong').ebay_photo_paths is None
