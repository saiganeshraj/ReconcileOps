"""Detailed unit tests for edge cases and invariant rules."""

from datetime import UTC, datetime
from decimal import Decimal

from reconcileops.domain.models import EventType, Order, ProviderEvent, RuleCode
from reconcileops.engine.reconciler import reconcile_snapshot


def test_duplicate_event_delivery_is_idempotent():
    cutoff = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
    ts = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)

    order = Order(
        order_id="ORD-1",
        customer_ref="CUST-1",
        currency="USD",
        expected_amount=Decimal("50.00"),
        ordered_at=ts,
        payment_due_at=ts,
    )

    # Identical event delivered twice
    event1 = ProviderEvent(
        event_id="EV-1",
        payment_id="PAY-1",
        order_id="ORD-1",
        event_type=EventType.CAPTURE,
        currency="USD",
        amount=Decimal("50.00"),
        occurred_at=ts,
    )
    event2 = ProviderEvent(
        event_id="EV-1",  # Same event_id
        payment_id="PAY-1",
        order_id="ORD-1",
        event_type=EventType.CAPTURE,
        currency="USD",
        amount=Decimal("50.00"),
        occurred_at=ts,
    )

    result = reconcile_snapshot([order], [event1, event2], cutoff)
    assert len(result.findings) == 0  # Clean match, not multiple captures or amount mismatch!


def test_partial_and_full_refunds():
    cutoff = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
    ts = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)

    order1 = Order(
        order_id="ORD-PARTIAL",
        customer_ref="CUST-P",
        currency="INR",
        expected_amount=Decimal("100.00"),
        ordered_at=ts,
        payment_due_at=ts,
    )

    events = [
        ProviderEvent(
            event_id="E-CAP",
            payment_id="PAY-P",
            order_id="ORD-PARTIAL",
            event_type=EventType.CAPTURE,
            currency="INR",
            amount=Decimal("100.00"),
            occurred_at=ts,
        ),
        ProviderEvent(
            event_id="E-REF",
            payment_id="PAY-P",
            order_id="ORD-PARTIAL",
            event_type=EventType.REFUND,
            currency="INR",
            amount=Decimal("30.00"),
            occurred_at=ts,
        ),
    ]

    # Net is 100 - 30 = 70, expected is 100 -> AMOUNT_MISMATCH
    result = reconcile_snapshot([order1], events, cutoff)
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.rule_code == RuleCode.AMOUNT_MISMATCH
    assert f.actual_amount == Decimal("70.00")
    assert f.discrepancy_amount == Decimal("-30.00")


def test_orphan_refund_without_capture():
    cutoff = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
    ts = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)

    event = ProviderEvent(
        event_id="E-REF-ORPHAN",
        payment_id="PAY-UNKNOWN",
        order_id="ORD-X",
        event_type=EventType.REFUND,
        currency="USD",
        amount=Decimal("25.00"),
        occurred_at=ts,
    )

    result = reconcile_snapshot([], [event], cutoff)
    assert len(result.findings) == 1
    assert result.findings[0].rule_code == RuleCode.ORPHAN_REFUND
    assert result.findings[0].entity_id == "PAY-UNKNOWN"


def test_excessive_refund_triggers_invalid_provider_data():
    cutoff = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
    ts = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)

    events = [
        ProviderEvent(
            event_id="E-CAP",
            payment_id="PAY-EXCESS",
            order_id="ORD-1",
            event_type=EventType.CAPTURE,
            currency="USD",
            amount=Decimal("100.00"),
            occurred_at=ts,
        ),
        ProviderEvent(
            event_id="E-REF",
            payment_id="PAY-EXCESS",
            order_id="ORD-1",
            event_type=EventType.REFUND,
            currency="USD",
            amount=Decimal("150.00"),  # Exceeds capture
            occurred_at=ts,
        ),
    ]

    result = reconcile_snapshot([], events, cutoff)
    assert any(f.rule_code == RuleCode.INVALID_PROVIDER_DATA for f in result.findings)


def test_cutoff_excludes_future_events():
    cutoff = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
    ts_past = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)
    ts_future = datetime(2026, 11, 17, 10, 0, 0, tzinfo=UTC)

    order = Order(
        order_id="ORD-1",
        customer_ref="CUST-1",
        currency="INR",
        expected_amount=Decimal("100.00"),
        ordered_at=ts_past,
        payment_due_at=ts_past,
    )

    event_future = ProviderEvent(
        event_id="E-FUTURE",
        payment_id="PAY-1",
        order_id="ORD-1",
        event_type=EventType.CAPTURE,
        currency="INR",
        amount=Decimal("100.00"),
        occurred_at=ts_future,  # After cutoff
    )

    # Since event is after cutoff, it must NOT be included in snapshot, so order is missing payment
    result = reconcile_snapshot([order], [event_future], cutoff)
    assert len(result.findings) == 1
    assert result.findings[0].rule_code == RuleCode.MISSING_PAYMENT
