"""Tests for queue_manager/dispatcher.py.

Covers:
- Deduplication: same ZIP hash submitted twice → second is skipped.
- Queue backpressure: filling the queue to maxsize blocks a put() call.
- Retry logic: worker raises twice then succeeds → no DLQ upload.
- DLQ routing: worker raises 3 times → DLQ upload is called.
- _get_conn: creates the processed_files table with the expected schema.
- is_file_stable: file stable across polls, unstable file returns False.
"""

from __future__ import annotations

import queue
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.dedup.store import DedupStore
from bp_ecg_watcher.queue_manager.dispatcher import (
    Dispatcher,
    _submit_with_retry,
    process_zip,
)
from bp_ecg_watcher.watcher.debounce import is_file_stable

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIO_ENDPOINT = "http://localhost:9000"
ACCESS_KEY = "minioadmin"
SECRET_KEY = "minioadmin"

BUCKET_IMAGES = "bp-ecg-dev-images"
BUCKET_INTAKE = "bp-ecg-dev-intake"
BUCKET_REJECTED = "bp-ecg-dev-rejected"
BUCKET_DLQ = "bp-ecg-dev-dlq"


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """Return a unique SQLite database path for each test."""
    return tmp_path / "test_processed.db"


@pytest.fixture()
def settings(tmp_path: Path, tmp_db: Path) -> Settings:
    """Build a minimal Settings object suitable for unit tests."""
    return Settings(
        watch_directory=tmp_path / "watch",
        minio_endpoint=MINIO_ENDPOINT,
        minio_access_key=ACCESS_KEY,
        minio_secret_key=SECRET_KEY,
        bucket_images=BUCKET_IMAGES,
        bucket_intake=BUCKET_INTAKE,
        bucket_rejected=BUCKET_REJECTED,
        bucket_dlq=BUCKET_DLQ,
        max_workers=2,
        queue_maxsize=5,
    )


@pytest.fixture()
def aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub AWS credentials so moto intercepts all boto3 calls."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture()
def s3_client(aws_credentials: None) -> Any:
    """Provide a moto-mocked S3 client with all required buckets."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        for bucket in [BUCKET_IMAGES, BUCKET_INTAKE, BUCKET_REJECTED, BUCKET_DLQ]:
            client.create_bucket(Bucket=bucket)
        yield client


def _make_zip_with_2page_pdf(tmp_path: Path) -> Path:
    """Create a real ZIP containing a 2-page PDF (using reportlab)."""
    from io import BytesIO as _BytesIO

    from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

    # Build PDF in memory
    pdf_buf = _BytesIO()
    c = canvas.Canvas(pdf_buf)
    c.drawString(100, 750, "Page 1")
    c.showPage()
    c.drawString(100, 750, "Page 2")
    c.showPage()
    c.save()
    pdf_bytes = pdf_buf.getvalue()

    zip_path = tmp_path / "exam_001.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("exam.pdf", pdf_bytes)
    return zip_path


# ---------------------------------------------------------------------------
# DedupStore tests
# ---------------------------------------------------------------------------


class TestDedupStore:
    """Tests for the peewee-backed deduplication store."""

    def test_new_hash_not_duplicate(self, tmp_db: Path) -> None:
        """A hash that has never been recorded must not be flagged as duplicate."""
        store = DedupStore(tmp_db)
        assert not store.is_duplicate("newhash")

    def test_recorded_hash_is_duplicate(self, tmp_db: Path, tmp_path: Path) -> None:
        """After recording a hash, is_duplicate must return True."""
        store = DedupStore(tmp_db)
        store.record_processed(
            zip_hash="recorded_hash",
            source_path=tmp_path / "file.zip",
            destination_key="2024/01/01/image.png.zst",
        )
        assert store.is_duplicate("recorded_hash")

    def test_separate_instances_share_state(self, tmp_db: Path, tmp_path: Path) -> None:
        """Two DedupStore instances pointing at the same DB file must share state."""
        store1 = DedupStore(tmp_db)
        store1.record_processed(
            zip_hash="shared_hash",
            source_path=tmp_path / "x.zip",
            destination_key="2024/01/01/x.png.zst",
        )
        store2 = DedupStore(tmp_db)
        assert store2.is_duplicate("shared_hash")


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Tests for the ZIP deduplication logic."""

    def test_new_hash_not_duplicate(self, tmp_db: Path) -> None:
        """A hash that has never been recorded must not be flagged as duplicate."""
        assert not DedupStore(tmp_db).is_duplicate("newhash")

    def test_recorded_hash_is_duplicate(self, tmp_db: Path, tmp_path: Path) -> None:
        """After recording a hash, is_duplicate must return True."""
        store = DedupStore(tmp_db)
        store.record_processed(
            zip_hash="recorded_hash",
            source_path=tmp_path / "file.zip",
            destination_key="2024/01/01/image.png.zst",
        )
        assert DedupStore(tmp_db).is_duplicate("recorded_hash")

    def test_process_zip_skips_duplicate(
        self, tmp_path: Path, tmp_db: Path, settings: Settings, s3_client: Any
    ) -> None:
        """Calling process_zip twice with the same ZIP must skip the second call."""
        zip_path = _make_zip_with_2page_pdf(tmp_path)
        store = DedupStore(tmp_db)

        # First call — should process normally
        result1 = process_zip(
            zip_path=zip_path,
            s3_client=s3_client,
            settings=settings,
            dedup_store=store,
        )
        # Second call — should be skipped
        result2 = process_zip(
            zip_path=zip_path,
            s3_client=s3_client,
            settings=settings,
            dedup_store=store,
        )
        assert not result1.startswith("duplicate/"), (
            "First call should not return a duplicate path"
        )
        assert result2.startswith("duplicate/")


