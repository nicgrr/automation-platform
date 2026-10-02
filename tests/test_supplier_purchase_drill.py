from scripts.supplier_purchase_drill import run


def test_supplier_purchase_drill_is_complete_and_has_no_external_calls():
    assert run() == {
        "status": "passed",
        "supplier_catalogue_link": "passed",
        "purchase_lot_link": "passed",
        "proportional_cost_allocation": "passed",
        "canonical_inventory_receipt": "passed",
        "duplicate_receipt_guard": "passed",
        "external_calls": "none",
    }
