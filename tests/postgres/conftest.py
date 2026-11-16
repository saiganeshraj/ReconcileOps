"""Isolate every PostgreSQL test in a fresh randomly named schema."""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from reconcileops.storage.models import Base


@pytest.fixture
def pg_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.fail(
            "Set TEST_DATABASE_URL to an isolated PostgreSQL database. These checks cannot use SQLite."
        )
    schema = "test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema}"}, pool_size=10, max_overflow=10
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()
