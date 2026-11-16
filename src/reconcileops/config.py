"""Configuration and application settings for ReconcileOps."""

from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    APP_NAME: str = "ReconcileOps"
    APP_ENV: str = "development"
    DEBUG: bool = True
    APP_SECRET_KEY: str = "change-in-production-demo-secret-key-32chars!"

    # Database
    DATABASE_URL: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/reconcileops",
        description="Database URL for SQLAlchemy 2.0 (PostgreSQL recommended; SQLite supported for local testing)",
    )
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # Redis & Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # Provider & Mock API
    MOCK_PROVIDER_URL: str = "http://localhost:8001"
    PROVIDER_REQUEST_TIMEOUT_SECONDS: float = 10.0
    PROVIDER_MAX_RETRIES: int = 3
    PROVIDER_BACKOFF_FACTOR: float = 0.5
    PROVIDER_RETRY_AFTER_CAP_SECONDS: float = 10.0

    # Demo Mode & Security
    DEMO_MODE: bool = True
    DEMO_TOKEN: str = "reconcileops-demo-secret-token"

    # Data Limits
    MAX_UPLOAD_BYTES: int = 5 * 1024 * 1024  # 5 MB
    MAX_UPLOAD_ROWS: int = 10_000
    DATA_DIR: Path = Path("data")
    UPLOAD_DIR: Path = Path("data/uploads")

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            lower = v.strip().lower()
            if lower in ("1", "true", "yes", "on", "debug"):
                return True
            if lower in ("0", "false", "no", "off", "release", "prod", "production"):
                return False
        return bool(v)


settings = Settings()
