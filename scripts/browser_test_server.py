"""Isolated browser harness when Linux containers are unavailable.

Uses actual PostgreSQL and a separate HTTP provider, but Celery eager delivery.
This is a test harness, not the supported local deployment or a Windows worker.
"""

import os
import subprocess
import sys

import uvicorn

if os.environ.get("APP_ENV") != "test" or "55432" not in os.environ.get("DATABASE_URL", ""):
    raise SystemExit(
        "Harness requires APP_ENV=test and the isolated localhost:55432 PostgreSQL cluster."
    )

from reconcileops.tasks.celery_app import celery_app

celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
provider = subprocess.Popen(
    [
        sys.executable,
        "-m",
        "uvicorn",
        "reconcileops.provider.mock_server:mock_provider_app",
        "--host",
        "127.0.0.1",
        "--port",
        "18001",
    ],
    creationflags=flags,
)
try:
    uvicorn.run("reconcileops.main:app", host="127.0.0.1", port=18000)
finally:
    provider.terminate()
    provider.wait(timeout=10)
