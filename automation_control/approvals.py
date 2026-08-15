from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .models import Approval, ApprovalStatus


class ApprovalStateError(Exception):
    pass


def decide_approval(session: Session, approval: Approval, *, approved: bool, actor: str, reason: str | None = None) -> Approval:
    if approval.status is not ApprovalStatus.PENDING:
        raise ApprovalStateError("approval has already been decided")
    approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    approval.decided_at = datetime.now(UTC)
    approval.decided_by = actor
    approval.reason = reason
    session.commit()
    return approval

