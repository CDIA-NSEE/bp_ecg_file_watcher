import queue
import threading
from pathlib import Path


class FileQueue:
    def __init__(self, maxsize: int = 100) -> None:
        self._queue: queue.Queue[Path | None] = queue.Queue(maxsize=maxsize)
        self._in_flight: set[Path] = set()
        self._lock = threading.Lock()

    def put(self, path: Path) -> bool:
        with self._lock:
            if path in self._in_flight:
                return False
            self._in_flight.add(path)
        self._queue.put(path)
        return True

    def get(self) -> Path | None:
        return self._queue.get()

    def task_done(self, path: Path | None) -> None:
        if path is not None:
            with self._lock:
                self._in_flight.discard(path)
        self._queue.task_done()

    def send_shutdown(self, num_workers: int) -> None:
        for _ in range(num_workers):
            self._queue.put(None)

    def join(self) -> None:
        self._queue.join()
