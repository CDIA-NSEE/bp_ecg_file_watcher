"""Tests for watcher/handler.py — ZipFileHandler.

Covers the on_closed (Linux inotify) and on_modified (macOS/Windows)
event handling paths, including debounce behaviour and queue backpressure.
"""

from __future__ import annotations

import queue
from pathlib import Path
from unittest.mock import patch

from watchdog.events import FileClosedEvent, FileModifiedEvent

from bp_ecg_watcher.watcher.handler import ZipFileHandler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zip(tmp_path: Path, name: str = "report.zip") -> Path:
    """Create a minimal placeholder ZIP file on disk."""
    p = tmp_path / name
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 50)
    return p


def _make_handler(
    tmp_path: Path,
    maxsize: int = 0,
    debounce_polls: int = 2,
    debounce_interval_ms: int = 0,
) -> tuple[ZipFileHandler, queue.Queue[Path]]:
    """Return a ZipFileHandler and its backing task queue."""
    q: queue.Queue[Path] = queue.Queue(maxsize=maxsize)
    handler = ZipFileHandler(
        task_queue=q,
        debounce_polls=debounce_polls,
        debounce_interval_ms=debounce_interval_ms,
    )
    return handler, q


# ---------------------------------------------------------------------------
# on_closed tests (Linux inotify path — no debounce needed)
# ---------------------------------------------------------------------------


class TestOnClosed:
    """Tests for ZipFileHandler.on_closed()."""

    def test_zip_file_enqueued(self, tmp_path: Path) -> None:
        """A FileClosedEvent for a .zip file must place the path on the queue."""
        zip_file = _make_zip(tmp_path)
        handler, q = _make_handler(tmp_path)

        event = FileClosedEvent(str(zip_file))
        handler.on_closed(event)

        assert q.qsize() == 1
        assert q.get_nowait() == zip_file

    def test_non_zip_not_enqueued(self, tmp_path: Path) -> None:
        """A FileClosedEvent for a non-ZIP file must be ignored."""
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        handler, q = _make_handler(tmp_path)

        event = FileClosedEvent(str(pdf))
        handler.on_closed(event)

        assert q.empty()

    def test_missing_file_not_enqueued(self, tmp_path: Path) -> None:
        """A FileClosedEvent for a path that no longer exists must be ignored."""
        missing = tmp_path / "gone.zip"  # not written to disk
        handler, q = _make_handler(tmp_path)

        event = FileClosedEvent(str(missing))
        handler.on_closed(event)

        assert q.empty()

    def test_uppercase_zip_extension_enqueued(self, tmp_path: Path) -> None:
        """Extension matching must be case-insensitive (.ZIP as well as .zip)."""
        big_zip = tmp_path / "REPORT.ZIP"
        big_zip.write_bytes(b"PK fake")
        handler, q = _make_handler(tmp_path)

        event = FileClosedEvent(str(big_zip))
        handler.on_closed(event)

        assert q.qsize() == 1

    def test_multiple_zips_all_enqueued(self, tmp_path: Path) -> None:
        """Multiple FileClosedEvents for distinct ZIPs must each be enqueued."""
        zips = [_make_zip(tmp_path, f"exam_{i}.zip") for i in range(3)]
        handler, q = _make_handler(tmp_path)

        for z in zips:
            handler.on_closed(FileClosedEvent(str(z)))

        assert q.qsize() == 3


# ---------------------------------------------------------------------------
# on_modified tests (macOS/Windows fallback path — debounce applied)
# ---------------------------------------------------------------------------


class TestOnModified:
    """Tests for ZipFileHandler.on_modified() with debounce."""

    def test_stable_zip_enqueued(self, tmp_path: Path) -> None:
        """A stable ZIP file must be enqueued after the debounce check passes."""
        zip_file = _make_zip(tmp_path)
        handler, q = _make_handler(tmp_path, debounce_polls=2, debounce_interval_ms=0)

        event = FileModifiedEvent(str(zip_file))
        handler.on_modified(event)

        assert q.qsize() == 1
        assert q.get_nowait() == zip_file

    def test_non_zip_not_enqueued(self, tmp_path: Path) -> None:
        """A FileModifiedEvent for a non-ZIP file must be ignored."""
        txt = tmp_path / "readme.txt"
        txt.write_bytes(b"text content")
        handler, q = _make_handler(tmp_path)

        event = FileModifiedEvent(str(txt))
        handler.on_modified(event)

        assert q.empty()

    def test_missing_file_not_enqueued(self, tmp_path: Path) -> None:
        """A FileModifiedEvent for a missing file must be ignored."""
        missing = tmp_path / "vanished.zip"
        handler, q = _make_handler(tmp_path)

        event = FileModifiedEvent(str(missing))
        handler.on_modified(event)

        assert q.empty()

    def test_unstable_file_not_enqueued(self, tmp_path: Path) -> None:
        """When is_file_stable returns False, the path must not be enqueued."""
        zip_file = _make_zip(tmp_path)
        handler, q = _make_handler(tmp_path)

        with patch("bp_ecg_watcher.watcher.handler.is_file_stable", return_value=False):
            event = FileModifiedEvent(str(zip_file))
            handler.on_modified(event)

        assert q.empty()

    def test_stable_file_enqueued_via_mock(self, tmp_path: Path) -> None:
        """When is_file_stable returns True, the path must be enqueued."""
        zip_file = _make_zip(tmp_path)
        handler, q = _make_handler(tmp_path)

        with patch("bp_ecg_watcher.watcher.handler.is_file_stable", return_value=True):
            event = FileModifiedEvent(str(zip_file))
            handler.on_modified(event)

        assert q.qsize() == 1


# ---------------------------------------------------------------------------
# _enqueue internals
# ---------------------------------------------------------------------------


class TestEnqueue:
    """Tests for ZipFileHandler._enqueue() helper."""

    def test_enqueue_without_debounce(self, tmp_path: Path) -> None:
        """With debounce=False, _enqueue must put the path immediately."""
        zip_file = _make_zip(tmp_path)
        handler, q = _make_handler(tmp_path)

        handler._enqueue(zip_file, debounce=False)

        assert q.qsize() == 1
        assert q.get_nowait() == zip_file

    def test_enqueue_with_debounce_stable(self, tmp_path: Path) -> None:
        """With debounce=True and a stable file, the path must be enqueued."""
        zip_file = _make_zip(tmp_path)
        handler, q = _make_handler(tmp_path, debounce_polls=2, debounce_interval_ms=0)

        handler._enqueue(zip_file, debounce=True)

        assert q.qsize() == 1

    def test_enqueue_with_debounce_unstable(self, tmp_path: Path) -> None:
        """With debounce=True and an unstable file, nothing must be enqueued."""
        zip_file = _make_zip(tmp_path)
        handler, q = _make_handler(tmp_path)

        with patch("bp_ecg_watcher.watcher.handler.is_file_stable", return_value=False):
            handler._enqueue(zip_file, debounce=True)

        assert q.empty()

    def test_enqueue_missing_path(self, tmp_path: Path) -> None:
        """If the path does not exist, _enqueue must silently skip it."""
        missing = tmp_path / "not_there.zip"
        handler, q = _make_handler(tmp_path)

        handler._enqueue(missing, debounce=False)

        assert q.empty()

    def test_queue_size_reported_after_enqueue(self, tmp_path: Path) -> None:
        """After enqueuing, the queue size must increase by 1."""
        zips = [_make_zip(tmp_path, f"f{i}.zip") for i in range(3)]
        handler, q = _make_handler(tmp_path)

        for z in zips:
            handler._enqueue(z, debounce=False)

        assert q.qsize() == 3
