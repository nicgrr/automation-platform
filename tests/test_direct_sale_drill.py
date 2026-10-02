from scripts.direct_sale_drill import run


def test_direct_sale_drill_is_complete_and_has_no_external_calls():
    assert run() == {
        "status": "passed",
        "atomic_stock_decrement": "passed",
        "customer_handoff": "passed",
        "cost_snapshot": "passed",
        "duplicate_submission_guard": "passed",
        "one_time_void_restore": "passed",
        "external_calls": "none",
    }
