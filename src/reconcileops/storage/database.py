"""Database engine, session management, and PostgreSQL advisory lock support."""

import hashlib
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from reconcileops.config import settings
from reconcileops.storage.models import Base

# Engine configuration
connect_args: dict[str, Any] = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False
    engine = create_engine(
        settings.DATABASE_URL,
        connect_args=connect_args,
        echo=False,
    )
else:
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_pre_ping=True,
        echo=False,
    )

SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Create all tables in the database."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for obtaining a database session."""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """Context manager for obtaining a database session outside HTTP requests."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def string_to_lock_id(key: str) -> int:
    """Convert an arbitrary string into a signed 64-bit integer for PostgreSQL advisory locks."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    # Signed 64-bit int (-2^63 to 2^63 - 1)
    val = int.from_bytes(digest[:8], byteorder="big", signed=True)
    return val


def acquire_advisory_lock(session: Session, lock_name: str) -> bool:
    """Acquire a transaction-level advisory lock in PostgreSQL.

    Returns True if lock acquired, False if another worker holds it.
    If dialect is SQLite, returns True (SQLite handles locking at file level).
    """
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        lock_id = string_to_lock_id(lock_name)
        result = session.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        ).scalar()
        return bool(result)
    # Non-postgres dialect fallback
    return True


def wait_for_lock(session: Session, name: str) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": string_to_lock_id(name)}
        )


@contextmanager
def execution_lease(session: Session, name: str):
    """Session lock on a dedicated connection survives business transaction commits."""
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        yield True
        return
    key = string_to_lock_id(name)
    with bind.connect() as connection:
        acquired = bool(
            connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        )
        connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                connection.commit()
