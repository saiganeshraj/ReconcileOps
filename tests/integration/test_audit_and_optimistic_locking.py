"""Integration tests for audit events and optimistic concurrency control."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from reconcileops.storage.finding_service import (
    OptimisticLockError,
    reopen_finding,
    resolve_finding,
)
from reconcileops.storage.models import (
    AuditEvent,
    Base,
    FindingModel,
    ReconciliationRun,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_finding_resolve_and_reopen_audit_flow(db_session: Session):
    run = ReconciliationRun(
        id="RUN-1",
        cutoff_time=datetime.now(UTC),
        rule_version="1.0.0",
        status="SUCCEEDED",
    )
    db_session.add(run)

    finding = FindingModel(
        id="F-1",
        run_id="RUN-1",
        rule_code="AMOUNT_MISMATCH",
        entity_type="order",
        entity_id="ORD-1",
        currency="INR",
        expected_amount=Decimal("100.00"),
        actual_amount=Decimal("80.00"),
        discrepancy_amount=Decimal("-20.00"),
        evidence_json='{"reason": "Underpaid"}',
        status="OPEN",
        version=1,
    )
    db_session.add(finding)
    db_session.commit()

    # 1. Resolve finding
    resolved = resolve_finding(
        db_session,
        finding_id="F-1",
        note="Approved concession of 20 INR per ticket #1234",
        expected_version=1,
        actor="alice_ops",
    )
    assert resolved.status == "RESOLVED"
    assert resolved.version == 2
    assert resolved.resolution_note == "Approved concession of 20 INR per ticket #1234"

    # 2. Concurrent operator tries to modify using stale version 1 -> Must raise OptimisticLockError
    with pytest.raises(OptimisticLockError, match="modified by another operator"):
        resolve_finding(
            db_session,
            finding_id="F-1",
            note="Conflicting resolve",
            expected_version=1,
            actor="bob_ops",
        )

    # 3. Reopen finding with valid version 2
    reopened = reopen_finding(
        db_session,
        finding_id="F-1",
        note="Reopening: customer disputed concession",
        expected_version=2,
        actor="manager_carol",
    )
    assert reopened.status == "REOPENED"
    assert reopened.version == 3

    # Check audit events recorded
    audits = (
        db_session.execute(
            select(AuditEvent).where(AuditEvent.entity_id == "F-1").order_by(AuditEvent.timestamp)
        )
        .scalars()
        .all()
    )
    assert len(audits) == 2
    assert audits[0].action == "RESOLVE_FINDING"
    assert audits[0].actor == "alice_ops"
    assert audits[1].action == "REOPEN_FINDING"
    assert audits[1].actor == "manager_carol"
