import io
import zipfile
from sqlalchemy import select,func
from test_vinted_listings import context
from automation_control.models import (CatalogItem,CollectibleProduct,PlatformConfig,InventoryItem,
    ListingPhotoBatch,ListingPhotoPair,ListingPhotoPairStatus)
from automation_control.vinted_prep import _draft,_get_row


def _data(**changes):
    return dict(title='My custom listing title',description='My checked condition text.',
                brand='POP MART',series='Create Your Taste',name='Milk Biscuit',
                price='29.25',intent='details',**changes)


def test_known_identity_is_visible_in_multiple_forms(context):
    s,c=context
    s.add(ListingPhotoBatch(id='batch'));s.flush()
    s.add(ListingPhotoPair(id='pair',batch_id='batch',position=1,front_image_path='/another.jpg',
                          status=ListingPhotoPairStatus.NEEDS_REVIEW,
                          extracted={'item_kind':'collectible','character':'Mailbox','brand':'POP MART','series':'Almost Hidden'}));s.commit()
    body=c.get('/collectibles/vinted').text
    assert body.count("name='brand'")==2
    assert body.count("name='series'")==2
    assert body.count("name='name' data-character")==2
    assert "value='Sonny Angel'" in body and "value='Cat Life'" in body
    assert "value='POP MART'" in body and "value='Almost Hidden'" in body
    assert '/collectibles/reference/brands' in body
    assert 'Series / Set' in body and 'Save details' in body


def test_details_round_trip_keeps_price_stock_and_catalogue(context):
    s,c=context
    original=s.get(CatalogItem,'sonny').name
    response=c.post('/collectibles/vinted/i-inv/save',data=_data(),follow_redirects=False)
    assert response.status_code==303
    value=s.get(PlatformConfig,'vinted_draft:i-inv').value
    assert (value['brand'],value['series'],value['name'])==('POP MART','Create Your Taste','Milk Biscuit')
    assert value['confirmed_price']=='14.99'
    assert value['title']=='My custom listing title' and value['description']=='My checked condition text.'
    assert s.get(InventoryItem,'inv').quantity==1 and s.get(InventoryItem,'inv').scan_image_path=='/photo.jpg'
    assert s.get(CatalogItem,'sonny').name==original
    assert s.get(CollectibleProduct,'sonny').brand=='Sonny Angel'
    page=c.get('/collectibles/vinted').text
    assert "value='Milk Biscuit'" in page and "value='Create Your Taste'" in page
    posting=c.get('/collectibles/vinted/i-inv/post').text
    assert "id='v-brand' readonly value='POP MART'" in posting
    assert "id='v-series' readonly value='Create Your Taste'" in posting
    assert "id='v-name' readonly value='Milk Biscuit'" in posting
    archive=zipfile.ZipFile(io.BytesIO(c.get('/collectibles/vinted/download').content))
    draft=archive.read(next(n for n in archive.namelist() if n.endswith('/draft.txt'))).decode()
    assert 'BRAND: POP MART' in draft and 'SERIES / SET: Create Your Taste' in draft
    assert 'FIGURE NAME: Milk Biscuit' in draft and 'CONFIRMED PRICE (AUD): 14.99' in draft


def test_only_explicit_price_confirmation_uses_price(context):
    s,c=context
    config=s.get(PlatformConfig,'vinted_draft:i-inv')
    config.value={**config.value,'confirmed_price':None,'suggested_price':'19.99','hold_reason':'Check edition'};s.commit()
    data=_data();data['price']='19.99'
    assert c.post('/collectibles/vinted/i-inv/save',data=data).status_code==200
    value=s.get(PlatformConfig,'vinted_draft:i-inv').value
    assert value['confirmed_price'] is None and value['hold_reason']=='Check edition'
    data.update(intent='price',price='20.01')
    c.post('/collectibles/vinted/i-inv/save',data=data)
    assert s.get(PlatformConfig,'vinted_draft:i-inv').value['confirmed_price']=='20.99'


def test_staged_identity_prefills_onboarding_and_survives_stock_link(context):
    s,c=context
    s.add(ListingPhotoBatch(id='batch'));s.flush()
    s.add(ListingPhotoPair(id='pair',batch_id='batch',position=1,front_image_path='/front.jpg',
        status=ListingPhotoPairStatus.NEEDS_REVIEW,extracted={'item_kind':'collectible','character':'Unknown figure','identity_confidence':'low'}));s.commit()
    c.post('/collectibles/vinted/p-pair/save',data=_data())
    html=c.get('/product-photo-review/pair?kind=collectible').text
    assert "name='name' value='Milk Biscuit'" in html
    assert "name='brand' value='POP MART'" in html
    assert "name='series' value='Create Your Taste'" in html
    response=c.post('/product-photo-review/pair',data={'kind':'collectible','name':'Milk Biscuit','brand':'POP MART',
        'series':'Create Your Taste','quantity':1,'confirmed':'yes'},follow_redirects=False)
    inv=s.scalar(select(InventoryItem).where(InventoryItem.id!='inv'))
    assert response.headers['location']=='/collectibles/vinted#i-'+inv.id
    draft=_draft(s,_get_row(s,'i-'+inv.id))
    assert draft['name']=='Milk Biscuit' and draft['brand']=='POP MART' and draft['series']=='Create Your Taste'
    assert draft['title']=='My custom listing title'
    assert s.scalar(select(func.count()).select_from(InventoryItem))==2


def test_blank_unknown_fields_and_invalid_keys_or_lengths(context):
    s,c=context
    cp=s.get(CollectibleProduct,'sonny');cp.brand=None;cp.series=None;s.commit()
    body=c.get('/collectibles/vinted').text
    assert 'Brand needs identification' in body and 'Confirm the brand.' in body
    data=_data();data['brand']='x'*129
    assert c.post('/collectibles/vinted/i-inv/save',data=data).status_code==400
    assert c.post('/collectibles/vinted/i-nonexistent/save',data=_data()).status_code==404
    assert s.get(PlatformConfig,'vinted_draft:i-inv').value['confirmed_price']=='14.99'


def test_explicitly_clearing_identity_differs_from_omitting_fields(context):
    s,c=context
    data=_data();data.update(brand='',series='')
    assert c.post('/collectibles/vinted/i-inv/save',data=data,follow_redirects=False).status_code==303
    value=s.get(PlatformConfig,'vinted_draft:i-inv').value
    assert value['brand']=='' and value['series']==''
    data['name']=''
    assert c.post('/collectibles/vinted/i-inv/save',data=data).status_code==400
    # A request from an older page must preserve the saved identity.
    assert c.post('/collectibles/vinted/i-inv/save',data={'title':'Old page title','description':'Details'}).status_code==200
    value=s.get(PlatformConfig,'vinted_draft:i-inv').value
    assert value['name']=='Milk Biscuit' and value['confirmed_price']=='14.99'
