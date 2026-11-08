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
