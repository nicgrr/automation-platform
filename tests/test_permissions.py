from automation_control.models import ToolPermission
from automation_control.permissions import PermissionDenied, PermissionService


def test_permissions_deny_by_default(session):
    decision = PermissionService().decide(session, "agent:default", "get_container_logs")
    assert decision.allowed is False


def test_explicit_grant_is_required(session):
    session.add(ToolPermission(principal="agent:ops", tool_name="get_container_status", allowed=True, requires_approval=False))
    session.commit()
    decision = PermissionService().require(session, "agent:ops", "get_container_status")
    assert decision.allowed is True
    try:
        PermissionService().require(session, "agent:ops", "restart_allowed_container")
    except PermissionDenied:
        pass
    else:
        raise AssertionError("unregistered restart was permitted")

