"""Application configuration.

Settings are loaded from environment variables and an optional ``.env`` file.
This module is a leaf node in the dependency graph: nothing in the application
imports it transitively back, which is what prevents circular imports at the
core level.
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Jobyn AI API.

    Every value can be overridden through an environment variable or a ``.env``
    file placed at the repository root. Prefixing the fields with ``APP_`` etc.
    is intentionally avoided: pydantic-settings reads fields case-insensitively
    and matches environment variables by field name.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "Jobyn AI"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: Literal["development", "test", "staging", "production"] = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    API_V1_PREFIX: str = "/api/v1"

    SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080

    DATABASE_URL: str = "sqlite+aiosqlite:///./jobyn.db"

    REDIS_URL: str = "redis://localhost:6379/0"

    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    CELERY_TASK_TRACK_STARTED: bool = True
    CELERY_WORKER_CONCURRENCY: int = 4

    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:8000"]

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance.

    The cache is invalidated in tests by clearing ``get_settings.cache_clear()``
    or by setting environment variables before the first import of this module.
    """
    return Settings()
