"""Thread-safe skip log writer for files excluded from processing."""

from __future__ import annotations

import threading
from pathlib import Path

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class SkipLogger:
    """Logs skipped (duplicate / invalid) files to a file and the terminal."""

    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._lock = threading.Lock()
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, zip_path: Path, reason: str) -> None:
        """Emit a warning to the terminal and append a line to the log file."""
        logger.warning("file_skipped", path=str(zip_path), reason=reason)
        line = f"{zip_path.name}\t{reason}\n"
        with self._lock:
            with self._path.open("a") as f:
                f.write(line)
