"""REST API routes for ReconcileOps."""

import json
import logging

from fastapi import APIRouter, Depends, File, Header, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from reconcileops.config import settings
from reconcileops.storage.csv_importer import (
    CSVValidationError,
    commit_import_batch,
    process_csv_preview,
)
from reconcileops.storage.database import get_db
from reconcileops.storage.finding_service import (
    OptimisticLockError,
    reopen_finding,
    resolve_finding,
)
from reconcileops.storage.models import (
    AuditEvent,
    FindingModel,
    ImportBatch,
    ProviderConflict,
    ProviderEventModel,
    ReconciliationRun,
    RunAttempt,
    SourceRow,
    SyncRun,
    utc_now,
)
from reconcileops.storage.reconcile_service import (
    IdempotencyConflictError,
    create_or_get_reconciliation_run,
)
from reconcileops.web.export import generate_findings_csv

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/api")


def validate_page(limit: int, offset: int):
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(400, "limit must be between 1 and 500; offset must be nonnegative.")


# Request/Response schemas
class ReviewActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(..., min_length=1, description="Reason for resolution or reopening")
    version: int = Field(..., ge=1, description="Optimistic locking version")


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cutoff_time: str = Field(..., description="ISO 8601 UTC timestamp for cutoff")


# Imports endpoints
@api_router.post("/imports/preview", status_code=status.HTTP_201_CREATED)
async def upload_csv_preview(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    content = await file.read(settings.MAX_UPLOAD_BYTES + 1)
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_BYTES} bytes (5 MB).",
        )

    try:
        batch = process_csv_preview(db, file.filename, content)
        db.commit()
    except CSVValidationError as err:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(err)) from err
    except Exception as err:
        db.rollback()
        logger.exception("Unexpected error parsing CSV preview")
        raise HTTPException(
            status_code=500, detail="Failed to process CSV. Check server logs."
        ) from err

    # Fetch sample source rows
    sample_rows = (
        db.execute(
            select(SourceRow)
            .where(SourceRow.import_batch_id == batch.id)
            .order_by(SourceRow.row_number)
            .limit(20)
        )
        .scalars()
        .all()
    )

    return {
        "batch_id": batch.id,
        "filename": batch.filename,
        "status": batch.status,
        "total_rows": batch.total_rows,
        "accepted_rows": batch.accepted_rows,
        "unchanged_rows": batch.unchanged_rows,
        "rejected_rows": batch.rejected_rows,
        "conflict_rows": batch.conflict_rows,
        "sample_rows": [
            {
                "row_number": r.row_number,
                "order_id": r.order_id,
                "disposition": r.disposition,
                "error_message": r.error_message,
            }
            for r in sample_rows
        ],
    }


@api_router.post("/imports/{batch_id}/commit")
def commit_batch_endpoint(
    batch_id: str,
    db: Session = Depends(get_db),
):
    try:
        batch = commit_import_batch(db, batch_id, actor="operator")
        db.commit()
        return {
            "batch_id": batch.id,
            "status": batch.status,
            "accepted_rows": batch.accepted_rows,
            "committed_at": batch.committed_at.isoformat() if batch.committed_at else None,
        }
    except ValueError as err:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(err)) from err


