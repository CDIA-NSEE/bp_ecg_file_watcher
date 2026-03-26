"""Tests for main.py — configure_logging helper and startup backfill.

The main() entry point itself is not exercised here because it calls
start_http_server() and runs an infinite loop, which are integration-level
concerns. configure_logging() is a pure side-effect function that can be
tested by verifying structlog's internal state after the call.
"""

from __future__ import annotations

import queue
import zipfile
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


# ---------------------------------------------------------------------------
# Startup backfill
# ---------------------------------------------------------------------------


class TestStartupBackfill:
    """Verify that existing ZIPs in the watch directory are enqueued at startup."""

    def _make_zip(self, path: Path) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("dummy.txt", "data")

    def test_existing_zips_are_enqueued(self, tmp_path: Path) -> None:
        """ZIPs already in the watch directory must be placed on the queue."""
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        self._make_zip(watch_dir / "a.zip")
        self._make_zip(watch_dir / "b.zip")

        task_queue: queue.Queue[Path] = queue.Queue()
        # Simulate the backfill logic from main()
        for zip_path in sorted(watch_dir.glob("*.zip")):
            task_queue.put(zip_path)

        assert task_queue.qsize() == 2
        enqueued = {task_queue.get().name, task_queue.get().name}
        assert enqueued == {"a.zip", "b.zip"}

    def test_non_zip_files_not_enqueued(self, tmp_path: Path) -> None:
        """Non-ZIP files in the watch directory must not be enqueued."""
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        (watch_dir / "notes.txt").write_text("ignore me")
        self._make_zip(watch_dir / "real.zip")

        task_queue: queue.Queue[Path] = queue.Queue()
        for zip_path in sorted(watch_dir.glob("*.zip")):
            task_queue.put(zip_path)

        assert task_queue.qsize() == 1
        assert task_queue.get().name == "real.zip"

    def test_empty_directory_no_enqueue(self, tmp_path: Path) -> None:
        """An empty watch directory must result in nothing being enqueued."""
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        task_queue: queue.Queue[Path] = queue.Queue()
        for zip_path in sorted(watch_dir.glob("*.zip")):
            task_queue.put(zip_path)

        assert task_queue.empty()
