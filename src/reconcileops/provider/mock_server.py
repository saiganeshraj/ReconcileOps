"""Mock Payment Provider API with cursor pagination and fault injection."""

import asyncio
import base64
import hashlib
import json
from copy import deepcopy
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel

from reconcileops.config import settings

mock_provider_app = FastAPI(
    title="ReconcileOps Mock Payment Provider",
    description="Simulated upstream payment gateway with pagination and fault injection.",
    version="1.0.0",
)


class FaultModeState:
    mode: str = "NONE"  # NONE, 429, 503_ONCE, TIMEOUT, 401
    has_triggered_503: bool = False


fault_state = FaultModeState()

# In-memory baseline provider events for the mock provider
DEFAULT_EVENTS = [
    {
        "event_id": "EV_100",
        "payment_id": "P100",
        "order_id": "O100",
        "event_type": "capture",
        "currency": "INR",
        "amount": "100.00",
        "occurred_at": "2026-11-14T10:00:00Z",
    },
    {
        "event_id": "EV_102",
        "payment_id": "P102",
        "order_id": "O102",
        "event_type": "capture",
        "currency": "INR",
        "amount": "250.00",
        "occurred_at": "2026-11-14T10:30:00Z",
    },
    {
        "event_id": "EV_103A",
        "payment_id": "P103_A",
        "order_id": "O103",
        "event_type": "capture",
        "currency": "INR",
        "amount": "100.00",
        "occurred_at": "2026-11-14T11:00:00Z",
    },
    {
        "event_id": "EV_103B",
        "payment_id": "P103_B",
        "order_id": "O103",
        "event_type": "capture",
        "currency": "INR",
        "amount": "100.00",
        "occurred_at": "2026-11-14T11:05:00Z",
    },
    {
        "event_id": "EV_104",
        "payment_id": "P104",
        "order_id": "O104",
        "event_type": "capture",
        "currency": "USD",
        "amount": "100.00",
        "occurred_at": "2026-11-14T12:00:00Z",
    },
    {
        "event_id": "EV_999",
        "payment_id": "P999",
        "order_id": "O999",
        "event_type": "capture",
        "currency": "INR",
        "amount": "40.00",
        "occurred_at": "2026-11-14T13:00:00Z",
    },
]

mock_events_store: list[dict[str, Any]] = [dict(e) for e in DEFAULT_EVENTS]
snapshots = {}


def encode_cursor(offset: int, snapshot: str) -> str:
    payload = json.dumps({"offset": offset, "snapshot": snapshot})
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8")


def decode_cursor(cursor: str | None) -> tuple[int, str] | None:
    if not cursor:
        return None
    try:
        payload = base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
        data = json.loads(payload)
        offset, snapshot = int(data["offset"]), data["snapshot"]
        if offset < 0 or snapshot not in snapshots:
            raise ValueError("Unknown snapshot or invalid offset")
        return offset, snapshot
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, "Invalid or expired pagination cursor.") from exc


class FaultConfigRequest(BaseModel):
    mode: str  # NONE, 429, 503_ONCE, TIMEOUT, 401
    retry_after: int = 1


@mock_provider_app.get("/health")
def health():
    return {"status": "ok", "provider": "mock-payment-gateway"}


@mock_provider_app.post("/demo/fault")
def set_fault_mode(
    req: FaultConfigRequest,
    x_demo_token: str = Header(..., alias="X-Demo-Token"),
):
    if not settings.DEMO_MODE or x_demo_token != settings.DEMO_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden: Valid demo token required")

    allowed = {"NONE", "429", "503_ONCE", "TIMEOUT", "401"}
    if req.mode.upper() not in allowed:
        raise HTTPException(status_code=400, detail=f"Mode must be one of {allowed}")

    fault_state.mode = req.mode.upper()
    fault_state.has_triggered_503 = False
    return {"status": "updated", "mode": fault_state.mode}


@mock_provider_app.post("/demo/reset")
def reset_provider(
    x_demo_token: str = Header(..., alias="X-Demo-Token"),
):
    if not settings.DEMO_MODE or x_demo_token != settings.DEMO_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden: Valid demo token required")

    global mock_events_store
    mock_events_store = [dict(e) for e in DEFAULT_EVENTS]
    fault_state.mode = "NONE"
    fault_state.has_triggered_503 = False
    return {"status": "reset_successful", "events_count": len(mock_events_store)}


@mock_provider_app.get("/events")
async def get_events(
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
):
    # Check fault modes
    if fault_state.mode == "401":
        raise HTTPException(status_code=401, detail="Unauthorized mock provider client")

    if fault_state.mode == "429":
        # Simulate rate limit with Retry-After header
        return Response(
            content=json.dumps({"detail": "Rate limit exceeded"}),
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": "1"},
        )

    if fault_state.mode == "503_ONCE":
        if not fault_state.has_triggered_503:
            fault_state.has_triggered_503 = True
            fault_state.mode = "NONE"  # One-time fault then self-clears
            raise HTTPException(status_code=503, detail="Temporary service outage (one-time test)")

    if fault_state.mode == "TIMEOUT":
        # Sleep for longer than client timeout
        await asyncio.sleep(15.0)

    position = decode_cursor(cursor)
    if position:
        offset, snapshot = position
    else:
        offset = 0
        snapshot = hashlib.sha256(
            json.dumps(mock_events_store, sort_keys=True).encode()
        ).hexdigest()
        snapshots[snapshot] = deepcopy(mock_events_store)
        if len(snapshots) > 128:
            del snapshots[next(iter(snapshots))]
    source = snapshots[snapshot]
    total = len(source)
    slice_events = source[offset : offset + limit]
    new_offset = offset + len(slice_events)

    next_cursor = encode_cursor(new_offset, snapshot) if new_offset < total else None

    return {
        "events": slice_events,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
        "total_available": total,
    }
