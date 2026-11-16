"""Provider synchronization client with cursor pagination and resilient retry logic."""

import hashlib
import json
import logging
import random
import re
import time
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from dateutil import parser as date_parser
from sqlalchemy import select
from sqlalchemy.orm import Session

from reconcileops.config import settings
from reconcileops.domain.models import ProviderEvent, ensure_utc
from reconcileops.domain.money import parse_money, validate_currency
from reconcileops.storage.database import execution_lease, wait_for_lock
from reconcileops.storage.models import (
    ProviderConflict,
    ProviderEventModel,
    SyncRun,
    utc_now,
)

logger = logging.getLogger(__name__)


class ProviderSyncError(Exception):
    def __init__(self, message: str, category: str = "PROVIDER_ERROR"):
        super().__init__(message)
        self.category = category


class PermanentProviderError(ProviderSyncError):
    pass


class TransientProviderError(ProviderSyncError):
    pass


def compute_event_canonical_hash(
    event_id: str,
    payment_id: str,
    order_id: str,
    event_type: str,
    currency: str,
    amount: Decimal,
    occurred_at: datetime,
) -> str:
    canonical = json.dumps(
        [
            event_id.strip(),
            payment_id.strip(),
            order_id.strip(),
            event_type.strip().lower(),
            currency.strip().upper(),
            f"{amount:.2f}",
            occurred_at.isoformat(),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ProviderClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
        retry_after_cap: float | None = None,
        transport: httpx.BaseTransport | None = None,
        page_size: int = 50,
        max_pages: int = 2000,
    ):
        self.base_url = (base_url or settings.MOCK_PROVIDER_URL).rstrip("/")
        self.timeout = timeout or settings.PROVIDER_REQUEST_TIMEOUT_SECONDS
        self.max_retries = settings.PROVIDER_MAX_RETRIES if max_retries is None else max_retries
        self.backoff_factor = (
            settings.PROVIDER_BACKOFF_FACTOR if backoff_factor is None else backoff_factor
        )
        self.retry_after_cap = (
            settings.PROVIDER_RETRY_AFTER_CAP_SECONDS
            if retry_after_cap is None
            else retry_after_cap
        )
        self.transport = transport
        if not 1 <= page_size <= 100 or not 1 <= max_pages <= 2000:
            raise ValueError("Provider page size must be 1–100 and page bound 1–2000.")
        self.page_size = page_size
        self.max_pages = max_pages
        self.retry_log = []

    def _pause(self, delay: float, category: str, attempt: int):
        delay = min(max(0, delay) + random.uniform(0.01, 0.1), self.retry_after_cap)
        entry = {"attempt": attempt, "category": category, "delay_seconds": round(delay, 4)}
        self.retry_log.append(entry)
        logger.warning("provider_retry %s", json.dumps(entry))
        time.sleep(delay)

    def _create_client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "timeout": self.timeout,
        }
        if self.transport:
            kwargs["transport"] = self.transport
        return httpx.Client(**kwargs)

    def fetch_page_with_retry(
        self,
        client: httpx.Client,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Fetch a single page of events with bounded exponential backoff and jitter."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        attempt = 0
        while attempt <= self.max_retries:
            attempt += 1
            try:
                response = client.get("/events", params=params)

                # Permanent 4xx errors should fail immediately without retries
                if response.status_code == 401:
                    raise PermanentProviderError(
                        "Unauthorized 401: Invalid credentials for payment provider", "AUTH_FAILURE"
                    )
                if response.status_code == 403:
                    raise PermanentProviderError(
                        "Forbidden 403: Access denied by payment provider", "AUTH_FAILURE"
                    )
                if response.status_code == 404:
                    raise PermanentProviderError(
                        "Not Found 404: Provider endpoint missing", "ENDPOINT_NOT_FOUND"
                    )

                # Handle Rate Limit 429
                if response.status_code == 429:
                    if attempt > self.max_retries:
                        raise TransientProviderError(
                            "Exceeded maximum retries on HTTP 429 Rate Limit", "RATE_LIMIT_EXCEEDED"
                        )
                    # Parse Retry-After header
                    retry_after_header = response.headers.get("Retry-After")
                    delay = self.backoff_factor * (2 ** (attempt - 1))
                    if retry_after_header:
                        try:
                            delay = float(retry_after_header)
                        except ValueError:
                            try:
                                delay = max(
                                    0,
                                    (
                                        parsedate_to_datetime(retry_after_header)
                                        - datetime.now(UTC)
                                    ).total_seconds(),
                                )
                            except (ValueError, TypeError, OverflowError):
                                pass
                    self._pause(delay, "HTTP_429", attempt)
                    continue

                # Handle 5xx Server Errors
                if response.status_code >= 500:
                    if attempt > self.max_retries:
                        raise TransientProviderError(
                            f"Server error {response.status_code} from provider after {attempt} attempts",
                            "PROVIDER_OUTAGE",
                        )
                    self._pause(self.backoff_factor * (2 ** (attempt - 1)), "HTTP_5XX", attempt)
                    continue

                if 400 <= response.status_code < 500:
                    raise PermanentProviderError(
                        f"Provider rejected request (HTTP {response.status_code}).",
                        "PERMANENT_HTTP_ERROR",
                    )
                # Success
                response.raise_for_status()
                return response.json()

            except (httpx.TimeoutException, httpx.NetworkError) as err:
                if attempt > self.max_retries:
                    raise TransientProviderError(
                        f"Network timeout / connection error after {attempt} attempts: {type(err).__name__}",
                        "NETWORK_TIMEOUT",
                    ) from err
                self._pause(self.backoff_factor * (2 ** (attempt - 1)), "NETWORK_TIMEOUT", attempt)

        raise TransientProviderError("Exceeded retry attempts", "RETRY_EXHAUSTED")

    def sync_all_events(
        self,
        session: Session,
        sync_run_id: str | None = None,
    ) -> SyncRun:
        """Fetch all events across pages and persist them into provider_events table safely."""
        with execution_lease(session, "provider_sync") as acquired:
            if not acquired:
                raise ProviderSyncError(
                    "Another sync is active; queued request can be dispatched later.", "SYNC_BUSY"
                )
            return self._sync_locked(session, sync_run_id)

    def _sync_locked(self, session: Session, sync_run_id: str | None) -> SyncRun:
        self.retry_log = []
        sync_run = session.get(SyncRun, sync_run_id) if sync_run_id else None
        if sync_run and sync_run.status == "SUCCEEDED":
            return sync_run
        if sync_run is None:
            sync_run = SyncRun(id=sync_run_id) if sync_run_id else SyncRun()
        session.add(sync_run)
        sync_run.status = "RUNNING"
        sync_run.started_at = utc_now()
        session.commit()
        sync_id = sync_run.id
        # Fetch and validate into memory before opening the publication transaction.
        session.commit()
        raw_events = []
        cursor = None
        seen_cursors = set()
        page_count = 0
        try:
            with self._create_client() as client:
                while True:
                    if page_count >= self.max_pages:
                        raise ValueError("Provider pagination exceeded the configured page bound.")
                    page_count += 1
                    page = self.fetch_page_with_retry(client, cursor=cursor, limit=self.page_size)
                    for raw in page["events"]:
                        if not isinstance(raw.get("amount"), str) or not re.fullmatch(
                            r"(?:0|[1-9]\d*)\.\d{2}", raw["amount"]
                        ):
                            raise ValueError(
                                "Provider amounts must be positive two-decimal strings."
                            )
                        if date_parser.isoparse(raw["occurred_at"]).tzinfo is None:
                            raise ValueError("Provider timestamps must include a timezone.")
                        event = ProviderEvent.model_validate(raw)
                        if event.amount <= 0 or any(
                            len(getattr(event, name)) > 128
                            for name in ("event_id", "payment_id", "order_id")
                        ):
                            raise ValueError(
                                "Provider amount must be positive and identifiers at most 128 characters."
                            )
                        raw_events.append(event.model_dump(mode="json"))
                    if len(raw_events) > 100000:
                        raise ValueError(
                            "Provider synchronization exceeds the 100,000 event bound."
                        )
                    next_cursor = page.get("next_cursor")
                    if not next_cursor:
                        break
                    if next_cursor in seen_cursors or len(raw_events) > 100000:
                        raise ValueError(
                            "Provider pagination exceeded limits or repeated a cursor."
                        )
                    seen_cursors.add(next_cursor)
                    cursor = next_cursor
        except Exception as exc:
            session.rollback()
            sync_run = session.get(SyncRun, sync_id)
            sync_run.status = "FAILED"
            sync_run.finished_at = utc_now()
            sync_run.failure_category = getattr(exc, "category", "INVALID_PROVIDER_RESPONSE")
            sync_run.retry_count = len(self.retry_log)
            sync_run.retry_log_json = json.dumps(self.retry_log)
            sync_run.error_message = "Provider synchronization failed: " + sync_run.failure_category
            session.commit()
            raise
        wait_for_lock(session, "snapshot_publish")

        # Query existing events to detect duplicate delivery vs. conflict
        existing_events_query = session.execute(
            select(ProviderEventModel.event_id, ProviderEventModel.canonical_hash)
        ).all()
        existing_events: dict[str, str] = {row[0]: row[1] for row in existing_events_query}

        total_fetched = len(raw_events)
        total_imported = 0
        total_duplicate = 0
        total_conflicted = 0
        try:
            for raw_event in raw_events:
                event_id = str(raw_event["event_id"]).strip()
                payment_id = str(raw_event["payment_id"]).strip()
                order_id = str(raw_event["order_id"]).strip()
                event_type = str(raw_event["event_type"]).strip().lower()
                currency = validate_currency(str(raw_event["currency"]))
                amount = parse_money(raw_event["amount"], field_name="amount")
                occurred_at = ensure_utc(date_parser.parse(str(raw_event["occurred_at"])))

                can_hash = compute_event_canonical_hash(
                    event_id, payment_id, order_id, event_type, currency, amount, occurred_at
                )

                if event_id in existing_events:
                    db_hash = existing_events[event_id]
                    if db_hash == can_hash:
                        total_duplicate += 1
                        # Duplicate delivery: safe to ignore
                        continue
                    else:
                        total_conflicted += 1
                        session.add(
                            ProviderConflict(
                                sync_run_id=sync_run.id,
                                event_id=event_id,
                                original_hash=db_hash,
                                incoming_hash=can_hash,
                                incoming_json=json.dumps(raw_event),
                            )
                        )
                        # Conflicting content: never overwrite silently
                        continue

                # New event
                event_model = ProviderEventModel(
                    event_id=event_id,
                    payment_id=payment_id,
                    order_id=order_id,
                    event_type=event_type,
                    currency=currency,
                    amount=amount,
                    occurred_at=occurred_at,
                    canonical_hash=can_hash,
                    sync_run_id=sync_run.id,
                )
                session.add(event_model)
                existing_events[event_id] = can_hash
                total_imported += 1

            sync_run.status = "SUCCEEDED"
            sync_run.retry_count = len(self.retry_log)
            sync_run.retry_log_json = json.dumps(self.retry_log)
            sync_run.finished_at = utc_now()
            sync_run.records_fetched = total_fetched
            sync_run.records_imported = total_imported
            sync_run.records_duplicate = total_duplicate
            sync_run.records_conflicted = total_conflicted
            sync_run.high_water_mark = sync_id
            session.flush()
            session.commit()
            return sync_run

        except Exception as exc:
            session.rollback()
            sync_run = session.get(SyncRun, sync_id)
            sync_run.status = "FAILED"
            sync_run.finished_at = utc_now()
            sync_run.records_fetched = total_fetched
            sync_run.records_imported = 0  # Rollback partial publish
            sync_run.failure_category = getattr(exc, "category", "UNEXPECTED_ERROR")
            sync_run.error_message = str(exc)
            session.flush()
            session.commit()
            raise
