# Architecture

ReconcileOps is a modular monolith with a separate synthetic provider process. `main.py` mounts REST routes, the Jinja workspace, static assets, and CSRF middleware. `config.py` reads environment settings. There is no separate frontend build or AI dependency.

## Data flow

1. `storage/csv_importer.py::process_csv_preview` validates upload structure/limits, normalizes fields, stores original source rows, records per-row dispositions, and writes a SHA-256-addressed CSV artifact. `commit_import_batch` serializes commits, rechecks existing identifiers, and inserts immutable orders. Changed existing records become conflicts.
2. The API commits a queued `SyncRun` before publishing its Celery task. `provider/client.py::sync_all_events` uses a provider-wide PostgreSQL session advisory lock. HTTP fetching occurs without an open business transaction. Pages are buffered and validated; all event inserts, duplicates/conflicts, sync counters, and successful completion publish together.
3. `create_or_get_reconciliation_run` serializes a request key, captures the latest successful sync ID, and stores eligible order/event memberships under a short publication lock. A boolean marks even an empty snapshot as captured. Ingestion uses the same publication lock, preventing half-published snapshots.
4. `execute_reconciliation_run` holds a per-run session advisory lock on a dedicated connection. It commits a visible RUNNING attempt, loads the immutable membership set, invokes `engine/reconciler.py::reconcile_snapshot`, and commits findings plus successful completion atomically. Failure rolls back result writes, then commits a failed attempt separately.
5. `finding_service.py` changes review status using SQLAlchemy versioned UPDATEs. The version predicate protects against stale in-memory sessions as well as stale HTTP requests. Audit insert and status update share a transaction.

## Storage

- `orders`: immutable canonical external order IDs, exact amounts, UTC timestamps.
- `import_batches`, `source_rows`: source identity, validation dispositions, original and normalized row content.
- `provider_events`, `provider_conflicts`, `sync_runs`: immutable accepted events, conflicting payload evidence, sync outcomes/retry history.
- `reconciliation_runs`, `run_attempts`, `snapshot_memberships`, `findings`: durable requests, attempts, frozen inputs, rule calculations/source IDs.
- `audit_events`: persisted actor/action/entity/before/after/note history.
- `demo_ownership`: explicit ownership boundary for safe synthetic reset.

PostgreSQL is authoritative. Redis is the task broker/backend and does not define business status. Shared CSV artifacts live in the bind-mounted project data directory; the database preserves the hash and original rows. Alembic revisions define schema changes; web startup does not create tables implicitly.

## Delivery semantics

At-least-once delivery is assumed. Celery uses late acknowledgements, worker-loss rejection, prefetch 1, and a 300-second task limit. The dispatcher can republish committed queued work after a web/broker crash. A session lock survives transaction commits and is released if its database connection dies. Recovery checks that same lock and never assumes elapsed time alone proves a worker is dead.

Provider pages are buffered up to 100,000 events and 2,000 pages, with repeated-cursor detection and bounded retries. The default page size is 50; process-level integration tests explicitly use two events per page. Even an endless sequence of empty pages terminates at the page bound. This v1 trades memory for simple atomic publication; it does not claim streaming arbitrary provider histories. Mock snapshots are content-addressed and bounded to 128 versions. No scheduler is included.
