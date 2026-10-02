from scripts.storefront_transaction_drill import run


def test_complete_storefront_transaction_drill_has_no_external_calls():
    result = run()
    assert result == {
        "status": "passed",
        "checkout_reservation": "passed",
        "signed_webhook_payment": "passed",
        "fulfilment": "passed",
        "cancellation_restore": "passed",
        "refund_restock": "passed",
        "external_calls": "none",
    }
