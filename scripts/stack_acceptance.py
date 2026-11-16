"""Disrupt only the local ReconcileOps demo; preserve all sources and run history.

Run after browser_smoke.py has loaded the six-order/six-event baseline. This
requires the normal Compose stack, not eager Celery or a mocked broker.
"""

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DOCKER = shutil.which("docker") or str(
    Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
)
COMMAND = [DOCKER, "compose", "--project-directory", str(ROOT), "-p", "reconcileops"]
BASE = "http://127.0.0.1:8000"
REPORT = ROOT / "artifacts/stack-acceptance.json"


def compose(*args, timeout=120):
    result = subprocess.run(
        [*COMMAND, *args], capture_output=True, text=True, timeout=timeout, check=True
    )
    return result.stdout.strip()


def web(source):
    return compose("exec", "-T", "web", "python", "-c", source)


def api(method, path, **kwargs):
    # Stateless requests avoid mixing the browser CSRF cookie with the CLI token.
    response = httpx.request(
        method, BASE + path, headers={"X-CSRF-Token": "cli"}, timeout=120, **kwargs
    )
    response.raise_for_status()
    return response.json()


def new_run():
    response = httpx.post(
        BASE + "/api/runs",
        headers={"X-CSRF-Token": "cli", "Idempotency-Key": "acceptance-" + uuid.uuid4().hex},
        json={"cutoff_time": "2026-11-16T12:00:00Z"},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["id"]


def wait_run(run_id, status="SUCCEEDED", timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = api("GET", "/api/runs/" + run_id)
        if detail["status"] == status:
            return detail
        time.sleep(0.5)
    raise TimeoutError(f"Run {run_id} did not become {status}: {detail}")


def stored_result(run_id, attempts):
    detail = wait_run(run_id)
    findings = api("GET", "/api/findings?run_id=" + run_id)
    assert (detail["orders_count"], detail["events_count"], detail["findings_count"]) == (6, 6, 7)
    assert len(findings) == 7
    assert len({(f["rule_code"], f["entity_type"], f["entity_id"]) for f in findings}) == 7
    assert len(detail["attempts"]) == attempts
    return {"run_id": run_id, "findings": 7, "attempts": detail["attempts"]}


def main():
    report = {"delivery": "real Linux Celery workers and Redis", "checks": {}}
    blocker = None
    try:
        assert (
            web(
                "from reconcileops.config import settings; "
                "from reconcileops.tasks.celery_app import celery_app; "
                "assert settings.DEMO_MODE; assert not celery_app.conf.task_always_eager; "
                "assert settings.MOCK_PROVIDER_URL == 'http://provider:8001'; print('demo-real')"
            )
            == "demo-real"
        )
        assert not any(
            r["status"] in {"QUEUED", "RUNNING"} for r in api("GET", "/api/runs?limit=500")
        )
        assert not any(s["status"] in {"QUEUED", "RUNNING"} for s in api("GET", "/api/syncs"))
        # Inspect labels before any disruption: never target another project's container.
        for service in ("worker", "redis"):
            container = compose("ps", "-q", service)
            label = subprocess.check_output(
                [
                    DOCKER,
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "com.docker.compose.project"}}',
                    container,
                ],
                text=True,
            ).strip()
            assert label == "reconcileops"

        run_id = new_run()
        stored_result(run_id, 1)
        web(
            "from reconcileops.tasks.tasks import reconcile_task; "
            f"jobs=[reconcile_task.delay({run_id!r}) for _ in range(4)]; "
            "[job.get(timeout=60) for job in jobs]; print('four deliveries completed')"
        )
        report["checks"]["duplicate_delivery"] = stored_result(run_id, 1)
        print(
            "PASS: four real duplicate deliveries, seven unique findings, one attempt", flush=True
        )

        # Queue first, then block snapshot reads so interruption occurs after the
        # worker has committed a genuine RUNNING attempt, without fake task code.
        compose("stop", "worker")
        run_id = new_run()
        source = (
            "from reconcileops.storage.database import engine; "
            "from sqlalchemy import text; "
            "c=engine.connect(); t=c.begin(); "
            "c.execute(text('LOCK TABLE snapshot_memberships IN ACCESS EXCLUSIVE MODE')); "
            "print('LOCKED', flush=True); input(); t.rollback(); c.close()"
        )
        blocker = subprocess.Popen(
            [*COMMAND, "exec", "-T", "web", "python", "-u", "-c", source],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert blocker.stdout.readline().strip() == "LOCKED"
        compose("start", "worker")
        wait_run(run_id, "RUNNING")
        compose("kill", "-s", "SIGKILL", "worker")
        blocker.communicate("\n", timeout=20)
        assert blocker.returncode == 0
        blocker = None
        recovered = json.loads(
            web(
                "import json; from reconcileops.storage.database import get_db_context; "
                "from reconcileops.tasks.recovery import recover_stale_running_runs; "
                "s=get_db_context(); db=s.__enter__(); "
                "ids=recover_stale_running_runs(db, stale_timeout_seconds=0); "
                "s.__exit__(None,None,None); print(json.dumps(ids))"
            )
        )
        assert run_id in recovered
        assert api("GET", "/api/runs/" + run_id)["status"] == "FAILED"
        compose("start", "worker")
        api("POST", "/api/runs/" + run_id + "/retry")
        result = stored_result(run_id, 2)
        assert [a["status"] for a in result["attempts"]] == ["FAILED", "SUCCEEDED"]
        report["checks"]["worker_interruption_recovery"] = result
        print(
            "PASS: killed running worker, recovered and retried, seven unique findings", flush=True
        )

        compose("stop", "redis")
        run_id = new_run()
        assert api("GET", "/api/runs/" + run_id)["status"] == "QUEUED"
        assert not api("GET", "/api/runs/" + run_id)["attempts"]
        compose("start", "redis")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if compose("exec", "-T", "redis", "redis-cli", "ping") == "PONG":
                break
            time.sleep(1)
        else:
            raise TimeoutError("Redis did not recover")
        # A zero age threshold selects the newly committed test run immediately;
        # production/manual recovery keeps its documented default of 15 seconds.
        web(
            "from reconcileops.storage.database import get_db_context; "
            "from reconcileops.tasks.recovery import dispatch_orphaned_queued_runs; "
            "s=get_db_context(); db=s.__enter__(); "
            "print(dispatch_orphaned_queued_runs(db, older_than_seconds=0)); "
            "s.__exit__(None,None,None)"
        )
        report["checks"]["redis_interruption_recovery"] = stored_result(run_id, 1)
        overview = api("GET", "/api/overview")
        assert (overview["orders"], overview["events"]) == (6, 6)
        assert api("GET", "/health/ready")["status"] == "ready"
        report["source_counts"] = {"orders": overview["orders"], "events": overview["events"]}
        report["status"] = "passed"
        print(
            "PASS: Redis outage preserved queued run; recovery produced seven unique findings",
            flush=True,
        )
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        if blocker is not None:
            blocker.communicate("\n", timeout=20)
        # Restore only the two demo services, without removing containers or data.
        compose("start", "redis", "worker")
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
