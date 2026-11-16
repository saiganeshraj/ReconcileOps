# ReconcileOps — Python Automation Portfolio Project

Prepared for Sai Ganesh Raj | Research checked 16 November 2026

## The recommendation

Build an order and payment reconciliation automation platform. A fictional online business exports orders, while its payment provider exposes payment events through an API. ReconcileOps imports both, finds missing or incorrect payments, routes exceptions for review, and produces an auditable report. A repeat run must not duplicate records or findings.

This is a portfolio demonstration using synthetic data, not a payment processor. It never moves money. Its distinction is reliable execution under failures, visible evidence, and explainable decisions. Reconciliation itself is an established business problem; do not describe the idea as a world-first invention.

## Why this project

A junior Python role at Cuculus explicitly mentions Python fundamentals, REST APIs, relational databases, Celery, Redis, Docker, debugging and Git. An Amazon automation role mentions Python, API integrations, data transformation, exception handling and SQL. The latter is a more experienced role, used here for direction rather than as evidence of fresher eligibility. These are illustrative postings, not a survey of the entire job market.

The project demonstrates those skills through one coherent workflow. It has broader Python backend relevance than a dashboard alone, and more engineering depth than a file organizer or a single web scraper. Compared with an open-ended autonomous agent, its outputs can be independently checked. AI can help build it; a paid model API is not required to run it.

| Recruiter can inspect | Evidence in this project |
| --- | --- |
| Python design | Typed domain models, small services, clear exception handling |
| API integration | Pagination, timeout handling, rate limits, provider adapter |
| SQL | Constraints, migrations, joins, transactions and indexes |
| Automation reliability | Durable runs, bounded retries, duplicate delivery handling |
| Data processing | CSV normalization, exact money arithmetic, validation |
| Testing and debugging | Known-answer fixtures, failure injection and structured logs |
| Delivery | Docker Compose, CI, clean setup and demo documentation |

## The five-minute demonstration

1. Load the bundled synthetic order CSV and synchronize the mock provider.
2. Start reconciliation and watch a real persisted run progress.
3. Show one missing payment, an amount mismatch, a duplicate capture and a currency mismatch.
4. Open a finding: show the source rows, the rule and the numerical evidence.
5. Resolve a finding with a note; show the audit event and the original evidence.
6. Enable a mock-provider transient error; show a bounded retry and eventual success.
7. Repeat the import and synchronization; show unchanged unique record counts.
8. Export the findings CSV. Optionally replay a worker delivery to show idempotency.

Do not claim performance, accuracy, users or time savings until actually measured. The demo should work without third-party credentials.

## Scope and stack

Use Python 3.12, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL, Celery with Redis, HTTPX, pytest, Ruff, and Docker Compose. Pin a compatible tested dependency set in a lock file. Use Jinja2 templates, plain CSS and a small amount of JavaScript for the dashboard. Avoid adding a separate frontend framework to this Python-focused project.

Use Python Decimal and PostgreSQL NUMERIC for money. API amounts are strings, and CSV money is parsed directly from strings. Never pass through float. Version one supports INR and USD with two decimal places and compares only the same currency. Reject excessive precision rather than silently rounding input.

Services: web, worker, PostgreSQL, Redis and a small mock payment-provider API. The mock provider is a separate process using synthetic fixtures. Use a mounted volume shared by web and worker for immutable CSV artifacts in the local Docker setup. PostgreSQL is the authoritative store for run status, source records, findings and audits; Redis is the queue, not the business record store.

Run Celery inside Linux containers on Windows. Do not promise a native Windows Celery worker setup. A browser-only hosted demo can be added after local completion, but public hosting is not necessary to finish the project.

### Required screens

- Overview: orders, captured amount by currency, open findings and recent run status. Never add different currencies together.
- Imports: upload, validation preview, rejected row details and import history.
- Runs: create a run, view stages, retry a failed run, inspect failure category.
- Findings: filter by type, order ID and status; inspect evidence; resolve or reopen with a note.
- Audit: time, actor, action, entity and before/after review status.
- Demo controls: explicitly labeled simulated failures; hidden or disabled outside demo mode.

Use a restrained, polished layout, readable tables, clear empty states and accessible controls. Every displayed metric must come from stored records, not hard-coded frontend numbers.

## Exact data contract

Orders CSV columns: order_id, customer_ref, currency, expected_amount, ordered_at, payment_due_at. All timestamps must include a timezone and normalize to UTC. expected_amount must be nonnegative with at most two decimal places. Required fields cannot be blank. Limit uploads to 5 MB and 10,000 rows for v1. Document UTF-8 CSV support and reject malformed CSV with understandable errors.

