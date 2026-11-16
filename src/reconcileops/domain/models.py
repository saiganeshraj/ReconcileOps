"""Domain models, enums and core contracts for ReconcileOps."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from reconcileops.domain.money import parse_money, validate_currency


class RuleCode(StrEnum):
    MISSING_PAYMENT = "MISSING_PAYMENT"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    MULTIPLE_CAPTURES = "MULTIPLE_CAPTURES"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    ORPHAN_PAYMENT = "ORPHAN_PAYMENT"
    ORPHAN_REFUND = "ORPHAN_REFUND"
    INVALID_PROVIDER_DATA = "INVALID_PROVIDER_DATA"


class EventType(StrEnum):
    CAPTURE = "capture"
    REFUND = "refund"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class FindingStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"


class ImportStatus(StrEnum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"


def ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime has timezone info and normalize to UTC.

    If naive (such as when retrieved from SQLite), treat as UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class Order(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str = Field(..., min_length=1, max_length=128)
    customer_ref: str = Field(..., min_length=1, max_length=128)
    currency: str
    expected_amount: Decimal
    ordered_at: datetime
    payment_due_at: datetime

    @field_validator("order_id", "customer_ref", mode="before")
    @classmethod
    def strip_identifiers(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("currency")
    @classmethod
    def check_currency(cls, v: str) -> str:
        return validate_currency(v)

    @field_validator("expected_amount", mode="before")
    @classmethod
    def check_amount(cls, v: Any) -> Decimal:
        return parse_money(v, allow_negative=False, field_name="expected_amount")

    @field_validator("ordered_at", "payment_due_at", mode="after")
    @classmethod
    def check_datetime(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Timestamp must include a timezone.")
        return ensure_utc(v)


class ProviderEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(..., min_length=1, max_length=128)
    payment_id: str = Field(..., min_length=1, max_length=128)
    order_id: str = Field(..., min_length=1, max_length=128)
    event_type: EventType
    currency: str
    amount: Decimal
    occurred_at: datetime

    @field_validator("event_id", "payment_id", "order_id", mode="before")
    @classmethod
    def strip_identifiers(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("currency")
    @classmethod
    def check_currency(cls, v: str) -> str:
        return validate_currency(v)

    @field_validator("amount", mode="before")
    @classmethod
    def check_amount(cls, v: Any) -> Decimal:
        amount = parse_money(v, allow_negative=False, field_name="amount")
        if amount <= 0:
            raise ValueError("Provider event amount must be positive.")
        return amount

    @field_validator("occurred_at", mode="after")
    @classmethod
    def check_datetime(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Timestamp must include a timezone.")
        return ensure_utc(v)


class PaymentAggregate(BaseModel):
    """Aggregated events for a single payment_id."""

    payment_id: str
    order_id: str
    currency: str
    capture_events: list[ProviderEvent] = Field(default_factory=list)
    refund_events: list[ProviderEvent] = Field(default_factory=list)
    captured_amount: Decimal = Field(default=Decimal("0.00"))
    refunded_amount: Decimal = Field(default=Decimal("0.00"))
    net_amount: Decimal = Field(default=Decimal("0.00"))
    is_valid: bool = True
    invalid_reason: str | None = None
    has_capture: bool = False


class FindingCandidate(BaseModel):
    """A finding emitted by the pure reconciliation engine."""

    rule_code: RuleCode
    entity_type: str  # "order", "payment", "event"
    entity_id: str
    currency: str | None = None
    expected_amount: Decimal | None = None
    actual_amount: Decimal | None = None
    discrepancy_amount: Decimal | None = None
    evidence: dict[str, Any]
