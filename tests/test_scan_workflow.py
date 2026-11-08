"""Real intake/stock/approval/publish/readback flow against isolated DB and fake eBay."""
from io import BytesIO
from decimal import Decimal
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace
import pytest
from PIL import Image
from sqlalchemy import select
from tests.test_scan_studio import workspace
from tests.test_ebay_publish import _configured_settings,add_category_mapping
from automation_control.models import *
from automation_control.stock_intake import confirm
from automation_control.listing_price_approval import approve,fingerprint
from automation_control.stock_sources import balances

@pytest.mark.asyncio
async def test_capture_to_storefront_without_real_marketplace_write(workspace,monkeypatch):
    from automation_control import ebay_publish as publish,ebay_oauth
    from automation_control.channel_verification import refresh_one
    from automation_control.adapters import ebay_trading
    from storefront import live_inventory
    client,s=workspace
    picture=BytesIO();Image.new('RGB',(800,1100),'blue').save(picture,'JPEG')
    with patch('automation_control.ebay_photo_batch._run_batch'):
        response=client.post('/ebay-listing-bulk-photos',headers={'Accept':'application/json'},data={
            'intake_mode':'new_stock','game_hint':'pokemon','has_backs':'no','storage_tray':'TEST-TRAY'},
            files={'photos':('card.jpg',picture.getvalue(),'image/jpeg')})
    assert response.status_code==201
    batch_id=response.json()['batch_id']
    pair=s.scalar(select(ListingPhotoPair).where(ListingPhotoPair.batch_id==batch_id))
    # Recognition output is deliberately deterministic; identity confirmation is real.
    s.add(CardSet(id='sv1',name='Scarlet & Violet'))
    s.add(CatalogItem(id='test-printing',name='Test Card',item_type=CatalogItemType.TCG_CARD))
    s.add(TcgCard(catalog_item_id='test-printing',game='pokemon',set_id='sv1',number='1',language='English'))
    pair.status=ListingPhotoPairStatus.NEEDS_REVIEW;pair.extracted={'character':'Test Card'};s.commit()
    listing=confirm(s,pair,mode='new_stock',catalog_id='test-printing',finish='normal',quantity=2,listing_id='',user='owner');s.commit()
    confirm(s,pair,mode='new_stock',catalog_id='test-printing',finish='normal',quantity=2,listing_id='',user='owner');s.commit()
    assert listing.quantity==2 and balances(s,listing.inventory_item_id)[0]['remaining']==2
    from automation_control.ebay_photo_batch import confirm_batch_storage
    result=confirm_batch_storage(batch_id,confirmation='stored',user='owner',session=s)
    assert result.status_code==303
    assert s.scalar(select(InventoryPlacement).where(InventoryPlacement.listing_photo_pair_id==pair.id)).confirmed_at
    approve(s,listing,'8.99','single','owner',fingerprint(s,listing));s.commit()
    add_category_mapping(s)
    from datetime import datetime,timezone,timedelta
    from automation_control.ebay_oauth import INVENTORY_WRITE_SCOPE
    s.add(EbayCredential(id=1,environment='sandbox',encrypted_access_token=b'test',encrypted_refresh_token=b'test',access_token_expires_at=datetime.now(timezone.utc)+timedelta(hours=1),scopes=INVENTORY_WRITE_SCOPE));s.commit()
    settings=_configured_settings().model_copy(update={'r2_endpoint_url':'https://example.test','r2_access_key_id':'test','r2_secret_access_key':'test','r2_bucket_name':'test'})
    monkeypatch.setattr(publish,'get_settings',lambda:settings)
    monkeypatch.setattr(publish,'ebay_services',lambda settings:(None,None))
    monkeypatch.setattr(publish,'valid_user_access_token',AsyncMock(return_value='test'))
    monkeypatch.setattr(publish,'_prepare_images',lambda *args:['https://example.test/card.jpg'])
    adapter=AsyncMock();adapter.get_offers.return_value=[];adapter.create_offer.return_value='offer';adapter.publish_offer.return_value='123456'
    monkeypatch.setattr(publish,'EbayWriteAdapter',lambda *args,**kw:adapter)
    result=await publish.publish_to_ebay(listing.id,confirmation='publish',user='owner',session=s)
    assert result.status_code==303 and adapter.publish_offer.await_count==1
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        await publish.publish_to_ebay(listing.id,confirmation='publish',user='owner',session=s)
    assert adapter.publish_offer.await_count==1
    details=ebay_trading.ItemDetails(['https://example.test/card.jpg'],'actual photo',title=listing.ebay_title,price=Decimal('8.99'),currency='AUD',quantity=2,status='Active')
    reader=SimpleNamespace(get_item_details=AsyncMock(return_value=details),client=SimpleNamespace(aclose=AsyncMock()))
    monkeypatch.setattr(ebay_oauth,'ebay_services',lambda settings:(None,None));monkeypatch.setattr(ebay_oauth,'valid_user_access_token',AsyncMock(return_value='test'))
    monkeypatch.setattr(ebay_trading,'EbayTradingReadAdapter',lambda *a,**kw:reader)
    await refresh_one(s,listing,_configured_settings())
    monkeypatch.setattr(live_inventory,'enabled',lambda:True)
    shown=live_inventory.project(s,listing)
    assert shown.price==Decimal('8.99') and shown.quantity==2
    assert shown.image_url=='https://example.test/card.jpg'
