"""Redis-backed deduplication store using atomic SET … NX.

Each call to ``RedisStore`` creates a lightweight wrapper. The Redis
``ConnectionPool`` is cached on the Dask worker object (inside a worker
process) or in a module-level dict (local / test mode) to avoid creating
a new pool per task invocation.
"""

from __future__ import annotations

from pathlib import Path

import redis

_POOL_ATTR = "_bp_ecg_redis_pool"

# Module-level fallback: keyed by redis_url, used outside Dask workers.
_PROCESS_POOLS: dict[str, redis.ConnectionPool] = {}


def _get_pool(redis_url: str) -> redis.ConnectionPool:
    """Return a connection pool for *redis_url*, creating it if needed.

    * Inside a Dask worker  → pool is stored on the worker object so it
      persists across tasks on the same worker process.
    * Outside a Dask worker → pool is stored in the module-level dict
      (one pool per URL per process).
    """
    try:
        from distributed import get_worker  # lazy: avoids hard dep in tests

        worker = get_worker()
        if not hasattr(worker, _POOL_ATTR):
            setattr(
                worker,
                _POOL_ATTR,
                redis.ConnectionPool.from_url(redis_url, max_connections=10),
            )
        return getattr(worker, _POOL_ATTR)  # type: ignore[no-any-return]
    except (ImportError, ValueError):
        # Not inside a Dask worker (local run, unit tests).
        if redis_url not in _PROCESS_POOLS:
            _PROCESS_POOLS[redis_url] = redis.ConnectionPool.from_url(
                redis_url, max_connections=10
            )
        return _PROCESS_POOLS[redis_url]


class RedisStore:
    """Redis-backed deduplication using atomic ``SET … NX``.

    The key space is ``bp_ecg:processed:{zip_hash}``.  The value stored is
    the output file path (informational only — not relied upon for logic).

    Args:
        redis_url: Redis connection URL, e.g. ``redis://localhost:6379``.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    def _client(self) -> redis.Redis:  # type: ignore[type-arg]
        return redis.Redis(connection_pool=_get_pool(self._redis_url))

    @staticmethod
    def _key(zip_hash: str) -> str:
        return f"bp_ecg:processed:{zip_hash}"

    def try_reserve(self, zip_hash: str) -> bool:
        """Atomically reserve *zip_hash* for processing.

        Sets the key to ``"pending"`` with NX (only if absent).  Returns
        ``True`` if the reservation succeeded (this worker owns the file),
        ``False`` if another worker already owns or completed it.

        Args:
            zip_hash: BLAKE3 hex digest of the raw ZIP bytes.

        Returns:
            ``True`` if this caller may proceed; ``False`` means skip.
        """
        return bool(self._client().set(self._key(zip_hash), "pending", nx=True))

    def mark_complete(
        self,
        zip_hash: str,
        output_path: str,
    ) -> None:
        """Overwrite the ``"pending"`` marker with the real output path.

        Args:
            zip_hash: BLAKE3 hex digest of the raw ZIP bytes.
            output_path: Path of the written output file.
        """
        self._client().set(self._key(zip_hash), output_path)

    def release(self, zip_hash: str) -> None:
        """Delete the reservation so the file can be retried by another worker.

        Called when processing fails after ``try_reserve`` so the hash is not
        permanently locked out.

        Args:
            zip_hash: BLAKE3 hex digest of the raw ZIP bytes.
        """
        self._client().delete(self._key(zip_hash))