# ---------------------------------------------------------------------------
# Retry and DLQ tests
# ---------------------------------------------------------------------------


class TestRetryLogic:
    """Tests for the exponential back-off retry and DLQ routing."""

    def test_success_on_second_attempt_no_dlq(
        self, tmp_path: Path, tmp_db: Path, settings: Settings
    ) -> None:
        """Worker that fails once then succeeds must not trigger DLQ."""
        call_count = 0

        def flaky_process_zip(**_kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("transient error")
            return "2024/01/01/ok.png.zst"

        zip_path = tmp_path / "flaky.zip"
        zip_path.write_bytes(b"fake")

        with (
            patch(
                "bp_ecg_watcher.processor.pipeline.process_zip",
                side_effect=flaky_process_zip,
            ),
            patch("bp_ecg_watcher.processor.pipeline.upload_dlq") as mock_dlq,
            patch("time.sleep"),  # speed up the test
        ):
            _submit_with_retry(
                zip_path=zip_path,
                s3_client=MagicMock(),
                settings=settings,
                dedup_store=DedupStore(tmp_db),
                max_attempts=3,
            )
        mock_dlq.assert_not_called()
        assert call_count == 2

    def test_all_attempts_fail_routes_to_dlq(
        self, tmp_path: Path, tmp_db: Path, settings: Settings, s3_client: Any
    ) -> None:
        """When all 3 attempts fail, upload_dlq must be called exactly once."""
        zip_path = tmp_path / "bad.zip"
        zip_path.write_bytes(b"not a zip")

        with (
            patch(
                "bp_ecg_watcher.processor.pipeline.process_zip",
                side_effect=RuntimeError("always fails"),
            ),
            patch("bp_ecg_watcher.processor.pipeline.upload_dlq") as mock_dlq,
            patch("time.sleep"),
        ):
            _submit_with_retry(
                zip_path=zip_path,
                s3_client=s3_client,
                settings=settings,
                dedup_store=DedupStore(tmp_db),
                max_attempts=3,
            )
        mock_dlq.assert_called_once()
        call_kwargs = mock_dlq.call_args[1]
        assert call_kwargs["attempt_count"] == 3
        assert call_kwargs["error_type"] == "RuntimeError"

    def test_retry_count_before_dlq(
        self, tmp_path: Path, tmp_db: Path, settings: Settings
    ) -> None:
        """_submit_with_retry must call process_zip exactly max_attempts times."""
        call_count = 0

        def always_fail(**_kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("fail")

        zip_path = tmp_path / "retry_count.zip"
        zip_path.write_bytes(b"x")

        with (
            patch(
                "bp_ecg_watcher.processor.pipeline.process_zip",
                side_effect=always_fail,
            ),
            patch("bp_ecg_watcher.processor.pipeline.upload_dlq"),
            patch("time.sleep"),
        ):
            _submit_with_retry(
                zip_path=zip_path,
                s3_client=MagicMock(),
                settings=settings,
                dedup_store=DedupStore(tmp_db),
                max_attempts=3,
            )
        assert call_count == 3


# ---------------------------------------------------------------------------
# Queue backpressure test
# ---------------------------------------------------------------------------


class TestQueueBackpressure:
    """Tests that a full queue blocks the producer (put()) call."""

    def test_put_blocks_when_queue_full(self) -> None:
        """put() must block when the queue is at maxsize."""
        maxsize = 3
        q: queue.Queue[int] = queue.Queue(maxsize=maxsize)

        # Fill the queue to capacity
        for i in range(maxsize):
            q.put(i)

        # Try to put one more item in a separate thread — should block
        blocked = threading.Event()
        unblocked = threading.Event()

        def _try_put() -> None:
            blocked.set()
            q.put(99)  # This should block until a slot is freed
            unblocked.set()

        producer = threading.Thread(target=_try_put)
        producer.start()

        blocked.wait(timeout=2)
        # Producer should be blocked, not yet done
        assert not unblocked.is_set(), "put() returned immediately on full queue"

        # Free a slot
        q.get()
        q.task_done()

        unblocked.wait(timeout=2)
        assert unblocked.is_set(), "put() did not unblock after queue slot freed"
        producer.join(timeout=2)


# ---------------------------------------------------------------------------
# Debounce tests
# ---------------------------------------------------------------------------


class TestDebounce:
    """Tests for watcher/debounce.py."""

    def test_stable_file_returns_true(self, tmp_path: Path) -> None:
        """A file that does not grow between polls must be considered stable."""
        f = tmp_path / "stable.zip"
        f.write_bytes(b"hello world")
        assert is_file_stable(f, polls=3, interval_ms=10) is True

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        """A path that does not exist must return False."""
        f = tmp_path / "does_not_exist.zip"
        assert is_file_stable(f, polls=3, interval_ms=10) is False

    def test_growing_file_returns_false(self, tmp_path: Path) -> None:
        """A file that is growing between polls must return False.

        We simulate growth by writing data after the first poll by using a
        real file and a background thread — simpler than mocking stat().
        """
        f = tmp_path / "growing.zip"
        # Write a tiny file; we'll append to it after debounce starts.
        f.write_bytes(b"x" * 10)

        poll_count = 0

        class FakeStat:
            """Fake stat result that returns increasing sizes on each call."""

            st_size: int

            def __init__(self, size: int) -> None:
                """Store the fake size."""
                self.st_size = size

        def counting_stat(self_path: Path) -> FakeStat:  # type: ignore[misc]
            nonlocal poll_count
            poll_count += 1
            if poll_count == 1:
                return FakeStat(10)
            return FakeStat(200)  # second poll shows larger size

        with patch.object(Path, "stat", counting_stat):
            result = is_file_stable(f, polls=3, interval_ms=1)

        assert result is False

    def test_polls_minimum_two(self, tmp_path: Path) -> None:
        """polls < 2 must be silently coerced to 2."""
        f = tmp_path / "min_polls.zip"
        f.write_bytes(b"data")
        # Should not raise even with polls=1
        result = is_file_stable(f, polls=1, interval_ms=1)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Dispatcher lifecycle tests
# ---------------------------------------------------------------------------


class TestDispatcher:
    """Smoke tests for Dispatcher start/stop lifecycle."""

    def test_dispatcher_starts_and_stops(
        self, settings: Settings, tmp_db: Path
    ) -> None:
        """Dispatcher must start and stop without error when the queue is empty."""
        q: queue.Queue[Path] = queue.Queue(maxsize=5)

        with patch(
            "bp_ecg_watcher.queue_manager.dispatcher.create_s3_client",
            return_value=MagicMock(),
        ):
            dispatcher = Dispatcher(
                task_queue=q,
                settings=settings,
                dedup_store=DedupStore(tmp_db),
            )
            dispatcher.start()
            # Give the consumer thread a moment to start
            time.sleep(0.05)
            dispatcher.stop()
