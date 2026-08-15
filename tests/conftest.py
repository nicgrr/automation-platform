import pytest
from sqlalchemy.orm import Session

from automation_control.database import Base, make_engine


@pytest.fixture
def session():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db

