import argparse

import pytest

from scripts.convert_to_ebay_template import PLACEHOLDER, convert_row, read_ebay_header


def _args(**overrides):
    base = dict(condition_id="456", location="Sydney NSW", dispatch_days="1",
                payment_policy="Payments", return_policy="Returns")
    base.update(overrides)
    return argparse.Namespace(**base)


def _row(**overrides):
    base = dict(sku="sku-1", title="Abra #54", description="Abra #54", condition="Near Mint",
                price="2.34", currency="AUD", quantity="1", weight_grams="15",
                image_url="https://example.test/abra.png", shipping_policy="Letter Mail",
                set_name="Unified Minds")
    base.update(overrides)
    return base


def test_read_ebay_header_rejects_a_file_that_isnt_the_real_template(tmp_path):
    bad = tmp_path / "not_a_template.csv"
    bad.write_text("sku,title,price\n1,Abra,2.34\n")
    with pytest.raises(SystemExit):
        read_ebay_header(str(bad))


def test_read_ebay_header_parses_the_r_delimited_real_template(tmp_path):
    template = tmp_path / "template.csv"
    template.write_bytes(
        "﻿Info,Version=1.0.0,Template=fx_category_template_EBAY_AU\r"
        "*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8),CustomLabel,*Category\r"
        .encode("utf-8")
    )
    info_row, header = read_ebay_header(str(template))
    assert header == ["*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8)", "CustomLabel", "*Category"]
    # The Info row comes back too: an upload without it fails every row.
    assert info_row.startswith("Info,Version=")


def test_a_single_card_row_splits_name_and_number_from_the_title():
    out = convert_row(_row(title="Abra #54"), _args())
    assert out["C:Card Name"] == "Abra"
    assert out["C:Card Number"] == "54"
    assert "C:Number of Cards" not in out


def test_a_bulk_lot_row_gets_card_count_instead_of_a_name_and_number():
    out = convert_row(_row(title="Obsidian Flames — bulk lot (21 cards)", weight_grams="143"), _args())
    assert out["C:Number of Cards"] == "21"
    assert "C:Card Name" not in out
    assert "C:Card Number" not in out


def test_weight_is_carried_through_with_a_grams_unit():
    out = convert_row(_row(weight_grams="635"), _args())
    assert out["C:Item Weight"] == "635 g"


def test_shipping_policy_passes_through_unchanged_from_the_upstream_export():
    out = convert_row(_row(shipping_policy="Trading Cards & Sealed TCG - Tracked Parcel"), _args())
    assert out["ShippingProfileName"] == "Trading Cards & Sealed TCG - Tracked Parcel"


def test_a_missing_real_value_stays_a_visible_placeholder_not_a_blank():
    """A blank cell in the uploaded file is easy to miss; a loud
    placeholder string is not -- this must never silently pass through
    an unfilled required field as empty."""
    out = convert_row(_row(), _args(location=PLACEHOLDER))
    assert out["*Location"] == PLACEHOLDER


def test_a_single_card_gets_the_individual_cards_category_not_the_lots_one():
    out = convert_row(_row(title="Abra #54"), _args())
    assert out["*Category"] == "183454"


def test_a_bulk_lot_gets_the_mixed_card_lots_category():
    out = convert_row(_row(title="Obsidian Flames — bulk lot (21 cards)"), _args())
    assert out["*Category"] == "183455"


def test_set_name_passes_through_to_the_required_c_set_field():
    out = convert_row(_row(set_name="Unified Minds"), _args())
    assert out["*C:Set"] == "Unified Minds"


def test_the_written_file_starts_with_the_info_row_and_a_bom(tmp_path):
    """The failure this guards produced "1,014 failed, 0 completed" on a
    real upload. File Exchange uses the Info row to identify which template
    a file was built from; without it every row fails while the row count
    still parses -- so it reads as a thousand bad listings rather than one
    bad file. The BOM goes with it: eBay's own template carries one and the
    data is not ASCII.
    """
    import subprocess
    import sys

    # Built from the converter's own output columns so a new field can't
    # quietly leave this test asserting against a stale template.
    columns = list(convert_row(_row(), _args()))
    action = "*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8)"
    header = [action] + [c for c in columns if c != action]

    template = tmp_path / "template.csv"
    template.write_bytes(
        ("\ufeffInfo,Version=1.0.0,Template=fx_category_template_EBAY_AU\r"
         + ",".join(header) + "\r").encode("utf-8")
    )
    source = tmp_path / "in.csv"
    source.write_text(
        "sku,title,description,condition,price,currency,quantity,weight_grams,image_url,shipping_policy,set_name\n"
        "abc,Abra #54,a Pok\u00e9mon card,New,2.34,AUD,1,15,http://x/y.png,letter,Mega Evolution\n",
        encoding="utf-8")
    out = tmp_path / "out.csv"

    subprocess.run([sys.executable, "-m", "scripts.convert_to_ebay_template",
                    "--template", str(template), "--in", str(source), "--out", str(out),
                    "--location", "Sydney", "--dispatch-days", "1",
                    "--payment-policy", "P", "--return-policy", "R"],
                   check=True, capture_output=True)

    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = raw.decode("utf-8-sig").splitlines()
    assert lines[0] == "Info,Version=1.0.0,Template=fx_category_template_EBAY_AU"
    assert lines[1].startswith("*Action(")
    assert lines[2].startswith("Add,")
