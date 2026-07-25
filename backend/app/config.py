from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    app_name: str = "Safar API"
    public_base_url: str = "http://127.0.0.1:8000"
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    supabase_secret_key: str | None = None

    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_maps_api_key: str | None = None
    token_encryption_key: str | None = None

    sarvam_api_key: str | None = None
    sarvam_model: str = "sarvam-105b"
    serpapi_api_key: str | None = None
    amadeus_client_id: str | None = None
    amadeus_client_secret: str | None = None
    amadeus_env: str = "test"

    travel_provider_mode: str = "auto"
    enable_resilience_demo: bool = True
    auth_disabled: bool = False
    database_path: Path = Path("safar.db")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_secret_key)

    @property
    def google_auth_ready(self) -> bool:
        return bool(
            self.google_client_id
            and self.google_client_secret
            and self.supabase_url
            and self.supabase_publishable_key
        )

    @property
    def google_callback_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/v1/auth/google/callback"

    @property
    def calendar_callback_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/v1/calendar/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