Provider event fields: event_id, payment_id, order_id, event_type, currency, amount, occurred_at. Types: capture and refund. Amount is a positive two-decimal string; refunds reference the original payment_id. event_id is globally unique within this single provider. Re-delivery of the identical event is ignored; re-delivery with conflicting content is recorded as an import conflict, never overwritten silently.

Mock API: GET /events?cursor=...&limit=... with opaque next_cursor and a stable snapshot for pagination. Include deterministic modes for 429 with Retry-After, one-time 503, timeout and permanent 401. Fault control requires demo mode plus a development token. No connections to real payment services are needed.

Duplicate CSV order_id with identical canonical values is ignored. A changed value for an existing order_id is a rejected conflict; v1 does not implement order amendments. Define canonicalization before hashing. Preserve original source row and a SHA-256 file hash. Display a validation preview and commit only on confirmation. Entirely invalid files must never appear successful. Report accepted, unchanged, rejected and conflicting row counts separately.

## Matching rules — implement these before the dashboard

A reconciliation run captures an immutable input snapshot, rule version and cutoff timestamp. Include only events with occurred_at at or before cutoff that existed in the captured snapshot. A later sync creates a new snapshot for the next run; never mutate an old result.

Aggregate events by payment_id first. Same event_id twice is duplicate delivery, not a duplicate charge. A payment can have one capture and multiple partial refunds up to the captured amount in v1. Multiple distinct capture events for one payment_id or refunds exceeding capture are invalid provider data and must be surfaced, not silently summed. For valid payments, net = capture minus refunds. A refund with no capture is an orphan-refund finding and is excluded from normal totals pending review.

Emit multiple rule findings when relevant, with a stable rule code and evidence. A review status applies to a finding, not to whether the underlying payment was financially corrected.

| Rule code | Condition |
| --- | --- |
| MISSING_PAYMENT | Due order has no valid capture; cutoff is at or after payment_due_at |
| AMOUNT_MISMATCH | Due order has valid same-currency captures, but net paid differs from expected_amount |
| MULTIPLE_CAPTURES | More than one distinct captured payment_id references the same order |
| CURRENCY_MISMATCH | A referenced order and a payment use different currencies; exclude mismatched payments from monetary comparison |
| ORPHAN_PAYMENT | Capture references an unknown order_id |
| ORPHAN_REFUND | Refund references payment_id with no capture in the snapshot |
| INVALID_PROVIDER_DATA | Conflicting capture records, excessive refunds or another explicitly documented provider invariant violation |

Orders not yet due do not receive missing or amount mismatch findings. Still show structural errors such as currency mismatch and duplicate captures. If an order has only wrong-currency payments and is due, both CURRENCY_MISMATCH and MISSING_PAYMENT may appear. Document this behavior. Use exact equality with a zero tolerance default, not fuzzy matching. A fully refunded paid order has net zero and therefore an amount mismatch against a positive expected amount; v1 has no return/cancellation workflow.

Baseline fixture at a fixed cutoff: O100 INR 100 with P100 capture 100 => clean; O101 INR 200 with no event and already due => missing; O102 INR 300 with capture 250 => amount mismatch; O103 INR 100 with two distinct payments of 100 => multiple captures and amount mismatch; O104 INR 100 with USD 100 capture => currency mismatch plus missing; unknown O999 capture INR 40 => orphan payment. Add a future-due unpaid order => no missing finding. Test this oracle explicitly.

## Storage and run reliability

Tables should cover orders, import_batches, source_rows, provider_events, sync_runs, reconciliation_runs, snapshot_memberships, findings and audit_events. Name and normalize implementation details sensibly. Add DB-level unique constraints for external identifiers, import idempotency, run request keys and findings scoped by run/rule/entity. Findings retain references to all supporting source records and the calculation values.

Run states: QUEUED -> RUNNING -> SUCCEEDED or FAILED. Retry creates an attempt with its own timestamps while preserving previous attempt history. A retry uses the same snapshot and rule version. A new reconciliation uses a new run ID. Finding insertion and successful completion must commit atomically, or use a documented staging strategy that prevents partial results appearing final.

Assume at-least-once task delivery. Use a PostgreSQL transaction and per-run advisory lock or equivalent to prevent two workers executing the same run concurrently. Do not hold a database transaction across slow network calls. Separate provider synchronization from pure reconciliation. Capture a committed sync high-water mark plus eligible order IDs in a short transaction to create reproducible snapshots.

