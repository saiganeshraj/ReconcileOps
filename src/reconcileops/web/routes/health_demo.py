"""Dependency readiness and gated synthetic demo controls."""

import logging

import httpx
import redis
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from reconcileops.config import settings
from reconcileops.storage.database import get_db
from reconcileops.storage.models import utc_now

logger = logging.getLogger(__name__)
health_demo_router = APIRouter()


@health_demo_router.get("/health/live")
def health_live():
    return {"status": "live", "timestamp": utc_now().isoformat()}


@health_demo_router.get("/health/ready")
def health_ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT version_num FROM alembic_version"))
        redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2).ping()
        httpx.get(settings.MOCK_PROVIDER_URL + "/health", timeout=2).raise_for_status()
    except Exception as exc:
        logger.warning("Readiness failed: %s", type(exc).__name__)
        raise HTTPException(
            503, "Dependencies not ready. Check migrations, PostgreSQL, Redis, and mock provider."
        ) from exc
    return {
        "status": "ready",
        "database": "connected",
        "redis": "connected",
        "provider": "connected",
    }


def require_demo():
    if not settings.DEMO_MODE:
        raise HTTPException(403, "Demo controls disabled outside DEMO_MODE.")


@health_demo_router.post("/api/demo/reset")
def reset_demo_data(x_demo_token: str | None = Header(None), db: Session = Depends(get_db)):
    require_demo()
    if x_demo_token != settings.DEMO_TOKEN:
        raise HTTPException(401, "Valid X-Demo-Token required for reset.")
    from reconcileops.storage.demo import reset_owned_demo

    try:
        return {"status": "reset_successful", **reset_owned_demo(db)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@health_demo_router.post("/api/demo/seed")
def seed_demo_data(db: Session = Depends(get_db)):
    require_demo()
    from reconcileops.scripts_seed import run_seed_baseline

    return {"status": "seed_successful", **run_seed_baseline(db)}
