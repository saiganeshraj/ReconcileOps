"""SQLAlchemy 2.0 ORM Models for ReconcileOps."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class DemoOwnership(Base):
    """Only explicitly seeded synthetic records are eligible for demo reset."""

    __tablename__ = "demo_ownership"
    entity_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(128), primary_key=True)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_rows: Mapped[int] = mapped_column(Integer, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0)
    conflict_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(32), default="PENDING", index=True
    )  # PENDING, COMMITTED, REJECTED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_rows: Mapped[list["SourceRow"]] = relationship(
        back_populates="import_batch", cascade="all, delete-orphan"
    )


class OrderModel(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    customer_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payment_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    import_batch_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("import_batches.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SourceRow(Base):
    __tablename__ = "source_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    import_batch_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("import_batches.id"), nullable=False, index=True
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    disposition: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # ACCEPTED, UNCHANGED, REJECTED, CONFLICT
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    import_batch: Mapped["ImportBatch"] = relationship(back_populates="source_rows")
    __table_args__ = (
        UniqueConstraint("import_batch_id", "row_number", name="uq_source_batch_row"),
    )


class ProviderEventModel(Base):
    __tablename__ = "provider_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payment_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # capture, refund
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sync_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("sync_runs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    status: Mapped[str] = mapped_column(
        String(32), default="QUEUED", index=True
    )  # QUEUED, RUNNING, SUCCEEDED, FAILED
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    records_fetched: Mapped[int] = mapped_column(Integer, default=0)
    records_imported: Mapped[int] = mapped_column(Integer, default=0)
    records_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    records_conflicted: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_log_json: Mapped[str] = mapped_column(Text, default="[]")
    high_water_mark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProviderConflict(Base):
    __tablename__ = "provider_conflicts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sync_run_id: Mapped[str] = mapped_column(ForeignKey("sync_runs.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(128))
    original_hash: Mapped[str] = mapped_column(String(64))
    incoming_hash: Mapped[str] = mapped_column(String(64))
    incoming_json: Mapped[str] = mapped_column(Text)


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True, index=True
    )
    cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    snapshot_captured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sync_high_water_mark: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="QUEUED", index=True
    )  # QUEUED, RUNNING, SUCCEEDED, FAILED
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    events_count: Mapped[int] = mapped_column(Integer, default=0)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[list["RunAttempt"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    findings: Mapped[list["FindingModel"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunAttempt(Base):
    __tablename__ = "run_attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("reconciliation_runs.id"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["ReconciliationRun"] = relationship(back_populates="attempts")
    __table_args__ = (UniqueConstraint("run_id", "attempt_number", name="uq_run_attempt_number"),)


class SnapshotMembership(Base):
    __tablename__ = "snapshot_memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("reconciliation_runs.id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)  # 'order', 'event'
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("run_id", "entity_type", "entity_id", name="uq_snapshot_membership"),
    )


class FindingModel(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("reconciliation_runs.id"), nullable=False, index=True
    )
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)  # order, payment, event
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    discrepancy_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="OPEN", index=True
    )  # OPEN, RESOLVED, REOPENED
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )  # Optimistic concurrency control
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    run: Mapped["ReconciliationRun"] = relationship(back_populates="findings")
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}

    __table_args__ = (
        UniqueConstraint(
            "run_id", "rule_code", "entity_type", "entity_id", name="uq_finding_run_rule_entity"
        ),
    )

    @property
    def evidence(self) -> dict[str, Any]:
        try:
            return json.loads(self.evidence_json)
        except json.JSONDecodeError:
            return {}


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="operator")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    before_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
