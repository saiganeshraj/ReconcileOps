"""Integration tests for CSV import validation, preview, and commit."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from reconcileops.storage.csv_importer import (
    CSVValidationError,
    commit_import_batch,
    process_csv_preview,
)
from reconcileops.storage.models import Base, OrderModel


@pytest.fixture
def db_session():
    # Use in-memory SQLite for rapid integration testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_csv_preview_and_commit_happy_path(db_session: Session):
    csv_data = (
        b"order_id,customer_ref,currency,expected_amount,ordered_at,payment_due_at\n"
        b"O100,CUST1,INR,100.00,2026-11-14T10:00:00Z,2026-11-15T10:00:00Z\n"
        b"O101,CUST2,USD,50.25,2026-11-14T11:00:00+00:00,2026-11-15T11:00:00+00:00\n"
    )

    batch = process_csv_preview(db_session, "orders_valid.csv", csv_data)
    assert batch.total_rows == 2
    assert batch.accepted_rows == 2
    assert batch.rejected_rows == 0
    assert batch.unchanged_rows == 0
    assert batch.conflict_rows == 0
    assert batch.status == "PENDING"

    committed_batch = commit_import_batch(db_session, batch.id)
    assert committed_batch.status == "COMMITTED"

    orders = db_session.execute(select(OrderModel)).scalars().all()
    assert len(orders) == 2
    o100 = db_session.get(OrderModel, "O100")
    assert o100 is not None
    assert o100.expected_amount == Decimal("100.00")
    assert o100.currency == "INR"


def test_csv_deduplication_and_conflict(db_session: Session):
    csv_1 = (
        b"order_id,customer_ref,currency,expected_amount,ordered_at,payment_due_at\n"
        b"O200,CUST1,INR,100.00,2026-11-14T10:00:00Z,2026-11-15T10:00:00Z\n"
    )

    batch1 = process_csv_preview(db_session, "batch1.csv", csv_1)
    commit_import_batch(db_session, batch1.id)

    # Second CSV with identical O200 (UNCHANGED) and modified O200 (CONFLICT)
    csv_repeat_identical = (
        b"order_id,customer_ref,currency,expected_amount,ordered_at,payment_due_at\n"
        b"O200,CUST1,INR,100.00,2026-11-14T10:00:00Z,2026-11-15T10:00:00Z\n"
    )
    batch2 = process_csv_preview(db_session, "batch2.csv", csv_repeat_identical)
    assert batch2.accepted_rows == 0
    assert batch2.unchanged_rows == 1
    assert batch2.conflict_rows == 0

    # Conflicting amount for O200
    csv_conflict = (
        b"order_id,customer_ref,currency,expected_amount,ordered_at,payment_due_at\n"
        b"O200,CUST1,INR,999.00,2026-11-14T10:00:00Z,2026-11-15T10:00:00Z\n"
    )
    batch3 = process_csv_preview(db_session, "batch3.csv", csv_conflict)
    assert batch3.conflict_rows == 1
    assert batch3.accepted_rows == 0


def test_csv_rejects_missing_headers_and_invalid_data(db_session: Session):
    csv_bad_headers = b"order_id,customer_ref\n1,2\n"
    with pytest.raises(CSVValidationError, match="Missing required CSV column"):
        process_csv_preview(db_session, "bad.csv", csv_bad_headers)

    # Invalid row: 3 decimal places amount, unparseable date
    csv_bad_rows = (
        b"order_id,customer_ref,currency,expected_amount,ordered_at,payment_due_at\n"
        b"O300,CUST,INR,100.555,2026-11-14T10:00:00Z,2026-11-15T10:00:00Z\n"
        b"O301,CUST,INR,50.00,invalid-date,2026-11-15T10:00:00Z\n"
    )
    batch = process_csv_preview(db_session, "bad_rows.csv", csv_bad_rows)
    assert batch.rejected_rows == 2
    assert batch.accepted_rows == 0

    # Committing entirely invalid batch must fail
    with pytest.raises(ValueError, match="Cannot commit import batch"):
        commit_import_batch(db_session, batch.id)
