import csv

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import Supplier, SupplierStatus


def _write_csv(path, rows, header=None):
    header = header or ["name", "contact", "website", "categories", "account_status", "wholesale_discount_pct", "minimum_order"]
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def test_import_creates_suppliers(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_suppliers_csv
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(import_suppliers_csv, "SessionLocal", sessionmaker(bind=engine))

    csv_path = tmp_path / "suppliers.csv"
    _write_csv(csv_path, [
        ["Big Wholesale Co", "sales@bw.com", "https://bw.com", "TCG", "active", "15", "250"],
        ["Blind Box Direct", "", "", "blind box", "researching", "", ""],
    ])

    result = import_suppliers_csv.main(["--file", str(csv_path)])
    assert result == 0

    with Session(engine) as session:
        suppliers = session.query(Supplier).order_by(Supplier.name).all()
        assert len(suppliers) == 2
        assert suppliers[0].name == "Big Wholesale Co"
        assert suppliers[0].account_status == SupplierStatus.ACTIVE
        assert suppliers[0].wholesale_discount_pct == 15
        assert suppliers[1].account_status == SupplierStatus.RESEARCHING


def test_reimporting_the_same_name_updates_instead_of_duplicating(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_suppliers_csv
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(import_suppliers_csv, "SessionLocal", sessionmaker(bind=engine))

    csv_path = tmp_path / "suppliers.csv"
    _write_csv(csv_path, [["Big Wholesale Co", "", "", "", "active", "10", ""]])
    import_suppliers_csv.main(["--file", str(csv_path)])

    _write_csv(csv_path, [["Big Wholesale Co", "", "", "", "active", "20", ""]])
    import_suppliers_csv.main(["--file", str(csv_path)])

    with Session(engine) as session:
        suppliers = session.query(Supplier).all()
        assert len(suppliers) == 1
        assert suppliers[0].wholesale_discount_pct == 20


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_suppliers_csv
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(import_suppliers_csv, "SessionLocal", sessionmaker(bind=engine))

    csv_path = tmp_path / "suppliers.csv"
    _write_csv(csv_path, [["Big Wholesale Co", "", "", "", "active", "10", ""]])
    import_suppliers_csv.main(["--file", str(csv_path), "--dry-run"])

    with Session(engine) as session:
        assert session.query(Supplier).count() == 0


def test_invalid_account_status_is_skipped_not_fatal(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)

    from scripts import import_suppliers_csv
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(import_suppliers_csv, "SessionLocal", sessionmaker(bind=engine))

    csv_path = tmp_path / "suppliers.csv"
    _write_csv(csv_path, [
        ["Good Supplier", "", "", "", "active", "", ""],
        ["Bad Supplier", "", "", "", "not-a-real-status", "", ""],
    ])
    result = import_suppliers_csv.main(["--file", str(csv_path)])
    assert result == 0

    with Session(engine) as session:
        names = {s.name for s in session.query(Supplier).all()}
        assert names == {"Good Supplier"}


def test_missing_name_column_is_a_fatal_error(tmp_path):
    from scripts import import_suppliers_csv

    csv_path = tmp_path / "bad.csv"
    _write_csv(csv_path, [["x"]], header=["contact"])
    try:
        import_suppliers_csv.main(["--file", str(csv_path)])
        assert False, "expected SystemExit"
    except SystemExit as exc:
        assert "name" in str(exc)
