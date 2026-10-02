from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import Approval, AuditEvent, JobRun


def test_deprecated_control_plane_writes_are_rejected_and_audited(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'deprecations.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        try:
            client = TestClient(app)
            approval = client.post("/approvals", json={})
            job = client.post("/jobs", json={})

            assert approval.status_code == 410
            assert approval.json()["detail"].startswith("the generic approval endpoint is deprecated")
            assert job.status_code == 410
            assert job.json()["detail"].startswith("the generic job endpoint is deprecated")
            assert session.scalars(select(Approval)).all() == []
            assert session.scalars(select(JobRun)).all() == []
            events = session.scalars(
                select(AuditEvent).where(AuditEvent.action.like("deprecated.%"))
            ).all()
            assert [(event.action, event.outcome) for event in events] == [
                ("deprecated.approval.write", "rejected"),
                ("deprecated.job.write", "rejected"),
            ]
        finally:
            app.dependency_overrides.clear()
