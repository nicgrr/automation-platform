from decimal import Decimal
from unittest.mock import AsyncMock
import pytest
from test_ebay_publish import session, client, make_listing, _configured_settings
from automation_control import ebay_publish, sleeve_studio as lot
from automation_control.models import CatalogItem, CatalogItemType, TcgCard, InventoryItem, ShopListingStatus, AccessoryProduct


def make_sleeves(session):
    listing = make_listing(session, status=ShopListingStatus.DRAFT)
    session.delete(session.get(TcgCard, listing.catalog_item_id))
    session.get(CatalogItem, listing.catalog_item_id).item_type = CatalogItemType.ACCESSORY
    session.add(AccessoryProduct(catalog_item_id=listing.catalog_item_id, brand='Pokemon', product_type='Card sleeves', pack_size='65 sleeves'))
    stock = InventoryItem(catalog_item_id=listing.catalog_item_id, quantity=1, condition='New')
    session.add(stock);session.flush()
    listing.inventory_item_id = stock.id
    listing.condition = 'New'
    listing.price = Decimal('4.99')
    listing.title = 'Pokemon Perfect Order Zygarde Card Sleeves 65 Pack Sealed'
    listing.description = 'Sealed pack of 65 Pokemon Perfect Order Zygarde card sleeves.'
    lot.save_details(session, listing, {'catalog_item_id': listing.catalog_item_id, 'reviewed': True,
        'game': 'pokemon', 'language': 'English', 'complete': True,
        'set_name': 'Perfect Order', 'character': 'Zygarde'})
    session.commit()
    return listing


def test_sleeves_preview_and_save_preserve_reviewed_identity(client, session, monkeypatch):
    listing = make_sleeves(session)
    monkeypatch.setattr(ebay_publish, 'get_settings', _configured_settings)
    check = ebay_publish.publication_preflight(session, listing, _configured_settings())
    assert check.ready and check.category_id == '183461'
    body = client.get(f'/shop-listings/{listing.id}/ebay-preview').text
    assert 'Sleeve pack details' in body and 'Zygarde' in body
    assert 'Unopened and complete' not in body
    response = client.post(f'/shop-listings/{listing.id}/ebay-studio', data={
        'ebay_title': listing.title, 'ebay_description': listing.description, 'price': '4.99',
        'quantity': '1', 'condition': 'New', 'sealed_language': 'English', 'sealed_complete': 'yes',
        'sealed_weight_g': '310', 'sealed_length_cm': '20', 'sealed_width_cm': '3', 'sealed_height_cm': '15'},
        follow_redirects=False)
    assert response.status_code == 303 and 'error=' not in response.headers['location']
    data = lot.details(session, listing)
    assert data['character'] == 'Zygarde' and data['reviewed'] is True
    assert data['set_name'] == 'Perfect Order'
    assert listing.status == ShopListingStatus.DRAFT


@pytest.mark.parametrize('change', [{'reviewed':False}, {'catalog_item_id':'other'}, {'character':''}, {'complete':False}])
def test_unreviewed_or_incomplete_lots_blocked(session, change):
    listing = make_sleeves(session)
    lot.save_details(session, listing, change);session.commit()
    assert not ebay_publish.publication_preflight(session, listing, _configured_settings()).ready


@pytest.mark.asyncio
async def test_publish_sleeves_uses_accessory_category_new_condition_and_letter(session, monkeypatch):
    listing = make_sleeves(session)
    monkeypatch.setattr(ebay_publish, 'get_settings', _configured_settings)
    monkeypatch.setattr(ebay_publish, 'ebay_services', lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, 'valid_user_access_token', AsyncMock(return_value='tok'))
    adapter = AsyncMock();adapter.get_offers.return_value=[]
    adapter.create_offer.return_value='lot-offer';adapter.publish_offer.return_value='lot-live'
    monkeypatch.setattr(ebay_publish, 'EbayWriteAdapter', lambda *a, **kw: adapter)
    result = await ebay_publish.publish_to_ebay(listing.id, confirmation='publish', pricing_override='', user='test', session=session)
    assert result.status_code == 303
    payload = adapter.upsert_inventory_item.await_args.args[1]
    assert payload['condition'] == 'NEW' and 'conditionDescriptors' not in payload
    assert payload['product']['aspects']['Number of Sleeves'] == ['65']
    assert payload['availability']['shipToLocationAvailability']['quantity'] == 1
    assert 'packageWeightAndSize' not in payload
    offer = adapter.create_offer.await_args.args[0]
    assert offer['categoryId'] == '183461'
    assert offer['listingPolicies']['fulfillmentPolicyId'] == _configured_settings().ebay_fulfillment_policy_id
