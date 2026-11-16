"""CSV parsing, validation, canonicalization, and import handling."""

import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from decimal import Decimal

from dateutil import parser as date_parser
from sqlalchemy import select
from sqlalchemy.orm import Session

from reconcileops.config import settings
from reconcileops.domain.money import (
    MoneyValidationError,
    parse_money,
    validate_currency,
)
from reconcileops.storage.database import acquire_advisory_lock, wait_for_lock
from reconcileops.storage.models import (
    AuditEvent,
    ImportBatch,
    OrderModel,
    SourceRow,
    utc_now,
)

EXPECTED_HEADERS = [
    "order_id",
    "customer_ref",
    "currency",
    "expected_amount",
    "ordered_at",
    "payment_due_at",
]


class CSVValidationError(Exception):
    """Raised when CSV structure is invalid."""

    pass


def compute_canonical_hash(
    order_id: str,
    customer_ref: str,
    currency: str,
    expected_amount: Decimal,
    ordered_at: datetime,
    payment_due_at: datetime,
) -> str:
    """Compute deterministic SHA-256 hash of canonical order fields."""
    canonical_str = json.dumps(
        [
            order_id.strip(),
            customer_ref.strip(),
            currency.strip().upper(),
            f"{expected_amount:.2f}",
            ordered_at.isoformat(),
            payment_due_at.isoformat(),
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


def parse_timestamp_with_tz(value: str, field_name: str) -> datetime:
    """Parse ISO or standard timestamp string and guarantee UTC timezone normalization."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty.")
    try:
        dt = date_parser.parse(cleaned)
    except Exception as err:
        raise ValueError(f"{field_name} contains invalid date/time format: '{value}'") from err

    if dt.tzinfo is None:
        raise ValueError(
            f"{field_name} '{value}' must include timezone offset (e.g. '2026-11-16T12:00:00Z' or '+00:00')."
        )
    return dt.astimezone(UTC)


def process_csv_preview(
    session: Session,
    filename: str,
    content_bytes: bytes,
) -> ImportBatch:
    """Parse and validate CSV content, recording preview results into ImportBatch and SourceRows."""
    file_sha256 = hashlib.sha256(content_bytes).hexdigest()
    if len(content_bytes) > settings.MAX_UPLOAD_BYTES:
        raise CSVValidationError("File exceeds 5 MB upload limit.")
    if not filename or len(filename) > 255 or any(c in filename for c in ("/", "\\", "\x00")):
        raise CSVValidationError("Use a filename without path separators (maximum 255 characters).")

    try:
        text_content = content_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        raise CSVValidationError(f"File '{filename}' is not valid UTF-8 text.") from err

    f = io.StringIO(text_content)
    if "\x00" in text_content:
        raise CSVValidationError("CSV must not contain NUL characters.")
    try:
        reader = csv.DictReader(f, strict=True)
    except Exception as err:
        raise CSVValidationError(f"Unable to parse CSV structure: {err}") from err

    try:
        fields = reader.fieldnames
    except csv.Error as err:
        raise CSVValidationError(f"Malformed CSV header: {err}") from err
    if not fields:
        raise CSVValidationError("CSV file is empty or missing headers.")

    cleaned_headers = [h.strip().lower() for h in reader.fieldnames if h]
    if len(set(cleaned_headers)) != len(cleaned_headers):
        raise CSVValidationError("Duplicate CSV headers are not supported.")
    for expected in EXPECTED_HEADERS:
        if expected not in cleaned_headers:
            raise CSVValidationError(
                f"Missing required CSV column '{expected}'. Expected columns: {', '.join(EXPECTED_HEADERS)}"
            )

    # Fetch existing orders to check for unchanged vs conflict
    existing_orders_query = session.execute(
        select(OrderModel.order_id, OrderModel.canonical_hash)
    ).all()
    existing_orders: dict[str, str] = {row[0]: row[1] for row in existing_orders_query}

    batch = ImportBatch(
        filename=filename,
        file_sha256=file_sha256,
        status="PENDING",
    )
    session.add(batch)
    session.flush()

    total_rows = 0
    accepted_rows = 0
    unchanged_rows = 0
    rejected_rows = 0
    conflict_rows = 0

    seen_in_batch: dict[str, str] = {}  # order_id -> canonical_hash

    lines = text_content.splitlines(keepends=True)
    reader.fieldnames = cleaned_headers
    parsed_rows = []
    previous_line = reader.line_num
    try:
        for row in reader:
            parsed_rows.append(
                (previous_line + 1, row, "".join(lines[previous_line : reader.line_num]))
            )
            previous_line = reader.line_num
            if len(parsed_rows) > settings.MAX_UPLOAD_ROWS:
                raise CSVValidationError("CSV exceeds the 10,000 row limit.")
    except csv.Error as err:
        raise CSVValidationError(f"Malformed CSV: {err}") from err
    if not parsed_rows:
        raise CSVValidationError("CSV contains no data rows.")
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    artifact = settings.UPLOAD_DIR / (file_sha256 + ".csv")
    try:
        with artifact.open("xb") as output:
            output.write(content_bytes)
    except FileExistsError:
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != file_sha256:
            raise CSVValidationError("Stored artifact failed its integrity check.") from None

    for idx, row, raw_row_str in parsed_rows:
        total_rows += 1
        order_id_raw = (row.get("order_id") or "").strip()

        # Validate required fields
        errors: list[str] = []
        if None in row or any(v is None for v in row.values()):
            errors.append("Row has a different number of fields from the header.")
        if not order_id_raw:
            errors.append("order_id cannot be blank.")
        customer_ref = (row.get("customer_ref") or "").strip()
        if not customer_ref:
            errors.append("customer_ref cannot be blank.")
        if len(order_id_raw) > 128 or len(customer_ref) > 128:
            errors.append("Identifiers must contain at most 128 characters.")

        currency = ""
        try:
            currency = validate_currency(row.get("currency") or "")
        except MoneyValidationError as err:
            errors.append(str(err))

        expected_amount = Decimal("0.00")
        try:
            expected_amount = parse_money(
                row.get("expected_amount") or "", field_name="expected_amount"
            )
        except MoneyValidationError as err:
            errors.append(str(err))

        ordered_at = None
        try:
            ordered_at = parse_timestamp_with_tz(row.get("ordered_at") or "", "ordered_at")
        except ValueError as err:
            errors.append(str(err))

        payment_due_at = None
        try:
            payment_due_at = parse_timestamp_with_tz(
                row.get("payment_due_at") or "", "payment_due_at"
            )
        except ValueError as err:
            errors.append(str(err))

        if errors:
            rejected_rows += 1
            source_row = SourceRow(
                import_batch_id=batch.id,
                row_number=idx,
                raw_content=raw_row_str,
                order_id=order_id_raw or None,
                disposition="REJECTED",
                error_message="; ".join(errors),
            )
            session.add(source_row)
            continue

        # Row passed format validation; calculate canonical hash
        assert ordered_at is not None and payment_due_at is not None
        can_hash = compute_canonical_hash(
            order_id_raw, customer_ref, currency, expected_amount, ordered_at, payment_due_at
        )

        # Check intra-file duplicate
        if order_id_raw in seen_in_batch:
            prior_hash = seen_in_batch[order_id_raw]
            if prior_hash == can_hash:
                unchanged_rows += 1
                disposition = "UNCHANGED"
                msg = "Duplicate canonical order row within this file."
            else:
                conflict_rows += 1
                disposition = "CONFLICT"
                msg = "Conflicting order values for identical order_id within this file."
            source_row = SourceRow(
                import_batch_id=batch.id,
                row_number=idx,
                raw_content=raw_row_str,
                order_id=order_id_raw,
                disposition=disposition,
                error_message=msg,
            )
            session.add(source_row)
            continue

        # Check existing orders in DB
        if order_id_raw in existing_orders:
            db_hash = existing_orders[order_id_raw]
            if db_hash == can_hash:
                unchanged_rows += 1
                disposition = "UNCHANGED"
                msg = "Order already exists with identical canonical data; ignored."
            else:
                conflict_rows += 1
                disposition = "CONFLICT"
                msg = "Existing order has conflicting values. Amendments are not supported in v1."
            source_row = SourceRow(
                import_batch_id=batch.id,
                row_number=idx,
                raw_content=raw_row_str,
                order_id=order_id_raw,
                disposition=disposition,
                error_message=msg,
            )
            session.add(source_row)
            seen_in_batch[order_id_raw] = can_hash
            continue

        # Accepted row!
        accepted_rows += 1
        seen_in_batch[order_id_raw] = can_hash
        source_row = SourceRow(
            import_batch_id=batch.id,
            row_number=idx,
            raw_content=raw_row_str,
            order_id=order_id_raw,
            disposition="ACCEPTED",
            error_message=None,
        )
        session.add(source_row)

    # Keep original source text and immutable normalized values separately.
    session.flush()
    rows_by_number = {number: row for number, row, raw in parsed_rows}
    for source in session.execute(
        select(SourceRow).where(
            SourceRow.import_batch_id == batch.id, SourceRow.disposition == "ACCEPTED"
        )
    ).scalars():
        source.normalized_content = json.dumps(rows_by_number[source.row_number])

    batch.total_rows = total_rows
    batch.accepted_rows = accepted_rows
    batch.unchanged_rows = unchanged_rows
    batch.rejected_rows = rejected_rows
    batch.conflict_rows = conflict_rows

    session.flush()
    return batch


def commit_import_batch(session: Session, batch_id: str, actor: str = "operator") -> ImportBatch:
    """Commit validated accepted rows from an import batch to the orders table."""
    if not acquire_advisory_lock(session, "order_import_commit"):
        raise ValueError("Another import is being committed; retry shortly.")
    wait_for_lock(session, "snapshot_publish")
    batch = session.get(ImportBatch, batch_id, populate_existing=True)
    if not batch:
        raise ValueError(f"Import batch '{batch_id}' not found.")

    if batch.status == "COMMITTED":
        return batch

    if batch.status == "REJECTED":
        raise ValueError(f"Import batch '{batch_id}' was rejected.")

    # Entirely invalid files must never appear successful
    if batch.accepted_rows == 0 and batch.unchanged_rows == 0 and batch.total_rows > 0:
        batch.status = "REJECTED"
        session.flush()
        raise ValueError("Cannot commit import batch: all rows are invalid or conflicting.")

    # Retrieve all accepted source rows
    stmt = (
        select(SourceRow)
        .where(SourceRow.import_batch_id == batch_id, SourceRow.disposition == "ACCEPTED")
        .order_by(SourceRow.row_number)
    )
    accepted_rows = session.execute(stmt).scalars().all()

    for sr in accepted_rows:
        # Re-parse the accepted raw content
        data = json.loads(sr.normalized_content)
        row_fields = [data[h] for h in EXPECTED_HEADERS]
        # Headers: order_id, customer_ref, currency, expected_amount, ordered_at, payment_due_at
        order_id = row_fields[0].strip()
        customer_ref = row_fields[1].strip()
        currency = validate_currency(row_fields[2])
        expected_amount = parse_money(row_fields[3], field_name="expected_amount")
        ordered_at = parse_timestamp_with_tz(row_fields[4], "ordered_at")
        payment_due_at = parse_timestamp_with_tz(row_fields[5], "payment_due_at")
        can_hash = compute_canonical_hash(
            order_id, customer_ref, currency, expected_amount, ordered_at, payment_due_at
        )

        order_model = OrderModel(
            order_id=order_id,
            customer_ref=customer_ref,
            currency=currency,
            expected_amount=expected_amount,
            ordered_at=ordered_at,
            payment_due_at=payment_due_at,
            canonical_hash=can_hash,
            import_batch_id=batch.id,
        )
        existing = session.get(OrderModel, order_id)
        if existing:
            batch.accepted_rows -= 1
            if existing.canonical_hash == can_hash:
                sr.disposition = "UNCHANGED"
                batch.unchanged_rows += 1
                sr.error_message = "Identical order committed after preview."
            else:
                sr.disposition = "CONFLICT"
                batch.conflict_rows += 1
                sr.error_message = (
                    "Conflicting order committed after preview; existing order preserved."
                )
            continue
        sr.error_message = None
        session.add(order_model)
        session.flush()

    batch.status = "COMMITTED" if batch.accepted_rows or batch.unchanged_rows else "REJECTED"
    batch.committed_at = utc_now()

    # Record audit event
    audit = AuditEvent(
        actor=actor,
        action="COMMIT_IMPORT" if batch.status == "COMMITTED" else "REJECT_IMPORT",
        entity_type="import_batch",
        entity_id=batch.id,
        before_state="PENDING",
        after_state=batch.status,
        note=f"Committed {batch.accepted_rows} orders from file {batch.filename}",
    )
    session.add(audit)
    session.flush()
    return batch