Persist a queued-run record before enqueueing. Provide a bounded dispatcher/recovery command to enqueue committed QUEUED records if the web process dies between DB commit and broker publish. Duplicate dispatch is safe. Identify stale RUNNING attempts and provide documented recovery with a lock check; do not blindly run them concurrently. Celery configuration must be consistent with the recovery design. Never claim exactly-once delivery.

Provider sync: explicit HTTP timeouts, bounded exponential backoff with jitter for timeouts/429/5xx, honor Retry-After within a configured cap, do not retry ordinary permanent 4xx errors. Persist progress safely and deduplicate a replayed page. Use a single-sync lease or DB lock to avoid overlapping syncs. Do not publish partial failed sync data as the current complete snapshot. Record error category and actionable message without leaking secrets.

Do not add a scheduler for v1. Manual trigger plus durable recovery demonstrates the important behavior without an extra scheduling service. Add scheduled runs only after the required acceptance checks pass.

## API surface

- POST /api/imports/preview and POST /api/imports/{id}/commit
- POST /api/syncs and GET /api/syncs/{id}
- POST /api/runs with Idempotency-Key; GET /api/runs and GET /api/runs/{id}
- GET /api/findings with pagination and filters; GET /api/findings/{id}
- POST /api/findings/{id}/resolve and /reopen, requiring a note
- GET /api/runs/{id}/findings.csv
- GET /health/live and /health/ready

Reusing the same Idempotency-Key and body returns the same run; a changed body with that key returns 409. Resolve/reopen uses optimistic version checks so concurrent reviewers cannot silently overwrite each other. Export all filtered rows or clearly label page-only behavior. Guard spreadsheet exports against formula injection from string fields starting with =, +, -, @ or control characters.

## Security and deployment boundary

Local v1 binds to localhost with synthetic data. Use a single demo operator and clearly label it; do not imply it has enterprise authentication or multi-tenant isolation. Keep secrets in environment variables, include .env.example without secrets, validate file content and filenames, prevent path traversal, escape HTML, and use CSRF protection for browser mutations. Never accept arbitrary URLs for the provider; use a configured allowlisted base URL.

Before any internet deployment add authenticated operator access, secure session cookies, TLS through the host, upload/rate limits and protected mutation endpoints. A public demo should use isolated synthetic fixtures, disable arbitrary uploads and external connectors, and reset safely without interfering with other visitors. Do not publish or create paid services automatically. Document this boundary in the README.

## Tests that must exist

Use deterministic fixtures and an injected clock. Test clean matching, each rule, partial and full refunds, duplicate event delivery, conflicting identifiers, currency separation, future due dates and malformed input. Test exact money behavior without floats. Test concurrent duplicate run requests against PostgreSQL, not just SQLite mocks.

Integration tests must cover pagination, 429/503 recovery, permanent 401 failure, page replay, crash before enqueue, worker redelivery, stale-attempt recovery, rollback without partial published results and resolve/reopen audit history. Include one browser smoke test for import -> sync -> run -> evidence -> resolve -> export. Tests must exercise real behavior rather than only mock method calls. Use the mock provider or HTTPX mock transport at the integration boundary; retain at least one test using the real mock service.

CI runs lint, core unit tests and PostgreSQL-backed integration tests; use a service container. Browser smoke can be a separate documented job. Never count skipped tests as passing. Produce a short acceptance report with commands, actual outcomes and remaining limitations. Measure 10,000-row processing on the available machine and report environment, duration and record counts without inventing a target result.

## Build sequence

1. Environment inspection, repository scaffold, pyproject, dependency lock, Docker services and health checks.
2. Pure Python reconciliation engine plus known-answer fixtures and tests.
3. Database models/migrations, CSV preview/commit and API validation.
4. Mock provider, sync adapter, pagination and bounded retry tests.
5. Durable runs, immutable snapshots, Celery worker, idempotency and recovery.
6. Dashboard, evidence view, review actions, audit and CSV export.
7. Full acceptance run, clean-start verification, CI and documentation.

Each step should result in functioning code. Do not substitute a polished dashboard for missing backend work. Do not build Kubernetes, microservices beyond the mock provider, a workflow designer, real bank integrations, OCR, a vector database or an autonomous agent. These increase scope without strengthening the core demonstration.

## Copy this instruction into Codex in Antigravity

Attach this entire Markdown file and paste:

