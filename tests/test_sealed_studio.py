from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from test_ebay_publish import session, client, make_listing, _configured_settings
from automation_control import ebay_publish, sealed_studio
from automation_control.models import (CatalogItem, CatalogItemType, InventoryItem, SealedProduct,
                                       ShopListingStatus, EbayListingLink, EbayListingLinkStatus)


def deck(session, *, packed=True):
    listing = make_listing(session, status=ShopListingStatus.DRAFT)
    session.get(CatalogItem, listing.catalog_item_id).item_type = CatalogItemType.SEALED_PRODUCT
    session.add(SealedProduct(catalog_item_id=listing.catalog_item_id, brand='Bandai',
                             game='One Piece', set_id='ST-30', product_type='Starter Deck EX'))
    stock = InventoryItem(catalog_item_id=listing.catalog_item_id, condition='New', quantity=1)
    session.add(stock)
    session.flush()
    listing.inventory_item_id = stock.id
    listing.condition = 'New'
    listing.title = 'One Piece ST-30 Luffy Ace Starter Deck EX English Sealed'
    listing.description = 'One unopened starter deck with all original contents. Actual product pictured.'
    listing.price = Decimal('89.99')
    sealed_studio.save_details(session, listing, sealed_studio.reviewed_data(
        'English', True, '350' if packed else '', '20' if packed else '',
        '15' if packed else '', '8' if packed else ''))
    session.commit()
    return listing


def test_sealed_preview_and_save(client, session, monkeypatch):
    listing = deck(session, packed=False)
    monkeypatch.setattr(ebay_publish, 'get_settings', _configured_settings)
    body = client.get(f'/shop-listings/{listing.id}/ebay-preview').text
    assert 'Sealed product details' in body and 'Packed weight (g)' in body
    assert 'TCG card details are missing' not in body
    assert "name='finish'" not in body
    check = ebay_publish.publication_preflight(session, listing, _configured_settings())
    assert check.category_id == '183457'
    assert [c.label for c in check.quality.blockers] == ['Parcel details']
    resp = client.post(f'/shop-listings/{listing.id}/ebay-studio', data={
        'ebay_title': listing.title, 'ebay_description': listing.description,
        'price': '89.99', 'quantity': '1', 'condition': 'New', 'sealed_language': 'English',
        'sealed_complete': 'yes', 'sealed_weight_g': '350', 'sealed_length_cm': '20',
        'sealed_width_cm': '15', 'sealed_height_cm': '8'}, follow_redirects=False)
    assert resp.status_code == 303 and 'error=' not in resp.headers['location']
    session.refresh(listing)
    assert listing.status == ShopListingStatus.DRAFT
    assert listing.price == Decimal('89.99')
    assert ebay_publish.publication_preflight(session, listing, _configured_settings()).ready
    assert sealed_studio.details(session, listing)['weight_g'] == '350'


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', '0', 'bad'])
def test_bad_parcel_measurements(value):
    with pytest.raises(ValueError):
        sealed_studio.reviewed_data('English', True, value, '20', '15', '8')


def test_wrong_product_type_and_stock_blocked(session):
    listing = deck(session)
    session.get(SealedProduct, listing.catalog_item_id).product_type = 'Booster Box'
    session.get(InventoryItem, listing.inventory_item_id).quantity = 0
    session.flush()
    check = ebay_publish.publication_preflight(session, listing, _configured_settings())
    assert not check.ready
    assert any('inventory' in e for e in check.errors)
    assert any('Product identity' in e for e in check.errors)


def test_legacy_revision_blocked(session):
    listing = deck(session)
    session.add(EbayListingLink(shop_listing_id=listing.id, sku=listing.id, marketplace_id='EBAY_AU',
                               ebay_listing_id='old', status=EbayListingLinkStatus.ACTIVE))
    session.commit()
    check = ebay_publish.publication_preflight(session, listing, _configured_settings(), allow_active_revision=True)
    assert any('legacy' in e for e in check.errors)


