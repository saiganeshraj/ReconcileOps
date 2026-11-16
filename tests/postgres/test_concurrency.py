"""Concurrency tests exercise PostgreSQL advisory locks and database CAS updates."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from reconcileops.storage.database import execution_lease
from reconcileops.storage.finding_service import resolve_finding
from reconcileops.storage.models import AuditEvent, FindingModel, ReconciliationRun, RunAttempt
from reconcileops.storage.reconcile_service import (
    create_or_get_reconciliation_run,
    execute_reconciliation_run,
)
from reconcileops.tasks.recovery import recover_stale_running_runs

pytestmark = pytest.mark.postgres
CUTOFF = datetime(2026, 11, 16, 12, tzinfo=UTC)


def test_concurrent_duplicate_requests_create_one_run(pg_engine):
    barrier = Barrier(6)

    def create():
        with Session(pg_engine) as db:
            barrier.wait(timeout=10)
            run, created = create_or_get_reconciliation_run(db, CUTOFF, "same-key")
            run_id = run.id
            db.commit()
            return run_id, created

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: create(), range(6)))
    assert len({value[0] for value in results}) == 1
    assert sum(value[1] for value in results) == 1
    with Session(pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(ReconciliationRun)) == 1


def test_concurrent_deliveries_execute_one_attempt(pg_engine):
    with Session(pg_engine) as db:
        run, _ = create_or_get_reconciliation_run(db, CUTOFF)
        run_id = run.id
        db.commit()
    barrier = Barrier(4)

    def execute():
        with Session(pg_engine) as db:
            barrier.wait(timeout=10)
            execute_reconciliation_run(db, run_id)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: execute(), range(4)))
    with Session(pg_engine) as db:
        assert db.get(ReconciliationRun, run_id).status == "SUCCEEDED"
        assert db.scalar(select(func.count()).select_from(RunAttempt)) == 1


def test_stale_recovery_checks_live_worker_lock(pg_engine):
    with Session(pg_engine) as db:
        run, _ = create_or_get_reconciliation_run(db, CUTOFF)
        run.status = "RUNNING"
        run.started_at = datetime.now(UTC) - timedelta(hours=1)
        run_id = run.id
        db.add(RunAttempt(run_id=run_id, attempt_number=1, status="RUNNING"))
        db.commit()
    with Session(pg_engine) as worker, Session(pg_engine) as recovery:
        with execution_lease(worker, f"reconcile_run_{run_id}") as acquired:
            assert acquired
            assert recover_stale_running_runs(recovery) == []
        assert recover_stale_running_runs(recovery) == [run_id]
        recovery.commit()
        assert recovery.get(ReconciliationRun, run_id).status == "FAILED"


def test_two_reviewers_cannot_overwrite_using_stale_identity_map(pg_engine):
    with Session(pg_engine) as db:
        run, _ = create_or_get_reconciliation_run(db, CUTOFF)
        db.add(
            FindingModel(
                id="finding",
                run_id=run.id,
                rule_code="MISSING_PAYMENT",
                entity_type="order",
                entity_id="O",
                evidence_json="{}",
                status="OPEN",
            )
        )
        db.commit()
    with Session(pg_engine) as alice, Session(pg_engine) as bob:
        # Both sessions deliberately keep stale loaded objects.
        a = alice.get(FindingModel, "finding")
        b = bob.get(FindingModel, "finding")
        assert a.version == b.version == 1
        resolve_finding(alice, "finding", "Alice review", 1)
        alice.commit()
        with pytest.raises(StaleDataError):
            resolve_finding(bob, "finding", "Bob stale review", 1)
        bob.rollback()
    with Session(pg_engine) as db:
        assert db.get(FindingModel, "finding").resolution_note == "Alice review"
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.entity_type == "finding")
            )
            == 1
        )