> Build only the ReconcileOps application according to the attached specification. Do not create a personal portfolio website; that is a separate future project. Start from an empty project; I have not created a repository or project files. Implement and verify the application, not just a plan. Inspect the operating system, available drives, Python, Git and Docker first. On Windows, prefer D:\Projects\ReconcileOps if D: exists and is writable, otherwise E:\Projects\ReconcileOps. If neither is available, ask for a writable non-C: location; do not silently create the project on C:. Do not overwrite an existing project. Use Linux Docker containers for PostgreSQL, Redis and Celery. If Docker is missing or not running, continue writing and unit-testing the pure Python engine, and report the precise prerequisite needed for full integration verification. Do not claim container checks passed if they were not run.
>
> Treat the attached rules and acceptance checks as requirements. Make reasonable implementation decisions and record them in docs/decisions.md. Use a modular monolith and keep the frontend lightweight. Implement in the specified sequence; run meaningful tests after each stage and fix failures. Continue until the local demo works or a specific environment blocker prevents further execution. Do not stop after scaffolding, a README, or placeholder endpoints. Do not use fabricated metrics, fake progress, random dashboard data, TODO implementations or catch-all exceptions that report success. AI API access must not be required. No paid services, publishing, real financial integrations or external messages.
>
> Create README.md, .env.example, a tested dependency lock, Docker Compose configuration, Alembic migrations, synthetic fixtures, tests, CI, docs/architecture.md, docs/decisions.md, docs/acceptance.md, docs/demo-script.md and docs/interview-guide.md. Include Windows PowerShell commands for setup and troubleshooting. Provide one documented command/script for local demo startup and another for deterministic demo seed/reset. Reset must explicitly target only demo records, refuse outside demo mode, and never delete unrelated data. The interview guide should explain the implemented code using file/function references, not generic textbook answers. Finish by reporting the actual project path, startup URL, executed test results, unmet checks and the exact five-minute demo steps.

If Codex stops due to its session limit, continue with:

> Read the specification, docs/decisions.md and docs/acceptance.md. Inspect existing code and actual test results. Resume from the first unmet requirement, preserving working features. Implement, test and update the acceptance report. Do not restart the project or expand scope.

## Definition of finished

The application starts from a clean clone using the documented steps. The seeded demo reproduces the baseline findings, exposes actual evidence, survives the tested transient error, and ignores duplicate imports and event deliveries. Concurrent/replayed tasks do not create duplicate results. Review actions persist audit history. Exports are valid. All claimed tests have actually run. Any remaining limitations are explicit.

Include screenshots of actual runs and a short recording. A working local demo with a clear recording is preferable to an unreliable public deployment. AI can accelerate implementation, but one uninterrupted prompt is not a guarantee of a correct completed application. Review and understand each stage.

## How to present it to recruiters

Use the repository link directly for this project and your GitHub profile in the resume header. README order: problem, short demo, screenshot, features, architecture, quick start, testing, failure-handling decisions, limitations. Mark it clearly as a personal project using simulated financial data.

After completion, an honest resume description can be: "Built a Python automation platform to reconcile order and payment records, with background processing, duplicate-safe imports, explainable exception detection and an auditable review workflow." Add the actual technologies and measured results only after verification.

Be able to explain Decimal versus float, a SQL join, the matching rules, transactions, idempotency versus retries, at-least-once delivery, snapshot reproducibility, pagination, and a failure you personally debugged. Change one rule and write its test yourself before interviews. A complex AI-generated repository alone does not establish expertise.

## Research sources and interpretation

Checked 16 November 2026. Job postings can close or change. The recommendation is an engineering judgment based on these examples, not a hiring guarantee.

1. Cuculus, Junior Python Developer: https://cuculus-gmbh.jobs.personio.com/job/2773015?language=en — direct employer example of junior Python backend requirements. Its framework is Django; choosing FastAPI here demonstrates overlapping API concepts but does not substitute for learning Django when a vacancy requires it.
2. Amazon, Application Engineer, AWS Quick: https://www.amazon.jobs/en/jobs/10401953/application-engineer-aws-quick — direct employer example of automation, Python, API integration and SQL expectations; not presented as a fresher vacancy.
3. FastAPI background tasks: https://fastapi.tiangolo.com/tutorial/background-tasks/ — distinguishes lightweight in-process tasks from heavier work suitable for external workers.
4. Celery task guide: https://docs.celeryq.dev/en/stable/userguide/tasks.html — retries, task acknowledgments and the importance of idempotency. A queue library alone does not guarantee durable business workflows.
5. Python Decimal: https://docs.python.org/3/library/decimal.html — explains decimal representation and accounting-related exactness.
6. Playwright Python Trace Viewer: https://playwright.dev/python/docs/trace-viewer — useful debugging evidence for the optional browser smoke-test trace, not a substitute for backend assertions.
