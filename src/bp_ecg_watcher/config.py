"""Configuration module for bp_ecg_file_watcher.

Reads all settings from environment variables and an optional .env file using
pydantic-settings. No values are hardcoded — every tunable parameter must come
from the environment.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables / .env file."""

    # Filesystem
    input_directory: Path
    output_directory: Path

    # Worker pools
    io_workers: int = 8
    cpu_workers: int = Field(default_factory=lambda: os.cpu_count() or 4)

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
