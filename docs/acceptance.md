# Acceptance record

Updated 16 November 2026. Project: `D:\Projects\ReconcileOps`. Repository: https://github.com/saiganeshraj/ReconcileOps. This record separates current executed acceptance from historical evidence. No inline/eager task test is counted as real Celery/Redis acceptance.

**Recruiter-demo acceptance passed.** All checks below completed, including normal-stack task delivery, real interruptions, and fresh-data startup/browser verification in a separate checkout. The initial failures are recorded with their fixes; no recruiter-demo check remains blocked.

## Current executed checks

Implementation commit under verification: `63afbbf5cf845530634b8fab006496059da454a3`. The final acceptance-report commit changes documentation only.

| Check | Actual command / environment | Outcome |
| --- | --- | --- |
| Docker storage relocation | Docker Desktop Settings > Resources > Advanced > Browse > Apply & restart > Yes, move it | **Passed.** Built-in relocation completed. UI shows `D:\DockerData\DockerDesktopWSL`; VHDX files were observed under its `disk` and `main` directories. No manual disk move/delete, reset, or volume removal. |
| Linux engine restart | `docker info --format '{{.OSType}}'` | **Passed:** `linux`, exit 0; UI reports Engine running. After relocation C: had 13.04 GB free and D: 188.97 GB free, before image builds. |
| Normal stack startup | `.\scripts\start-demo.ps1` | **Passed after the fixes below.** PostgreSQL, Redis, provider, web, and worker healthy; migration service exited 0. Web binds only `127.0.0.1:8000`. |
| Locked Linux dependency installation | Docker image build: `pip install -r requirements.lock`, then `pip install --no-deps -e .`; `docker compose exec -T web python -m pip check` | **Passed.** Includes Redis client 6.4.0. No broken requirements. |
| Core regression suite after dependency fix | `.venv-check\Scripts\python.exe -m pytest tests/unit tests/integration -q` | **57 passed in 16.41s**, zero skipped; one Starlette TestClient deprecation warning. Core storage tests use SQLite. |
| Lint / formatting | `.venv-check\Scripts\ruff.exe check src tests scripts alembic`; `ruff.exe format --check src tests scripts alembic` | **Passed:** 58 files formatted. |
| Dedicated PostgreSQL database | `docker compose exec -T postgres createdb -U reconcileops reconcileops_test`; `docker compose exec -T -e TEST_DATABASE_URL=postgresql+psycopg://reconcileops:local-demo@postgres:5432/reconcileops_test web pytest tests/postgres -q` | **4 passed in 15.88s**, zero skipped. Test-created random schemas are confined to `reconcileops_test`, never the application database. |
| Migrations / schema drift | Startup migration; `docker compose exec -T web alembic check` | **Passed:** migration exited 0; no new upgrade operations detected. |
| Full browser workflow | `SMOKE_BASE_URL=http://localhost:8000`, `SMOKE_OUTPUT_DIR=artifacts/browser-compose`, `PLAYWRIGHT_BROWSERS_PATH=data/browsers`; `.venv\Scripts\python.exe scripts/browser_smoke.py` | **Passed:** browser import/commit, real queued provider sync, reconciliation, original-source inspection, resolve/audit, and full CSV export. Asserted 6 orders, 6 events, 7 findings, exported review status/note, no JavaScript errors, and mobile document-width assertion. |
| Real duplicate delivery | `.venv-check\Scripts\python.exe -u scripts/stack_acceptance.py` | **Passed:** four additional tasks published to Redis and completed by Celery; same run retained exactly 1 attempt and 7 unique findings. |
| Real worker interruption / recovery | Same script: queue run, hold a PostgreSQL snapshot-table lock, start worker, observe persisted RUNNING attempt, `docker compose kill -s SIGKILL worker`, release lock, recover/retry and restart | **Passed:** attempt 1 FAILED, attempt 2 SUCCEEDED, exactly 7 unique persisted findings. This verifies manual recovery/retry after actual worker termination. |
| Real Redis interruption / recovery | Same script: `docker compose stop redis`, submit run, verify durable QUEUED record with no attempts, `docker compose start redis`, wait for PONG, dispatch queued run | **Passed:** recovered run SUCCEEDED with 1 attempt and 7 unique findings; final source counts remained 6 orders / 6 events; readiness passed. |
| Separate clean checkout | `git clone --no-hardlinks --branch main --single-branch D:\Projects\ReconcileOps artifacts/recruiter-final-checkout`; run its own `scripts/start-demo.ps1` under `COMPOSE_PROJECT_NAME=reconcileops-clean-acceptance` | **Passed:** unchanged tracked checkout of implementation commit, separate bind-mounted data/network, all five services healthy, migration exit 0, readiness passed. Database initially had zero orders/events/findings/runs. Docker build cache was reused. |
| Fresh-checkout browser workflow | Run that checkout's `scripts/browser_smoke.py` with `SMOKE_OUTPUT_DIR=D:\Projects\ReconcileOps\artifacts\browser-clean-checkout` | **Passed:** fresh import/sync/run/evidence/resolve/export with real worker/Redis, 6 orders / 6 events / 7 findings, persisted review, no JavaScript errors, mobile width assertion. Checkout remained clean. |

The four PostgreSQL tests exercise concurrent idempotency requests, concurrent deliveries producing one attempt, recovery refusing a live worker lease, and stale reviewer updates rejected by SQL optimistic version checks. Core tests also cover refunds, malformed inputs, failed sync publication rollback, bounded provider retry/pagination, source evidence, CSRF, export sanitization, and demo reset ownership. These test types have different scopes; the real-stack rows above provide the broker/worker evidence.

## Fixes found during actual acceptance

