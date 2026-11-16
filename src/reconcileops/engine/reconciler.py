"""Pure reconciliation engine implementing deterministic matching rules.

This module is completely decoupled from databases and network I/O,
making it fast, purely testable, and deterministic.
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from reconcileops.domain.models import (
    EventType,
    FindingCandidate,
    Order,
    PaymentAggregate,
    ProviderEvent,
    RuleCode,
    ensure_utc,
)

RULE_VERSION: str = "1.0.0"


class ReconciliationResult:
    def __init__(
        self,
        cutoff: datetime,
        rule_version: str,
        orders_evaluated: int,
        events_evaluated: int,
        payments_evaluated: int,
        findings: list[FindingCandidate],
    ):
        self.cutoff = cutoff
        self.rule_version = rule_version
        self.orders_evaluated = orders_evaluated
        self.events_evaluated = events_evaluated
        self.payments_evaluated = payments_evaluated
        self.findings = findings

    @property
    def total_findings(self) -> int:
        return len(self.findings)

    @property
    def findings_by_rule(self) -> dict[RuleCode, int]:
        counts: dict[RuleCode, int] = defaultdict(int)
        for f in self.findings:
            counts[f.rule_code] += 1
        return dict(counts)


def aggregate_provider_events(
    events: list[ProviderEvent],
    cutoff: datetime,
) -> tuple[dict[str, PaymentAggregate], list[FindingCandidate]]:
    """Aggregate provider events up to cutoff by payment_id and detect provider invariants.

    Invariants checked:
    - Same event_id twice is deduplicated (handled prior to aggregation).
    - Multiple distinct capture events for one payment_id -> INVALID_PROVIDER_DATA.
    - Refund without capture -> ORPHAN_REFUND.
    - Refunds exceeding capture amount -> INVALID_PROVIDER_DATA.
    - Mixed currencies in the same payment_id -> INVALID_PROVIDER_DATA.
    """
    cutoff = ensure_utc(cutoff)
    findings: list[FindingCandidate] = []

    # 1. Filter events occurring at or before cutoff
    eligible_events = [e for e in events if e.occurred_at <= cutoff]

    # 2. Deduplicate exact duplicate deliveries of event_id
    deduped_events_map: dict[str, ProviderEvent] = {}
    for ev in eligible_events:
        if ev.event_id not in deduped_events_map:
            deduped_events_map[ev.event_id] = ev

    # 3. Group by payment_id
    events_by_payment: dict[str, list[ProviderEvent]] = defaultdict(list)
    for ev in deduped_events_map.values():
        events_by_payment[ev.payment_id].append(ev)

    aggregates: dict[str, PaymentAggregate] = {}

    for payment_id, p_events in events_by_payment.items():
        captures = [e for e in p_events if e.event_type == EventType.CAPTURE]
        refunds = [e for e in p_events if e.event_type == EventType.REFUND]

        sample_event = captures[0] if captures else p_events[0]
        order_id = sample_event.order_id
        currency = sample_event.currency

        # Check for mixed currencies within the same payment_id
        all_currencies = {e.currency for e in p_events}
        all_orders = {e.order_id for e in p_events}
        if len(all_orders) > 1:
            findings.append(
                FindingCandidate(
                    rule_code=RuleCode.INVALID_PROVIDER_DATA,
                    entity_type="payment",
                    entity_id=payment_id,
                    currency=currency,
                    evidence={
                        "condition": "Mixed order IDs for a single payment_id",
                        "payment_id": payment_id,
                        "order_ids": sorted(all_orders),
                        "event_ids": [e.event_id for e in p_events],
                    },
                )
            )
            aggregates[payment_id] = PaymentAggregate(
                payment_id=payment_id,
                order_id=order_id,
                currency=currency,
                capture_events=captures,
                refund_events=refunds,
                is_valid=False,
                invalid_reason="Mixed order IDs",
                has_capture=bool(captures),
            )
            continue
        if len(all_currencies) > 1:
            findings.append(
                FindingCandidate(
                    rule_code=RuleCode.INVALID_PROVIDER_DATA,
                    entity_type="payment",
                    entity_id=payment_id,
                    currency=currency,
                    evidence={
                        "condition": "Mixed currencies detected for single payment_id",
                        "payment_id": payment_id,
                        "currencies": list(all_currencies),
                        "event_ids": [e.event_id for e in p_events],
                    },
                )
            )
            aggregates[payment_id] = PaymentAggregate(
                payment_id=payment_id,
                order_id=order_id,
                currency=currency,
                capture_events=captures,
                refund_events=refunds,
                is_valid=False,
                invalid_reason="Mixed currencies in single payment_id",
                has_capture=len(captures) > 0,
            )
            continue

        # Check for refund without capture -> ORPHAN_REFUND
        if not captures and refunds:
            total_refunded = sum((r.amount for r in refunds), Decimal("0.00"))
            findings.append(
                FindingCandidate(
                    rule_code=RuleCode.ORPHAN_REFUND,
                    entity_type="payment",
                    entity_id=payment_id,
                    currency=currency,
                    actual_amount=total_refunded,
                    evidence={
                        "condition": "Refund references payment_id with no capture in the snapshot",
                        "payment_id": payment_id,
                        "order_id": order_id,
                        "refunded_amount": str(total_refunded),
                        "refund_events": [
                            {
                                "event_id": r.event_id,
                                "amount": str(r.amount),
                                "occurred_at": r.occurred_at.isoformat(),
                            }
                            for r in refunds
                        ],
                    },
                )
            )
            aggregates[payment_id] = PaymentAggregate(
                payment_id=payment_id,
                order_id=order_id,
                currency=currency,
                refund_events=refunds,
                refunded_amount=total_refunded,
                is_valid=False,
                invalid_reason="Orphan refund without capture",
                has_capture=False,
            )
            continue

        # Check for multiple distinct capture events for one payment_id -> INVALID_PROVIDER_DATA
        if len(captures) > 1:
            findings.append(
                FindingCandidate(
                    rule_code=RuleCode.INVALID_PROVIDER_DATA,
                    entity_type="payment",
                    entity_id=payment_id,
                    currency=currency,
                    evidence={
                        "condition": "Multiple distinct capture events for single payment_id",
                        "payment_id": payment_id,
                        "captures_count": len(captures),
                        "capture_events": [
                            {
                                "event_id": c.event_id,
                                "amount": str(c.amount),
                                "occurred_at": c.occurred_at.isoformat(),
                            }
                            for c in captures
                        ],
                    },
                )
            )
            aggregates[payment_id] = PaymentAggregate(
                payment_id=payment_id,
                order_id=order_id,
                currency=currency,
                capture_events=captures,
                refund_events=refunds,
                is_valid=False,
                invalid_reason="Multiple distinct captures for single payment_id",
                has_capture=True,
            )
            continue

        # Single valid capture
        captured_amount = captures[0].amount if captures else Decimal("0.00")
        refunded_amount = sum((r.amount for r in refunds), Decimal("0.00"))

        # Check if refunds exceed capture -> INVALID_PROVIDER_DATA
        if refunded_amount > captured_amount:
            findings.append(
                FindingCandidate(
                    rule_code=RuleCode.INVALID_PROVIDER_DATA,
                    entity_type="payment",
                    entity_id=payment_id,
                    currency=currency,
                    actual_amount=refunded_amount,
                    expected_amount=captured_amount,
                    discrepancy_amount=refunded_amount - captured_amount,
                    evidence={
                        "condition": "Refunds exceed captured amount for payment_id",
                        "payment_id": payment_id,
                        "captured_amount": str(captured_amount),
                        "refunded_amount": str(refunded_amount),
                        "excess_amount": str(refunded_amount - captured_amount),
                    },
                )
            )
            aggregates[payment_id] = PaymentAggregate(
                payment_id=payment_id,
                order_id=order_id,
                currency=currency,
                capture_events=captures,
                refund_events=refunds,
                captured_amount=captured_amount,
                refunded_amount=refunded_amount,
                is_valid=False,
                invalid_reason="Refunds exceed captured amount",
                has_capture=True,
            )
            continue

        # Normal valid payment
        net_amount = captured_amount - refunded_amount
        aggregates[payment_id] = PaymentAggregate(
            payment_id=payment_id,
            order_id=order_id,
            currency=currency,
            capture_events=captures,
            refund_events=refunds,
            captured_amount=captured_amount,
            refunded_amount=refunded_amount,
            net_amount=net_amount,
            is_valid=True,
            invalid_reason=None,
            has_capture=True,
        )

    return aggregates, findings


def reconcile_snapshot(
    orders: list[Order],
    events: list[ProviderEvent],
    cutoff: datetime,
) -> ReconciliationResult:
    """Execute pure reconciliation for an immutable snapshot of orders and events at cutoff."""
    cutoff = ensure_utc(cutoff)
    orders_map: dict[str, Order] = {o.order_id: o for o in orders}

    # 1. Aggregate events and extract payment-level findings
    payment_aggregates, payment_findings = aggregate_provider_events(events, cutoff)
    findings: list[FindingCandidate] = list(payment_findings)

    # 2. Check for ORPHAN_PAYMENT (Captures referencing unknown order_id)
    payments_by_order: dict[str, list[PaymentAggregate]] = defaultdict(list)
    for p_id, agg in payment_aggregates.items():
        if agg.has_capture:
            if agg.order_id not in orders_map:
                findings.append(
                    FindingCandidate(
                        rule_code=RuleCode.ORPHAN_PAYMENT,
                        entity_type="payment",
                        entity_id=p_id,
                        currency=agg.currency,
                        actual_amount=agg.captured_amount if agg.is_valid else None,
                        evidence={
                            "condition": "Capture references an unknown order_id",
                            "payment_id": p_id,
                            "order_id": agg.order_id,
                            "captured_amount": str(agg.captured_amount) if agg.is_valid else None,
                            "invalid_provider_data": not agg.is_valid,
                            "capture_event_ids": [e.event_id for e in agg.capture_events],
                            "currency": agg.currency,
                            "occurred_at": (
                                agg.capture_events[0].occurred_at.isoformat()
                                if agg.capture_events
                                else None
                            ),
                        },
                    )
                )
            else:
                payments_by_order[agg.order_id].append(agg)

    # 3. Evaluate each order
    for order in orders:
        order_payments = payments_by_order.get(order.order_id, [])
        is_due = cutoff >= order.payment_due_at

        # Check CURRENCY_MISMATCH
        currency_mismatches = [p for p in order_payments if p.currency != order.currency]
        for mismatch in currency_mismatches[:1]:
            findings.append(
                FindingCandidate(
                    rule_code=RuleCode.CURRENCY_MISMATCH,
                    entity_type="order",
                    entity_id=order.order_id,
                    currency=order.currency,
                    evidence={
                        "condition": "A referenced order and payment use different currencies",
                        "order_id": order.order_id,
                        "order_currency": order.currency,
                        "payment_id": mismatch.payment_id,
                        "payment_currency": mismatch.currency,
                        "payment_amount": str(mismatch.captured_amount)
                        if mismatch.is_valid
                        else None,
                        "mismatched_payments": [
                            {
                                "payment_id": p.payment_id,
                                "currency": p.currency,
                                "capture_event_ids": [e.event_id for e in p.capture_events],
                            }
                            for p in currency_mismatches
                        ],
                    },
                )
            )

        # Exclude mismatched currency payments from monetary comparison
        same_currency_payments = [p for p in order_payments if p.currency == order.currency]
        valid_same_currency_payments = [
            p for p in same_currency_payments if p.is_valid and p.has_capture
        ]

        # Check MULTIPLE_CAPTURES
        # "More than one distinct captured payment_id references the same order"
        captured_payment_ids = {p.payment_id for p in order_payments if p.has_capture}
        if len(captured_payment_ids) > 1:
            findings.append(
                FindingCandidate(
                    rule_code=RuleCode.MULTIPLE_CAPTURES,
                    entity_type="order",
                    entity_id=order.order_id,
                    currency=order.currency,
                    evidence={
                        "condition": "More than one distinct captured payment_id references the same order",
                        "order_id": order.order_id,
                        "payment_ids": sorted(captured_payment_ids),
                        "count": len(captured_payment_ids),
                    },
                )
            )

        # Monetary evaluation applies only if order is due
        # "Orders not yet due do not receive missing or amount mismatch findings."
        if is_due:
            # MISSING_PAYMENT: "Due order has no valid capture; cutoff is at or after payment_due_at"
            # Note: If an order has only wrong-currency payments and is due,
            # valid_same_currency_payments is empty, so MISSING_PAYMENT is also emitted.
            if not valid_same_currency_payments:
                findings.append(
                    FindingCandidate(
                        rule_code=RuleCode.MISSING_PAYMENT,
                        entity_type="order",
                        entity_id=order.order_id,
                        currency=order.currency,
                        expected_amount=order.expected_amount,
                        actual_amount=Decimal("0.00"),
                        discrepancy_amount=order.expected_amount,
                        evidence={
                            "condition": "Due order has no valid same-currency capture",
                            "order_id": order.order_id,
                            "expected_amount": str(order.expected_amount),
                            "currency": order.currency,
                            "payment_due_at": order.payment_due_at.isoformat(),
                            "cutoff": cutoff.isoformat(),
                            "payments_found": len(order_payments),
                            "valid_same_currency_captures": 0,
                        },
                    )
                )
            else:
                # AMOUNT_MISMATCH: "Due order has valid same-currency captures, but net paid differs from expected_amount"
                total_net_paid = sum(
                    (p.net_amount for p in valid_same_currency_payments), Decimal("0.00")
                )
                if total_net_paid != order.expected_amount:
                    discrepancy = total_net_paid - order.expected_amount
                    findings.append(
                        FindingCandidate(
                            rule_code=RuleCode.AMOUNT_MISMATCH,
                            entity_type="order",
                            entity_id=order.order_id,
                            currency=order.currency,
                            expected_amount=order.expected_amount,
                            actual_amount=total_net_paid,
                            discrepancy_amount=discrepancy,
                            evidence={
                                "condition": "Due order has valid same-currency captures, but net paid differs from expected_amount",
                                "order_id": order.order_id,
                                "expected_amount": str(order.expected_amount),
                                "actual_net_paid": str(total_net_paid),
                                "discrepancy": str(discrepancy),
                                "currency": order.currency,
                                "contributing_payments": [
                                    {
                                        "payment_id": p.payment_id,
                                        "captured": str(p.captured_amount),
                                        "refunded": str(p.refunded_amount),
                                        "net": str(p.net_amount),
                                    }
                                    for p in valid_same_currency_payments
                                ],
                            },
                        )
                    )

    return ReconciliationResult(
        cutoff=cutoff,
        rule_version=RULE_VERSION,
        orders_evaluated=len(orders),
        events_evaluated=len(events),
        payments_evaluated=len(payment_aggregates),
        findings=findings,
    )
