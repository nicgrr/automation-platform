import pytest
from fastapi import HTTPException

from automation_control.docker_broker import api


class FakeGateway:
    def status(self, name: str) -> dict:
        return {"Status": "running", "Running": True, "Health": {"Status": "healthy"}}

    def logs(self, name: str, tail: int) -> str:
        return "safe log"


def test_broker_enforces_allowlist(monkeypatch):
    monkeypatch.setenv("ALLOWED_CONTAINERS", "grocery-web")
    monkeypatch.setattr(api, "gateway", FakeGateway())
    assert api.containers() == {"containers": ["grocery-web"]}
    assert api.status("grocery-web") == {"name": "grocery-web", "status": "running", "running": True}
    with pytest.raises(HTTPException) as exc:
        api.status("portainer")
    assert exc.value.status_code == 404


def test_broker_has_no_mutating_routes():
    methods = {(method, route.path) for route in api.app.routes for method in getattr(route, "methods", set())}
    assert not any(method in {"POST", "PUT", "PATCH", "DELETE"} for method, _ in methods)
    assert not any("exec" in path or "restart" in path for _, path in methods)