- **PostgreSQL startup race:** the old socket-only `pg_isready` accepted PostgreSQL's temporary initialization server before TCP was available. Migrations then failed with connection refused. Compose now checks `pg_isready -h 127.0.0.1 ...`.
- **Worker dependency incompatibility:** the real worker exited with `VersionMismatch`: Redis transport requires redis-py 6.1.0 or later, while the lock contained 5.3.1. The lock now uses 6.4.0; both dependency manifests require `>=6.1.0,<7.0.0`.
- **Worker readiness:** Compose now waits for a directed Celery ping, rather than treating an initially running process as sufficient readiness.
- **Browser smoke timing and selection:** the first real-stack smoke created a run before provider sync finished (snapshot had zero events). A later attempt selected an older row while filters refreshed. The smoke now explicitly polls terminal task status, scopes inspection to the new run and O102, and asserts the exact baseline and persisted review in the exported CSV. Earlier runs were preserved.
- **Test database documentation:** README formerly pointed PostgreSQL tests at `reconcileops`. It now creates/reuses `reconcileops_test` and explicitly prohibits using the application database.

No reconciliation rules, application features, public exposure, or production hardening were added.

## Real-stack evidence and limits

Normal-stack browser run: `30816c31-98a6-4963-bf25-4d230a79f710`. Actual local evidence: `artifacts/browser-compose/result.json`, `trace.zip`, `findings.csv`, screenshots, and `recordings/`. The tracked screenshots in `docs/screenshots/` now come from that normal-stack run; evidence and mobile screenshots were inspected.

Fresh-checkout browser run: `0f8bacb9-a3da-4c64-8097-030f6a5895e7`; corresponding trace, recording, screenshots, export, and result are under `artifacts/browser-clean-checkout/`. Only the original demo web service was temporarily stopped to free port 8000. After verification the temporary project's services were stopped without removing its containers or data, and the original web service was restored.

Final readback after restoring the original web service confirmed all three disruption-test runs still SUCCEEDED with seven stored/returned/unique findings and attempt counts 1, 2, and 1 respectively. Original source totals remained six orders and six events; readiness passed. Six separate runs and 39 open findings remain because earlier snapshots and acceptance runs were deliberately preserved.

Disruption results: `artifacts/stack-acceptance.json`:

- Duplicate delivery: `b9be6f10-fce8-4c7d-8109-7782e5f3706c`.
- Worker interruption: `c7153310-1946-4e9e-b056-a91a6753f4bd`.
- Redis interruption: `117fcb4d-fd73-4fd2-908c-8227d6be4bfe`.

The disruption script targets only Compose project `reconcileops`, checks worker/Redis project labels, requires demo mode and non-eager Celery, and refuses existing queued/running work. A database lock supplies a deterministic interruption point in actual application code; no fake task implementation is substituted. It preserves sources and history and restores the worker/Redis services.

For fast deterministic acceptance the recovery calls use `stale_timeout_seconds=0` and `older_than_seconds=0`. Normal recovery defaults remain 300 seconds for stale work and 15 seconds for queued dispatch. Whole-container worker loss was followed by manual recovery and an API retry; automatic Redis visibility-timeout redelivery after whole-container loss was **not** verified. No exactly-once delivery, automatic recovery scheduler, or production resilience is claimed. Duplicate findings are checked within each run; separate runs intentionally retain separate findings.

Generated traces, databases, videos, exports, and environments remain ignored by Git. `.env` remains excluded; `.env.example` contains demo placeholders. No Docker disks were manually moved/deleted, no volumes were removed, and no application records were deleted/reset. Only temporary test schemas in the dedicated test database were dropped by PostgreSQL fixtures.

## GitHub and CI

The initial implementation was successfully pushed to `main` as `6c47d0629f0dc301cdb315834d61420fce00ccfe`. The previous statement that the repository had not been pushed or CI had not run was stale. GitHub API verification showed successful `core` and `postgres` jobs, including migration, schema drift, and test steps: https://github.com/saiganeshraj/ReconcileOps/actions/runs/43907858889.

That CI run applies to the initial commit. The acceptance fixes are locally verified as described above; subsequent CI runs must be identified by their own commit SHA. Commits use the personal `saiganeshraj` account and GitHub noreply identity.

## Earlier evidence retained for context

Before Docker acceptance, an isolated native PostgreSQL 17 cluster was created at `artifacts/recruiter-acceptance-postgres`, bound to `127.0.0.1:55433`, with dedicated database `reconcileops_acceptance`. `TEST_DATABASE_URL` and `DATABASE_URL` targeted only that database. Four PostgreSQL tests passed in 3.09s; `alembic upgrade head` and `alembic check` passed. That acceptance server was stopped cleanly and its data retained.

The older browser harness at `127.0.0.1:18000` used real PostgreSQL/provider HTTP but inline/eager tasks. Its evidence remains under `artifacts/browser/`; it is historical and is not the proof of Celery/Redis delivery above.

Earlier measured benchmark (not rerun in this acceptance): Python 3.12.2, Windows 11 build 26200, in-memory SQLite, 10,000 orders, zero events, and 10,000 persisted missing-payment findings. Preview 3.3465s; commit 15.7243s; snapshot/reconciliation/persistence 3.1591s. Machine-readable evidence: [benchmark.json](benchmark.json). This is not a PostgreSQL throughput result.

## Remaining boundary

The project remains a localhost-only synthetic recruiter demo, not a public-production service. Authentication, visitor isolation, TLS/session hardening, monitoring, and operational backup/restore acceptance remain outside this task. One TestClient deprecation warning remains. Browser traces/videos can be regenerated locally; only selected screenshots are tracked.
