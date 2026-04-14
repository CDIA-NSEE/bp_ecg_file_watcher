"""Processing pipeline for the bp_ecg batch processor.

Two-stage hybrid pipeline:
- ``rasterize_and_compress`` — CPU-heavy; runs in a ProcessPoolExecutor worker.
  Must be a top-level function so it is picklable.
- ``process_file`` — I/O stage; runs in a ThreadPoolExecutor worker.
  Reads the ZIP, validates, delegates CPU work, writes output.
"""

from __future__ import annotations

import zipfile
from concurrent.futures import ProcessPoolExecutor
from io import BytesIO
from pathlib import Path

import structlog
import zstandard

from bp_ecg_watcher.config import Settings
from bp_ecg_watcher.dedup.store import DedupStore
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
    """CPU-heavy stage: rasterize all pages, resize, re-embed as PDF, compress.

    Top-level function so it is picklable for ProcessPoolExecutor.

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
    settings: Settings,
    dedup_store: DedupStore,
    skip_logger: SkipLogger,
    cpu_pool: ProcessPoolExecutor,
) -> None:
    """I/O stage: read ZIP, validate, delegate CPU work, write output.

    Runs in a thread from the I/O ThreadPoolExecutor.

    Args:
        zip_path: Path to the .zip file to process.
        settings: Application settings.
        dedup_store: Shared deduplication store.
        skip_logger: Logs skipped files to file and terminal.
        cpu_pool: ProcessPoolExecutor for CPU-heavy work.
    """
    zip_bytes = zip_path.read_bytes()
    zip_hash = hash_bytes(zip_bytes)

    if dedup_store.is_duplicate(zip_hash):
        skip_logger.log(zip_path, "duplicate")
        return

    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            pdf_names = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
            if not pdf_names:
                raise ValueError("No PDF found inside ZIP")
            pdf_bytes_raw = zf.read(pdf_names[0])
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        skip_logger.log(zip_path, f"zip-error: {exc}")
        return

    validation = validate_pdf(BytesIO(pdf_bytes_raw))
    if isinstance(validation, ValidationFailure):
        skip_logger.log(zip_path, str(validation.reason))
        return

    compressed = cpu_pool.submit(
        rasterize_and_compress,
        pdf_bytes_raw,
        settings.rasterization_dpi,
        settings.image_max_side_px,
        settings.zstd_level,
    ).result()

    out_path = settings.output_directory / f"{zip_hash}.pdf.zst"
    out_path.write_bytes(compressed)
    dedup_store.record_processed(zip_hash, zip_path, str(out_path))
