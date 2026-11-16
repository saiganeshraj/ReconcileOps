"""Reconciliation run lifecycle, immutable snapshots, and execution service."""

import json
import uuid
from collections import defaultdict
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from reconcileops.domain.models import EventType, Order, ProviderEvent, ensure_utc
from reconcileops.engine.reconciler import RULE_VERSION, reconcile_snapshot
from reconcileops.storage.database import execution_lease, wait_for_lock
from reconcileops.storage.models import (
    AuditEvent,
    DemoOwnership,
    FindingModel,
    OrderModel,
    ProviderEventModel,
    ReconciliationRun,
    RunAttempt,
    SnapshotMembership,
    SyncRun,
    utc_now,
)


class IdempotencyConflictError(Exception):
    pass


def create_or_get_reconciliation_run(
    session: Session,
    cutoff_time: datetime,
    idempotency_key: str | None = None,
) -> tuple[ReconciliationRun, bool]:
    """Create a new reconciliation run or retrieve existing by idempotency key.

    Returns (run, created).
    Raises IdempotencyConflictError if the idempotency key was previously used with a different cutoff.
    """
    cutoff_time = ensure_utc(cutoff_time)
    if idempotency_key:
        wait_for_lock(session, f"request:{idempotency_key.strip()}")

    if idempotency_key:
        clean_key = idempotency_key.strip()
        existing = session.execute(
            select(ReconciliationRun).where(ReconciliationRun.idempotency_key == clean_key)
        ).scalar_one_or_none()

        if existing:
            # Compare the complete normalized timestamp, including microseconds.
            if ensure_utc(existing.cutoff_time) != cutoff_time:
                raise IdempotencyConflictError(
                    f"Idempotency key '{clean_key}' was already used with a different cutoff time "
                    f"({existing.cutoff_time.isoformat()} vs {cutoff_time.isoformat()})."
                )
            return existing, False

    new_run = ReconciliationRun(
        id=str(uuid.uuid4()),
        idempotency_key=idempotency_key.strip() if idempotency_key else None,
        cutoff_time=cutoff_time,
        rule_version=RULE_VERSION,
        status="QUEUED",
        created_at=utc_now(),
    )
    session.add(new_run)
    session.flush()
    wait_for_lock(session, "snapshot_publish")
    new_run.sync_high_water_mark = session.scalar(
        select(SyncRun.id)
        .where(SyncRun.status == "SUCCEEDED")
        .order_by(SyncRun.finished_at.desc())
        .limit(1)
    )
    session.flush()
    for entity_type, ids in (
        ("order", session.scalars(select(OrderModel.order_id)).all()),
        (
            "event",
            session.scalars(
                select(ProviderEventModel.event_id).where(
                    ProviderEventModel.occurred_at <= cutoff_time
                )
            ).all(),
        ),
    ):
        session.add_all(
            [
                SnapshotMembership(run_id=new_run.id, entity_type=entity_type, entity_id=value)
                for value in ids
            ]
        )
    new_run.snapshot_captured = True
    session.flush()
    members = session.scalars(
        select(SnapshotMembership).where(SnapshotMembership.run_id == new_run.id)
    ).all()
    owned = {(r.entity_type, r.entity_id) for r in session.scalars(select(DemoOwnership))}
    if members and all((m.entity_type, m.entity_id) in owned for m in members):
        session.add(DemoOwnership(entity_type="run", entity_id=new_run.id))
        session.flush()
    return new_run, True


def execute_reconciliation_run(
    session: Session,
    run_id: str,
    actor: str = "worker",
) -> ReconciliationRun:
    """Execute a reconciliation run with advisory locking, snapshot freezing, and atomic commit."""
    with execution_lease(session, f"reconcile_run_{run_id}") as acquired:
        if not acquired:
            return session.get(ReconciliationRun, run_id)
        return _execute_locked(session, run_id, actor)


