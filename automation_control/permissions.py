from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ToolPermission


class PermissionDenied(Exception):
    pass


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    requires_approval: bool
    constraints: dict[str, Any]


class PermissionService:
    """Deny-by-default tool authorization."""

    def decide(self, session: Session, principal: str, tool_name: str) -> PermissionDecision:
        permission = session.scalar(
            select(ToolPermission).where(
                ToolPermission.principal == principal,
                ToolPermission.tool_name == tool_name,
            )
        )
        if permission is None or not permission.allowed:
            return PermissionDecision(False, True, {})
        return PermissionDecision(True, permission.requires_approval, permission.constraints)

    def require(self, session: Session, principal: str, tool_name: str) -> PermissionDecision:
        decision = self.decide(session, principal, tool_name)
        if not decision.allowed:
            raise PermissionDenied(f"principal is not permitted to use {tool_name}")
        return decision

