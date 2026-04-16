"""Dask-based distributed runner for the bp_ecg batch processor.

Selects the right Dask client backend automatically:

* ``settings.dask_scheduler`` is set  → connect to a running scheduler
  (docker-compose or explicit TCP address).
* ``SLURM_JOB_ID`` env var is present  → spawn Dask workers as SLURM jobs
  via ``dask-jobqueue`` (HPC mode).
* Neither condition                     → ``LocalCluster`` on the current
  machine (development / single-node testing).
"""

from __future__ import annotations

import itertools
import os
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

import structlog
from distributed import Client, LocalCluster, as_completed

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.processor.pipeline import process_file

if TYPE_CHECKING:
    pass

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# Maximum number of futures held in memory at once.  Keeps scheduler RAM
# bounded when processing millions of files.
_SUBMIT_CHUNK: int = 100_000


def build_client(settings: Settings) -> Client:
    """Create the appropriate Dask ``Client`` for the current environment.

    Selection order:

    1. ``settings.dask_scheduler`` is non-empty → remote scheduler.
    2. ``SLURM_JOB_ID`` is set                 → SLURMCluster (HPC).
    3. Fallback                                 → LocalCluster.

    Args:
        settings: Application settings.

    Returns:
        A connected ``Client``.  The caller is responsible for closing it
        (use as a context manager).
    """
    scheduler = settings.dask_scheduler
    if scheduler:
        logger.info("dask_mode", mode="remote", scheduler=scheduler)
        return Client(address=scheduler)

    if os.environ.get("SLURM_JOB_ID"):
        from dask_jobqueue import SLURMCluster  # type: ignore[import-untyped]

        logger.info(
            "dask_mode",
            mode="slurm",
            n_workers=settings.n_workers,
            cores=settings.cores_per_worker,
            memory_gb=settings.mem_per_worker_gb,
        )
        cluster = SLURMCluster(
            queue="cpu",
            cores=settings.cores_per_worker,
            processes=settings.cores_per_worker,
            memory=f"{settings.mem_per_worker_gb}GB",
            nanny=True,
            walltime="48:00:00",
            # Propagate all env vars (including REDIS_URL) to worker jobs.
            job_extra_directives=["--export=ALL"],
        )
        cluster.scale(settings.n_workers)
        return Client(cluster)

    logger.info("dask_mode", mode="local", n_workers=settings.n_workers)
    return Client(
        LocalCluster(
            n_workers=settings.n_workers,
            threads_per_worker=1,
            processes=True,
        )
    )


class DaskRunner:
    """Orchestrates Dask-distributed processing of ZIP files.

    Args:
        settings: Application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def run(
        self,
        zip_paths: Iterable[Path],
        *,
        _client: Client | None = None,
    ) -> tuple[int, int]:
        """Submit *zip_paths* to Dask workers and track completion.

        Files are submitted in chunks of ``_SUBMIT_CHUNK`` to bound scheduler
        memory when processing millions of files.

        Args:
            zip_paths: Iterable of paths to ``.zip`` files.
            _client: Optional pre-built ``Client`` (testing hook — bypasses
                ``build_client`` and skips context-manager close).

        Returns:
            ``(processed, failed)`` counts.
        """
        skip_log_path = self._settings.output_directory / "skip.log"
        task = partial(
            process_file,
            settings=self._settings,
            skip_log_path=skip_log_path,
        )

        if _client is not None:
            return self._run_with_client(_client, task, zip_paths)

        with build_client(self._settings) as client:
            return self._run_with_client(client, task, zip_paths)

    def _run_with_client(
        self,
        client: Client,
        task: partial,  # type: ignore[type-arg]
        zip_paths: Iterable[Path],
    ) -> tuple[int, int]:
        processed = 0
        failed = 0

        for chunk in itertools.batched(zip_paths, _SUBMIT_CHUNK):
            futures = client.map(task, list(chunk), pure=False)
            for future in as_completed(futures, raise_errors=False):
                if future.status == "error":
                    failed += 1
                    logger.error("file_failed", error=str(future.exception()))
                else:
                    processed += 1

                total = processed + failed
                if total % 1_000 == 0:
                    logger.info(
                        "progress",
                        total=total,
                        processed=processed,
                        failed=failed,
                    )

        return processed, failed