def _execute_locked(session: Session, run_id: str, actor: str) -> ReconciliationRun:

    run = session.get(ReconciliationRun, run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    # If run has already succeeded, duplicate delivery is safe and returns existing result
    if run.status == "SUCCEEDED":
        return run
    unfinished = session.scalars(
        select(RunAttempt).where(RunAttempt.run_id == run_id, RunAttempt.status == "RUNNING")
    ).all()
    for previous in unfinished:
        previous.status = "FAILED"
        previous.finished_at = utc_now()
        previous.error_message = (
            "Worker delivery ended without completing; execution lease was released."
        )
    if run.attempt_count >= run.max_attempts:
        run.status = "FAILED"
        run.failure_category = "ATTEMPTS_EXHAUSTED"
        run.finished_at = utc_now()
        session.commit()
        return run
    # Create new attempt record
    run.attempt_count += 1
    attempt = RunAttempt(
        run_id=run.id,
        attempt_number=run.attempt_count,
        status="RUNNING",
        started_at=utc_now(),
    )
    session.add(attempt)
    run.status = "RUNNING"
    run.started_at = utc_now()
    session.commit()

    try:
        if run.rule_version != RULE_VERSION:
            raise ValueError("This worker does not support the stored rule version.")
        if not run.snapshot_captured:
            raise ValueError(
                "Run has no captured snapshot. Create a new run rather than reconstructing old inputs."
            )

        # Load snapshot entities
        order_ids_stmt = select(SnapshotMembership.entity_id).where(
            SnapshotMembership.run_id == run.id,
            SnapshotMembership.entity_type == "order",
        )
        snapshotted_order_ids = set(session.execute(order_ids_stmt).scalars().all())

        event_ids_stmt = select(SnapshotMembership.entity_id).where(
            SnapshotMembership.run_id == run.id,
            SnapshotMembership.entity_type == "event",
        )
        snapshotted_event_ids = set(session.execute(event_ids_stmt).scalars().all())

        db_orders = (
            session.execute(
                select(OrderModel).where(OrderModel.order_id.in_(snapshotted_order_ids))
            )
            .scalars()
            .all()
            if snapshotted_order_ids
            else []
        )

        db_events = (
            session.execute(
                select(ProviderEventModel).where(
                    ProviderEventModel.event_id.in_(snapshotted_event_ids)
                )
            )
            .scalars()
            .all()
            if snapshotted_event_ids
            else []
        )

        if len(db_orders) != len(snapshotted_order_ids) or len(db_events) != len(
            snapshotted_event_ids
        ):
            raise ValueError(
                "Snapshot source records are missing. Restore the immutable source data before retrying."
            )

        # Convert to pure domain models
        domain_orders = [
            Order(
                order_id=o.order_id,
                customer_ref=o.customer_ref,
                currency=o.currency,
                expected_amount=o.expected_amount,
                ordered_at=ensure_utc(o.ordered_at),
                payment_due_at=ensure_utc(o.payment_due_at),
            )
            for o in db_orders
        ]

        domain_events = [
            ProviderEvent(
                event_id=e.event_id,
                payment_id=e.payment_id,
                order_id=e.order_id,
                event_type=EventType(e.event_type),
                currency=e.currency,
                amount=e.amount,
                occurred_at=ensure_utc(e.occurred_at),
            )
            for e in db_events
        ]

        # Execute pure reconciliation
        recon_result = reconcile_snapshot(domain_orders, domain_events, run.cutoff_time)

        # Clear prior findings for this run if retrying
        session.execute(delete(FindingModel).where(FindingModel.run_id == run.id))

        # Insert findings
        by_order = defaultdict(list)
        by_payment = defaultdict(list)
        for event in db_events:
            by_order[event.order_id].append(event)
            by_payment[event.payment_id].append(event)
        for f in recon_result.findings:
            related_orders = (
                {f.entity_id}
                if f.entity_type == "order"
                else {e.order_id for e in by_payment[f.entity_id]}
            )
            related_events = [e for order_id in related_orders for e in by_order[order_id]]
            f.evidence["source_event_ids"] = [e.event_id for e in related_events]
            f.evidence["source_order_ids"] = sorted(related_orders)
            finding_model = FindingModel(
                run_id=run.id,
                rule_code=f.rule_code.value,
                entity_type=f.entity_type,
                entity_id=f.entity_id,
                currency=f.currency,
                expected_amount=f.expected_amount,
                actual_amount=f.actual_amount,
                discrepancy_amount=f.discrepancy_amount,
                evidence_json=json.dumps(f.evidence),
                status="OPEN",
                version=1,
            )
            session.add(finding_model)

        run.orders_count = recon_result.orders_evaluated
        run.events_count = recon_result.events_evaluated
        run.findings_count = recon_result.total_findings
        run.status = "SUCCEEDED"
        run.finished_at = utc_now()
        run.error_message = None
        run.failure_category = None

        attempt.status = "SUCCEEDED"
        attempt.finished_at = utc_now()

        # Record audit event
        audit = AuditEvent(
            actor=actor,
            action="EXECUTE_RECONCILIATION_RUN",
            entity_type="reconciliation_run",
            entity_id=run.id,
            before_state="RUNNING",
            after_state="SUCCEEDED",
            note=f"Reconciliation completed with {run.findings_count} findings across {run.orders_count} orders.",
        )
        session.add(audit)
        session.flush()
        session.commit()
        return run

    except Exception as exc:
        session.rollback()
        run = session.get(ReconciliationRun, run_id)
        attempt = session.get(RunAttempt, attempt.id)
        run.status = "FAILED"
        run.finished_at = utc_now()
        run.error_message = str(exc)
        run.failure_category = type(exc).__name__

        attempt.status = "FAILED"
        attempt.finished_at = utc_now()
        attempt.error_message = str(exc)

        session.flush()
        session.commit()
        raise
