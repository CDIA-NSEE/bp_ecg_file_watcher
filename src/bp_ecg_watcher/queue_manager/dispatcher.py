"""Batch runner for the bp_ecg processor.

Processes all .zip files in the input directory using a hybrid pool:
threads handle I/O (read ZIP, write output), processes handle CPU work
(rasterize + compress). Files are processed in chunks to bound memory
usage when handling millions of files.
"""

from __future__ import annotations

import itertools
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

import structlog

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.dedup.store import DedupStore
from bp_ecg_watcher.processor.pipeline import process_file
from bp_ecg_watcher.processor.skip_logger import SkipLogger

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_CHUNK_SIZE = 10_000


class BatchRunner:
    """Orchestrates chunk-wise batch processing of ZIP files.

    Args:
        settings: Application settings (worker counts, paths, etc.).
        dedup_store: Shared deduplication store.
        skip_logger: Logs skipped files to file and terminal.
    """

    def __init__(
        self,
        settings: Settings,
        dedup_store: DedupStore,
        skip_logger: SkipLogger,
    ) -> None:
        self._settings = settings
        self._dedup = dedup_store
        self._skip_logger = skip_logger

    def run(self, zip_paths: Iterable[Path]) -> tuple[int, int]:
        """Process all zip_paths and return (processed, failed) counts.

        Submits work in chunks of _CHUNK_SIZE to avoid holding millions of
        futures in memory simultaneously.
        """
        processed = 0
        failed = 0

        with (
            ProcessPoolExecutor(max_workers=self._settings.cpu_workers) as cpu_pool,
            ThreadPoolExecutor(max_workers=self._settings.io_workers) as io_pool,
        ):
            for chunk in itertools.batched(zip_paths, _CHUNK_SIZE):
                futures = {
                    io_pool.submit(
                        process_file,
                        zp,
                        self._settings,
                        self._dedup,
                        self._skip_logger,
                        cpu_pool,
                    ): zp
                    for zp in chunk
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                        processed += 1
                    except Exception as exc:
                        failed += 1
                        logger.error(
                            "file_failed",
                            path=str(futures[future]),
                            error=str(exc),
                        )
                    total = processed + failed
                    if total % 1000 == 0:
                        logger.info("progress", total=total, processed=processed, failed=failed)

        return processed, failed
