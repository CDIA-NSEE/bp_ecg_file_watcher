"""File-stability debounce helper for the bp_ecg_file_watcher service.

Polls a file's on-disk size at regular intervals and only signals readiness
when the size has been stable across all polls. This prevents processing
files that are still being written by the producer.
"""

from __future__ import annotations

import time
from pathlib import Path

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def is_file_stable(
    path: Path,
    polls: int = 3,
    interval_ms: int = 200,
) -> bool:
    """Return True only if *path* has the same size across *polls* consecutive checks.

    Polls the file's byte size at intervals of *interval_ms* milliseconds. If the
    size is identical across all polls the file is considered fully written and
    stable. If the size changes during any interval, the function returns False.

    Args:
        path: Filesystem path of the file to check.
        polls: Number of consecutive polls that must return the same size.
            Must be >= 2 to make any meaningful comparison.
        interval_ms: Sleep duration between polls in milliseconds.

    Returns:
        ``True`` when the file size is stable, ``False`` when the file is
        still growing or when it cannot be stat-ted.
    """
    if polls < 2:
        polls = 2

    interval_sec: float = interval_ms / 1000.0
    previous_size: int = -1

    for poll_index in range(polls):
        try:
            current_size: int = path.stat().st_size
        except OSError as exc:
            logger.warning(
                "debounce_stat_failed",
                path=str(path),
                poll_index=poll_index,
                error=str(exc),
            )
            return False

        logger.debug(
            "debounce_poll",
            path=str(path),
            poll_index=poll_index,
            size_bytes=current_size,
        )

        if poll_index > 0 and current_size != previous_size:
            logger.info(
                "debounce_size_changed",
                path=str(path),
                previous_bytes=previous_size,
                current_bytes=current_size,
            )
            return False

        previous_size = current_size

        if poll_index < polls - 1:
            time.sleep(interval_sec)

    logger.debug(
        "debounce_stable",
        path=str(path),
        size_bytes=previous_size,
        polls=polls,
    )
    return True
