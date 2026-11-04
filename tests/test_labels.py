from datetime import UTC, datetime

import pytest

from automation_control.labels import ebay_label_lines, shop_order_label_lines
from automation_control.models import EbayImportedOrder, ShopOrderStatus
from tests.test_shop_orders import client, make_order, session  # noqa: F401

SNAPSHOT = {
    "buyer": {"username": "buyer123"},
    "fulfillmentStartInstructions": [{"shippingStep": {"shipTo": {
        "fullName": "Jo Citizen",
        "primaryPhone": {"phoneNumber": "+61 412 345 678"},
        "contactAddress": {"addressLine1": "5 Example St", "addressLine2": "Unit 2", "city": "Sydney",
                           "stateOrProvince": "NSW", "postalCode": "2000", "countryCode": "AU"},
    }}}],
}


def test_ebay_label_lines_put_postcode_with_locality_and_hide_home_country():
    assert ebay_label_lines(SNAPSHOT) == [
        "Jo Citizen", "5 Example St", "Unit 2", "Sydney NSW 2000", "+61 412 345 678",
    ]


def test_ebay_label_lines_show_foreign_country_and_fall_back_to_username():
    snap = {"buyer": {"username": "u1"}, "fulfillmentStartInstructions": [{"shippingStep": {"shipTo": {
        "contactAddress": {"addressLine1": "1 Rd", "city": "Auckland", "postalCode": "1010", "countryCode": "NZ"}}}}]}
    assert ebay_label_lines(snap) == ["u1", "1 Rd", "Auckland 1010", "NZ"]


def test_shop_label_page_has_name_address_and_sender(client, session):  # noqa: F811
    order = make_order(session, status=ShopOrderStatus.PAID)
    order.ship_to_phone = "0404 959 211"
    session.commit()
    assert shop_order_label_lines(order) == ["A Buyer", "1 Test St", "0404 959 211"]
    page = client.get(f"/labels/shop/{order.id}?print=1")
    assert page.status_code == 200
    assert "A Buyer" in page.text and "1 Test St" in page.text
    assert "0404 959 211" in page.text
    assert "150mm 100mm" in page.text and "window.print()" in page.text
    assert "data:image/png;base64," in page.text
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


def _a4_pdf_with_corner_label(path):
    from PIL import Image, ImageDraw

    page = Image.new("RGB", (1654, 2339), "white")  # A4 at 200 dpi
    draw = ImageDraw.Draw(page)
    draw.rectangle((50, 50, 850, 1250), outline="black", width=10)
    draw.rectangle((100, 100, 800, 300), fill="black")
    page.save(path, "PDF", resolution=200)


def test_postage_label_is_cropped_and_fitted_to_the_sticker(tmp_path):
    from automation_control.labels import LABEL_H, LABEL_W, postage_label_image

    pdf = tmp_path / "label.pdf"
    _a4_pdf_with_corner_label(pdf)
    image = postage_label_image(pdf.read_bytes())
    assert image.size == (LABEL_W, LABEL_H) and image.mode == "1"
    black = [(x, y) for x in range(0, LABEL_W, 8) for y in range(0, LABEL_H, 8) if image.getpixel((x, y)) == 0]
    assert black and min(x for x, _ in black) < 30 and max(x for x, _ in black) > LABEL_W - 40


def test_postage_label_rejects_garbage_and_blank_pages():
    import pytest

    from automation_control.labels import postage_label_image

    with pytest.raises(RuntimeError, match="not a PDF"):
        postage_label_image(b"hello")
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (100, 100), "white").save(buf, "PNG")
    with pytest.raises(RuntimeError, match="blank"):
        postage_label_image(buf.getvalue())


def test_postage_upload_route_prints_and_reports_errors(client, tmp_path, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr("automation_control.labels.send_job", lambda job: sent.append(job))
    pdf = tmp_path / "label.pdf"
    _a4_pdf_with_corner_label(pdf)
    assert client.get("/labels/postage").status_code == 200
    ok = client.post("/labels/postage/print", files={"file": ("l.pdf", pdf.read_bytes(), "application/pdf")})
    assert ok.status_code == 200 and sent and sent[0].startswith(b"SIZE 100 mm,150 mm")
    bad = client.post("/labels/postage/print", files={"file": ("l.txt", b"nope", "text/plain")})
    assert bad.status_code == 502 and "not a PDF" in bad.json()["detail"]


def test_ebay_label_lines_drop_ebays_own_reference_code():
    snap = {"fulfillmentStartInstructions": [{"shippingStep": {"shipTo": {"fullName": "Jo Citizen", "contactAddress": {
        "addressLine1": "ebay:t27v2mj", "addressLine2": "58 Rockman Dr", "city": "Raceview",
        "stateOrProvince": "QLD", "postalCode": "4305", "countryCode": "AU"}}}}]}
    assert ebay_label_lines(snap) == ["Jo Citizen", "58 Rockman Dr", "Raceview QLD 4305"]


def test_ebay_label_phone_falls_back_to_buyer_registration_address():
    snap = {
        "buyer": {"buyerRegistrationAddress": {"primaryPhone": {"phoneNumber": "0412 000 111"}}},
        "fulfillmentStartInstructions": [{"shippingStep": {"shipTo": {
            "fullName": "Jo Citizen",
            "contactAddress": {"addressLine1": "5 Example St", "city": "Sydney",
                               "stateOrProvince": "NSW", "postalCode": "2000", "countryCode": "AU"},
        }}}],
    }
    assert ebay_label_lines(snap)[-1] == "0412 000 111"


def _text_rows(lines):
    """Every drawn row of the address, by capturing what the renderer writes."""
    from PIL import ImageDraw

    import automation_control.labels as labels

    drawn = []
    real = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        drawn.append((xy, text, self.textlength(text, font=kwargs.get("font"))))
        return real(self, xy, text, *args, **kwargs)

    ImageDraw.ImageDraw.text = spy
    try:
        labels.render_landscape_label(lines)
    finally:
        ImageDraw.ImageDraw.text = real
    return drawn


@pytest.mark.parametrize("lines", [
    ["Alex Example", "1 Sample Road", "Example Heights QLD 4000", "0400000000"],
    ["Alexandria Christodoulopoulos-Fitzgerald", "Unit 12, Level 3, The Very Long Apartment Building Name",
     "123-125 Extraordinarily Long Boulevard Parade", "Mount Example North Heights NSW 2000", "0400000000"],
    ["X" * 120, "1 Sample Road"],
])
def test_nothing_on_the_label_runs_off_the_edge(lines):
    from automation_control.labels import LABEL_H, LABEL_W

    for (x, y), _text, width in _text_rows(lines):
        assert x + width <= LABEL_H
        assert y < LABEL_W - 20


def test_every_character_of_the_address_is_on_the_label():
    lines = ["Alexandria Christodoulopoulos-Fitzgerald", "1 Sample Road", "Example Heights QLD 4000"]
    drawn = "".join(text for _xy, text, _width in _text_rows(lines)).replace(" ", "")
    assert all(line.replace(" ", "") in drawn for line in lines)


def test_a_normal_address_keeps_the_postcode_with_its_suburb():
    rows = [text for _xy, text, _width in _text_rows(["Alex Example", "Example Heights QLD 4000"])]
    assert "Example Heights QLD 4000" in rows


def test_the_label_is_one_bit_at_the_printers_size():
    from automation_control.labels import render_label_image

    image = render_label_image(["Alex Example", "1 Sample Road"])
    assert (image.size, image.mode) == ((800, 1200), "1")
