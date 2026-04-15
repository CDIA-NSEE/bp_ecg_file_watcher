"""Thread-safe and cross-process skip log writer for excluded files."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class SkipLogger:
    """Logs skipped (duplicate / invalid) files to a file and the terminal.

    Uses ``fcntl.flock()`` for cross-process mutual exclusion so concurrent
    Dask workers on the same machine cannot interleave log lines.  On NFS
    filesystems (SLURM multi-node), NFSv4 advisory locks are honoured by
    modern kernels — each write is short (< 512 bytes) so contention is
    minimal even if locking is not fully atomic.
    """

    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, zip_path: Path, reason: str) -> None:
        """Emit a warning to the terminal and append a line to the log file."""
        logger.warning("file_skipped", path=str(zip_path), reason=reason)
        line = f"{zip_path.name}\t{reason}\n"
        fd = os.open(str(self._path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line.encode())
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
