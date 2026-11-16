"""Celery background tasks for ReconcileOps."""

import logging
from typing import Any

from reconcileops.provider.client import ProviderClient
from reconcileops.storage.database import get_db_context
from reconcileops.storage.reconcile_service import execute_reconciliation_run
from reconcileops.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="reconcileops.tasks.reconcile_task", max_retries=3, acks_late=True)
def reconcile_task(self, run_id: str) -> dict[str, Any]:
    """Execute a reconciliation run within a Celery background worker."""
    logger.info("Starting reconciliation task for run_id=%s", run_id)
    with get_db_context() as session:
        try:
            run = execute_reconciliation_run(session, run_id, actor="celery_worker")
            logger.info(
                "Completed run_id=%s with status=%s, findings=%d",
                run_id,
                run.status,
                run.findings_count,
            )
            return {
                "run_id": run.id,
                "status": run.status,
                "orders_count": run.orders_count,
                "findings_count": run.findings_count,
            }
        except Exception as exc:
            logger.exception("Failed execution for run_id=%s: %s", run_id, exc)
            # Re-raise for Celery retry or failure recording
            raise


@celery_app.task(
    bind=True, name="reconcileops.tasks.sync_provider_task", max_retries=3, acks_late=True
)
def sync_provider_task(self, sync_run_id: str | None = None) -> dict[str, Any]:
    """Execute an upstream payment provider sync within a Celery background worker."""
    logger.info("Starting provider sync task, sync_run_id=%s", sync_run_id)
    client = ProviderClient()
    with get_db_context() as session:
        try:
            sync_run = client.sync_all_events(session, sync_run_id=sync_run_id)
            logger.info(
                "Completed sync_run_id=%s: fetched=%d, imported=%d, duplicates=%d",
                sync_run.id,
                sync_run.records_fetched,
                sync_run.records_imported,
                sync_run.records_duplicate,
            )
            return {
                "sync_run_id": sync_run.id,
                "status": sync_run.status,
                "imported": sync_run.records_imported,
                "duplicates": sync_run.records_duplicate,
            }
        except Exception as exc:
            logger.exception("Failed sync_run_id=%s: %s", sync_run_id, exc)
            raise
