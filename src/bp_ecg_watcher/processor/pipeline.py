"""Processing pipeline for the bp_ecg batch processor.

Single-stage pipeline executed directly inside each Dask worker process:
``process_file`` reads the ZIP, validates the PDF, rasterizes and compresses
it, and writes the output.  All CPU-heavy work runs inline — Dask already
assigns one worker process per CPU core, so there is no need for a nested
``ProcessPoolExecutor``.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import structlog
import zstandard

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.dedup.redis_store import RedisStore
from bp_ecg_watcher.processor.extractor import rasterize_all_pages
from bp_ecg_watcher.processor.hasher import hash_bytes
from bp_ecg_watcher.processor.image import images_to_pdf_bytes, resize_image
from bp_ecg_watcher.processor.skip_logger import SkipLogger
from bp_ecg_watcher.validator.pdf_validator import ValidationFailure, validate_pdf

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def rasterize_and_compress(
    pdf_bytes_raw: bytes,
    dpi: int,
    max_side: int,
    zstd_level: int,
) -> bytes:
    """Rasterize all pages, resize, re-embed as PDF, compress with zstd.

    Args:
        pdf_bytes_raw: Raw PDF bytes extracted from the ZIP.
        dpi: Rasterization resolution.
        max_side: Maximum pixel dimension for resize.
        zstd_level: Zstandard compression level (1-22).

    Returns:
        zstd-compressed PDF bytes.
    """
    pdf_buf = BytesIO(pdf_bytes_raw)
    pil_images = rasterize_all_pages(pdf_buf, dpi=dpi)
    resized = [resize_image(img, max_side_px=max_side)[0] for img in pil_images]
    output_pdf = images_to_pdf_bytes(resized)
    return zstandard.ZstdCompressor(level=zstd_level).compress(output_pdf)


def process_file(
    zip_path: Path,
    *,
    settings: Settings,
    redis_url: str,
    skip_log_path: Path,
) -> None:
    """Read ZIP, validate, rasterize, compress, and write output.

    Designed to run inside a Dask worker.  Each call reconstructs lightweight
    helpers (``RedisStore``, ``SkipLogger``) from plain serialisable arguments
    so that this function remains picklable.

    Args:
        zip_path: Path to the .zip file to process.
        settings: Application settings (picklable Pydantic model).
        redis_url: Redis connection URL for deduplication.
        skip_log_path: Path to the skip log file.
    """
    dedup_store = RedisStore(redis_url)
    skip_logger = SkipLogger(skip_log_path)

    zip_bytes = zip_path.read_bytes()
    zip_hash = hash_bytes(zip_bytes)

    # Atomically reserve this hash.  If another worker already owns it
    # (status "pending" or complete), skip immediately — no TOCTOU window.
    if not dedup_store.try_reserve(zip_hash):
        skip_logger.log(zip_path, "duplicate")
        return

    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            pdf_names = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
            if not pdf_names:
                raise ValueError("No PDF found inside ZIP")
            pdf_bytes_raw = zf.read(pdf_names[0])
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        dedup_store.release(zip_hash)  # allow retry if ZIP was transient error
        skip_logger.log(zip_path, f"zip-error: {exc}")
        return

    validation = validate_pdf(BytesIO(pdf_bytes_raw))
    if isinstance(validation, ValidationFailure):
        dedup_store.release(zip_hash)
        skip_logger.log(zip_path, str(validation.reason))
        return

    compressed = rasterize_and_compress(
        pdf_bytes_raw,
        settings.rasterization_dpi,
        settings.image_max_side_px,
        settings.zstd_level,
    )

    out_path = settings.output_directory / f"{zip_hash}.pdf.zst"
    out_path.write_bytes(compressed)
    dedup_store.mark_complete(zip_hash, str(out_path))
    logger.debug("file_processed", path=str(zip_path), output=str(out_path))

