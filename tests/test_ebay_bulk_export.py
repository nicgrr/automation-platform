"""The one-click eBay export: chaining export_ebay_bulk_listing.py and
convert_to_ebay_template.py behind a single button rather than two manual
runs.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        ebay_ship_from_location="Sydney, NSW, Australia", ebay_dispatch_days=1,
        ebay_listing_payment_policy_name="Trading Cards - Standard Payment Policy",
        ebay_listing_return_policy_name="Trading Cards & Sealed TCG - Refund Policy",
        ebay_letter_shipping_policy_name="Trading Cards & Sealed TCG - Australia Post",
        ebay_parcel_shipping_policy_name="Trading Cards & Sealed TCG - Australia Post",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def client():
    original = app.state.settings
    app.dependency_overrides[require_dashboard_user] = lambda: "nic"
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.state.settings = original


def test_the_page_gates_on_missing_config(client):
    app.state.settings = _settings(ebay_letter_shipping_policy_name=None,
                                   ebay_listing_payment_policy_name=None)
    page = client.get("/ebay-export").text
    assert "Can't build the export" in page
    assert "letter shipping policy name" in page
    assert "payment policy name" in page
    # Only the missing ones are named.
    assert "dispatch days" not in page.split("missing from .env.local:")[1].split("</p>")[0]


def test_the_page_offers_the_form_once_configured(client):
    app.state.settings = _settings()
    page = client.get("/ebay-export").text
    assert "Build &amp; download" in page
    assert "Can't build" not in page


def test_build_refuses_without_config_rather_than_running_with_placeholders(client):
    """The old two-script path silently fell back to REPLACE_ME
    placeholders baked into the file eBay would then reject. This must
    refuse outright instead."""
    app.state.settings = _settings(ebay_ship_from_location=None)
    resp = client.post("/ebay-export/build", data={"mode": "both"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_build_passes_the_letter_and_parcel_policy_names_through(client):
    """These were being silently dropped in an earlier version of this
    route -- the export script fell back to its own placeholder shipping
    policy names, which eBay would reject on upload. Caught only by
    running the whole chain end to end against the real account."""
    app.state.settings = _settings()
    with patch("scripts.export_ebay_bulk_listing.main", return_value=0) as export_main, \
         patch("scripts.convert_to_ebay_template.main", return_value=0), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_bytes", return_value=b"csv,data"):
        client.post("/ebay-export/build", data={"mode": "both"})

    called_args = export_main.call_args[0][0]
    assert "--letter-policy" in called_args
    assert "Trading Cards & Sealed TCG - Australia Post" in called_args
    assert "--parcel-policy" in called_args


def test_build_streams_the_finished_csv_as_a_download(client):
    app.state.settings = _settings()
    with patch("scripts.export_ebay_bulk_listing.main", return_value=0), \
         patch("scripts.convert_to_ebay_template.main", return_value=0), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_bytes", return_value=b"Info,Version=1.0.0\r\n*Action(...),...\r\n"):
        resp = client.post("/ebay-export/build", data={"mode": "both"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=ebay_bulk_listing_ready.csv" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"Info,Version")


def test_a_failed_export_step_reports_the_real_exit_code_not_a_500(client):
    app.state.settings = _settings()
    with patch("scripts.export_ebay_bulk_listing.main", return_value=1):
        resp = client.post("/ebay-export/build", data={"mode": "both"}, follow_redirects=False)

    assert resp.status_code == 303
    assert "Export step failed" in resp.headers["location"] or "error=" in resp.headers["location"]


def test_a_failed_convert_step_is_reported_too(client):
    app.state.settings = _settings()
    with patch("scripts.export_ebay_bulk_listing.main", return_value=0), \
         patch("scripts.convert_to_ebay_template.main", return_value=1):
        resp = client.post("/ebay-export/build", data={"mode": "both"}, follow_redirects=False)

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_singles_only_and_lots_only_map_to_the_right_flag(client):
    app.state.settings = _settings()
    for mode, flag in [("singles-only", "--singles-only"), ("lots-only", "--lots-only")]:
        with patch("scripts.export_ebay_bulk_listing.main", return_value=0) as export_main, \
             patch("scripts.convert_to_ebay_template.main", return_value=0), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_bytes", return_value=b"data"):
            client.post("/ebay-export/build", data={"mode": mode})
        assert flag in export_main.call_args[0][0]


def test_ebay_export_is_reachable_from_the_nav(client):
    from automation_control.ui import ADMIN_NAV

    hrefs = [href for _, links, _ in ADMIN_NAV for href, _ in links]
    assert "/ebay-export" in hrefs