@api_router.get("/imports")
def list_import_batches(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    validate_page(limit, offset)
    batches = (
        db.execute(
            select(ImportBatch).order_by(ImportBatch.created_at.desc()).offset(offset).limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": b.id,
            "filename": b.filename,
            "status": b.status,
            "total_rows": b.total_rows,
            "accepted_rows": b.accepted_rows,
            "unchanged_rows": b.unchanged_rows,
            "rejected_rows": b.rejected_rows,
            "conflict_rows": b.conflict_rows,
            "created_at": b.created_at.isoformat(),
            "committed_at": b.committed_at.isoformat() if b.committed_at else None,
        }
        for b in batches
    ]


# Provider Sync endpoints
@api_router.post("/syncs", status_code=status.HTTP_202_ACCEPTED)
def trigger_provider_sync(db: Session = Depends(get_db)):
    from reconcileops.tasks.tasks import sync_provider_task

    sync_run = SyncRun(status="QUEUED", started_at=utc_now())
    db.add(sync_run)
    db.commit()

    # The queued database record is durable even if broker publication fails.
    try:
        sync_provider_task.delay(sync_run_id=sync_run.id)
    except Exception as exc:
        logger.warning("Sync remains QUEUED; use recovery dispatcher: %s", type(exc).__name__)

    db.refresh(sync_run)
    return {"sync_id": sync_run.id, "status": sync_run.status}


@api_router.get("/syncs/{sync_id}")
def get_sync_status(sync_id: str, db: Session = Depends(get_db)):
    sync_run = db.get(SyncRun, sync_id)
    if not sync_run:
        raise HTTPException(status_code=404, detail="Sync run not found")
    return {
        "id": sync_run.id,
        "status": sync_run.status,
        "started_at": sync_run.started_at.isoformat() if sync_run.started_at else None,
        "finished_at": sync_run.finished_at.isoformat() if sync_run.finished_at else None,
        "records_fetched": sync_run.records_fetched,
        "records_imported": sync_run.records_imported,
        "records_duplicate": sync_run.records_duplicate,
        "records_conflicted": sync_run.records_conflicted,
        "retry_count": sync_run.retry_count,
        "retry_log": json.loads(sync_run.retry_log_json),
        "conflicts": [
            {
                "event_id": c.event_id,
                "original_hash": c.original_hash,
                "incoming_hash": c.incoming_hash,
                "incoming": json.loads(c.incoming_json),
            }
            for c in db.scalars(
                select(ProviderConflict).where(ProviderConflict.sync_run_id == sync_id).limit(100)
            )
        ],
        "failure_category": sync_run.failure_category,
        "error_message": sync_run.error_message,
    }


# Reconciliation Runs endpoints
@api_router.post("/runs")
def create_reconciliation_run_endpoint(
    req: CreateRunRequest,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    from reconcileops.tasks.tasks import reconcile_task

    try:
        from reconcileops.storage.csv_importer import parse_timestamp_with_tz

        cutoff = parse_timestamp_with_tz(req.cutoff_time, "cutoff_time")
        if not idempotency_key or not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ValueError("Idempotency-Key header required (1–128 characters).")
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Invalid cutoff_time: {err}") from err

    try:
        run, created = create_or_get_reconciliation_run(
            db, cutoff_time=cutoff, idempotency_key=idempotency_key
        )
        db.commit()
    except IdempotencyConflictError as err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err)) from err

    if created:
        response.status_code = status.HTTP_201_CREATED
        # Enqueue Celery task, with synchronous execution fallback
        try:
            reconcile_task.delay(run.id)
        except Exception as exc:
            logger.warning("Run remains QUEUED; use recovery dispatcher: %s", type(exc).__name__)
    else:
        response.status_code = status.HTTP_200_OK

    db.refresh(run)
    return {
        "id": run.id,
        "idempotency_key": run.idempotency_key,
        "cutoff_time": run.cutoff_time.isoformat(),
        "status": run.status,
        "created": created,
        "orders_count": run.orders_count,
        "events_count": run.events_count,
        "findings_count": run.findings_count,
    }


