from io import BytesIO
from unittest.mock import patch
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.config import Settings
from automation_control.models import ListingPhotoBatch, ListingPhotoPair, ListingPhotoPairStatus, InventoryItem, ShopListing

@pytest.fixture
def workspace(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "test.db"}', connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    old = app.state.settings
    with Session(engine) as db:
        app.dependency_overrides[get_session] = lambda: db
        app.dependency_overrides[require_dashboard_user] = lambda: 'owner'
        app.state.settings = Settings(anthropic_api_key='test', scan_media_dir=str(tmp_path / 'media'))
        try:
            yield TestClient(app), db
        finally:
            app.state.settings = old
            app.dependency_overrides.clear()

def test_workspace_and_summary_are_read_only(workspace):
    client, db = workspace
    b = ListingPhotoBatch(total_pairs=1); db.add(b);db.flush()
    db.add(ListingPhotoPair(batch_id=b.id,position=1,front_image_path='/private/secret.jpg',status=ListingPhotoPairStatus.NEEDS_REVIEW,
                           extracted={'character':'<script>alert(1)</script>'},issue='Choose printing'))
    db.commit()
    r = client.get('/scan-studio/data')
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-store'
    assert '/private/' not in r.text
    assert r.json()['cards'][0]['status'] == 'needs_review'
    assert db.query(InventoryItem).count() == db.query(ShopListing).count() == 0
    html = client.get('/scan-studio').text
    assert 'scan-studio.js' in html and 'My batches' in html
    assert 'method="post"' in html  # shared CSRF handler supplies token
    assert '<script>alert(1)</script>' not in html

def test_missing_batch_returns_404(workspace):
    assert workspace[0].get('/scan-studio/data?batch_id=missing').status_code == 404

def test_workspace_requires_login(workspace):
    client, db = workspace
    app.dependency_overrides.pop(require_dashboard_user)
    assert client.get('/scan-studio/data',headers={'Accept':'application/json'},follow_redirects=False).status_code == 401

def test_async_upload_returns_batch_without_adding_stock(workspace):
    client, db = workspace
    image = BytesIO();Image.new('RGB',(600,800),'white').save(image,'JPEG')
    with patch('automation_control.ebay_photo_batch._run_batch') as worker:
        r = client.post('/ebay-listing-bulk-photos',headers={'Accept':'application/json'},
            data={'game_hint':'pokemon','has_backs':'no'},files={'photos':('front.jpg',image.getvalue(),'image/jpeg')})
    assert r.status_code == 201
    bid = r.json()['batch_id']
    assert db.get(ListingPhotoBatch,bid).total_pairs == 1
    assert db.query(InventoryItem).count() == db.query(ShopListing).count() == 0
    assert worker.call_count == 1
    assert client.get('/scan-studio/data?batch_id='+bid).json()['selected'] == bid
