"""Entry point for the bp_ecg_file_watcher service.

Bootstraps structured logging, starts a Prometheus metrics HTTP server,
creates the watchdog observer and dispatcher, and installs SIGTERM/SIGINT
handlers for clean shutdown.

Usage::

    python -m bp_ecg_watcher.main
"""

from __future__ import annotations

import queue
import signal
import sys
import time
import types
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from prometheus_client import start_http_server
from watchdog.observers import Observer

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.metrics import queue_depth
from bp_ecg_watcher.queue_manager.dispatcher import Dispatcher
from bp_ecg_watcher.watcher.handler import ZipFileHandler

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def configure_logging(settings: Settings) -> None:
    """Configure structlog based on the runtime environment.

    In production (``settings.environment != "dev"``) the pipeline outputs
    newline-delimited JSON. In development it uses the colourful console
    renderer for readability.

    Args:
        settings: Application settings controlling log level and environment.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if settings.environment == "dev":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def main() -> None:
    """Run the bp_ecg_file_watcher service until interrupted.

    Lifecycle:

    1. Load :class:`~bp_ecg_watcher.config.Settings` from the environment.
    2. Configure structlog.
    3. Start the Prometheus metrics HTTP server.
    4. Create a bounded :class:`queue.Queue`.
    5. Start the :class:`~bp_ecg_watcher.queue_manager.dispatcher.Dispatcher`.
    6. Start the watchdog :class:`~watchdog.observers.Observer`.
    7. Block until SIGTERM / SIGINT arrives.
    8. Graceful shutdown: stop observer → drain queue → stop dispatcher.
    """
    settings: Settings = Settings()  # type: ignore[call-arg]
    configure_logging(settings)

    log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__).bind(
        environment=settings.environment,
        version=settings.watcher_version,
    )
    log.info("service_starting", watch_dir=str(settings.watch_directory))

    # Ensure the watched directory exists
    settings.watch_directory.mkdir(parents=True, exist_ok=True)

    # Start Prometheus metrics endpoint
    start_http_server(settings.metrics_port)
    log.info("metrics_server_started", port=settings.metrics_port)

    # Build shared task queue
    task_queue: queue.Queue[Path] = queue.Queue(maxsize=settings.queue_maxsize)

    # Build and start the dispatcher
    dispatcher: Dispatcher = Dispatcher(
        task_queue=task_queue,
        settings=settings,
    )
    dispatcher.start()

    # Build the watchdog event handler and observer
    handler: ZipFileHandler = ZipFileHandler(
        task_queue=task_queue,
        debounce_polls=settings.debounce_polls,
        debounce_interval_ms=settings.debounce_interval_ms,
    )

    observer: BaseObserver = Observer()
    observer.schedule(handler, str(settings.watch_directory), recursive=False)
    observer.start()

    log.info("observer_started", watch_dir=str(settings.watch_directory))

    # ── Enqueue any ZIPs already present at startup ─────────────────────
    existing_zips = sorted(settings.watch_directory.glob("*.zip"))
    if existing_zips:
        log.info("startup_backfill_found", count=len(existing_zips))
        for zip_path in existing_zips:
            task_queue.put(zip_path)
            log.debug("startup_backfill_enqueued", path=str(zip_path))

    # ── Shutdown handler ────────────────────────────────────────────────
    def _shutdown(signum: int, frame: types.FrameType | None) -> None:
        """Handle SIGTERM / SIGINT for graceful shutdown.

        Args:
            signum: Signal number received.
            frame: Current stack frame (unused).
        """
        log.info("shutdown_signal_received", signum=signum)
        observer.stop()
        observer.join()
        log.info("observer_stopped")
        dispatcher.stop()
        log.info("shutdown_complete")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # ── Main loop ───────────────────────────────────────────────────────
    try:
        while True:
            queue_depth.set(task_queue.qsize())
            # Block the main thread — watchdog runs in its own thread
            time.sleep(1.0)
    except KeyboardInterrupt:
        _shutdown(signal.SIGINT, None)


if __name__ == "__main__":
    main()
