"""Recovery and dispatch mechanisms for durable runs and crash resiliency."""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from reconcileops.storage.database import execution_lease, get_db_context
from reconcileops.storage.models import ReconciliationRun, RunAttempt, SyncRun, utc_now
from reconcileops.tasks.tasks import reconcile_task, sync_provider_task

logger = logging.getLogger(__name__)


def dispatch_orphaned_queued_runs(session: Session, older_than_seconds: int = 15) -> list[str]:
    """Find committed runs stuck in QUEUED state (e.g. web process died before broker publish) and re-enqueue them."""
    threshold = utc_now() - timedelta(seconds=older_than_seconds)
    stmt = (
        select(ReconciliationRun)
        .where(
            ReconciliationRun.status == "QUEUED",
            ReconciliationRun.created_at <= threshold,
        )
        .order_by(ReconciliationRun.created_at)
        .limit(100)
    )
    stuck_runs = session.execute(stmt).scalars().all()
    dispatched_ids: list[str] = []

    for run in stuck_runs:
        logger.warning("Re-dispatching orphaned QUEUED run_id=%s", run.id)
        try:
            reconcile_task.delay(run.id)
            dispatched_ids.append(run.id)
        except Exception as exc:
            logger.error("Failed to re-enqueue run_id=%s: %s", run.id, exc)

    for sync in session.scalars(
        select(SyncRun)
        .where(SyncRun.status == "QUEUED", SyncRun.created_at <= threshold)
        .limit(100)
    ):
        try:
            sync_provider_task.delay(sync.id)
            dispatched_ids.append(sync.id)
        except Exception as exc:
            logger.error("Sync dispatch failed: %s", type(exc).__name__)
    return dispatched_ids


def recover_stale_running_runs(session: Session, stale_timeout_seconds: int = 300) -> list[str]:
    """Identify stale RUNNING runs whose workers may have crashed without updating status.

    Marks them as FAILED so they can be reviewed and retried safely.
    """
    threshold = utc_now() - timedelta(seconds=stale_timeout_seconds)
    stmt = select(ReconciliationRun).where(
        ReconciliationRun.status == "RUNNING",
        ReconciliationRun.started_at <= threshold,
    )
    stale_runs = session.execute(stmt.limit(100)).scalars().all()
    recovered_ids: list[str] = []

    for run in stale_runs:
        with execution_lease(session, f"reconcile_run_{run.id}") as acquired:
            if not acquired:
                continue
            session.refresh(run)
            if run.status != "RUNNING":
                continue
            _mark_stale(session, run, stale_timeout_seconds)
            recovered_ids.append(run.id)
            session.commit()

    for sync in session.scalars(
        select(SyncRun)
        .where(SyncRun.status == "RUNNING", SyncRun.started_at <= threshold)
        .limit(100)
    ):
        with execution_lease(session, "provider_sync") as acquired:
            if not acquired:
                continue
            session.refresh(sync)
            if sync.status == "RUNNING":
                sync.status = "FAILED"
                sync.failure_category = "WORKER_TIMEOUT_OR_CRASH"
                sync.finished_at = utc_now()
                recovered_ids.append(sync.id)
                session.commit()
    session.flush()
    return recovered_ids


def _mark_stale(session, run, stale_timeout_seconds):
    logger.warning("Recovering stale RUNNING run_id=%s (started_at=%s)", run.id, run.started_at)
    run.status = "FAILED"
    run.failure_category = "WORKER_TIMEOUT_OR_CRASH"
    run.error_message = (
        f"Execution timed out or worker crashed after {stale_timeout_seconds}s without completion."
    )
    run.finished_at = utc_now()

    # Update latest attempt
    latest_attempt = (
        session.execute(
            select(RunAttempt)
            .where(RunAttempt.run_id == run.id)
            .order_by(RunAttempt.attempt_number.desc())
        )
        .scalars()
        .first()
    )

    if latest_attempt and latest_attempt.status == "RUNNING":
        latest_attempt.status = "FAILED"
        latest_attempt.error_message = run.error_message
        latest_attempt.finished_at = utc_now()


if __name__ == "__main__":
    with get_db_context() as session:
        print(
            {
                "recovered": recover_stale_running_runs(session),
                "dispatched": dispatch_orphaned_queued_runs(session),
            }
        )
