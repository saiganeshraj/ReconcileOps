"""Delete only seed-owned records, preserving unrelated data and immutable runs."""

from contextlib import ExitStack

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from reconcileops.config import settings
from reconcileops.storage.database import execution_lease, wait_for_lock
from reconcileops.storage.models import (
    AuditEvent,
    DemoOwnership,
    FindingModel,
    ImportBatch,
    OrderModel,
    ProviderConflict,
    ProviderEventModel,
    ReconciliationRun,
    RunAttempt,
    SnapshotMembership,
    SourceRow,
    SyncRun,
)


def reset_owned_demo(db: Session) -> dict:
    if not settings.DEMO_MODE:
        raise ValueError("Reset requires DEMO_MODE=true.")
    with ExitStack() as leases:
        acquired = leases.enter_context(execution_lease(db, "provider_sync"))
        if not acquired:
            raise ValueError("Provider sync is active. Wait before resetting.")
        wait_for_lock(db, "snapshot_publish")
        ownership = {}
        for record in db.scalars(select(DemoOwnership)):
            ownership.setdefault(record.entity_type, set()).add(record.entity_id)
        orders = ownership.get("order", set())
        events = ownership.get("event", set())
        runs = ownership.get("run", set())
        for run_id in sorted(runs):
            if not leases.enter_context(execution_lease(db, f"reconcile_run_{run_id}")):
                raise ValueError("A demo run is active. Wait before resetting.")
            run = db.get(ReconciliationRun, run_id)
            if run and run.status in {"QUEUED", "RUNNING"}:
                raise ValueError(
                    "A demo run is queued or running. Wait for completion before resetting."
                )
        db.execute(delete(FindingModel).where(FindingModel.run_id.in_(runs)))
        db.execute(delete(RunAttempt).where(RunAttempt.run_id.in_(runs)))
        db.execute(delete(SnapshotMembership).where(SnapshotMembership.run_id.in_(runs)))
        db.execute(delete(ReconciliationRun).where(ReconciliationRun.id.in_(runs)))
        used = db.scalar(
            select(SnapshotMembership.id)
            .where(
                (
                    (SnapshotMembership.entity_type == "order")
                    & SnapshotMembership.entity_id.in_(orders)
                )
                | (
                    (SnapshotMembership.entity_type == "event")
                    & SnapshotMembership.entity_id.in_(events)
                )
            )
            .limit(1)
        )
        if used:
            raise ValueError(
                "Seeded sources are referenced by immutable runs. Reset refused; use a separate demo database for a fresh start."
            )
        db.execute(delete(OrderModel).where(OrderModel.order_id.in_(orders)))
        db.execute(delete(ProviderEventModel).where(ProviderEventModel.event_id.in_(events)))
        batches = ownership.get("batch", set())
        protected = set(
            db.scalars(
                select(OrderModel.import_batch_id).where(OrderModel.import_batch_id.in_(batches))
            )
        )
        batches -= protected
        db.execute(delete(SourceRow).where(SourceRow.import_batch_id.in_(batches)))
        db.execute(delete(ImportBatch).where(ImportBatch.id.in_(batches)))
        syncs = ownership.get("sync", set())
        syncs -= set(
            db.scalars(
                select(ProviderEventModel.sync_run_id).where(
                    ProviderEventModel.sync_run_id.in_(syncs)
                )
            )
        )
        db.execute(delete(ProviderConflict).where(ProviderConflict.sync_run_id.in_(syncs)))
        db.execute(delete(SyncRun).where(SyncRun.id.in_(syncs)))
        for kind, ids in [
            ("order", orders),
            ("event", events),
            ("batch", batches),
            ("sync", syncs),
            ("run", runs),
        ]:
            db.execute(
                delete(DemoOwnership).where(
                    DemoOwnership.entity_type == kind, DemoOwnership.entity_id.in_(ids)
                )
            )
        db.add(
            AuditEvent(
                actor="demo_operator",
                action="RESET_OWNED_DEMO",
                entity_type="system",
                entity_id="seeded-fixture",
                note="Removed only explicitly seed-owned records; audit history retained.",
            )
        )
        db.commit()
        return {"orders_removed": len(orders), "events_removed": len(events)}
