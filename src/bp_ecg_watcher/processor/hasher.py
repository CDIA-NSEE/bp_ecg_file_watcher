"""BLAKE3 hashing utilities for the bp_ecg_file_watcher pipeline.

Provides deterministic content-addressed hashes of raw byte strings using the
BLAKE3 cryptographic hash function. The hash is used both for deduplication and
as the object key in MINIO.
"""

from __future__ import annotations

import blake3
import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def hash_bytes(data: bytes) -> str:
    """Compute the BLAKE3 hex digest of *data*.

    Args:
        data: Raw bytes to hash.

    Returns:
        A lowercase hexadecimal string representation of the 256-bit BLAKE3 digest.
    """
    digest: str = blake3.blake3(data).hexdigest()
    logger.debug("blake3_hash_computed", length=len(data), digest_prefix=digest[:12])
    return digest
