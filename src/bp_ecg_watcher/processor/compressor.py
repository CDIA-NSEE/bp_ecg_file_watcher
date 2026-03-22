"""Zstandard streaming compression utilities for the bp_ecg_file_watcher pipeline.

Provides a :class:`ByteCountingReader` proxy that wraps any readable byte stream
and transparently counts the number of bytes that pass through. This is used to
measure the compressed size *after* streaming upload without buffering the entire
compressed payload in memory.

Also provides :func:`compress_bytes` as a simple helper for tests and standalone
use that compresses a byte string in a single call and returns the compressed bytes
together with the byte count.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from io import BytesIO
from typing import IO

import structlog
import zstandard

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class ByteCountingReader:
    """Wraps a readable binary stream and counts bytes as they pass through.

    Intended to be placed between the zstandard ``stream_reader`` and boto3's
    ``upload_fileobj`` so that the final compressed byte count is known after
    the upload completes without materialising the compressed blob in memory.

    Args:
        source: Any object that exposes a ``read(n)`` interface.
    """

    def __init__(self, source: IO[bytes]) -> None:
        """Initialise the counter and store a reference to the underlying stream."""
        self._src: IO[bytes] = source
        self.bytes_read: int = 0

    def read(self, n: int = -1) -> bytes:
        """Read up to *n* bytes from the underlying stream and update the counter.

        Args:
            n: Maximum number of bytes to read. ``-1`` reads until EOF.

        Returns:
            The bytes returned by the underlying stream.
        """
        chunk: bytes = self._src.read(n)
        self.bytes_read += len(chunk)
        return chunk


@contextmanager
def compressed_counting_reader(
    image_bytes: bytes,
    level: int = 9,
) -> Generator[ByteCountingReader, None, None]:
    """Context manager yielding a :class:`ByteCountingReader` over zstd-compressed data.

    Opens a ``zstandard`` stream_reader around *image_bytes* and wraps it in a
    :class:`ByteCountingReader`. The caller reads from the yielded object (e.g. via
    ``boto3.upload_fileobj``) and then inspects ``.bytes_read`` after the context exits.

    Args:
        image_bytes: Raw (uncompressed) PNG image data.
        level: Zstandard compression level (1–22). Defaults to 9.

    Yields:
        A :class:`ByteCountingReader` that reads zstd-compressed bytes.

    Example::

        with compressed_counting_reader(image_bytes, level=9) as reader:
            s3.upload_fileobj(reader, bucket, key)
        compressed_size = reader.bytes_read
    """
    cctx = zstandard.ZstdCompressor(level=level)
    image_buffer = BytesIO(image_bytes)
    with cctx.stream_reader(image_buffer) as compressed_stream:
        counting_reader = ByteCountingReader(compressed_stream)  # type: ignore[arg-type]
        logger.debug(
            "compressed_stream_opened",
            compression_level=level,
            source_bytes=len(image_bytes),
        )
        yield counting_reader
    logger.debug(
        "compressed_stream_closed",
        bytes_out=counting_reader.bytes_read,
    )


def compress_bytes(data: bytes, level: int = 9) -> tuple[bytes, int]:
    """Compress *data* with zstandard and return the compressed bytes and their size.

    Convenience helper for tests and ad-hoc use. Not used in the streaming upload path.

    Args:
        data: Raw bytes to compress.
        level: Zstandard compression level (1–22). Defaults to 9.

    Returns:
        A tuple of ``(compressed_bytes, len(compressed_bytes))``.
    """
    cctx = zstandard.ZstdCompressor(level=level)
    compressed: bytes = cctx.compress(data)
    return compressed, len(compressed)
