"""Integration tests for durable runs, idempotency, retry attempts, and snapshot freezing."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from reconcileops.storage.models import (
    Base,
    OrderModel,
    ProviderEventModel,
    RunAttempt,
    SnapshotMembership,
)
from reconcileops.storage.reconcile_service import (
    IdempotencyConflictError,
    create_or_get_reconciliation_run,
    execute_reconciliation_run,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_idempotency_key_behavior(db_session: Session):
    cutoff = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
    key = "idem-req-12345"

    # First call creates the run
    run1, created1 = create_or_get_reconciliation_run(db_session, cutoff, idempotency_key=key)
    assert created1 is True
    assert run1.idempotency_key == key

    # Reusing same key and same cutoff returns identical run
    run2, created2 = create_or_get_reconciliation_run(db_session, cutoff, idempotency_key=key)
    assert created2 is False
    assert run2.id == run1.id

    # Reusing same key with different cutoff raises conflict
    different_cutoff = cutoff + timedelta(hours=5)
    with pytest.raises(IdempotencyConflictError, match="different cutoff time"):
        create_or_get_reconciliation_run(db_session, different_cutoff, idempotency_key=key)


def test_reconciliation_run_execution_and_idempotent_redelivery(db_session: Session):
    cutoff = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
    ts = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)

    # Insert test order and event
    order = OrderModel(
        order_id="ORD-1",
        customer_ref="CUST-1",
        currency="INR",
        expected_amount=Decimal("100.00"),
        ordered_at=ts,
        payment_due_at=ts,
        canonical_hash="hash1",
    )
    db_session.add(order)

    event = ProviderEventModel(
        event_id="EV-1",
        payment_id="PAY-1",
        order_id="ORD-1",
        event_type="capture",
        currency="INR",
        amount=Decimal("100.00"),
        occurred_at=ts,
        canonical_hash="evhash1",
    )
    db_session.add(event)
    db_session.commit()

    run, _ = create_or_get_reconciliation_run(db_session, cutoff)
    executed_run = execute_reconciliation_run(db_session, run.id)

    assert executed_run.status == "SUCCEEDED"
    assert executed_run.findings_count == 0
    assert executed_run.attempt_count == 1

    # Check snapshots created
    memberships = (
        db_session.execute(select(SnapshotMembership).where(SnapshotMembership.run_id == run.id))
        .scalars()
        .all()
    )
    assert len(memberships) == 2  # 1 order, 1 event

    # Worker redelivery (simulated at-least-once message redelivery)
    redelivered_run = execute_reconciliation_run(db_session, run.id)
    assert redelivered_run.status == "SUCCEEDED"
    assert redelivered_run.attempt_count == 1  # Unchanged, did not duplicate execution


def test_retry_failed_run_preserves_attempt_history(db_session: Session):
    cutoff = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
    run, _ = create_or_get_reconciliation_run(db_session, cutoff)

    # Simulate attempt 1 that failed
    run.status = "FAILED"
    run.attempt_count = 1
    attempt1 = RunAttempt(
        run_id=run.id,
        attempt_number=1,
        status="FAILED",
        error_message="Simulated temporary database lock timeout",
    )
    db_session.add(attempt1)
    db_session.commit()

    # Retry the run
    retried_run = execute_reconciliation_run(db_session, run.id)
    assert retried_run.status == "SUCCEEDED"
    assert retried_run.attempt_count == 2

    # Verify both attempts are preserved in history
    attempts = (
        db_session.execute(
            select(RunAttempt)
            .where(RunAttempt.run_id == run.id)
            .order_by(RunAttempt.attempt_number)
        )
        .scalars()
        .all()
    )
    assert len(attempts) == 2
    assert attempts[0].status == "FAILED"
    assert attempts[1].status == "SUCCEEDED"
