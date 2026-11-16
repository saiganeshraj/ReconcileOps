"""Exercise the provider as a separate real HTTP process, not only an ASGI adapter."""

import os
import socket
import subprocess
import sys
import time

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from reconcileops.config import settings
from reconcileops.provider.client import ProviderClient
from reconcileops.storage.models import Base


def test_real_mock_service_pagination_and_503_recovery():
    with socket.socket() as socket_handle:
        socket_handle.bind(("127.0.0.1", 0))
        port = socket_handle.getsockname()[1]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "reconcileops.provider.mock_server:mock_provider_app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env={**os.environ, "DEMO_MODE": "true"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                if httpx.get(url + "/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise AssertionError("Real provider process failed to start.")
            time.sleep(0.1)
        response = httpx.post(
            url + "/demo/fault",
            json={"mode": "503_ONCE"},
            headers={"X-Demo-Token": settings.DEMO_TOKEN},
        )
        assert response.status_code == 200
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            sync = ProviderClient(
                base_url=url, backoff_factor=0, retry_after_cap=0, page_size=2
            ).sync_all_events(db)
            assert sync.records_imported == 6
            assert sync.retry_count == 1
            assert sync.status == "SUCCEEDED"
    finally:
        process.terminate()
        process.wait(timeout=10)
