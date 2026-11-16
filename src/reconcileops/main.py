"""Main FastAPI application entrypoint for ReconcileOps."""

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from reconcileops.config import settings
from reconcileops.web.dashboard import router as dashboard_router
from reconcileops.web.routes.api import api_router
from reconcileops.web.routes.health_demo import health_demo_router

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database on startup."""
    logger.info("ReconcileOps starting; schema is managed by Alembic migrations.")
    yield
    logger.info("ReconcileOps shut down.")


app = FastAPI(
    title="ReconcileOps",
    description="Order and Payment Reconciliation Automation Platform",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    """Double-submit token for browsers; non-browser clients require a custom header."""
    token = request.cookies.get("reconcileops_csrf")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        browser = bool(
            request.headers.get("Origin") or request.headers.get("Sec-Fetch-Site") or token
        )
        if browser:
            valid = token and secrets.compare_digest(token, supplied)
            origin = request.headers.get("Origin")
            valid = valid and (not origin or origin == str(request.base_url).rstrip("/"))
        else:
            valid = supplied == "cli"
        if not valid:
            return JSONResponse(
                {"detail": "Valid X-CSRF-Token required. CLI clients use X-CSRF-Token: cli."},
                status_code=403,
            )
    response = await call_next(request)
    if request.method == "GET" and not token:
        response.set_cookie(
            "reconcileops_csrf", secrets.token_urlsafe(32), samesite="strict", httponly=False
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'"
        if request.url.path != "/docs"
        else "default-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net"
    )
    return response


app.include_router(health_demo_router)
app.include_router(api_router)
app.include_router(dashboard_router)
app.mount(
    "/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static"
)
app.mount(
    "/fixtures",
    StaticFiles(directory=str(Path(__file__).resolve().parents[2] / "fixtures")),
    name="fixtures",
)


@app.get("/api/info")
def root():
    return {
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health/live",
        "api": "/api",
    }
