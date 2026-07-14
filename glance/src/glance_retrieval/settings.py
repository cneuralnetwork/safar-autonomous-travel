"""Central, environment-driven paths and model configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Paths are relative to the project root by default."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="GLANCE_", extra="ignore")

    project_root: Path = Path(__file__).resolve().parents[2]
    data_dir: Path | None = None
    qdrant_url: str = "http://localhost:6333"
    records_path: Path = Path("data/processed/corpus.jsonl")
    generic_model: str = "openai/clip-vit-base-patch32"
    fashion_model: str = "Marqo/marqo-fashionCLIP"
    fashion_adapter: str | None = None
    query_model: str = "Qwen/Qwen2.5-1.5B-Instruct"
    device: str = "auto"
    use_qwen_parser: bool = False

    @property
    def resolved_data_dir(self) -> Path:
        return self.data_dir or self.project_root / "data"

    @property
    def resolved_records_path(self) -> Path:
        return self.records_path if self.records_path.is_absolute() else self.project_root / self.records_path

    @property
    def static_dir(self) -> Path:
        return self.project_root / "src" / "glance_retrieval" / "static"


@lru_cache
def get_settings() -> Settings:
    return Settings()
