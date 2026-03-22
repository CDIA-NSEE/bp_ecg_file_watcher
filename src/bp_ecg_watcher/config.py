"""Configuration module for bp_ecg_file_watcher.

Reads all settings from environment variables and an optional .env file using
pydantic-settings. No values are hardcoded — every secret and tunable parameter
must come from the environment.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables / .env file."""

    # Filesystem
    watch_directory: Path

    # MINIO connection
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_use_ssl: bool = False

    # Bucket names
    bucket_images: str = "bp-ecg-dev-images"
    bucket_intake: str = "bp-ecg-dev-intake"
    bucket_rejected: str = "bp-ecg-dev-rejected"
    bucket_dlq: str = "bp-ecg-dev-dlq"

    # Worker pool
    max_workers: int = 4
    queue_maxsize: int = 50

    # Processing parameters
    rasterization_dpi: int = 300
    image_max_side_px: int = 1200
    zstd_level: int = 9

    # File stability / debounce
    debounce_polls: int = 3
    debounce_interval_ms: int = 200

    # Application metadata — resolved at startup from package metadata
    watcher_version: str = Field(
        default_factory=lambda: _get_package_version(),
    )

    # Observability
    metrics_port: int = 8000
    log_level: str = "INFO"
    environment: str = "dev"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


def _get_package_version() -> str:
    """Return the installed package version, falling back to 'dev' if not installed."""
    try:
        return importlib.metadata.version("bp_ecg_file_watcher")
    except importlib.metadata.PackageNotFoundError:
        return "dev"