@pytest.mark.asyncio
async def test_publish_requires_confirmation_and_uses_sealed_payload(session, monkeypatch):
    listing = deck(session)
    monkeypatch.setattr(ebay_publish, 'get_settings', _configured_settings)
    monkeypatch.setattr(ebay_publish, 'ebay_services', lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, 'valid_user_access_token', AsyncMock(return_value='tok'))
    adapter = AsyncMock()
    adapter.get_offers.return_value = []
    adapter.create_offer.return_value = 'offer-sealed'
    adapter.publish_offer.return_value = 'ebay-sealed'
    monkeypatch.setattr(ebay_publish, 'EbayWriteAdapter', lambda *a, **kw: adapter)
    with pytest.raises(HTTPException):
        await ebay_publish.publish_to_ebay(listing.id, confirmation='', user='test', session=session)
    adapter.upsert_inventory_item.assert_not_called()
    result = await ebay_publish.publish_to_ebay(listing.id, confirmation='publish', user='test', session=session)
    assert result.status_code == 303
    payload = adapter.upsert_inventory_item.await_args.args[1]
    assert payload['packageWeightAndSize']['packageType'] == 'MAILING_BOX'
    assert payload['condition'] == 'NEW' and 'conditionDescriptors' not in payload
    assert payload['product']['aspects']['Game'] == ['One Piece CCG']
    assert payload['packageWeightAndSize']['weight'] == {'value': .35, 'unit': 'KILOGRAM'}
    offer = adapter.create_offer.await_args.args[0]
    assert offer['categoryId'] == '183457'
    assert offer['listingPolicies']['fulfillmentPolicyId'] == _configured_settings().ebay_sealed_fulfillment_policy_id
    assert offer['pricingSummary']['price']['value'] == '89.99'


@pytest.mark.asyncio
async def test_direct_revision_preserves_sealed_fields(session, monkeypatch):
    listing = deck(session)
    session.add(EbayListingLink(shop_listing_id=listing.id, sku=listing.id, marketplace_id='EBAY_AU',
                               ebay_listing_id='live', ebay_offer_id='offer', status=EbayListingLinkStatus.ACTIVE))
    session.commit()
    monkeypatch.setattr(ebay_publish, 'get_settings', _configured_settings)
    monkeypatch.setattr(ebay_publish, 'ebay_services', lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, 'valid_user_access_token', AsyncMock(return_value='tok'))
    adapter = AsyncMock()
    adapter.get_inventory_item.return_value = {'product': {'title': 'Old', 'imageUrls': listing.ebay_photo_paths}}
    monkeypatch.setattr(ebay_publish, 'EbayWriteAdapter', lambda *a, **kw: adapter)
    response = await ebay_publish.revise_listing_on_ebay(listing.id, confirmation='revise', user='test', session=session)
    assert response.status_code == 303
    payload = adapter.upsert_inventory_item.await_args.args[1]
    assert payload['condition'] == 'NEW'
    assert payload['product']['aspects']['Configuration'] == ['Deck']
    offer = adapter.update_offer.await_args.args[1]
    assert offer['listingPolicies']['fulfillmentPolicyId'] == _configured_settings().ebay_sealed_fulfillment_policy_id


def test_reserved_stock_blocked(session):
    from automation_control.models import InventoryStatus
    listing = deck(session)
    session.get(InventoryItem, listing.inventory_item_id).status = InventoryStatus.RESERVED
    session.flush()
    assert not ebay_publish.publication_preflight(session, listing, _configured_settings()).ready


def test_reload_publish_returns_to_studio_without_writes(client, session):
    listing = deck(session)
    session.add(EbayListingLink(shop_listing_id=listing.id, sku=listing.id, marketplace_id='EBAY_AU',
                               status=EbayListingLinkStatus.FAILED, last_error='Invalid postage package'))
    session.commit()
    response = client.get(f'/shop-listings/{listing.id}/publish-to-ebay', follow_redirects=False)
    assert response.status_code == 303
    assert '/ebay-preview?error=' in response.headers['location']
    assert 'Invalid%20postage%20package' in response.headers['location']
    assert listing.status == ShopListingStatus.DRAFT


@pytest.mark.asyncio
async def test_sealed_publish_failure_returns_review_page(session, monkeypatch):
    from automation_control.ebay_publish import EbayApiError
    listing = deck(session)
    monkeypatch.setattr(ebay_publish, 'get_settings', _configured_settings)
    monkeypatch.setattr(ebay_publish, 'ebay_services', lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, 'valid_user_access_token', AsyncMock(return_value='tok'))
    adapter = AsyncMock()
    adapter.get_offers.return_value = []
    adapter.create_offer.return_value = 'failed-offer'
    adapter.publish_offer.side_effect = EbayApiError('Invalid postage package')
    monkeypatch.setattr(ebay_publish, 'EbayWriteAdapter', lambda *a, **kw: adapter)
    response = await ebay_publish.publish_to_ebay(listing.id, confirmation='publish', user='test', session=session)
    assert response.status_code == 303
    assert '/ebay-preview?error=' in response.headers['location']
    adapter.publish_offer.assert_awaited_once()
