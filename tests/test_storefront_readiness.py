from automation_control.config import Settings
from automation_control.storefront_readiness import storefront_legal_issues, storefront_readiness_issues


def test_launch_is_blocked_when_payment_and_legal_facts_are_missing():
    issues = storefront_readiness_issues(Settings())
    assert any("Payment configuration missing" in issue for issue in issues)
    assert any("Legal details missing" in issue for issue in issues)
    assert any("not been marked reviewed" in issue for issue in issues)


def test_launch_is_ready_only_when_every_gate_is_complete():
    settings = Settings(
        stripe_secret_key="secret",
        stripe_publishable_key="publishable",
        stripe_webhook_secret="webhook",
        storefront_business_name="Example Pty Ltd",
        storefront_business_abn="00 000 000 000",
        storefront_contact_email="shop@example.test",
        storefront_gst_statement="Prices include GST",
        storefront_retention_period="7 years",
        storefront_legal_approved=True,
    )
    assert storefront_readiness_issues(settings) == []
    assert storefront_legal_issues(settings) == []
