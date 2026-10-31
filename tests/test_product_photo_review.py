from test_photo_identification_review import context
from sqlalchemy import select,func
from automation_control.models import InventoryItem,ShopListing,ListingPhotoPair,ListingPhotoPairStatus


def test_collectible_retake_and_repeat_keep_stock(context):
    s,c=context
    data={'kind':'collectible','name':'Test figure','brand':'Maker','quantity':2,'confirmed':'yes'}
    r=c.post('/product-photo-review/pair',data=data,follow_redirects=False)
    assert r.status_code==303
    assert c.post('/product-photo-review/pair',data=data,follow_redirects=False).headers['location']==r.headers['location']
    i=s.scalar(select(InventoryItem));l=s.scalar(select(ShopListing));l.price=25;s.commit()
    s.add(ListingPhotoPair(id='retake',batch_id='batch',position=2,front_image_path='/new-front.jpg',back_image_path='/new-back.jpg',status=ListingPhotoPairStatus.NEEDS_REVIEW));s.commit()
    data.update(existing=i.catalog_item_id,quantity=99)
    assert c.post('/product-photo-review/retake',data=data,follow_redirects=False).status_code==303
    assert s.scalar(select(func.count()).select_from(InventoryItem))==1
    assert i.quantity==2 and l.quantity==2 and l.price==25
    assert l.ebay_photo_paths==['/new-front.jpg','/new-back.jpg']


def test_slab_requires_label_and_reuses_certificate(context):
    s,c=context
    p=s.get(ListingPhotoPair,'pair');p.back_image_path='/back.jpg';p.extracted={'graded':'CGC 10'};s.commit()
    data={'kind':'graded','name':'Pikachu 025 English','company':'CGC','grade':'10','certificate':'123456','confirmed':'yes','quantity':8}
    assert c.post('/product-photo-review/pair',data={**data,'certificate':''},follow_redirects=False).status_code==400
    assert c.post('/product-photo-review/pair',data=data,follow_redirects=False).status_code==303
    i=s.scalar(select(InventoryItem));assert i.quantity==1 and i.certification_number=='123456'
    s.add(ListingPhotoPair(id='retake',batch_id='batch',position=2,front_image_path='/retake.jpg',back_image_path='/back2.jpg',status=ListingPhotoPairStatus.NEEDS_REVIEW));s.commit()
    assert c.post('/product-photo-review/retake',data=data,follow_redirects=False).status_code==303
    assert s.scalar(select(func.count()).select_from(InventoryItem))==1
    assert s.scalar(select(func.count()).select_from(ShopListing))==1


def test_slab_cannot_merge_raw_and_needs_actual_back(context):
    s,c=context
    p=s.get(ListingPhotoPair,'pair');p.extracted={'graded':'Vault 9'};s.commit()
    data={'catalog':'card','mode':'new','finish':'holo','quantity':1,'confirmed':'yes'}
    assert c.post('/photo-review/pair/save',data=data).status_code==409
    from automation_control.ebay_photo_batch import _apply_pair
    p.status=ListingPhotoPairStatus.READY
    assert 'Graded' in _apply_pair(s,p)
    assert c.post('/product-photo-review/pair',data={'kind':'graded','name':'Card','company':'CGC','grade':'9','certificate':'123','confirmed':'yes'}).status_code==400


def test_mixed_review_page_and_hub(context):
    s,c=context
    assert 'Certificate number' in c.get('/product-photo-review/pair?kind=graded').text
    body=c.get('/scan-add').text
    assert '/ebay-listing-bulk-photos' in body
    assert 'Cards — bulk only' not in body


def test_collectibles_show_figure_fields_and_redirect_card_form(context):
    s,c=context
    p=s.get(ListingPhotoPair,'pair');p.extracted={'item_kind':'collectible','character':'Duck','brand':'Sonny Angel','series':'Animal Series'};s.commit()
    r=c.get('/photo-review/pair',follow_redirects=False)
    assert r.status_code==303 and r.headers['location']=='/product-photo-review/pair'
    body=c.get(r.headers['location']).text
    assert 'Character / figure name' in body and 'Variant / colourway' in body
    assert "name='certificate'" not in body and "name='grade'" not in body
    assert 'Card name and number' not in body
    from automation_control.ebay_photo_batch import _pair_card
    tile=_pair_card(p,s)
    assert 'Review collectible details' in tile and 'Animal Series' in tile
    assert 'Printed number' not in tile and 'Rarity' not in tile and 'Identify / match catalogue' not in tile


def test_graded_card_review_retains_slab_fields(context):
    s,c=context
    p=s.get(ListingPhotoPair,'pair');p.extracted={'graded':'CGC 10'};s.commit()
    body=c.get('/photo-review/pair').text
    assert 'Certificate number' in body and 'Grading company' in body and 'Card name and number' in body
