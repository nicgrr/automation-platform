from datetime import UTC, datetime

from automation_control.labels import ebay_label_lines, shop_order_label_lines
from automation_control.models import EbayImportedOrder, ShopOrderStatus
from tests.test_shop_orders import client, make_order, session  # noqa: F401

SNAPSHOT = {
    "buyer": {"username": "buyer123"},
    "fulfillmentStartInstructions": [{"shippingStep": {"shipTo": {
        "fullName": "Jo Citizen",
        "contactAddress": {"addressLine1": "5 Example St", "addressLine2": "Unit 2", "city": "Sydney",
                           "stateOrProvince": "NSW", "postalCode": "2000", "countryCode": "AU"},
    }}}],
}


def test_ebay_label_lines_put_postcode_with_locality_and_hide_home_country():
    assert ebay_label_lines(SNAPSHOT) == ["Jo Citizen", "5 Example St", "Unit 2", "Sydney NSW 2000"]


def test_ebay_label_lines_show_foreign_country_and_fall_back_to_username():
    snap = {"buyer": {"username": "u1"}, "fulfillmentStartInstructions": [{"shippingStep": {"shipTo": {
        "contactAddress": {"addressLine1": "1 Rd", "city": "Auckland", "postalCode": "1010", "countryCode": "NZ"}}}}]}
    assert ebay_label_lines(snap) == ["u1", "1 Rd", "Auckland 1010", "NZ"]


def test_shop_label_page_has_name_address_and_sender(client, session):  # noqa: F811
    order = make_order(session, status=ShopOrderStatus.PAID)
    assert shop_order_label_lines(order) == ["A Buyer", "1 Test St"]
    page = client.get(f"/labels/shop/{order.id}?print=1")
    assert page.status_code == 200
    assert "A Buyer" in page.text and "1 Test St" in page.text
    assert "150mm 100mm" in page.text and "window.print()" in page.text
    assert "FROM: NL Vault 0405209496" in page.text
    assert f"/labels/shop/{order.id}/print" in client.get(f"/shop-orders/{order.id}").text


def test_ebay_label_route_and_unknown_order(client, session):  # noqa: F811
    session.add(EbayImportedOrder(ebay_order_id="12-34", sku="S1", quantity=1, marketplace_id="EBAY_AU", applied_at=datetime.now(UTC),
                                  observed_at=datetime.now(UTC), raw_snapshot=SNAPSHOT))
    session.commit()
    page = client.get("/labels/ebay/12-34")
    assert page.status_code == 200 and "Jo Citizen" in page.text and "Sydney NSW 2000" in page.text
    assert client.get("/labels/ebay/nope").status_code == 404
    assert "/labels/ebay/12-34/print" in client.get("/ebay-sales").text


def test_tspl_job_is_a_full_label_bitmap_and_prints_via_cups_raw(client, session, monkeypatch):  # noqa: F811
    import subprocess

    from automation_control import labels

    image = labels.render_label_image(["Jo Citizen", "5 Example St", "Sydney NSW 2000"])
    assert image.size == (800, 1200) and image.mode == "1"
    job = labels.tspl_for_image(image)
    assert job.startswith(b"SIZE 100 mm,150 mm") and b"BITMAP 0,0,100,1200,0," in job and job.endswith(b"PRINT 1,1\r\n")
    assert len(job) > 100 * 1200

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw["input"])) or subprocess.CompletedProcess(cmd, 0, b"", b""))
    order = make_order(session)
    assert client.post(f"/labels/shop/{order.id}/print").json() == {"ok": True}
    assert calls[0][0][:5] == ["lp", "-d", labels.PRINTER_QUEUE, "-o", "raw"] and calls[0][1].startswith(b"SIZE")

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"printer offline"))
    failed = client.post(f"/labels/shop/{order.id}/print")
    assert failed.status_code == 502 and "printer offline" in failed.json()["detail"]
    assert client.post("/labels/shop/nope/print").status_code == 404


def test_pack_assistant_has_one_print_label_button_for_any_status(client, session):  # noqa: F811
    for status in (ShopOrderStatus.PAID, ShopOrderStatus.PACKED, ShopOrderStatus.SHIPPED):
        order = make_order(session, status=status, reference=f"NLV-{status.value}")
        page = client.get(f"/shop-orders/{order.id}/pack-sheet").text
        assert page.count(f"data-label-print='/labels/shop/{order.id}/print'") == 1
