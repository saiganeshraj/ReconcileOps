# Explaining this implementation

Read the functions below and run their tests. This repository is a personal synthetic-data project; it does not establish production experience or payment-processing certification.

## Why Decimal instead of float?

`domain/money.py::parse_money` creates Decimal directly from strings, refuses float input, and checks scale/finite range. ORM amounts use NUMERIC(14,2) in `storage/models.py`. `tests/unit/test_money.py` and regression tests show excess precision and non-finite rejection. Show why `0.1 + 0.2` as binary floats is unsuitable for the exact comparisons in the engine.

## How are the rules applied?

`engine/reconciler.py::aggregate_provider_events` groups by payment ID before comparing orders. A repeated event ID is delivery duplication; two distinct payment IDs captured against one order are multiple captures. Multiple capture events for the same payment, excessive refunds, mixed currencies, and inconsistent order IDs violate provider invariants. `reconcile_snapshot` performs structural checks even for future-due orders, then exact monetary comparisons only when due. `tests/unit/test_reconciler_oracle.py` is the seven-finding known-answer baseline.

## What SQL work matters here?

`create_or_get_reconciliation_run` selects eligible order/event IDs, stores snapshot memberships, and uses a unique request key. Snapshot loading selects source records through those membership IDs. A useful SQL join to explain is: join snapshot_memberships on entity_id to orders where entity_type='order' and run_id=:run_id. `web/dashboard.py::overview` groups provider captures by currency; summing INR and USD together would be meaningless.

## What does a transaction protect?

`provider/client.py::_sync_locked` publishes a fully fetched sync atomically. A failed later page never becomes the current accepted source state. `storage/reconcile_service.py::_execute_locked` commits attempt start, then commits all findings and SUCCEEDED together. Its exception branch rolls back result writes before persisting a failed attempt. `test_failed_finding_write_rolls_back_and_preserves_attempt` injects an actual ORM insert failure and verifies no partial findings survive.

## Idempotency versus retry

Idempotency means repeating a request/delivery has the same business effect: `create_or_get_reconciliation_run` returns the original run for the same key and normalized cutoff. Retry means trying failed work again: the run retains its ID/snapshot/rules while a new RunAttempt records execution. `tasks/recovery.py` redispatches queued work after a commit/publish gap. Explain why queue delivery remains at least once, even with duplicate-safe processing.

## How are concurrent workers and reviewers protected?

`storage/database.py::execution_lease` uses PostgreSQL session advisory locks on dedicated connections, so locks survive business commits. Reconciliation and stale recovery use the same per-run lock. Request-key creation uses a transaction advisory lock plus a database unique constraint. `FindingModel.__mapper_args__` supplies a version column, and `finding_service.py` increments the version on review. A stale identity map still fails the SQL UPDATE predicate. `tests/postgres/test_concurrency.py` exercises real PostgreSQL rather than SQLite stand-ins.

## What happened during the handover audit?

The inherited tests passed while two important paths were broken: stale CSV preview commit used ORM merge and could overwrite an existing order, and synchronization attempted to insert a second row for an already-queued sync ID. The fixes recheck canonical hashes during serialized commit and load/reuse the existing sync record. The HTTP workflow and regression tests demonstrate those behaviors directly. Another gap was empty snapshots being mistaken for uncaptured inputs; the explicit captured flag prevents later data from entering an old run.

## How does pagination/retry work?

`provider/mock_server.py` produces opaque cursors tied to a stable content snapshot. `ProviderClient.fetch_page_with_retry` bounds attempts, handles timeout/network/429/5xx categories, honors Retry-After within a cap, and avoids retrying permanent 4xx responses. Sync retry evidence is persisted. Pages are replay-safe because event IDs and canonical hashes distinguish identical delivery from conflicting content.

## What should you change yourself before an interview?

Add one small documented rule change in `engine/reconciler.py`, write a known-answer test first, and explain its effects on future-due orders, refunds, currency separation, and finding identity. Do not change the zero-tolerance matching policy casually. Be ready to run the baseline and explain every finding from its source records.
