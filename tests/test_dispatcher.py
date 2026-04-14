"""Tests for queue_manager/dispatcher.py — BatchRunner."""

from __future__ import annotations

import zipfile
from concurrent.futures import ProcessPoolExecutor
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.dedup.store import DedupStore
from bp_ecg_watcher.processor.pipeline import process_file
from bp_ecg_watcher.processor.skip_logger import SkipLogger
from bp_ecg_watcher.queue_manager.dispatcher import BatchRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        input_directory=tmp_path / "input",
        output_directory=tmp_path / "output",
        io_workers=2,
        cpu_workers=1,
    )


@pytest.fixture()
def skip_logger(tmp_path: Path) -> SkipLogger:
    return SkipLogger(tmp_path / "output" / "skip.log")


def _make_zip_with_2page_pdf(path: Path) -> Path:
    from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "Page 1")
    c.showPage()
    c.drawString(100, 750, "Page 2")
    c.showPage()
    c.save()
    zip_path = path / "exam.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("exam.pdf", buf.getvalue())
    return zip_path


# ---------------------------------------------------------------------------
# DedupStore unit tests
# ---------------------------------------------------------------------------


class TestDedupStore:

    def test_new_hash_not_duplicate(self, tmp_db: Path) -> None:
        store = DedupStore(tmp_db)
        assert not store.is_duplicate("newhash")

    def test_recorded_hash_is_duplicate(self, tmp_db: Path, tmp_path: Path) -> None:
        store = DedupStore(tmp_db)
        store.record_processed("myhash", tmp_path / "f.zip", "/out/myhash.pdf.zst")
        assert store.is_duplicate("myhash")

    def test_separate_instances_share_state(self, tmp_db: Path, tmp_path: Path) -> None:
        DedupStore(tmp_db).record_processed("shared", tmp_path / "x.zip", "/out/x.pdf.zst")
        assert DedupStore(tmp_db).is_duplicate("shared")


# ---------------------------------------------------------------------------
# BatchRunner tests
# ---------------------------------------------------------------------------


class TestBatchRunner:

    def test_empty_input_returns_zero_counts(
        self, settings: Settings, tmp_db: Path, tmp_path: Path, skip_logger: SkipLogger
    ) -> None:
        runner = BatchRunner(settings, DedupStore(tmp_db), skip_logger)
        processed, failed = runner.run([])
        assert processed == 0
        assert failed == 0

    def test_valid_zip_is_processed(
        self, settings: Settings, tmp_db: Path, tmp_path: Path, skip_logger: SkipLogger
    ) -> None:
        settings.output_directory.mkdir(parents=True, exist_ok=True)
        zip_path = _make_zip_with_2page_pdf(tmp_path)
        runner = BatchRunner(settings, DedupStore(tmp_db), skip_logger)
        processed, failed = runner.run([zip_path])
        assert processed == 1
        assert failed == 0
        zst_files = list(settings.output_directory.glob("*.pdf.zst"))
        assert len(zst_files) == 1

    def test_duplicate_zip_is_skipped(
        self, settings: Settings, tmp_db: Path, tmp_path: Path, skip_logger: SkipLogger
    ) -> None:
        settings.output_directory.mkdir(parents=True, exist_ok=True)
        zip_path = _make_zip_with_2page_pdf(tmp_path)
        store = DedupStore(tmp_db)
        runner = BatchRunner(settings, store, skip_logger)
        runner.run([zip_path])  # first run records the hash
        processed, failed = runner.run([zip_path])  # second run should skip
        assert processed == 1  # process_file returns None (no exception) for duplicates
        assert failed == 0

    def test_broken_zip_counts_as_processed_not_failed(
        self, settings: Settings, tmp_db: Path, tmp_path: Path, skip_logger: SkipLogger
    ) -> None:
        settings.output_directory.mkdir(parents=True, exist_ok=True)
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        runner = BatchRunner(settings, DedupStore(tmp_db), skip_logger)
        processed, failed = runner.run([bad])
        # process_file logs and returns without raising — counted as processed
        assert processed == 1
        assert failed == 0

    def test_exception_in_worker_counts_as_failed(
        self, settings: Settings, tmp_db: Path, tmp_path: Path, skip_logger: SkipLogger
    ) -> None:
        zip_path = tmp_path / "x.zip"
        zip_path.write_bytes(b"x")
        with patch(
            "bp_ecg_watcher.queue_manager.dispatcher.process_file",
            side_effect=RuntimeError("boom"),
        ):
            runner = BatchRunner(settings, DedupStore(tmp_db), skip_logger)
            processed, failed = runner.run([zip_path])
        assert failed == 1
        assert processed == 0
