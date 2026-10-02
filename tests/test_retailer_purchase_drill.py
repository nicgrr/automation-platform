from scripts.retailer_purchase_drill import run


def test_retailer_purchase_drill_is_complete_and_has_no_external_calls():
    result = run()
    assert result == {
        "status": "passed",
        "purchase_lot_link": "passed",
        "canonical_inventory": "passed",
        "landed_cost_allocation": "passed",
        "fund_ledger_link": "passed",
        "allocated_stock_refund_guard": "passed",
        "refund_reversal": "passed",
        "repurchase_reactivation": "passed",
        "external_calls": "none",
    }
