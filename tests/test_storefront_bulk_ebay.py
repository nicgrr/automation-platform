"""Bulk & Bundles: live eBay bulk/bundle listings shown as their own category."""

import pytest

from automation_control.models import EbayListing, EbayListingLink, EbayListingLinkStatus
from storefront.bulk_ebay import bulk_listings, is_bulk_title
from tests.test_departments import add_card, client, session  # noqa: F401  (fixtures)


def _ebay(session, ebay_id, title, price_minor=995, status="Active", quantity=5, image=True):
    session.add(EbayListing(
        ebay_listing_id=ebay_id, title=title, currency="AUD", current_price_minor=price_minor,
        listing_status=status, quantity=quantity,
        raw_snapshot={"view_item_url": f"https://www.ebay.com.au/itm/{ebay_id}",
                      "image_urls": [f"https://i.ebayimg.com/images/{ebay_id}.jpg"] if image else []},
    ))
    session.commit()


@pytest.mark.parametrize("title,expected", [
    ("Pokemon 100 Card Bulk Lot Mixed Sets English NM", True),
    ("One Piece TCG Bulk Lot Common Uncommon Rare Mixed Cards ENGLISH", True),
    ("Pokémon TCG Mystery Energy Cards | Random Holo", True),
    ("Pokemon Card Bundle x4 Probopass Illustration Rare", True),
    ("Dragon Ball Z Super Battle Carddass 50x Japanese Vintage", True),
    ("EKWB Watercooling Bundle Pump Reservoir CPU Block", False),  # not cards
    ("Piccolo SDV11-PUR4 Pickup Rare Dragon Ball Super Divers Japanese", False),  # a single
])
def test_bulk_titles(title, expected):
    assert is_bulk_title(title) is expected


def test_only_live_bulk_card_listings_are_shown(session):
    _ebay(session, "1", "Pokemon 50 Card Bulk Lot Mixed Sets")
    _ebay(session, "2", "One Piece TCG Bulk Lot Mixed Cards", status="Ended")
    _ebay(session, "3", "EKWB Watercooling Bundle")
    _ebay(session, "4", "Charizard 4/102 Base Set Holo")
    assert [r["id"] for r in bulk_listings(session)] == ["1"]


def test_a_listing_already_on_the_site_as_a_single_is_not_repeated(session):
    listing_id = add_card(session, "Pika")
    _ebay(session, "9", "Pokemon Card Bundle x2 Pika")
    session.add(EbayListingLink(shop_listing_id=listing_id, sku="S", marketplace_id="EBAY_AU",
                                ebay_listing_id="9", status=EbayListingLinkStatus.ACTIVE))
    session.commit()
    assert bulk_listings(session) == []


def test_bulk_page_lists_and_links_to_ebay(client, session):
    _ebay(session, "1", "Pokemon 50 Card Bulk Lot Mixed Sets", price_minor=795)
    _ebay(session, "2", "Dragon Ball Super TCG Random Bulk Cards", price_minor=395)
    body = client.get("/shop/bulk").text
    assert "Bulk &amp; Bundles" in body
    assert "href='https://www.ebay.com.au/itm/1'" in body and "$7.95" in body
    assert "i.ebayimg.com/images/1.jpg" in body
    assert "All bulk" in body and "Dragon Ball" in body  # sub-category tabs


def test_bulk_page_filters_by_game(client, session):
    _ebay(session, "1", "Pokemon 50 Card Bulk Lot Mixed Sets")
    _ebay(session, "2", "Dragon Ball Super TCG Random Bulk Cards")
    body = client.get("/shop/bulk?sub=dragon_ball").text
    assert "Random Bulk Cards" in body and "50 Card Bulk Lot" not in body


def test_homepage_gets_a_bulk_tile_only_when_there_is_bulk(client, session):
    add_card(session, "Pika")
    assert "href='/shop/bulk'><div class='dept-hero'>" not in client.get("/").text
    _ebay(session, "1", "Pokemon 50 Card Bulk Lot Mixed Sets")
    assert "class='dept-card' href='/shop/bulk'" in client.get("/").text


def test_ebay_images_are_allowed_by_the_security_policy(client):
    assert "https://i.ebayimg.com" in client.get("/").headers["content-security-policy"]


def test_non_ebay_urls_are_never_used(session):
    session.add(EbayListing(ebay_listing_id="x", title="Pokemon Bulk Lot", currency="AUD", current_price_minor=100,
                            listing_status="Active", raw_snapshot={"view_item_url": "https://evil.example/x",
                                                                   "image_urls": ["https://evil.example/i.jpg"]}))
    session.commit()
    assert bulk_listings(session) == []
