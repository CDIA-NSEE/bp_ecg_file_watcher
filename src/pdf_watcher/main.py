import signal
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from threading import Thread
from typing import Any

import structlog

from pdf_watcher.config import get_settings
from pdf_watcher.logging_setup import setup_logging
from pdf_watcher.processor import process_file
from pdf_watcher.queue_manager import FileQueue
from pdf_watcher.watcher import start_observer

log = structlog.get_logger(__name__)


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_file, settings.log_level)

    log.info(
        "application_starting",
        watch_dir=str(settings.watch_dir),
        output_dir=str(settings.output_dir),
        rejected_dir=str(settings.rejected_dir),
        log_file=str(settings.log_file),
        log_level=settings.log_level,
        max_workers=settings.max_workers,
    )

    file_queue = FileQueue(maxsize=settings.queue_max_size)

    process_pool: ProcessPoolExecutor | None = None
    if settings.use_process_pool_for_zstd:
        process_pool = ProcessPoolExecutor(max_workers=settings.max_process_workers)

    thread_pool = ThreadPoolExecutor(max_workers=settings.max_workers)

    def worker() -> None:
        while True:
            path = file_queue.get()
            if path is None:
                file_queue.task_done(None)
                break
            try:
                process_file(path, settings, process_pool)
            finally:
                file_queue.task_done(path)

    worker_threads: list[Thread] = []
    for _ in range(settings.max_workers):
        t = Thread(target=worker, daemon=True)
        t.start()
        worker_threads.append(t)

    observer = start_observer(file_queue, settings)

    def shutdown(signum: int, frame: Any) -> None:
        log.info("shutdown_signal_received", signal=signum)
        observer.stop()
        observer.join()
        file_queue.send_shutdown(settings.max_workers)
        thread_pool.shutdown(wait=True)
        if process_pool is not None:
            process_pool.shutdown(wait=True)
        log.info("application_stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
