import time
from pathlib import Path

import structlog
from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from pdf_watcher.config import Settings
from pdf_watcher.queue_manager import FileQueue

log = structlog.get_logger(__name__)


def wait_for_file_ready(path: Path, interval: float, retries: int) -> bool:
    prev_size: int = -1
    stable_count: int = 0

    for _ in range(retries):
        time.sleep(interval)

        if not path.exists():
            log.warning("file_disappeared", path=str(path))
            return False

        try:
            current_size = path.stat().st_size
        except OSError:
            log.warning("file_stat_failed", path=str(path))
            return False

        if current_size > 0 and current_size == prev_size:
            stable_count += 1
            if stable_count >= 2:
                try:
                    with path.open("rb"):
                        pass
                    return True
                except OSError:
                    stable_count = 0
        else:
            stable_count = 0

        prev_size = current_size

    log.warning("file_not_ready", path=str(path))
    return False


class ZipFileHandler(FileSystemEventHandler):
    def __init__(self, file_queue: FileQueue, settings: Settings) -> None:
        super().__init__()
        self._file_queue = file_queue
        self._settings = settings

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return

        path = Path(str(event.src_path))

        if path.suffix.lower() != ".zip":
            return

        log.info("zip_detected", path=str(path))

        ready = wait_for_file_ready(
            path,
            interval=self._settings.file_stable_interval,
            retries=self._settings.file_stable_retries,
        )

        if not ready:
            log.warning("file_not_ready_skipped", path=str(path))
            return

        enqueued = self._file_queue.put(path)
        if enqueued:
            log.info("file_enqueued", path=str(path))
        else:
            log.info("file_already_in_flight", path=str(path))


def start_observer(file_queue: FileQueue, settings: Settings) -> BaseObserver:
    handler = ZipFileHandler(file_queue=file_queue, settings=settings)
    observer = Observer()
    observer.schedule(handler, str(settings.watch_dir), recursive=False)
    observer.start()
    log.info("observer_started", watch_dir=str(settings.watch_dir))
    return observer
