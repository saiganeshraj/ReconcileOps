import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from reconcileops.provider.client import ProviderClient
from reconcileops.storage.models import Base, ProviderConflict, ProviderEventModel

EVENT = {
    "event_id": "same",
    "payment_id": "P",
    "order_id": "O",
    "event_type": "capture",
    "currency": "INR",
    "amount": "100.00",
    "occurred_at": "2026-11-14T10:00:00Z",
}


def test_replayed_page_and_conflict_keep_original_event():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    def handler(request):
        if not request.url.params.get("cursor"):
            return httpx.Response(200, json={"events": [EVENT], "next_cursor": "second"})
        return httpx.Response(
            200, json={"events": [EVENT, {**EVENT, "amount": "200.00"}], "next_cursor": None}
        )

    with Session(engine) as db:
        run = ProviderClient(transport=httpx.MockTransport(handler)).sync_all_events(db)
        assert run.records_imported == 1
        assert run.records_duplicate == 1
        assert run.records_conflicted == 1
        assert str(db.get(ProviderEventModel, "same").amount) == "100.00"
        assert db.scalar(select(ProviderConflict)).incoming_json


def test_429_retry_after_is_capped_and_then_recovers(monkeypatch):
    calls = []
    delays = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3600"})
        return httpx.Response(200, json={"events": [], "next_cursor": None})

    monkeypatch.setattr("reconcileops.provider.client.time.sleep", delays.append)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = ProviderClient(
            transport=httpx.MockTransport(handler), retry_after_cap=0.1
        ).sync_all_events(db)
        assert run.status == "SUCCEEDED"
        assert run.retry_count == 1
        assert delays == [0.1]


def test_endless_empty_pages_have_a_hard_bound():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"events": [], "next_cursor": str(len(calls))})

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        with pytest.raises(ValueError, match="page bound"):
            ProviderClient(transport=httpx.MockTransport(handler), max_pages=2).sync_all_events(db)
    assert len(calls) == 2
