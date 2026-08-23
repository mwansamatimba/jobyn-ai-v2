"""Application configuration."""

import sys
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "Jobyn AI"
    APP_VERSION: str = "2.0.0"

    ENVIRONMENT: Literal[
        "development",
        "test",
        "staging",
        "production",
    ] = "development"

    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # LLM
    LLM_PROVIDER: str = "nvidia"
    NVIDIA_API_KEY: str | None = None
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_MODEL: str = ""
    LLM_TIMEOUT_SECONDS: float = 60.0

    TABITOKEN_API_KEY: str = "sk-ZDTlMGhJMcGFBNWmRnh3fgrZlu8Zi6FJddhXpBRK6pgCnFEk" 
    TABITOKEN_BASE_URL: str = "https://tabitoken.com/v1"
    TABITOKEN_COVER_LETTER_MODEL: str = "claude-opus-5"
    API_V1_PREFIX: str = "/api/v1"
    TABITOKEN_FALLBACK_ENABLED: bool = True
    SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080

    DATABASE_URL: str = "sqlite+aiosqlite:///./jobyn.db"

    REDIS_URL: str = "redis://localhost:6379/0"

    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    CELERY_TASK_TRACK_STARTED: bool = True
    CELERY_WORKER_CONCURRENCY: int = 4

    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:8000"
    ]

    @field_validator(
        "BACKEND_CORS_ORIGINS",
        mode="before",
    )
    @classmethod
    def split_cors_origins(cls, value):

        if isinstance(value, str):
            return [
                item.strip()
                for item in value.split(",")
                if item.strip()
            ]

        return value


    @property
    def is_production(self):
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:

    return Settings()


settings = get_settings()

# --- Backward-compatibility shim --------------------------------------------
# This module replaces the old `backend/core/config/` package (which had a
# `settings.py` submodule). Anything still doing
# `from backend.core.config.settings import settings` would otherwise fail
# with "ModuleNotFoundError: ... 'backend.core.config' is not a package".
# Registering this module under the old dotted path lets those imports keep
# resolving without having to touch every call site right now.
sys.modules[f"{__name__}.settings"] = sys.modules[__name__]