"""Tests for storage/local_writer.py."""

from __future__ import annotations

from pathlib import Path

from bp_ecg_watcher.storage.local_writer import write_processed


class TestWriteProcessed:

    def test_file_written_at_flat_path(self, tmp_path: Path) -> None:
        dest = write_processed(b"data", tmp_path, "abc123")
        assert dest == tmp_path / "abc123.pdf.zst"
        assert dest.exists()

    def test_file_content_matches_input(self, tmp_path: Path) -> None:
        data = b"\x00\x01compressed"
        dest = write_processed(data, tmp_path, "deadbeef")
        assert dest.read_bytes() == data

    def test_creates_missing_parent_directory(self, tmp_path: Path) -> None:
        out = tmp_path / "deep" / "nested"
        dest = write_processed(b"x", out, "hash1")
        assert dest.exists()

    def test_returns_path_instance(self, tmp_path: Path) -> None:
        dest = write_processed(b"data", tmp_path, "h")
        assert isinstance(dest, Path)

    def test_overwrite_existing_file(self, tmp_path: Path) -> None:
        write_processed(b"first", tmp_path, "same_hash")
        dest = write_processed(b"second", tmp_path, "same_hash")
        assert dest.read_bytes() == b"second"

    def test_different_hashes_produce_separate_files(self, tmp_path: Path) -> None:
        d1 = write_processed(b"a", tmp_path, "hash_a")
        d2 = write_processed(b"b", tmp_path, "hash_b")
        assert d1 != d2
        assert d1.read_bytes() == b"a"
        assert d2.read_bytes() == b"b"
