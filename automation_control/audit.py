from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent


SENSITIVE_KEY_PARTS = ("password", "secret", "token", "api_key", "authorization", "code")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if any(part in key.lower() for part in SENSITIVE_KEY_PARTS) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def record_event(session: Session, **fields: Any) -> AuditEvent:
    fields["details"] = redact(fields.get("details", {}))
    event = AuditEvent(**fields)
    session.add(event)
    session.commit()
    return event
