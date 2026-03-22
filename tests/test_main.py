"""Tests for main.py — configure_logging helper.

The main() entry point itself is not exercised here because it calls
start_http_server() and runs an infinite loop, which are integration-level
concerns. configure_logging() is a pure side-effect function that can be
tested by verifying structlog's internal state after the call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.main import configure_logging


@pytest.fixture
def dev_settings(tmp_path: Path) -> Settings:
    """Settings with environment='dev'."""
    return Settings(
        watch_directory=tmp_path,
        minio_endpoint="http://localhost:9000",
        minio_access_key="key",
        minio_secret_key="secret",
        environment="dev",
    )


@pytest.fixture
def prod_settings(tmp_path: Path) -> Settings:
    """Settings with environment='prod'."""
    return Settings(
        watch_directory=tmp_path,
        minio_endpoint="http://localhost:9000",
        minio_access_key="key",
        minio_secret_key="secret",
        environment="prod",
    )


class TestConfigureLogging:
    """Verify configure_logging() runs without error in both environments."""

    def test_dev_environment_no_exception(self, dev_settings: Settings) -> None:
        """configure_logging must not raise in dev mode."""
        configure_logging(dev_settings)  # must not raise

    def test_prod_environment_no_exception(self, prod_settings: Settings) -> None:
        """configure_logging must not raise in prod mode."""
        configure_logging(prod_settings)  # must not raise

    def test_can_call_multiple_times(self, dev_settings: Settings) -> None:
        """configure_logging must be idempotent — safe to call repeatedly."""
        configure_logging(dev_settings)
        configure_logging(dev_settings)  # second call must not raise
