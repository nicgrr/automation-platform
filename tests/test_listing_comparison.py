from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from automation_control.database import Base
from automation_control.models import CatalogItem,CatalogItemType,ShopListing,EbayListing,EbayListingLink
from automation_control.listing_comparison import comparison_html

@pytest.fixture
def data(tmp_path):
    engine=create_engine(f'sqlite:///{tmp_path / "compare.db"}')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(CatalogItem(id='card',name='Card',item_type=CatalogItemType.TCG_CARD))
        l=ShopListing(id='local',catalog_item_id='card',title='Card',price=Decimal('8.99'),quantity=2)
        s.add(l);s.commit();yield s,l

def test_missing_link_is_not_claimed_unpublished(data):
    s,l=data
    assert 'does not prove' in comparison_html(s,l)
    s.add(EbayListingLink(shop_listing_id=l.id,sku=l.id,marketplace_id='EBAY_AU',ebay_listing_id='123'));s.commit()
    assert 'no cached eBay observation' in comparison_html(s,l)

def test_observation_differences_staleness_and_escaping(data):
    s,l=data
    s.add(EbayListingLink(shop_listing_id=l.id,sku=l.id,marketplace_id='EBAY_AU',ebay_listing_id='123'))
    r=EbayListing(ebay_listing_id='123',title='<script>bad</script>',currency='AUD',current_price_minor=599,
        quantity=None,listing_status='Active',observed_at=datetime.now(timezone.utc)-timedelta(days=2))
    s.add(r);s.commit()
    html=comparison_html(s,l,'Reviewed title')
    assert 'Different' in html and 'Unverified' in html and 'Older than 24 hours' in html
    assert '&lt;script&gt;' in html and '<script>bad' not in html
    assert 'Photos and description are not verified' in html
    r.current_price_minor=899;r.quantity=2;s.commit()
    assert comparison_html(s,l).count('Matches cached value')==2
    assert l.price==Decimal('8.99') and l.quantity==2

def test_confirmed_export_fallback_and_explicit_link_precedence(data):
    from automation_control.models import EbayExportBatch,EbayExportItem,EbayExportItemStatus
    s,l=data
    batch=EbayExportBatch(filename='test.csv',row_count=1,selected_count=1);s.add(batch);s.flush()
    export=EbayExportItem(batch_id=batch.id,sku=l.id,kind='single',status=EbayExportItemStatus.CONFIRMED,ebay_listing_id='111')
    s.add(export);s.commit()
    assert '/itm/111' in comparison_html(s,l)
    s.add(EbayListingLink(shop_listing_id=l.id,sku=l.id,marketplace_id='EBAY_AU',ebay_listing_id='222'));s.commit()
    html=comparison_html(s,l)
    assert '/itm/222' in html and '/itm/111' not in html

def test_fresh_readback_updates_only_cache(data,monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    from automation_control import channel_verification as cv
    from automation_control import ebay_oauth
    from automation_control.adapters import ebay_trading
    s,l=data
    s.add(EbayListingLink(shop_listing_id=l.id,sku=l.id,marketplace_id='EBAY_AU',ebay_listing_id='123'));s.commit()
    details=ebay_trading.ItemDetails(['https://i.ebayimg.com/front.jpg'],'description',title='Card',price=Decimal('5.99'),currency='AUD',quantity=1,status='Active')
    adapter=SimpleNamespace(get_item_details=AsyncMock(return_value=details),client=SimpleNamespace(aclose=AsyncMock()))
    monkeypatch.setattr(ebay_oauth,'ebay_services',lambda settings:(None,None))
    monkeypatch.setattr(ebay_oauth,'valid_user_access_token',AsyncMock(return_value='test'))
    monkeypatch.setattr(ebay_trading,'EbayTradingReadAdapter',lambda *a,**kw:adapter)
    settings=SimpleNamespace(ebay_client_id='test',ebay_env='sandbox',ebay_marketplace_id='EBAY_AU')
    row=asyncio.run(cv.refresh_one(s,l,settings))
    assert row.current_price_minor==599 and row.quantity==1
    assert l.price==Decimal('8.99') and l.quantity==2
    assert row.raw_snapshot['image_urls']==details.picture_urls
    assert 'Compare saved photos' in comparison_html(s,l)

def test_photo_review_fingerprint_changes_with_refresh_or_saved_content(data,tmp_path):
    from automation_control.channel_verification import photo_evidence
    s,l=data
    photo=tmp_path/'card.jpg';photo.write_bytes(b'first actual photo')
    l.ebay_photo_paths=[str(photo)];s.commit()
    remote=EbayListing(ebay_listing_id='123',title='Card',currency='AUD',current_price_minor=899,quantity=2,listing_status='Active',raw_snapshot={'image_urls':['https://i.ebayimg.com/card.jpg'],'details_imported_at':'first'})
    a=photo_evidence(s,l,remote)[2]
    photo.write_bytes(b'replacement actual photo')
    b=photo_evidence(s,l,remote)[2]
    remote.raw_snapshot={**remote.raw_snapshot,'details_imported_at':'second'}
    c=photo_evidence(s,l,remote)[2]
    assert len({a,b,c})==3
