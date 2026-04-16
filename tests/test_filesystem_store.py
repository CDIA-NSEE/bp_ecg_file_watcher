"""Tests for dedup/filesystem_store.py — FilesystemStore."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bp_ecg_watcher.dedup.filesystem_store import FilesystemStore


@pytest.fixture()
def store(tmp_path: Path) -> FilesystemStore:
    return FilesystemStore(tmp_path / "dedup")


# ---------------------------------------------------------------------------
# try_reserve
# ---------------------------------------------------------------------------


class TestTryReserve:
    def test_first_reservation_succeeds(self, store: FilesystemStore) -> None:
        assert store.try_reserve("abc123") is True

    def test_second_reservation_for_same_hash_fails(
        self, store: FilesystemStore
    ) -> None:
        store.try_reserve("abc123")
        assert store.try_reserve("abc123") is False

    def test_different_hashes_succeed_independently(
        self, store: FilesystemStore
    ) -> None:
        assert store.try_reserve("hash1") is True
        assert store.try_reserve("hash2") is True

    def test_pending_file_created_on_reserve(
        self, store: FilesystemStore, tmp_path: Path
    ) -> None:
        store.try_reserve("abc123")
        assert (tmp_path / "dedup" / "abc123.pending").exists()

    def test_returns_false_when_done_file_exists(
        self, store: FilesystemStore, tmp_path: Path
    ) -> None:
        # Simulate a completed file from a previous run.
        (tmp_path / "dedup" / "abc123.done").write_text("some/path", encoding="utf-8")
        assert store.try_reserve("abc123") is False


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


class TestRelease:
    def test_release_removes_pending_file(
        self, store: FilesystemStore, tmp_path: Path
    ) -> None:
        store.try_reserve("abc123")
        store.release("abc123")
        assert not (tmp_path / "dedup" / "abc123.pending").exists()

    def test_release_allows_re_reservation(self, store: FilesystemStore) -> None:
        store.try_reserve("abc123")
        store.release("abc123")
        assert store.try_reserve("abc123") is True

    def test_release_is_idempotent(self, store: FilesystemStore) -> None:
        """Releasing a non-existent pending file must not raise."""
        store.release("never_reserved")  # should not raise


# ---------------------------------------------------------------------------
# mark_complete
# ---------------------------------------------------------------------------


class TestMarkComplete:
    def test_done_file_created(
        self, store: FilesystemStore, tmp_path: Path
    ) -> None:
        store.try_reserve("abc123")
        store.mark_complete("abc123", "/output/abc123.pdf.zst")
        assert (tmp_path / "dedup" / "abc123.done").exists()

    def test_pending_file_removed_after_complete(
        self, store: FilesystemStore, tmp_path: Path
    ) -> None:
        store.try_reserve("abc123")
        store.mark_complete("abc123", "/output/abc123.pdf.zst")
        assert not (tmp_path / "dedup" / "abc123.pending").exists()

    def test_done_file_contains_output_path(
        self, store: FilesystemStore, tmp_path: Path
    ) -> None:
        store.try_reserve("abc123")
        store.mark_complete("abc123", "/output/abc123.pdf.zst")
        content = (tmp_path / "dedup" / "abc123.done").read_text(encoding="utf-8")
        assert content == "/output/abc123.pdf.zst"

    def test_second_reserve_fails_after_complete(
        self, store: FilesystemStore
    ) -> None:
        store.try_reserve("abc123")
        store.mark_complete("abc123", "/output/abc123.pdf.zst")
        assert store.try_reserve("abc123") is False


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_only_one_thread_wins_reservation(self, tmp_path: Path) -> None:
        """Exactly one of N concurrent try_reserve calls for the same hash wins."""
        store = FilesystemStore(tmp_path / "dedup")
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(store.try_reserve, "shared_hash") for _ in range(8)]
            results = [f.result() for f in futures]

        assert results.count(True) == 1
        assert results.count(False) == 7
