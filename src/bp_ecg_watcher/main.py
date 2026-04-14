"""Entry point for the bp_ecg batch processor.

Scans input_directory for .zip files and processes them all.

Usage::

    python -m bp_ecg_watcher.main
"""

from __future__ import annotations

import structlog

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.dedup.store import DedupStore
from bp_ecg_watcher.processor.skip_logger import SkipLogger
from bp_ecg_watcher.queue_manager.dispatcher import BatchRunner


def configure_logging(settings: Settings) -> None:
    """Configure structlog for dev (coloured) or production (JSON) output."""
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if settings.environment == "dev"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def main() -> None:
    """Scan input_directory for .zip files and process them all."""
    settings: Settings = Settings()  # type: ignore[call-arg]
    configure_logging(settings)

    settings.input_directory.mkdir(parents=True, exist_ok=True)
    settings.output_directory.mkdir(parents=True, exist_ok=True)

    dedup = DedupStore()
    skip_logger = SkipLogger(settings.output_directory / "skip.log")

    log = logger.bind(
        input_dir=str(settings.input_directory),
        output_dir=str(settings.output_directory),
        io_workers=settings.io_workers,
        cpu_workers=settings.cpu_workers,
    )
    log.info("batch_starting")

    zip_paths = settings.input_directory.glob("*.zip")
    runner = BatchRunner(settings, dedup, skip_logger)
    processed, failed = runner.run(zip_paths)

    log.info("batch_complete", processed=processed, failed=failed)


if __name__ == "__main__":
    main()
