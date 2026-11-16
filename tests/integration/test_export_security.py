"""Tests for CSV Export formula injection sanitization (CWE-1236)."""

from decimal import Decimal

from reconcileops.storage.models import FindingModel
from reconcileops.web.export import generate_findings_csv, sanitize_csv_field


def test_sanitize_csv_field():
    assert sanitize_csv_field("=cmd|'/C calc'!A0") == "'=cmd|'/C calc'!A0"
    assert sanitize_csv_field("+1+2") == "'+1+2"
    assert sanitize_csv_field("-SUM(A1:A10)") == "'-SUM(A1:A10)"
    assert sanitize_csv_field("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert sanitize_csv_field("Clean String") == "Clean String"
    assert sanitize_csv_field("123.45") == "123.45"
    assert sanitize_csv_field(None) == ""


def test_generate_findings_csv_sanitizes_dangerous_content():
    f = FindingModel(
        id="=malicious_id",
        run_id="run-1",
        rule_code="MISSING_PAYMENT",
        entity_type="order",
        entity_id="+dangerous_order",
        currency="INR",
        expected_amount=Decimal("100.00"),
        actual_amount=Decimal("0.00"),
        discrepancy_amount=Decimal("100.00"),
        evidence_json='{"note": "@dangerous"}',
        status="OPEN",
        version=1,
    )

    csv_text = generate_findings_csv([f])
    lines = csv_text.strip().splitlines()
    assert len(lines) == 2
    row = lines[1]
    assert "'=malicious_id" in row
    assert "'+dangerous_order" in row
