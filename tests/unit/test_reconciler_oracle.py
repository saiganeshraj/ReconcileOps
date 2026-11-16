"""Explicit oracle test for baseline reconciliation rules."""

from datetime import UTC, datetime
from decimal import Decimal

from reconcileops.domain.models import EventType, Order, ProviderEvent, RuleCode
from reconcileops.engine.reconciler import reconcile_snapshot


def test_baseline_fixture_oracle():
    # Fixed cutoff
    cutoff = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
    due_past = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)
    ordered_at = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    due_future = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)

    orders = [
        # O100 INR 100 with P100 capture 100 => clean
        Order(
            order_id="O100",
            customer_ref="CUST_1",
            currency="INR",
            expected_amount=Decimal("100.00"),
            ordered_at=ordered_at,
            payment_due_at=due_past,
        ),
        # O101 INR 200 with no event and already due => missing
        Order(
            order_id="O101",
            customer_ref="CUST_2",
            currency="INR",
            expected_amount=Decimal("200.00"),
            ordered_at=ordered_at,
            payment_due_at=due_past,
        ),
        # O102 INR 300 with capture 250 => amount mismatch
        Order(
            order_id="O102",
            customer_ref="CUST_3",
            currency="INR",
            expected_amount=Decimal("300.00"),
            ordered_at=ordered_at,
            payment_due_at=due_past,
        ),
        # O103 INR 100 with two distinct payments of 100 => multiple captures and amount mismatch
        Order(
            order_id="O103",
            customer_ref="CUST_4",
            currency="INR",
            expected_amount=Decimal("100.00"),
            ordered_at=ordered_at,
            payment_due_at=due_past,
        ),
        # O104 INR 100 with USD 100 capture => currency mismatch plus missing
        Order(
            order_id="O104",
            customer_ref="CUST_5",
            currency="INR",
            expected_amount=Decimal("100.00"),
            ordered_at=ordered_at,
            payment_due_at=due_past,
        ),
        # Future-due unpaid order => no missing finding
        Order(
            order_id="O105_FUTURE",
            customer_ref="CUST_6",
            currency="INR",
            expected_amount=Decimal("500.00"),
            ordered_at=ordered_at,
            payment_due_at=due_future,
        ),
    ]

    events = [
        # P100 for O100
        ProviderEvent(
            event_id="EV_100",
            payment_id="P100",
            order_id="O100",
            event_type=EventType.CAPTURE,
            currency="INR",
            amount=Decimal("100.00"),
            occurred_at=ordered_at,
        ),
        # P102 for O102 (250 instead of 300)
        ProviderEvent(
            event_id="EV_102",
            payment_id="P102",
            order_id="O102",
            event_type=EventType.CAPTURE,
            currency="INR",
            amount=Decimal("250.00"),
            occurred_at=ordered_at,
        ),
        # P103_A and P103_B for O103 (two distinct payments of 100)
        ProviderEvent(
            event_id="EV_103A",
            payment_id="P103_A",
            order_id="O103",
            event_type=EventType.CAPTURE,
            currency="INR",
            amount=Decimal("100.00"),
            occurred_at=ordered_at,
        ),
        ProviderEvent(
            event_id="EV_103B",
            payment_id="P103_B",
            order_id="O103",
            event_type=EventType.CAPTURE,
            currency="INR",
            amount=Decimal("100.00"),
            occurred_at=ordered_at,
        ),
        # P104 for O104 with USD 100 capture
        ProviderEvent(
            event_id="EV_104",
            payment_id="P104",
            order_id="O104",
            event_type=EventType.CAPTURE,
            currency="USD",
            amount=Decimal("100.00"),
            occurred_at=ordered_at,
        ),
        # unknown O999 capture INR 40 => orphan payment
        ProviderEvent(
            event_id="EV_999",
            payment_id="P999",
            order_id="O999",
            event_type=EventType.CAPTURE,
            currency="INR",
            amount=Decimal("40.00"),
            occurred_at=ordered_at,
        ),
    ]

    result = reconcile_snapshot(orders, events, cutoff)

    # Group findings by entity_id
    findings_by_entity: dict[str, list[RuleCode]] = {}
    for f in result.findings:
        findings_by_entity.setdefault(f.entity_id, []).append(f.rule_code)

    # 1. O100: clean
    assert "O100" not in findings_by_entity
    assert "P100" not in findings_by_entity

    # 2. O101: missing payment
    assert findings_by_entity["O101"] == [RuleCode.MISSING_PAYMENT]

    # 3. O102: amount mismatch
    assert findings_by_entity["O102"] == [RuleCode.AMOUNT_MISMATCH]

    # 4. O103: multiple captures and amount mismatch
    assert set(findings_by_entity["O103"]) == {
        RuleCode.MULTIPLE_CAPTURES,
        RuleCode.AMOUNT_MISMATCH,
    }

    # 5. O104: currency mismatch plus missing payment
    assert set(findings_by_entity["O104"]) == {
        RuleCode.CURRENCY_MISMATCH,
        RuleCode.MISSING_PAYMENT,
    }

    # 6. P999: orphan payment
    assert findings_by_entity["P999"] == [RuleCode.ORPHAN_PAYMENT]

    # 7. O105_FUTURE: future-due unpaid order has no missing finding
    assert "O105_FUTURE" not in findings_by_entity