@api_router.get("/runs")
def list_runs(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    validate_page(limit, offset)
    runs = (
        db.execute(
            select(ReconciliationRun)
            .order_by(ReconciliationRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "idempotency_key": r.idempotency_key,
            "cutoff_time": r.cutoff_time.isoformat(),
            "status": r.status,
            "attempt_count": r.attempt_count,
            "orders_count": r.orders_count,
            "events_count": r.events_count,
            "findings_count": r.findings_count,
            "failure_category": r.failure_category,
            "created_at": r.created_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in runs
    ]


@api_router.get("/runs/{run_id}")
def get_run_detail(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ReconciliationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    attempts = (
        db.execute(
            select(RunAttempt)
            .where(RunAttempt.run_id == run.id)
            .order_by(RunAttempt.attempt_number)
        )
        .scalars()
        .all()
    )

    return {
        "id": run.id,
        "idempotency_key": run.idempotency_key,
        "cutoff_time": run.cutoff_time.isoformat(),
        "rule_version": run.rule_version,
        "sync_high_water_mark": run.sync_high_water_mark,
        "status": run.status,
        "attempt_count": run.attempt_count,
        "orders_count": run.orders_count,
        "events_count": run.events_count,
        "findings_count": run.findings_count,
        "failure_category": run.failure_category,
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "attempts": [
            {
                "attempt_number": a.attempt_number,
                "status": a.status,
                "started_at": a.started_at.isoformat(),
                "finished_at": a.finished_at.isoformat() if a.finished_at else None,
                "error_message": a.error_message,
            }
            for a in attempts
        ],
    }


@api_router.post("/runs/{run_id}/retry")
def retry_failed_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ReconciliationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    from reconcileops.storage.database import execution_lease

    with execution_lease(db, f"reconcile_run_{run_id}") as acquired:
        db.refresh(run)
        if not acquired or run.status != "FAILED" or run.attempt_count >= run.max_attempts:
            raise HTTPException(
                status_code=409, detail="Only failed runs below the attempt limit can be retried."
            )
        run.status = "QUEUED"
        db.commit()

    from reconcileops.tasks.tasks import reconcile_task

    try:
        reconcile_task.delay(run.id)
    except Exception as exc:
        logger.warning("Retry remains QUEUED; use recovery dispatcher: %s", type(exc).__name__)

    db.refresh(run)
    return {"id": run.id, "status": run.status, "attempt_count": run.attempt_count}


# Findings & Review endpoints
@api_router.get("/findings")
def list_findings(
    run_id: str | None = None,
    rule_code: str | None = None,
    status: str | None = None,
    order_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    validate_page(limit, offset)
    stmt = select(FindingModel)
    if run_id:
        stmt = stmt.where(FindingModel.run_id == run_id)
    if rule_code:
        stmt = stmt.where(FindingModel.rule_code == rule_code)
    if status:
        stmt = stmt.where(FindingModel.status == status)
    if order_id:
        matching_payment = select(ProviderEventModel.payment_id).where(
            ProviderEventModel.order_id == order_id
        )
        stmt = stmt.where(
            (FindingModel.entity_id == order_id)
            | (
                (FindingModel.entity_type == "payment")
                & FindingModel.entity_id.in_(matching_payment)
            )
        )

    stmt = stmt.order_by(FindingModel.created_at.desc()).offset(offset).limit(limit)
    findings = db.execute(stmt).scalars().all()

    return [
        {
            "id": f.id,
            "run_id": f.run_id,
            "rule_code": f.rule_code,
            "entity_type": f.entity_type,
            "entity_id": f.entity_id,
            "currency": f.currency,
            "expected_amount": str(f.expected_amount) if f.expected_amount is not None else None,
            "actual_amount": str(f.actual_amount) if f.actual_amount is not None else None,
            "discrepancy_amount": str(f.discrepancy_amount)
            if f.discrepancy_amount is not None
            else None,
            "status": f.status,
            "version": f.version,
            "resolution_note": f.resolution_note,
            "created_at": f.created_at.isoformat(),
        }
        for f in findings
    ]


@api_router.get("/findings/{finding_id}")
def get_finding_detail(finding_id: str, db: Session = Depends(get_db)):
    finding = db.get(FindingModel, finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")

    audits = (
        db.execute(
            select(AuditEvent)
            .where(AuditEvent.entity_id == finding.id)
            .order_by(AuditEvent.timestamp.desc())
        )
        .scalars()
        .all()
    )
    source_order_ids = finding.evidence.get("source_order_ids", [])
    source_event_ids = finding.evidence.get("source_event_ids", [])
    source_rows = db.scalars(
        select(SourceRow).where(
            SourceRow.order_id.in_(source_order_ids), SourceRow.disposition == "ACCEPTED"
        )
    ).all()
    source_events = db.scalars(
        select(ProviderEventModel).where(ProviderEventModel.event_id.in_(source_event_ids))
    ).all()

    return {
        "id": finding.id,
        "run_id": finding.run_id,
        "rule_code": finding.rule_code,
        "entity_type": finding.entity_type,
        "entity_id": finding.entity_id,
        "currency": finding.currency,
        "expected_amount": str(finding.expected_amount)
        if finding.expected_amount is not None
        else None,
        "actual_amount": str(finding.actual_amount) if finding.actual_amount is not None else None,
        "discrepancy_amount": str(finding.discrepancy_amount)
        if finding.discrepancy_amount is not None
        else None,
        "evidence": finding.evidence,
        "source_records": {
            "orders": [
                {
                    "source_row_id": r.id,
                    "batch_id": r.import_batch_id,
                    "row_number": r.row_number,
                    "raw_content": r.raw_content,
                }
                for r in source_rows
            ],
            "events": [
                {
                    "event_id": e.event_id,
                    "payment_id": e.payment_id,
                    "order_id": e.order_id,
                    "event_type": e.event_type,
                    "currency": e.currency,
                    "amount": str(e.amount),
                    "occurred_at": e.occurred_at.isoformat(),
                    "sync_run_id": e.sync_run_id,
                }
                for e in source_events
            ],
        },
        "status": finding.status,
        "version": finding.version,
        "resolution_note": finding.resolution_note,
        "created_at": finding.created_at.isoformat(),
        "updated_at": finding.updated_at.isoformat(),
        "audit_events": [
            {
                "timestamp": a.timestamp.isoformat(),
                "actor": a.actor,
                "action": a.action,
                "before_state": a.before_state,
                "after_state": a.after_state,
                "note": a.note,
            }
            for a in audits
        ],
    }


@api_router.post("/findings/{finding_id}/resolve")
def resolve_finding_endpoint(
    finding_id: str,
    req: ReviewActionRequest,
    db: Session = Depends(get_db),
):
    try:
        finding = resolve_finding(
            db,
            finding_id=finding_id,
            note=req.note,
            expected_version=req.version,
            actor="operator",
        )
        db.commit()
        return {
            "id": finding.id,
            "status": finding.status,
            "version": finding.version,
            "resolution_note": finding.resolution_note,
        }
    except (OptimisticLockError, StaleDataError) as err:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err)) from err
    except ValueError as err:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(err)) from err


@api_router.post("/findings/{finding_id}/reopen")
def reopen_finding_endpoint(
    finding_id: str,
    req: ReviewActionRequest,
    db: Session = Depends(get_db),
):
    try:
        finding = reopen_finding(
            db,
            finding_id=finding_id,
            note=req.note,
            expected_version=req.version,
            actor="operator",
        )
        db.commit()
        return {
            "id": finding.id,
            "status": finding.status,
            "version": finding.version,
            "resolution_note": finding.resolution_note,
        }
    except (OptimisticLockError, StaleDataError) as err:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err)) from err
    except ValueError as err:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(err)) from err


# CSV Export
@api_router.get("/runs/{run_id}/findings.csv")
def export_run_findings_csv(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ReconciliationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    findings = (
        db.execute(
            select(FindingModel)
            .where(FindingModel.run_id == run_id)
            .order_by(FindingModel.created_at)
        )
        .scalars()
        .all()
    )

    csv_content = generate_findings_csv(findings)
    filename = f"findings_run_{run_id}.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
