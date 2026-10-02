from scripts.whatnot_show_drill import run


def test_whatnot_show_drill_is_complete_and_has_no_external_calls():
    result = run()
    assert result["status"] == "passed"
    assert result["inventory_reservation"] == "passed"
    assert result["show_start_and_completion"] == "passed"
    assert result["sale_and_fee_capture"] == "passed"
    assert result["customer_handoff"] == "passed"
    assert result["unsold_restore"] == "passed"
    assert result["giveaway_depletion"] == "passed"
    assert result["cancellation_restore"] == "passed"
    assert result["external_calls"] == "none"
