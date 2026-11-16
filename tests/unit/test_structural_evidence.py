from datetime import UTC, datetime

from reconcileops.domain.models import Order, ProviderEvent, RuleCode
from reconcileops.engine.reconciler import reconcile_snapshot

TS = datetime(2026, 11, 15, 10, tzinfo=UTC)


def event(event_id, **changes):
    return ProviderEvent(
        **(
            {
                "event_id": event_id,
                "payment_id": "P",
                "order_id": "O",
                "event_type": "capture",
                "currency": "INR",
                "amount": "100.00",
                "occurred_at": TS,
            }
            | changes
        )
    )


def order():
    return Order(
        order_id="O",
        customer_ref="C",
        currency="INR",
        expected_amount="100.00",
        ordered_at=TS,
        payment_due_at=TS,
    )


def test_refund_delivery_order_does_not_invent_capture_currency_mismatch():
    refund = event("refund", event_type="refund", currency="USD", amount="10.00")
    capture = event("capture")
    result = reconcile_snapshot([order()], [refund, capture], TS)
    assert {f.rule_code for f in result.findings} == {
        RuleCode.INVALID_PROVIDER_DATA,
        RuleCode.MISSING_PAYMENT,
    }


def test_invalid_orphan_capture_does_not_claim_a_zero_amount():
    result = reconcile_snapshot([], [event("first"), event("second")], TS)
    orphan = next(f for f in result.findings if f.rule_code == RuleCode.ORPHAN_PAYMENT)
    assert orphan.actual_amount is None
    assert orphan.evidence["captured_amount"] is None
    assert set(orphan.evidence["capture_event_ids"]) == {"first", "second"}


def test_two_wrong_currency_payments_share_one_complete_currency_finding():
    result = reconcile_snapshot(
        [order()],
        [
            event("one", payment_id="P1", currency="USD"),
            event("two", payment_id="P2", currency="USD"),
        ],
        TS,
    )
    findings = [f for f in result.findings if f.rule_code == RuleCode.CURRENCY_MISMATCH]
    assert len(findings) == 1
    assert {p["payment_id"] for p in findings[0].evidence["mismatched_payments"]} == {"P1", "P2"}
