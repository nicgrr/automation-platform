from test_photo_identification_review import context
from sqlalchemy import select,func
from automation_control.models import ListingPhotoPair,InventoryItem,ShopListing
from automation_control.vinted_prep import rows_for_review,_status


def test_collectible_confirmation_goes_to_vinted_and_clears_scan_warning(context):
    s,c=context
    p=s.get(ListingPhotoPair,'pair')
    p.extracted={'item_kind':'collectible','character':'Figure','identity_confidence':'low'}
    s.commit()
    assert 'Save and open Vinted preparation' in c.get('/product-photo-review/pair').text
    data={'kind':'collectible','name':'Figure','brand':'Maker','quantity':1,'confirmed':'yes'}
    response=c.post('/product-photo-review/pair',data=data,follow_redirects=False)
    inv=s.scalar(select(InventoryItem))
    assert response.headers['location']=='/collectibles/vinted#i-'+inv.id
    again=c.post('/product-photo-review/pair',data=data,follow_redirects=False)
    assert again.headers['location']==response.headers['location']
    assert s.scalar(select(func.count()).select_from(InventoryItem))==1
    row=rows_for_review(s)[0]
    assert 'Confirm the exact identity / edition.' not in _status(row,{})
    assert 'Price needs your confirmation.' in _status(row,{})


def test_slab_confirmation_keeps_listing_studio(context):
    s,c=context
    p=s.get(ListingPhotoPair,'pair');p.back_image_path='/actual-back.jpg';s.commit()
    data={'kind':'graded','name':'Card','company':'CGC','grade':'10','certificate':'test-123','confirmed':'yes'}
    response=c.post('/product-photo-review/pair',data=data,follow_redirects=False)
    listing=s.scalar(select(ShopListing))
    assert response.headers['location']==f'/shop-listings/{listing.id}/ebay-preview'
    assert rows_for_review(s)==[]
