"""Queue-based dispatcher for the bp_ecg_file_watcher pipeline.

Implements the Producer → Queue → Consumer pattern:

- The watchdog handler (producer) places ZIP paths onto a bounded
  ``queue.Queue``.
- A single consumer thread reads from the queue and submits work to a
  ``ThreadPoolExecutor``.
- Each worker calls :func:`~bp_ecg_watcher.processor.pipeline.submit_with_retry`
  which validates → processes → uploads, retrying up to 3 times with
  exponential back-off before routing to the DLQ.
- Deduplication is handled by
  :class:`~bp_ecg_watcher.dedup.store.DedupStore` (peewee + SQLite WAL),
  shared across all worker threads in the executor.

No intermediate files are written to disk at any stage.
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import structlog

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.dedup.store import DedupStore
from bp_ecg_watcher.processor.pipeline import process_zip, submit_with_retry  # noqa: F401 (re-export)
from bp_ecg_watcher.storage.minio_client import create_s3_client, upload_dlq  # noqa: F401 (re-export)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# Alias kept for backward-compatibility with existing test imports.
_submit_with_retry = submit_with_retry


class Dispatcher:
    """Bridges the watchdog queue and the ThreadPoolExecutor worker pool.

    A single consumer thread reads :class:`~pathlib.Path` objects from
    *task_queue* and submits each one to the executor.  The consumer thread
    runs until :meth:`stop` is called, after which it drains the queue and
    waits for all in-flight futures to complete.

    Args:
        task_queue: The bounded queue populated by the watchdog handler.
        settings: Application settings instance.
        dedup_store: Optional pre-constructed deduplication store.  When
            omitted, a default store at ``~/.bp_ecg/processed.db`` is used.
    """

    def __init__(
        self,
        task_queue: queue.Queue[Path],
        settings: Settings,
        dedup_store: DedupStore | None = None,
    ) -> None:
        """Initialise the dispatcher (does not start the consumer thread)."""
        self._queue: queue.Queue[Path] = task_queue
        self._settings: Settings = settings
        self._dedup_store: DedupStore = (
            dedup_store if dedup_store is not None else DedupStore()
        )
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=settings.max_workers,
            thread_name_prefix="bp_ecg_worker",
        )
        self._s3_client: Any = create_s3_client(
            endpoint_url=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            use_ssl=settings.minio_use_ssl,
        )
        self._consumer_thread: threading.Thread = threading.Thread(
            target=self._consume,
            name="bp_ecg_dispatcher",
            daemon=True,
        )
        self._stop_event: threading.Event = threading.Event()
        self._futures: list[Future[None]] = []

    def start(self) -> None:
        """Start the consumer thread.

        Safe to call only once.  The consumer will run until :meth:`stop`
        is called.
        """
        logger.info("dispatcher_starting", max_workers=self._settings.max_workers)
        self._consumer_thread.start()

    def stop(self) -> None:
        """Signal the consumer to finish and wait for all work to complete.

        Stops accepting new items from the queue, waits for all in-flight
        futures to complete, and shuts down the executor.
        """
        logger.info("dispatcher_stopping")
        self._stop_event.set()
        self._queue.join()
        self._consumer_thread.join(timeout=30)
        self._executor.shutdown(wait=True)
        logger.info("dispatcher_stopped")

    def _consume(self) -> None:
        """Consumer thread body.

        Reads paths from the queue and submits them to the executor.  Marks
        each task as done (``task_done()``) regardless of success or failure
        so that :meth:`stop` can drain the queue reliably.
        """
        while not self._stop_event.is_set():
            try:
                zip_path: Path = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            future: Future[None] = self._executor.submit(
                submit_with_retry,
                zip_path,
                self._s3_client,
                self._settings,
                self._dedup_store,
            )
            self._futures.append(future)

            self._queue.task_done()

        logger.debug("dispatcher_consumer_exited")

