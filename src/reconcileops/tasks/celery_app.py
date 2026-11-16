"""Celery application configuration."""

from celery import Celery

from reconcileops.config import settings

celery_app = Celery(
    "reconcileops",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["reconcileops.tasks.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes hard limit
    worker_prefetch_multiplier=1,  # Prevent worker starving other tasks
    task_acks_late=True,  # At-least-once delivery: acknowledge task only after execution finishes
    task_reject_on_worker_lost=True,  # Re-queue task if worker crashes
)
