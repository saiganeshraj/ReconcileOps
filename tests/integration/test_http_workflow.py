"""Real HTTP workflow with a real application and isolated SQL storage."""

import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from reconcileops.main import app
from reconcileops.provider.client import ProviderClient
from reconcileops.provider.mock_server import fault_state, mock_provider_app
from reconcileops.storage.database import get_db
from reconcileops.storage.models import Base
from reconcileops.storage.reconcile_service import execute_reconciliation_run


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def session_dependency():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = session_dependency
    from reconcileops.tasks.tasks import reconcile_task, sync_provider_task

    def run(run_id):
        with Session(engine) as session:
            execute_reconciliation_run(session, run_id)

    def sync(sync_run_id):
        class Adapter(ProviderClient):
            def _create_client(self):
                return TestClient(mock_provider_app)

        with Session(engine) as session:
            Adapter(backoff_factor=0, retry_after_cap=0).sync_all_events(session, sync_run_id)

    monkeypatch.setattr(reconcile_task, "delay", run)
    monkeypatch.setattr(sync_provider_task, "delay", sync)
    fault_state.mode = "NONE"
    with TestClient(app) as client:
        client.get("/")
        client.headers["X-CSRF-Token"] = client.cookies["reconcileops_csrf"]
        yield client
    app.dependency_overrides.clear()
    engine.dispose()


def test_http_import_sync_run_evidence_review_export(client):
    from pathlib import Path

    content = Path("fixtures/orders_baseline.csv").read_bytes()
    preview = client.post(
        "/api/imports/preview", files={"file": ("orders.csv", content, "text/csv")}
    )
    assert preview.status_code == 201, preview.text
    assert preview.json()["accepted_rows"] == 6
    assert client.post("/api/imports/" + preview.json()["batch_id"] + "/commit").status_code == 200
    sync = client.post("/api/syncs")
    assert sync.status_code == 202, sync.text
    assert client.get("/api/syncs/" + sync.json()["sync_id"]).json()["status"] == "SUCCEEDED"
    body = {"cutoff_time": "2026-11-16T12:00:00Z"}
    run = client.post("/api/runs", json=body, headers={"Idempotency-Key": "http-flow"})
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "SUCCEEDED"
    assert run.json()["findings_count"] == 7
    replay = client.post("/api/runs", json=body, headers={"Idempotency-Key": "http-flow"})
    assert replay.json()["id"] == run.json()["id"]
    findings = client.get("/api/findings", params={"order_id": "O102"}).json()
    finding = client.get("/api/findings/" + findings[0]["id"]).json()
    assert finding["source_records"]["orders"][0]["raw_content"]
    assert finding["source_records"]["events"][0]["amount"] == "250.00"
    url = "/api/findings/" + finding["id"]
    resolved = client.post(
        url + "/resolve",
        json={"note": "Checked synthetic provider evidence", "version": finding["version"]},
    )
    assert resolved.status_code == 200, resolved.text
    assert (
        client.post(
            url + "/reopen", json={"note": "Stale reviewer", "version": finding["version"]}
        ).status_code
        == 409
    )
    reopened = client.post(
        url + "/reopen",
        json={"note": "Second review requested", "version": resolved.json()["version"]},
    )
    assert reopened.status_code == 200
    assert len(client.get(url).json()["audit_events"]) == 2
    export = client.get("/api/runs/" + run.json()["id"] + "/findings.csv")
    assert len(list(csv.DictReader(io.StringIO(export.text)))) == 7
    assert client.get("/api/overview").json()["captured_by_currency"] == {
        "INR": "590.00",
        "USD": "100.00",
    }


def test_cross_origin_browser_mutation_rejected(client):
    assert (
        client.post("/api/syncs", headers={"Origin": "https://untrusted.example"}).status_code
        == 403
    )
    assert client.post("/api/syncs", headers={"X-CSRF-Token": ""}).status_code == 403


def test_broker_failure_leaves_durable_queue_record(client, monkeypatch):
    from reconcileops.tasks.tasks import reconcile_task

    def unavailable(*args):
        raise ConnectionError("broker down")

    monkeypatch.setattr(reconcile_task, "delay", unavailable)
    result = client.post(
        "/api/runs",
        headers={"Idempotency-Key": "crash-before-enqueue"},
        json={"cutoff_time": "2026-11-16T12:00:00Z"},
    )
    assert result.status_code == 201
    assert result.json()["status"] == "QUEUED"
    assert client.get("/api/runs/" + result.json()["id"]).json()["attempts"] == []
