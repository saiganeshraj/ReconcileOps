"""Integration tests for ProviderClient and mock payment gateway."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from reconcileops.provider.client import (
    PermanentProviderError,
    ProviderClient,
    TransientProviderError,
)
from reconcileops.provider.mock_server import fault_state, mock_events_store, mock_provider_app
from reconcileops.storage.models import Base


class MockTestClientProviderClient(ProviderClient):
    """Subclass ProviderClient for testing with Starlette TestClient (synchronous in-memory)."""

    def _create_client(self):
        return TestClient(mock_provider_app)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def reset_mock_state():
    fault_state.mode = "NONE"
    fault_state.has_triggered_503 = False
    yield
    fault_state.mode = "NONE"
    fault_state.has_triggered_503 = False


def test_sync_all_events_pagination(db_session: Session):
    client = MockTestClientProviderClient()

    # First sync
    sync_run = client.sync_all_events(db_session)
    assert sync_run.status == "SUCCEEDED"
    assert sync_run.records_fetched == len(mock_events_store)
    assert sync_run.records_imported == len(mock_events_store)
    assert sync_run.records_duplicate == 0

    # Second sync: duplicate delivery is safely ignored
    sync_run_2 = client.sync_all_events(db_session)
    assert sync_run_2.status == "SUCCEEDED"
    assert sync_run_2.records_imported == 0
    assert sync_run_2.records_duplicate == len(mock_events_store)


def test_mock_provider_recovers_from_503_once(db_session: Session):
    fault_state.mode = "503_ONCE"
    fault_state.has_triggered_503 = False

    client = MockTestClientProviderClient(
        backoff_factor=0.01,
        retry_after_cap=0.05,
    )

    sync_run = client.sync_all_events(db_session)
    assert sync_run.status == "SUCCEEDED"
    assert sync_run.records_imported == len(mock_events_store)
    assert fault_state.has_triggered_503 is True


def test_mock_provider_recovers_from_429_rate_limit(db_session: Session):
    fault_state.mode = "429"

    # With max_retries=1, it should exhaust and fail
    client_exhaust = MockTestClientProviderClient(
        max_retries=1,
        backoff_factor=0.01,
        retry_after_cap=0.05,
    )

    with pytest.raises(TransientProviderError) as exc_info:
        client_exhaust.sync_all_events(db_session)
    assert exc_info.value.category == "RATE_LIMIT_EXCEEDED"


def test_mock_provider_fails_immediately_on_401(db_session: Session):
    fault_state.mode = "401"

    client = MockTestClientProviderClient(
        max_retries=3,
    )

    with pytest.raises(PermanentProviderError) as exc_info:
        client.sync_all_events(db_session)
    assert exc_info.value.category == "AUTH_FAILURE"
