import pytest
from sqlalchemy.orm import Session

from automation_control.database import Base, make_engine


@pytest.fixture
def session():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture(autouse=True)
def _no_trained_foil_model(monkeypatch):
    """Tests must not depend on whatever real model happens to be trained
    on disk (scan_ingest_data/models/foil_classifier.joblib) -- that file
    reflects live production /foil-review data, not a fixed, versioned
    test fixture, and letting it influence outcomes would make the suite
    flakier as more real labels accumulate over time. A test that
    specifically exercises the trained-classifier path in
    scan_ingest/foil.py monkeypatches foil_features.predict itself to a
    fixed value, which overrides this.
    """
    monkeypatch.setattr("automation_control.scan_ingest.foil_features._load_model", lambda: None)

