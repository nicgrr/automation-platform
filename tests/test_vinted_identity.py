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
                price='29.25',intent='details',copy_mode='generate',**changes)


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
    assert response.headers['location']=='/collectibles/vinted/i-inv/post'
    value=s.get(PlatformConfig,'vinted_draft:i-inv').value
    assert (value['brand'],value['series'],value['name'])==('POP MART','Create Your Taste','Milk Biscuit')
    assert value['confirmed_price']=='14.99'
    assert value['title']=='POP MART Milk Biscuit Create Your Taste Figure'
    assert 'Figure: Milk Biscuit' in value['description'] and 'Opened / used' in value['description']
    assert value['previous_copy']['description']=='Opened figure.'
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
    assert draft['title']=='POP MART Milk Biscuit Create Your Taste Figure'
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


def test_generation_uses_new_identity_and_preserves_explicit_contents(context):
    s,c=context
    data=_data();data.update(condition='Used — mark on base',included='Figure and original box; no card.',
                            condition_notes='Small scratch on the back.',copy_mode='generate')
    c.post('/collectibles/vinted/i-inv/save',data=data)
    data.update(brand='Sonny Angel',series='Animal Series 1',name='Duck')
    c.post('/collectibles/vinted/i-inv/save',data=data)
    value=s.get(PlatformConfig,'vinted_draft:i-inv').value
    assert 'Milk Biscuit' not in value['title']+value['description']
    for fact in ('Figure: Duck','Used — mark on base','Figure and original box; no card.','Small scratch on the back.'):
        assert fact in value['description']
    assert value['confirmed_price']=='14.99'
    # A later identity-only request preserves the entered condition/accessories.
    c.post('/collectibles/vinted/i-inv/save',data={'name':'Owl'})
    value=s.get(PlatformConfig,'vinted_draft:i-inv').value
    assert 'Figure: Owl' in value['description'] and 'original box' in value['description']


def test_custom_copy_can_be_kept_and_invalid_save_is_atomic(context):
    s,c=context
    data=_data();data['copy_mode']='custom'
    c.post('/collectibles/vinted/i-inv/save',data=data)
    value=s.get(PlatformConfig,'vinted_draft:i-inv').value
    assert value['title']=='My custom listing title' and value['description']=='My checked condition text.'
    data.update(title='',intent='price',price='25',condition_notes='x'*2001)
    assert c.post('/collectibles/vinted/i-inv/save',data=data).status_code==400
    assert s.get(PlatformConfig,'vinted_draft:i-inv').value==value


def test_custom_mode_survives_reload_and_price_confirmation(context):
    from urllib.parse import urlencode
    s,c=context
    data=_data();data['copy_mode']='custom'
    c.post('/collectibles/vinted/i-inv/save',data=data)
    body=c.get('/collectibles/vinted').text
    checkbox=body.split("type='checkbox' name='copy_mode'",1)[1].split('>',1)[0]
    assert 'checked' not in checkbox
    data.update(price='22',intent='price')
    submitted=list(data.items())
    c.post('/collectibles/vinted/i-inv/save',content=urlencode(submitted),
           headers={'content-type':'application/x-www-form-urlencoded'})
    value=s.get(PlatformConfig,'vinted_draft:i-inv').value
    assert value['title']=='My custom listing title' and value['description']=='My checked condition text.'
    assert value['confirmed_price']=='22.99' and value['copy_mode']=='custom'
    # Re-enabling generation posts hidden custom then checked generate, as browsers do.
    submitted.append(('copy_mode','generate'))
    c.post('/collectibles/vinted/i-inv/save',content=urlencode(submitted),
           headers={'content-type':'application/x-www-form-urlencoded'})
    assert s.get(PlatformConfig,'vinted_draft:i-inv').value['title']=='POP MART Milk Biscuit Create Your Taste Figure'


def test_legacy_condition_and_accessory_facts_stay_custom_until_rewrite(context):
    s,c=context
    config=s.get(PlatformConfig,'vinted_draft:i-inv')
    config.value={**config.value,'description':'Scratch on base. Includes box and collector card.'};s.commit()
    body=c.get('/collectibles/vinted').text
    checkbox=body.split("type='checkbox' name='copy_mode'",1)[1].split('>',1)[0]
    assert 'checked' not in checkbox
    response=c.post('/collectibles/vinted/i-inv/save',data={
        'title':'Sonny Angel','description':config.value['description'],'intent':'price','price':'20'})
    assert response.status_code==200
    value=s.get(PlatformConfig,'vinted_draft:i-inv').value
    assert value['description']=='Scratch on base. Includes box and collector card.'
    assert value['copy_mode']=='custom' and value['confirmed_price']=='20.99'


def test_staged_save_opens_copy_screen_with_stock_review_blocker(context):
    s,c=context
    s.add(ListingPhotoBatch(id='batch'));s.flush()
    s.add(ListingPhotoPair(id='pair',batch_id='batch',position=1,front_image_path='/front.jpg',
        status=ListingPhotoPairStatus.NEEDS_REVIEW,extracted={'item_kind':'collectible','character':'Owl'}));s.commit()
    response=c.post('/collectibles/vinted/p-pair/save',data=_data(),follow_redirects=False)
    assert response.headers['location']=='/collectibles/vinted/p-pair/post'
    body=c.get(response.headers['location']).text
    assert "data-copy='v-title'" in body and 'linked to stock' in body
    assert s.scalar(select(func.count()).select_from(InventoryItem))==1


def test_original_photo_download_and_invalid_index(context,tmp_path):
    s,c=context
    photo=tmp_path/'actual.jpg';photo.write_bytes(b'original-photo-bytes')
    s.get(InventoryItem,'inv').scan_image_path=str(photo);s.commit()
    response=c.get('/collectibles/vinted/i-inv/photo/0/download')
    assert response.status_code==200 and response.content==b'original-photo-bytes'
    assert 'attachment;' in response.headers['content-disposition']
    assert c.get('/collectibles/vinted/i-inv/photo/-1/download').status_code==404
    assert c.get('/collectibles/vinted/i-inv/photo/1/download').status_code==404
