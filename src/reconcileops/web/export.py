"""CSV Export generation with CSV Formula Injection (CWE-1236) sanitization."""

import csv
import io
from typing import Any

from reconcileops.storage.models import FindingModel

DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_field(val: Any) -> str:
    """Sanitize string values to prevent formula injection in Excel/Calc/Sheets.

    If a string begins with =, +, -, @, or tab/return, prepend a single quote (').
    """
    if val is None:
        return ""
    text = str(val)
    if text and (
        text.startswith(DANGEROUS_PREFIXES)
        or ord(text[0]) < 32
        or text.lstrip().startswith(("=", "+", "-", "@"))
    ):
        return f"'{text}"
    return text


def generate_findings_csv(findings: list[FindingModel]) -> str:
    """Generate CSV string of findings with sanitized formula fields."""
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    headers = [
        "finding_id",
        "run_id",
        "rule_code",
        "entity_type",
        "entity_id",
        "currency",
        "expected_amount",
        "actual_amount",
        "discrepancy_amount",
        "status",
        "version",
        "resolution_note",
        "created_at",
        "evidence",
    ]
    writer.writerow(headers)

    for f in findings:
        writer.writerow(
            [
                sanitize_csv_field(f.id),
                sanitize_csv_field(f.run_id),
                sanitize_csv_field(f.rule_code),
                sanitize_csv_field(f.entity_type),
                sanitize_csv_field(f.entity_id),
                sanitize_csv_field(f.currency),
                sanitize_csv_field(f.expected_amount),
                sanitize_csv_field(f.actual_amount),
                sanitize_csv_field(f.discrepancy_amount),
                sanitize_csv_field(f.status),
                sanitize_csv_field(f.version),
                sanitize_csv_field(f.resolution_note),
                sanitize_csv_field(f.created_at.isoformat() if f.created_at else ""),
                sanitize_csv_field(f.evidence_json),
            ]
        )

    return output.getvalue()
