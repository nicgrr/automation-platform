from decimal import Decimal
from unittest.mock import AsyncMock
import pytest
from test_ebay_publish import session, client, make_listing, _configured_settings
from automation_control import ebay_publish, card_lot_studio as lot
from automation_control.models import CatalogItem, CatalogItemType, TcgCard, InventoryItem, ShopListingStatus


def make_lot(session):
    listing = make_listing(session, status=ShopListingStatus.DRAFT)
    session.delete(session.get(TcgCard, listing.catalog_item_id))
    session.get(CatalogItem, listing.catalog_item_id).item_type = CatalogItemType.OTHER
    stock = InventoryItem(catalog_item_id=listing.catalog_item_id, quantity=1, condition='Near Mint')
    session.add(stock);session.flush()
    listing.inventory_item_id = stock.id
    listing.condition = 'Near Mint'
    listing.price = Decimal('44.99')
    listing.title = 'Pokemon Fuecoco Crocalor Skeledirge ex Promo Card Lot with Holder'
    listing.description = 'Three removable English promo cards with the pictured holder. Near Mint.'
    lot.save_details(session, listing, {'catalog_item_id': listing.catalog_item_id, 'reviewed': True,
        'game': 'pokemon', 'language': 'English', 'complete': True, 'holder_included': True,
        'cards': [{'name': 'Fuecoco', 'number': '079'}, {'name': 'Crocalor', 'number': '080'},
                  {'name': 'Skeledirge ex', 'number': '081'}],
        'weight_g': '300', 'length_cm': '20', 'width_cm': '3', 'height_cm': '15'})
    session.commit()
    return listing


def test_lot_preview_and_save_preserve_reviewed_identity(client, session, monkeypatch):
    listing = make_lot(session)
    monkeypatch.setattr(ebay_publish, 'get_settings', _configured_settings)
    check = ebay_publish.publication_preflight(session, listing, _configured_settings())
    assert check.ready and check.category_id == '183455'
    body = client.get(f'/shop-listings/{listing.id}/ebay-preview').text
    assert 'Card lot details' in body and 'Fuecoco' in body
    assert 'Unopened and complete' not in body
    response = client.post(f'/shop-listings/{listing.id}/ebay-studio', data={
        'ebay_title': listing.title, 'ebay_description': listing.description, 'price': '44.99',
        'quantity': '1', 'condition': 'Near Mint', 'sealed_language': 'English', 'sealed_complete': 'yes',
        'sealed_weight_g': '310', 'sealed_length_cm': '20', 'sealed_width_cm': '3', 'sealed_height_cm': '15'},
        follow_redirects=False)
    assert response.status_code == 303 and 'error=' not in response.headers['location']
    data = lot.details(session, listing)
    assert len(data['cards']) == 3 and data['reviewed'] is True and data['weight_g'] == '310'
    assert listing.status == ShopListingStatus.DRAFT


@pytest.mark.parametrize('change', [{'reviewed':False}, {'catalog_item_id':'other'}, {'cards':[]}, {'complete':False}, {'weight_g':''}])
def test_unreviewed_or_incomplete_lots_blocked(session, change):
    listing = make_lot(session)
    lot.save_details(session, listing, change);session.commit()
    assert not ebay_publish.publication_preflight(session, listing, _configured_settings()).ready


@pytest.mark.asyncio
async def test_publish_lot_uses_lot_category_used_condition_and_parcel(session, monkeypatch):
    listing = make_lot(session)
    monkeypatch.setattr(ebay_publish, 'get_settings', _configured_settings)
    monkeypatch.setattr(ebay_publish, 'ebay_services', lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, 'valid_user_access_token', AsyncMock(return_value='tok'))
    adapter = AsyncMock();adapter.get_offers.return_value=[]
    adapter.create_offer.return_value='lot-offer';adapter.publish_offer.return_value='lot-live'
    monkeypatch.setattr(ebay_publish, 'EbayWriteAdapter', lambda *a, **kw: adapter)
    result = await ebay_publish.publish_to_ebay(listing.id, confirmation='publish', pricing_override='', user='test', session=session)
    assert result.status_code == 303
    payload = adapter.upsert_inventory_item.await_args.args[1]
    assert payload['condition'] == 'USED_EXCELLENT' and 'conditionDescriptors' not in payload
    assert payload['product']['aspects']['Number of Cards'] == ['3']
    assert payload['availability']['shipToLocationAvailability']['quantity'] == 1
    assert payload['packageWeightAndSize']['packageType'] == 'MAILING_BOX'
    offer = adapter.create_offer.await_args.args[0]
    assert offer['categoryId'] == '183455'
    assert offer['listingPolicies']['fulfillmentPolicyId'] == _configured_settings().ebay_sealed_fulfillment_policy_id
