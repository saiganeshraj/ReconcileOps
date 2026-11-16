"""Demo reset ownership must never spill into unrelated records."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from reconcileops.scripts_seed import own
from reconcileops.storage.csv_importer import commit_import_batch, process_csv_preview
from reconcileops.storage.demo import reset_owned_demo
from reconcileops.storage.models import Base, OrderModel, ReconciliationRun
from reconcileops.storage.reconcile_service import (
    create_or_get_reconciliation_run,
    execute_reconciliation_run,
)

CSV = b"order_id,customer_ref,currency,expected_amount,ordered_at,payment_due_at\nDEMO,C,INR,100.00,2026-11-14T10:00:00Z,2026-11-15T10:00:00Z\n"
CUTOFF = datetime(2026, 11, 16, 12, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def demo_order(db):
    batch = process_csv_preview(db, "seed.csv", CSV)
    commit_import_batch(db, batch.id)
    own(db, "order", ["DEMO"])
    own(db, "batch", [batch.id])
    db.commit()


def unrelated(db):
    db.add(
        OrderModel(
            order_id="UNRELATED",
            customer_ref="real-import",
            currency="USD",
            expected_amount=Decimal("30.00"),
            ordered_at=CUTOFF,
            payment_due_at=CUTOFF,
            canonical_hash="unrelated",
        )
    )
    db.commit()


def test_owned_reset_preserves_unrelated_order_and_run(db):
    demo_order(db)
    demo_run, _ = create_or_get_reconciliation_run(db, CUTOFF)
    execute_reconciliation_run(db, demo_run.id)
    unrelated(db)
    result = reset_owned_demo(db)
    assert result["orders_removed"] == 1
    assert db.get(OrderModel, "DEMO") is None
    assert db.get(OrderModel, "UNRELATED") is not None
    assert db.get(ReconciliationRun, demo_run.id) is None


def test_mixed_snapshot_is_protected_from_reset(db):
    demo_order(db)
    unrelated(db)
    run, _ = create_or_get_reconciliation_run(db, CUTOFF)
    execute_reconciliation_run(db, run.id)
    with pytest.raises(ValueError, match="immutable runs"):
        reset_owned_demo(db)
    db.rollback()
    assert db.get(OrderModel, "DEMO") is not None
    assert db.get(OrderModel, "UNRELATED") is not None
    assert db.get(ReconciliationRun, run.id).status == "SUCCEEDED"
