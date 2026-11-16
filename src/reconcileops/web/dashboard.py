"""Server rendered operator workspace; all metrics come from persisted records."""

from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reconcileops.config import settings
from reconcileops.storage.database import get_db
from reconcileops.storage.models import (
    AuditEvent,
    FindingModel,
    ImportBatch,
    OrderModel,
    ProviderEventModel,
    ReconciliationRun,
    SourceRow,
    SyncRun,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/", include_in_schema=False)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={"demo_mode": settings.DEMO_MODE}
    )


@router.get("/api/overview")
def overview(db: Session = Depends(get_db)):
    amounts = db.execute(
        select(ProviderEventModel.currency, func.sum(ProviderEventModel.amount))
        .where(ProviderEventModel.event_type == "capture")
        .group_by(ProviderEventModel.currency)
    ).all()
    return {
        "orders": db.scalar(select(func.count()).select_from(OrderModel)),
        "events": db.scalar(select(func.count()).select_from(ProviderEventModel)),
        "open_findings": db.scalar(
            select(func.count()).select_from(FindingModel).where(FindingModel.status != "RESOLVED")
        ),
        "runs": db.scalar(select(func.count()).select_from(ReconciliationRun)),
        "captured_by_currency": {currency: str(amount) for currency, amount in amounts},
    }


@router.get("/api/audit")
def audit(limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(400, "Invalid pagination")
    return db.scalars(
        select(AuditEvent).order_by(AuditEvent.timestamp.desc()).offset(offset).limit(limit)
    ).all()


@router.get("/api/syncs")
def syncs(db: Session = Depends(get_db)):
    return db.scalars(select(SyncRun).order_by(SyncRun.created_at.desc()).limit(50)).all()


@router.get("/api/imports/{batch_id}/rows")
def import_rows(batch_id: str, limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    if not db.get(ImportBatch, batch_id):
        raise HTTPException(404, "Import not found")
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(400, "Invalid pagination")
    return db.scalars(
        select(SourceRow)
        .where(SourceRow.import_batch_id == batch_id)
        .order_by(SourceRow.row_number)
        .offset(offset)
        .limit(limit)
    ).all()


@router.post("/api/demo/fault")
def demo_fault(body: dict):
    if not settings.DEMO_MODE:
        raise HTTPException(403, "Demo controls are disabled")
    try:
        response = httpx.post(
            settings.MOCK_PROVIDER_URL + "/demo/fault",
            json=body,
            headers={"X-Demo-Token": settings.DEMO_TOKEN},
            timeout=5,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Mock provider control failed; check provider logs.") from exc
