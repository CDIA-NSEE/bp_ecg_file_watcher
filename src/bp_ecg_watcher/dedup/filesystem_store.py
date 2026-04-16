"""Filesystem-based deduplication store using atomic file creation.

Uses POSIX ``O_CREAT|O_EXCL`` to atomically claim a hash. Works on any
POSIX-compliant filesystem including BeeGFS (Drummond shared storage) and
local ext4/tmpfs (docker-compose dev environment).

Key space on disk::

    {dedup_dir}/{zip_hash}.pending  — reserved, in-flight
    {dedup_dir}/{zip_hash}.done     — completed
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class FilesystemStore:
    """Filesystem-based deduplication using atomic ``O_CREAT|O_EXCL``.

    Args:
        dedup_dir: Directory where per-hash sentinel files are stored.
            Created automatically if it does not exist.
    """

    def __init__(self, dedup_dir: Path) -> None:
        self._dir = dedup_dir
        dedup_dir.mkdir(parents=True, exist_ok=True)

    def _pending(self, zip_hash: str) -> Path:
        return self._dir / f"{zip_hash}.pending"

    def _done(self, zip_hash: str) -> Path:
        return self._dir / f"{zip_hash}.done"

    def try_reserve(self, zip_hash: str) -> bool:
        """Atomically reserve *zip_hash* for processing.

        Creates ``{hash}.pending`` with ``O_CREAT|O_EXCL``.  Returns ``True``
        if this caller created the file (reservation succeeded), ``False`` if
        the file already existed (another worker owns or completed it).

        Args:
            zip_hash: BLAKE3 hex digest of the raw ZIP bytes.

        Returns:
            ``True`` if this caller may proceed; ``False`` means skip.
        """
        # Fast path: already done from a previous run.
        if self._done(zip_hash).exists():
            return False
        try:
            fd = os.open(
                str(self._pending(zip_hash)),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def mark_complete(self, zip_hash: str, output_path: str) -> None:
        """Atomically promote the pending marker to done.

        Renames ``{hash}.pending`` → ``{hash}.done``.  The rename is atomic
        on POSIX filesystems, so no partial state is visible.

        Args:
            zip_hash: BLAKE3 hex digest of the raw ZIP bytes.
            output_path: Path of the written output file (stored inside the
                sentinel file for human inspection only).
        """
        pending = self._pending(zip_hash)
        done = self._done(zip_hash)
        pending.write_text(output_path, encoding="utf-8")
        pending.rename(done)
        logger.debug("dedup_marked_complete", hash=zip_hash, output=output_path)

    def release(self, zip_hash: str) -> None:
        """Delete the pending marker so the file can be retried.

        Called when processing fails after ``try_reserve`` so the hash is not
        permanently locked out.

        Args:
            zip_hash: BLAKE3 hex digest of the raw ZIP bytes.
        """
        try:
            self._pending(zip_hash).unlink()
        except FileNotFoundError:
            pass
