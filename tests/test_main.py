"""Tests for main.py — configure_logging helper."""

from __future__ import annotations

from pathlib import Path

import pytest

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.main import configure_logging


@pytest.fixture
def dev_settings(tmp_path: Path) -> Settings:
    return Settings(
        input_directory=tmp_path / "input",
        output_directory=tmp_path / "output",
        environment="dev",
    )


@pytest.fixture
def prod_settings(tmp_path: Path) -> Settings:
    return Settings(
        input_directory=tmp_path / "input",
        output_directory=tmp_path / "output",
        environment="prod",
    )


class TestConfigureLogging:
    """Verify configure_logging() runs without error in both environments."""

    def test_dev_environment_no_exception(self, dev_settings: Settings) -> None:
        configure_logging(dev_settings)

    def test_prod_environment_no_exception(self, prod_settings: Settings) -> None:
        configure_logging(prod_settings)

    def test_can_call_multiple_times(self, dev_settings: Settings) -> None:
        configure_logging(dev_settings)
        configure_logging(dev_settings)
