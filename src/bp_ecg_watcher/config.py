"""Configuration module for bp_ecg_file_watcher.

Reads all settings from environment variables and an optional .env file using
pydantic-settings. No values are hardcoded — every tunable parameter must come
from the environment.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables / .env file."""

    # Filesystem
    input_directory: Path
    output_directory: Path

    # Deduplication — defaults to <output_directory>/.dedup when not set
    dedup_directory: Path | None = None

    # Dask cluster
    dask_scheduler: str | None = None
    n_workers: int = 24
    cores_per_worker: int = 8
    mem_per_worker_gb: int = 16

    # Processing parameters
    rasterization_dpi: int = 300
    image_max_side_px: int = 1200
    zstd_level: int = 9

    # Observability
    log_level: str = "INFO"
    environment: str = "dev"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @model_validator(mode="after")
    def _resolve_dedup_directory(self) -> "Settings":
        """Default dedup_directory to <output_directory>/.dedup if not set."""
        if self.dedup_directory is None:
            object.__setattr__(self, "dedup_directory", self.output_directory / ".dedup")
        return self
