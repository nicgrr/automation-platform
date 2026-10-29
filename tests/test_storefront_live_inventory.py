from decimal import Decimal
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from automation_control.database import Base
from automation_control.models import CatalogItem,CatalogItemType,ShopListing,ShopListingStatus,EbayListing,EbayListingLink,EbayListingLinkStatus
from storefront import live_inventory as live

@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(live,'enabled',lambda:True)
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        item=CatalogItem(id='card',name='Card',item_type=CatalogItemType.TCG_CARD)
        s.add(item);s.flush()
        listing=ShopListing(id='single',catalog_item_id='card',title='Saved draft title',price=Decimal('2.92'),quantity=4,status=ShopListingStatus.DRAFT)
        remote=EbayListing(id='remote',ebay_listing_id='123',title='Published title',current_price_minor=599,currency='AUD',quantity=1,listing_status='Active',raw_snapshot={})
        s.add_all([listing,remote]);s.flush()
        s.add(EbayListingLink(shop_listing_id='single',sku='single',marketplace_id='EBAY_AU',status=EbayListingLinkStatus.ACTIVE,ebay_listing_id='123'))
        s.commit()
        yield s

def test_live_draft_shows_published_values_without_overwriting_review(db):
    original=db.get(ShopListing,'single')
    shown=live.project(db,original)
    assert (shown.price,shown.quantity,shown.title)==(Decimal('5.99'),1,'Published title')
    assert db.scalar(select(ShopListing.id).where(live.available()))=='single'
    db.commit();db.refresh(original)
    assert original.price==Decimal('2.92') and original.quantity==4 and original.status==ShopListingStatus.DRAFT

@pytest.mark.parametrize('status,quantity',[('Ended',1),('Active',0)])
def test_ended_or_sold_out_does_not_show(db,status,quantity):
    r=db.get(EbayListing,'remote');r.listing_status=status;r.quantity=quantity;db.flush()
    assert live.project(db,db.get(ShopListing,'single')) is None
    assert db.scalar(select(ShopListing.id).where(live.available())) is None

def test_unpublished_active_local_listing_is_hidden(db):
    l=db.get(ShopListing,'single');l.status=ShopListingStatus.ACTIVE
    db.delete(db.query(EbayListingLink).one());db.flush()
    assert live.project(db,l) is None
    assert len(live.unlinked_rows(db))==1


def test_catalogue_gallery_uses_published_photos_not_pending_edit(db):
    from storefront.common import _art
    from storefront.app import _detail_art
    listing = db.get(ShopListing, 'single')
    listing.ebay_photo_paths = ['https://example.com/unpublished.jpg']
    remote = db.get(EbayListing, 'remote')
    remote.raw_snapshot = {'image_urls': ['https://example.com/live.jpg', 'https://example.com/live-back.jpg']}
    db.flush()
    shown = live.project(db, listing)
    assert shown.image_url == 'https://example.com/live.jpg'
    tile = _art(None, shown, db)
    detail = _detail_art(None, shown, db)
    assert 'live.jpg' in tile and 'unpublished' not in tile
    assert 'live-back.jpg' in detail and 'unpublished' not in detail
