from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


Environment = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    """
    Application configuration.

    Values are loaded from environment variables.

    Vercel:
        Environment variables are supplied by the Vercel runtime.

    Local development:
        Values can be supplied through .env.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    APP_NAME: str = "Jobyn AI"
    ENVIRONMENT: Environment = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    API_V1_PREFIX: str = "/api/v1"

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    SECRET_KEY: str = Field(
        default="dev-only-change-this-secret-key",
        min_length=16,
    )

    JWT_ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    DATABASE_URL: str = "sqlite+aiosqlite:///./jobyn.db"

    # ------------------------------------------------------------------
    # Redis / Celery
    # ------------------------------------------------------------------

    REDIS_URL: str = "redis://localhost:6379/0"

    CELERY_BROKER_URL: str = "redis://localhost:6379/0"

    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    CELERY_TASK_TRACK_STARTED: bool = False

    CELERY_WORKER_CONCURRENCY: int = 1

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------

    BACKEND_CORS_ORIGINS: list[str] = Field(default_factory=list)

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> list[str]:
        """
        Accept either:

        JSON:
            ["https://example.com", "http://localhost:3000"]

        Comma separated:
            https://example.com,http://localhost:3000

        Or a single origin.
        """

        if value is None:
            return []

        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]

        if isinstance(value, str):
            value = value.strip()

            if not value:
                return []

            # Try JSON first.
            if value.startswith("["):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return [
                            str(item).strip()
                            for item in parsed
                            if str(item).strip()
                        ]
                except json.JSONDecodeError:
                    pass

            return [
                item.strip()
                for item in value.split(",")
                if item.strip()
            ]

        return [str(value)]

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def normalize_environment(cls, value: object) -> str:
        if value is None:
            return "development"

        value = str(value).strip().lower()

        if not value:
            return "development"

        return value

    @field_validator("DEBUG", mode="before")
    @classmethod
    def normalize_debug(cls, value: object) -> bool:
        if value is None:
            return False

        if isinstance(value, bool):
            return value

        value = str(value).strip().lower()

        if value in {"true", "1", "yes", "on"}:
            return True

        if value in {"false", "0", "no", "off", ""}:
            return False

        return False

    @field_validator(
        "CELERY_TASK_TRACK_STARTED",
        mode="before",
    )
    @classmethod
    def normalize_celery_tracking(cls, value: object) -> bool:
        if value is None:
            return False

        if isinstance(value, bool):
            return value

        value = str(value).strip().lower()

        if value in {"true", "1", "yes", "on"}:
            return True

        return False

    @field_validator(
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "CELERY_WORKER_CONCURRENCY",
        mode="before",
    )
    @classmethod
    def normalize_positive_int(cls, value: object) -> int:
        if value is None:
            return 1

        if isinstance(value, int):
            return value

        value = str(value).strip()

        if not value:
            return 1

        return int(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()