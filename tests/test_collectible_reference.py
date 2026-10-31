import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.collectible_reference import canonical, remember
from automation_control.database import Base, get_session
from automation_control.models import (
    CatalogItem,
    CatalogItemType,
    CollectibleProduct,
    CollectibleReference,
    InventoryItem,
    ListingPhotoBatch,
    ListingPhotoPair,
    ListingPhotoPairStatus,
)


@pytest.fixture
def context(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/ref.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[require_dashboard_user] = lambda: "test"
        # No `with`: entering TestClient runs the lifespan's production schema check.
        yield s, TestClient(app)
        app.dependency_overrides.clear()


def test_remembering_ignores_case_and_stray_spaces(context):
    s, _ = context
    remember(s, "Sonny Angel", "Vegetable Series", "Cauliflower")
    remember(s, "sonny angel ", "Vegetable  Series ", "cauliflower")
    s.commit()
    assert s.scalar(select(func.count()).select_from(CollectibleReference)) == 1
    assert canonical(s, "SONNY ANGEL", "vegetable series ", "CAULIFLOWER") == ("Sonny Angel", "Vegetable Series", "Cauliflower")


def test_brands_include_the_curated_list_and_remembered_ones(context):
    s, client = context
    remember(s, "Smiski", "Bath Series", "Scrubbing")
    s.commit()
    brands = client.get("/collectibles/reference/brands").json()
    assert {"Sonny Angel", "Pop Mart", "Smiski"} <= set(brands)


def test_series_follow_the_chosen_brand(context):
    s, client = context
    remember(s, "Sonny Angel", "Cat Life Series", "Persian")
    s.commit()
    series = client.get("/collectibles/reference/series", params={"brand": "sonny angel"}).json()
    assert "Cat Life Series" in series and "Animal Series 1" in series  # remembered + curated
    assert "Twinkle Twinkle Create Your Taste Series" not in series      # Pop Mart's, not Sonny Angel's


def test_characters_follow_the_chosen_series_with_secrets_last(context):
    s, client = context
    remember(s, "Sonny Angel", "Cat Life Series", "Persian")
    remember(s, "Sonny Angel", "Cat Life Series", "Lucky Cat", is_secret=True)
    remember(s, "Sonny Angel", "Animal Series 2", "Kappa")
    s.commit()
    chars = client.get("/collectibles/reference/characters",
                       params={"brand": "Sonny Angel", "series": "cat life series"}).json()
    assert chars == [{"name": "Persian", "secret": False}, {"name": "Lucky Cat", "secret": True}]


def test_curated_characters_appear_for_their_series(context):
    _, client = context
    chars = client.get("/collectibles/reference/characters",
                       params={"brand": "Pop Mart", "series": "Twinkle Twinkle Create Your Taste Series"}).json()
    assert {"name": "White Bread", "secret": False} in chars


def _pair(s):
    s.add(ListingPhotoBatch(id="batch", total_pairs=1))
    s.flush()
    s.add(ListingPhotoPair(id="p", batch_id="batch", position=1, front_image_path="/f.jpg",
                           status=ListingPhotoPairStatus.NEEDS_REVIEW, extracted={"item_kind": "collectible"}))
    s.commit()


def _save(client, **fields):
    data = {"kind": "collectible", "confirmed": "yes", "quantity": "1", "condition": "Used", **fields}
    return client.post("/product-photo-review/p", data=data, follow_redirects=False)


def test_onboarding_a_figure_remembers_its_series_and_character(context):
    s, client = context
    _pair(s)
    assert _save(client, brand="Sonny Angel", series="Dog Time Series", name="Shiba").status_code == 303
    assert canonical(s, "sonny angel", "dog time series", "shiba") == ("Sonny Angel", "Dog Time Series", "Shiba")
    product = s.scalars(select(CollectibleProduct)).one()
    assert (product.brand, product.series, product.character) == ("Sonny Angel", "Dog Time Series", "Shiba")


def test_the_form_has_brand_series_character_in_that_order_with_the_picker(context):
    s, client = context
    _pair(s)
    body = client.get("/product-photo-review/p").text
    assert body.index("name='brand'") < body.index("name='series'") < body.index("name='name'")
    assert "/collectibles/reference/series" in body and "data-character" in body


def test_a_figure_already_in_the_catalogue_is_reused_not_refused(context):
    s, client = context
    s.add(CatalogItem(id="kappa", name="Kappa", item_type=CatalogItemType.COLLECTIBLE))
    s.flush()
    s.add(CollectibleProduct(catalog_item_id="kappa", brand="Sonny Angel", series="Animal Series 2", character="Kappa"))
    s.add(InventoryItem(id="inv", catalog_item_id="kappa", condition="Used", quantity=1))
    s.commit()
    _pair(s)
    resp = _save(client, brand="sonny angel", series="Animal Series 2 ", name="kappa")
    assert resp.status_code == 303
    assert s.scalar(select(func.count()).select_from(CatalogItem)) == 1
    assert s.get(ListingPhotoPair, "p").proposed_catalog_item_id == "kappa"
