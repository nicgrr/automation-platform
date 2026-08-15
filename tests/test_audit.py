from automation_control.audit import record_event


def test_audit_redacts_sensitive_fields(session):
    event = record_event(
        session,
        actor_type="agent",
        actor_id="test",
        action="adapter.call",
        resource_type="ebay",
        resource_id=None,
        outcome="success",
        correlation_id="00000000-0000-0000-0000-000000000000",
        details={"token": "do-not-store", "nested": {"password": "do-not-store", "safe": 1}},
    )
    assert event.details["token"] == "[REDACTED]"
    assert event.details["nested"] == {"password": "[REDACTED]", "safe": 1}

