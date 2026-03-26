"""Watchdog FileSystemEventHandler for the bp_ecg_file_watcher service.

Listens for file-close events (Linux) or modification events (macOS/Windows)
on ZIP files in the watched directory. Verified files are placed on a bounded
queue.Queue to apply backpressure when workers fall behind.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import structlog
from watchdog.events import (
    FileClosedEvent,
    FileModifiedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)

from bp_ecg_watcher.watcher.debounce import is_file_stable

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class ZipFileHandler(FileSystemEventHandler):
    """Watchdog event handler that enqueues stable ZIP files for processing.

    On Linux, :class:`watchdog.observers.inotify.InotifyObserver` delivers
    :class:`~watchdog.events.FileClosedEvent` events that fire only after the
    file descriptor has been closed by the writing process. This is the safest
    signal that a file is fully written.

    On macOS and Windows the observer does not support ``FileClosedEvent``, so
    we fall back to ``FileModifiedEvent`` combined with the debounce size-
    stability check in :mod:`bp_ecg_watcher.watcher.debounce`.

    Args:
        task_queue: The bounded queue that workers consume from. ``put()``
            will block the event-handler thread when the queue is full,
            providing backpressure to the file producer.
        debounce_polls: Number of size-stability polls to run. Forwarded to
            :func:`~bp_ecg_watcher.watcher.debounce.is_file_stable`.
        debounce_interval_ms: Sleep interval between polls in milliseconds.
    """

    def __init__(
        self,
        task_queue: queue.Queue[Path],
        debounce_polls: int = 3,
        debounce_interval_ms: int = 200,
    ) -> None:
        """Initialise the handler with a reference to the shared task queue."""
        super().__init__()
        self._queue: queue.Queue[Path] = task_queue
        self._debounce_polls: int = debounce_polls
        self._debounce_interval_ms: int = debounce_interval_ms
        self._inflight: set[Path] = set()
        self._inflight_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public event callbacks
    # ------------------------------------------------------------------

    def on_closed(self, event: FileSystemEvent) -> None:
        """Handle FileClosedEvent (Linux InotifyObserver).

        Only ZIP files are accepted. No debounce poll is needed here because
        the file-close event guarantees the writer has finished.

        Args:
            event: The watchdog filesystem event.
        """
        if isinstance(event, FileClosedEvent) and not event.is_directory:
            path = Path(str(event.src_path))
            if path.suffix.lower() == ".zip":
                self._enqueue(path, debounce=False)

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle FileModifiedEvent (macOS FSEvents / Windows ReadDirectoryChangesW).

        Used as the fallback on platforms that do not emit FileClosedEvent.
        A debounce size-stability check is performed before enqueuing.

        Args:
            event: The watchdog filesystem event.
        """
        if isinstance(event, FileModifiedEvent) and not event.is_directory:
            path = Path(str(event.src_path))
            if path.suffix.lower() == ".zip":
                self._enqueue(path, debounce=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enqueue(self, path: Path, *, debounce: bool) -> None:
        """Validate file stability (optionally) and place the path on the queue.

        The queue's ``put()`` call blocks when the queue is full; this is
        intentional — it applies backpressure to the watchdog thread so that
        the watcher cannot flood memory with unprocessed paths.

        Args:
            path: Filesystem path of the ZIP file to enqueue.
            debounce: When ``True``, run the size-stability poll before
                enqueuing. When ``False``, enqueue immediately (used after
                a ``FileClosedEvent`` which already guarantees stability).
        """
        if not path.exists():
            logger.debug("handler_path_gone", path=str(path))
            return

        if debounce:
            stable: bool = is_file_stable(
                path,
                polls=self._debounce_polls,
                interval_ms=self._debounce_interval_ms,
            )
            if not stable:
                logger.info("handler_file_unstable", path=str(path))
                return

        with self._inflight_lock:
            if path in self._inflight:
                logger.debug("handler_already_inflight", path=str(path))
                return
            self._inflight.add(path)

        logger.info("handler_enqueuing", path=str(path))
        self._queue.put(path)  # blocks if queue is full (backpressure)
        logger.debug(
            "handler_enqueued",
            path=str(path),
            queue_size=self._queue.qsize(),
        )
