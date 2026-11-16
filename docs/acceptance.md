# Acceptance record

This record distinguishes executed checks from prepared checks. Updated 16 November 2026. Project: `D:\Projects\ReconcileOps`. The application implementation is substantially complete; full Docker deployment acceptance remains blocked on moving Docker's disk off C:.

## Actual results

| Check | Command / environment | Actual outcome |
| --- | --- | --- |
| Inherited baseline | `.venv\Scripts\python.exe -m pytest -q` before changes | 28 passed; storage checks used SQLite |
| Fresh dependency install | Create `.venv-check`; `pip install -r requirements.lock`; `pip install --no-deps -e .` | Succeeded from a new Python 3.12 environment on D: |
| Dependency validation | `.venv-check\Scripts\python.exe -m pip check` | No broken requirements found |
| Final core suite | `.venv-check\Scripts\python.exe -m pytest tests/unit tests/integration -q` | **57 passed in 9.15s**, one Starlette TestClient deprecation warning, zero skipped |
| PostgreSQL concurrency | Set `TEST_DATABASE_URL=postgresql+psycopg://reconcileops_test@127.0.0.1:55432/postgres`; `.venv-check\Scripts\python.exe -m pytest tests/postgres -q` | **4 passed in 7.83s**, zero skipped |
| Initial migrations | Set `DATABASE_URL` to isolated localhost:55432 PostgreSQL; `alembic upgrade head` | All three revisions applied successfully |
| Schema drift | Same isolated PostgreSQL; `alembic check` | No new upgrade operations detected |
| Lint | `.venv-check\Scripts\ruff.exe check src tests scripts alembic` | All checks passed |
| Formatting | `.venv-check\Scripts\ruff.exe format --check src tests scripts alembic` | 57 files already formatted |
| Compose syntax | `docker compose config --quiet` | Passed; this does not prove containers start |
| Browser workflow | Playwright Chromium against `http://127.0.0.1:18000` | Passed, no JavaScript errors; actual PostgreSQL + separate provider HTTP process + explicit inline test task delivery |
| Mobile layout | 390 × 844 browser viewport | Passed document-width overflow assertion; actual screenshot inspected |
| 10,000-row workload | `.venv\Scripts\python.exe scripts/benchmark.py` | 10,000 orders, zero events, 10,000 persisted missing-payment findings; measurements below |

The core suite covers known-answer rules, exact money, refunds, malformed CSV, stale preview conflicts, duplicate delivery, conflicting event evidence, empty immutable snapshots, subsecond idempotency conflicts, failed later sync pages, storage rollback without partial results, bounded pagination/retries, source evidence, resolve/reopen audit history, CSRF rejection, demo reset ownership, abandoned attempts, bounded redelivery, and recovery after a committed queue/publish gap. One test launches the mock provider as a separate real HTTP process and verifies paginated 503 recovery. Broker publication is replaced at the boundary in core recovery tests; actual Redis delivery is not claimed.

The PostgreSQL tests cover six concurrent duplicate requests creating one run, four concurrent worker deliveries producing one attempt, recovery refusing an active worker lease, and SQL version checks preventing a reviewer with a stale identity map from overwriting another review.

## Benchmark

Environment: Python 3.12.2, Windows 11 build 26200, isolated in-memory SQLite, 757,853-byte synthetic CSV. Preview: **3.3465 s**; commit: **15.7243 s**; snapshot/reconciliation/persistence: **3.1591 s**. Counts were asserted against stored records. This is a measured unpaid-order workload, not a PostgreSQL throughput claim. Machine-readable results: [benchmark.json](benchmark.json).

## Browser evidence and preview

The local test preview is `http://127.0.0.1:18000`. It uses `scripts/browser_test_server.py`, PostgreSQL data at `data/verification-postgres`, and a provider process on localhost:18001. It executes the actual Celery task functions in eager/inline mode for test verification; it is not a supported native Windows Celery worker deployment. Redis readiness is expected to fail in this harness.

The browser imported/committed the baseline, synchronized the provider, persisted a seven-finding run, inspected original order/event evidence, resolved O102 with a note, checked the audit trail, and exported a full-run CSV. Repeated runs retained six unique orders and six unique provider events. Latest recorded run: `06056c5a-9c1a-44f0-8e39-52448d563166`.

Actual screenshots are retained in [screenshots/overview.png](screenshots/overview.png), [screenshots/evidence.png](screenshots/evidence.png), and [screenshots/mobile.png](screenshots/mobile.png). Local generated artifacts include `artifacts/browser/trace.zip`, `findings.csv`, `result.json`, and recording `artifacts/browser/recordings/55213bb4269acaeee84145490ee48ab4.webm`. Generated artifacts are ignored by Git and can be regenerated with the documented script.

## Remaining sign-off checks

- **Blocked:** Clean-start Docker image build and five-service startup, Linux Celery/Redis delivery, live broker outage/recovery, and worker-process-loss verification. The Compose configuration is prepared and validated, but these execution checks have not run.
- **Blocked:** Run the browser smoke against the normal Compose URL `http://localhost:8000`, rather than the explicitly labeled inline harness.
- **Not run remotely:** GitHub Actions. Its workflow is prepared; equivalent lint/core/PostgreSQL commands ran locally. No repository was pushed or published.

Docker Desktop was initially stopped; startup succeeded and reported a Linux engine. At the last storage inspection, its VHDX remained at `C:\Users\HP\AppData\Local\Docker\wsl\disk\docker_data.vhdx`, its settings had no D: data location, and `D:\DockerData` did not exist. Image downloads were deliberately withheld because the user requested keeping new project storage off the nearly full C: drive.

The desktop-control tools failed to initialize with `failed to write kernel assets: The system cannot find the path specified (os error 3)`. The user must finish the approved move through Docker Desktop → Settings → Resources → Advanced → Disk image location → D: → Apply & Restart. Once that location is verified, run `scripts/start-demo.ps1`, then the README's container acceptance commands and browser smoke. Record those actual results here before marking the whole project signed off.

No blocked, skipped, prepared, or unexecuted check is counted as passing. No native Windows Celery worker, public deployment, or production authentication is claimed.
