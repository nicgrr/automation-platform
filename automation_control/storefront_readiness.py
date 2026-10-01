"""Truthful, non-secret NL Vault launch-readiness checks."""

from .config import Settings


def storefront_legal_issues(settings: Settings) -> list[str]:
    issues: list[str] = []
    legal_fields = (
        ("business name", settings.storefront_business_name),
        ("ABN", settings.storefront_business_abn),
        ("contact email", settings.storefront_contact_email),
        ("GST statement", settings.storefront_gst_statement),
        ("record-retention period", settings.storefront_retention_period),
    )
    missing_legal = [label for label, value in legal_fields if not value]
    if missing_legal:
        issues.append("Legal details missing: " + ", ".join(missing_legal))
    if not settings.storefront_legal_approved:
        issues.append("Privacy and sale terms have not been marked reviewed and approved")
    return issues


def storefront_readiness_issues(settings: Settings) -> list[str]:
    issues: list[str] = []
    payment_fields = (
        ("Stripe secret key", settings.stripe_secret_key),
        ("Stripe publishable key", settings.stripe_publishable_key),
        ("Stripe webhook secret", settings.stripe_webhook_secret),
    )
    missing_payments = [label for label, value in payment_fields if not value]
    if missing_payments:
        issues.append("Payment configuration missing: " + ", ".join(missing_payments))

    issues.extend(storefront_legal_issues(settings))
    return issues
