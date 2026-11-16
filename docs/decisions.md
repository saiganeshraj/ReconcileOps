# Implementation decisions

## Resume and preserve working code

The inherited repository had a pure engine, models, HTTP endpoints and 28 passing tests. It lacked the dashboard, migration revisions, Compose files, CI, and documentation. Existing code was retained and audited. This is a continuation; the build guide's old empty-project instruction was treated as historical context, not an instruction to restart.

## D: storage

The project is `D:\Projects\ReconcileOps`. Compose bind-mounts PostgreSQL, Redis, and shared artifacts into its `data/` subtree. Browser downloads/cache can also be placed there. Docker's own disk location is a separate Docker Desktop setting and must be verified independently. The user requested avoiding new project data on C:.

## Canonicalization and immutable sources

Trim identifiers, uppercase supported currency, parse Decimal directly, format two decimal places, normalize aware timestamps to UTC, and serialize fields as a JSON array before SHA-256. JSON avoids delimiter ambiguity. Scientific notation, non-finite amounts, excessive precision, overflow, negative order amounts, zero/negative provider amounts, and naive source timestamps are rejected. The supported numeric range is below 10^12, matching NUMERIC(14,2).

CSV column order is independent of commit order. Original text and normalized values are stored separately, including multiline quoted rows. A commit revalidates conflicts under a database lock; it never uses ORM merge to overwrite immutable orders from an old preview.

Repeated uploads create separate preview/history batches. Import idempotency applies to canonical order IDs and repeated commit of the same batch; it does not erase the history of repeated operator uploads. Source-row identity is unique within each batch.

## Snapshots and locks

Snapshots are captured at run creation, including eligible event IDs and the last completed sync ID. Events that arrive later belong to future runs. `snapshot_captured` distinguishes empty snapshots from uncaptured legacy records. Production PostgreSQL session locks protect workers across visible attempt commits; short transaction locks serialize snapshot publication and request-key creation. SQLite is only a lightweight test backend and does not simulate those locks.

## Sync publication

Fetch/validate pages outside a business transaction, then publish the complete batch in one transaction. A failed later page publishes no provider events. Conflicting event IDs retain the first accepted event and write a separate conflict record. Retry logs retain categories, attempt numbers and sleep durations; HTTP payloads/credentials are not included in those logs. Only configured provider URLs are used; no API accepts arbitrary connector URLs.

## Finding identity and interpretation

A finding is unique by run, rule, entity type, and entity ID. Multiple wrong-currency payments on one order are grouped into one currency finding with all payment evidence, avoiding duplicate uniqueness failures. A payment referencing inconsistent order IDs is invalid provider data. Valid same-currency captures drive exact zero-tolerance comparisons. Wrong-currency-only due orders produce currency mismatch plus missing payment. Full refunds produce net zero, not an implicit cancellation.

Review status is independent of financial correctness. Resolving/reopening requires a note and version. SQLAlchemy's database version predicate prevents actual concurrent lost updates; checking a Python object alone is insufficient.

## Durable publication and recovery

Queued records commit before task dispatch. Broker failure leaves recoverable QUEUED state; it never silently switches HTTP requests to synchronous work. The recovery command is bounded and manual. Stale running attempts are failed only after acquiring the same execution lease used by workers. Retries preserve inputs/rules and attempt history, with at most three reconciliation attempts. No exactly-once claim is made.

## Local security and reset

Local demo only, one labeled operator. Browser mutations require same-origin CSRF protection. CLI mutations require an explicit custom header. HTML is escaped; dynamic tables use textContent. Exports neutralize formula prefixes/control characters. Demo fault controls require demo mode and the configured development token on the upstream provider. Reset requires explicit seed ownership, protects mixed/unrelated snapshots, checks active work, and retains audit history.

## Verification boundary

Core SQLite tests, PostgreSQL concurrency tests, live-stack checks, and browser smoke are separate claims. Missing infrastructure is recorded as unverified, never counted as a pass. Dependency pins reflect actual tested installations; the browser helper uses a separately pinned optional dependency. Benchmark timings describe the actual workload/storage/machine, with no invented performance target.
