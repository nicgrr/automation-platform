from automation_control.api import _listing_pipeline_section, _read_csv_rows


def test_no_output_files_shows_placeholder(tmp_path):
    html = _listing_pipeline_section(tmp_path / "missing")
    assert "No output yet" in html


def test_reads_and_escapes_bulk_upload_csv(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "ebay_bulk_upload.csv").write_text(
        "CustomLabel,*Title,*StartPrice,BestOfferAutoAcceptPrice,MinimumBestOfferPrice\n"
        'NIC-01,"<script>alert(1)</script>",45.00,41.40,32.00\n'
    )
    html = _listing_pipeline_section(out_dir)
    assert "NIC-01" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Listings (1)" in html
    assert "No image_naming_checklist.csv found" in html
    assert "No validation_report.csv found" in html


def test_reads_checklist_and_validation_report(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "image_naming_checklist.csv").write_text(
        "Listing ID,Listing,Image filename,What to shoot\n01,Some Title,01-1.jpg,Card FRONT\n"
    )
    (out_dir / "validation_report.csv").write_text("Warning\nlisting 'NIC-02': price at floor\n")
    html = _listing_pipeline_section(out_dir)
    assert "Image checklist (1 photos)" in html
    assert "01-1.jpg" in html
    assert "Validation warnings (1)" in html
    assert "price at floor" in html


def test_read_csv_rows_returns_none_for_missing_file(tmp_path):
    assert _read_csv_rows(tmp_path / "nope.csv") is None


def test_read_csv_rows_returns_none_for_empty_file(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("")
    assert _read_csv_rows(path) is None
