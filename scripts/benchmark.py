"""Measure 10,000-row import and reconciliation in isolated temporary storage."""

import json
import platform
import time
from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from reconcileops.storage.csv_importer import commit_import_batch, process_csv_preview
from reconcileops.storage.models import Base, OrderModel
from reconcileops.storage.reconcile_service import (
    create_or_get_reconciliation_run,
    execute_reconciliation_run,
)


def main():
    count = 10000
    header = "order_id,customer_ref,currency,expected_amount,ordered_at,payment_due_at\n"
    rows = "".join(
        f"BENCH{i},CUSTOMER{i},INR,100.00,2026-11-14T10:00:00Z,2026-11-15T10:00:00Z\n"
        for i in range(count)
    )
    content = (header + rows).encode()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        start = time.perf_counter()
        batch = process_csv_preview(db, "benchmark.csv", content)
        preview_seconds = time.perf_counter() - start
        start = time.perf_counter()
        commit_import_batch(db, batch.id)
        db.commit()
        commit_seconds = time.perf_counter() - start
        start = time.perf_counter()
        run, _ = create_or_get_reconciliation_run(db, datetime(2026, 11, 16, 12, tzinfo=UTC))
        db.commit()
        execute_reconciliation_run(db, run.id)
        reconciliation_seconds = time.perf_counter() - start
        assert db.scalar(select(func.count()).select_from(OrderModel)) == count
        assert run.findings_count == count
        print(
            json.dumps(
                {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "storage": "isolated in-memory SQLite; not a PostgreSQL throughput claim",
                    "orders": count,
                    "events": 0,
                    "findings": run.findings_count,
                    "csv_bytes": len(content),
                    "preview_seconds": round(preview_seconds, 4),
                    "commit_seconds": round(commit_seconds, 4),
                    "snapshot_reconcile_persist_seconds": round(reconciliation_seconds, 4),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
