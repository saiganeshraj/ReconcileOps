"""Regression checks for gaps discovered in the inherited implementation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from reconcileops.domain.money import MoneyValidationError, parse_money
from reconcileops.provider.client import ProviderClient
from reconcileops.storage.csv_importer import (
    CSVValidationError,
    commit_import_batch,
    process_csv_preview,
)
from reconcileops.storage.models import (
    Base,
    FindingModel,
    OrderModel,
    ProviderEventModel,
    ReconciliationRun,
    RunAttempt,
    SyncRun,
)
from reconcileops.storage.reconcile_service import (
    IdempotencyConflictError,
    create_or_get_reconciliation_run,
    execute_reconciliation_run,
)

HEADER = "order_id,customer_ref,currency,expected_amount,ordered_at,payment_due_at\n"
ROW = "NEW,CUSTOMER,INR,100.00,2026-11-14T10:00:00Z,2026-11-15T10:00:00Z\n"
CUTOFF = datetime(2026, 11, 16, 12, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1000000000000.00"])
def test_money_rejects_nonfinite_and_overflow(value):
    with pytest.raises(MoneyValidationError):
        parse_money(value)


def test_reordered_columns_and_multiline_are_committed_correctly(db):
    content = 'currency,order_id,customer_ref,expected_amount,payment_due_at,ordered_at\nINR,NEW,"Customer\nTwo",100.00,2026-11-15T10:00:00Z,2026-11-14T10:00:00Z\n'
    batch = process_csv_preview(db, "orders.csv", content.encode())
    commit_import_batch(db, batch.id)
    assert db.get(OrderModel, "NEW").customer_ref == "Customer\nTwo"


def test_stale_preview_never_overwrites_existing_order(db):
    first = process_csv_preview(db, "first.csv", (HEADER + ROW).encode())
    stale = process_csv_preview(
        db, "second.csv", (HEADER + ROW.replace("100.00", "999.00")).encode()
    )
    commit_import_batch(db, first.id)
    commit_import_batch(db, stale.id)
    assert db.get(OrderModel, "NEW").expected_amount == Decimal("100.00")
    assert stale.conflict_rows == 1
    assert stale.status == "REJECTED"


@pytest.mark.parametrize(
    "body", [HEADER, HEADER + '"unterminated', HEADER.replace("customer_ref", "order_id") + ROW]
)
def test_malformed_csv_rejected(db, body):
    with pytest.raises(CSVValidationError):
        process_csv_preview(db, "bad.csv", body.encode())


def test_empty_snapshot_stays_empty_after_later_import(db):
    run, _ = create_or_get_reconciliation_run(db, CUTOFF, "empty-snapshot")
    db.commit()
    batch = process_csv_preview(db, "orders.csv", (HEADER + ROW).encode())
    commit_import_batch(db, batch.id)
    db.commit()
    execute_reconciliation_run(db, run.id)
    assert run.orders_count == 0
    assert run.findings_count == 0


def test_idempotency_compares_subsecond_changes(db):
    create_or_get_reconciliation_run(db, CUTOFF, "exact")
    with pytest.raises(IdempotencyConflictError):
        create_or_get_reconciliation_run(db, CUTOFF + timedelta(microseconds=1), "exact")


def test_sync_reuses_persisted_record_and_redelivery(db):
    queued = SyncRun(status="QUEUED")
    db.add(queued)
    db.commit()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"events": [], "next_cursor": None})

    client = ProviderClient(transport=httpx.MockTransport(handler))
    result = client.sync_all_events(db, queued.id)
    client.sync_all_events(db, queued.id)
    assert result.status == "SUCCEEDED"
    assert db.scalar(select(func.count()).select_from(SyncRun)) == 1
    assert len(calls) == 1


def test_failed_later_page_publishes_nothing(db):
    def handler(request):
        if request.url.params.get("cursor"):
            return httpx.Response(401)
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "event_id": "E",
                        "payment_id": "P",
                        "order_id": "O",
                        "event_type": "capture",
                        "currency": "INR",
                        "amount": "100.00",
                        "occurred_at": "2026-11-14T10:00:00Z",
                    }
                ],
                "next_cursor": "next",
            },
        )

    with pytest.raises(Exception, match="401"):
        ProviderClient(transport=httpx.MockTransport(handler)).sync_all_events(db)
    assert db.scalar(select(func.count()).select_from(ProviderEventModel)) == 0
    assert db.scalar(select(SyncRun)).status == "FAILED"


def test_failed_finding_write_rolls_back_and_preserves_attempt(db, monkeypatch):
    batch = process_csv_preview(db, "orders.csv", (HEADER + ROW).encode())
    commit_import_batch(db, batch.id)
    run, _ = create_or_get_reconciliation_run(db, CUTOFF)
    db.commit()
    from sqlalchemy import event

    def fail_insert(*args):
        raise RuntimeError("injected finding storage failure")

    event.listen(FindingModel, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError):
            execute_reconciliation_run(db, run.id)
    finally:
        event.remove(FindingModel, "before_insert", fail_insert)
    assert db.scalar(select(func.count()).select_from(FindingModel)) == 0
    assert db.get(ReconciliationRun, run.id).status == "FAILED"
    assert db.scalar(select(RunAttempt)).status == "FAILED"
    execute_reconciliation_run(db, run.id)
    assert db.get(ReconciliationRun, run.id).status == "SUCCEEDED"
    assert db.scalar(select(func.count()).select_from(RunAttempt)) == 2


def test_worker_loss_preserves_failed_attempt_before_redelivery(db):
    run, _ = create_or_get_reconciliation_run(db, CUTOFF)
    run.status = "RUNNING"
    run.attempt_count = 1
    db.add(RunAttempt(run_id=run.id, attempt_number=1, status="RUNNING"))
    db.commit()
    execute_reconciliation_run(db, run.id)
    attempts = db.scalars(
        select(RunAttempt).where(RunAttempt.run_id == run.id).order_by(RunAttempt.attempt_number)
    ).all()
    assert [attempt.status for attempt in attempts] == ["FAILED", "SUCCEEDED"]
    assert run.attempt_count == 2


def test_worker_redelivery_does_not_exceed_attempt_limit(db):
    run, _ = create_or_get_reconciliation_run(db, CUTOFF)
    run.status = "RUNNING"
    run.attempt_count = 3
    db.add(RunAttempt(run_id=run.id, attempt_number=3, status="RUNNING"))
    db.commit()
    execute_reconciliation_run(db, run.id)
    assert run.status == "FAILED"
    assert run.failure_category == "ATTEMPTS_EXHAUSTED"
    assert run.attempt_count == 3
    assert db.scalar(select(RunAttempt)).status == "FAILED"


def test_dispatcher_recovers_a_committed_run_after_publish_gap(db, monkeypatch):
    from reconcileops.storage.models import utc_now
    from reconcileops.tasks.recovery import dispatch_orphaned_queued_runs

    run, _ = create_or_get_reconciliation_run(db, CUTOFF)
    run.created_at = utc_now() - timedelta(seconds=60)
    run_id = run.id
    db.commit()
    engine = db.get_bind()

    def deliver(persisted_run_id):
        with Session(engine) as worker:
            execute_reconciliation_run(worker, persisted_run_id)

    monkeypatch.setattr("reconcileops.tasks.recovery.reconcile_task.delay", deliver)
    with Session(engine) as restarted_dispatcher:
        assert dispatch_orphaned_queued_runs(restarted_dispatcher) == [run_id]
        assert dispatch_orphaned_queued_runs(restarted_dispatcher) == []
    db.expire_all()
    assert db.get(ReconciliationRun, run_id).status == "SUCCEEDED"
    assert db.get(ReconciliationRun, run_id).attempt_count == 1
