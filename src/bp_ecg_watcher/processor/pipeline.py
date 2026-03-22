"""Per-ZIP processing pipeline for bp_ecg_file_watcher.

Extracted from the dispatcher to keep each module focused.
Contains:
- ``process_zip``: the full validate → compress → upload pipeline.
- ``submit_with_retry``: exponential back-off wrapper with DLQ routing.

No intermediate files are ever written to disk.
"""

from __future__ import annotations

import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import structlog
import zstandard

from bp_ecg_watcher.dedup.store import DedupStore
from bp_ecg_watcher.metrics import (
    files_processed_total,
    files_rejected_total,
    processing_duration_seconds,
)
from bp_ecg_watcher.processor.extractor import rasterize_page2
from bp_ecg_watcher.processor.hasher import hash_bytes
from bp_ecg_watcher.processor.image import image_to_png_bytes, resize_image
from bp_ecg_watcher.storage.minio_client import (
    build_metadata,
    create_s3_client,
    format_exc_info,
    upload_dlq,
    upload_image,
    upload_intake,
    upload_rejected,
)
from bp_ecg_watcher.validator.pdf_validator import (
    ValidationFailure,
    ValidationSuccess,
    validate_pdf,
)
from bp_ecg_watcher.config import Settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def process_zip(
    zip_path: Path,
    s3_client: Any,
    settings: Settings,
    dedup_store: DedupStore,
) -> str:
    """Validate, process, and upload a single ZIP file in-memory.

    Pipeline steps:

    1. Read ZIP bytes and compute BLAKE3 for deduplication.
    2. Check deduplication store — skip if already processed.
    3. Extract the PDF from the ZIP.
    4. Upload original ZIP to the intake bucket (audit trail).
    5. Validate page count.
    6. If invalid → route to the rejected bucket and return.
    7. Rasterize page 2, resize, encode as PNG.
    8. Compress PNG with zstandard (size known before upload).
    9. Build metadata with exact compressed size (no copy_object race).
    10. Upload compressed image to the images bucket.
    11. Record the ZIP hash in the deduplication store.

    Args:
        zip_path: Filesystem path of the ZIP file to process.
        s3_client: Configured boto3 S3 client pointing at MINIO.
        settings: Application settings instance.
        dedup_store: Deduplication store shared across worker threads.

    Returns:
        The S3 object key of the uploaded image (or rejected/duplicate object).

    Raises:
        Exception: Any unhandled error from the processing pipeline.
    """
    processing_start: datetime = datetime.now(UTC)

    bound_logger: structlog.stdlib.BoundLogger = logger.bind(
        source_zip=zip_path.name,
        environment=settings.environment,
    )

    # ── 1. Read ZIP bytes ──────────────────────────────────────────────
    zip_bytes: bytes = zip_path.read_bytes()
    zip_hash: str = hash_bytes(zip_bytes)
    bound_logger = bound_logger.bind(zip_hash=zip_hash[:12])

    # ── 2. Deduplication check ─────────────────────────────────────────
    if dedup_store.is_duplicate(zip_hash):
        bound_logger.warning("duplicate_zip_skipped", zip_hash=zip_hash)
        return f"duplicate/{zip_hash}"

    # ── 3. Extract PDF from ZIP ────────────────────────────────────────
    date_prefix: str = datetime.now(UTC).strftime("%Y/%m/%d")
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            pdf_names: list[str] = [
                n for n in zf.namelist() if n.lower().endswith(".pdf")
            ]
            if not pdf_names:
                raise ValueError("No PDF found inside ZIP")
            pdf_bytes_raw: bytes = zf.read(pdf_names[0])
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        bound_logger.error("zip_read_error", error=str(exc))
        rejected_key: str = f"{date_prefix}/{zip_path.stem}_{zip_hash[:8]}.zip"
        upload_rejected(
            s3_client=s3_client,
            bucket=settings.bucket_rejected,
            key=rejected_key,
            zip_bytes=zip_bytes,
            rejection_reason="zip-read-error",
            actual_pages=0,
            source_zip_path=zip_path,
            watcher_version=settings.watcher_version,
        )
        files_rejected_total.labels(reason="zip-read-error").inc()
        return rejected_key

    pdf_bytes: BytesIO = BytesIO(pdf_bytes_raw)

    # ── 4. Upload intake (audit trail) ────────────────────────────────
    intake_key: str = f"{date_prefix}/{zip_path.stem}_{zip_hash[:8]}.zip"
    upload_intake(
        s3_client=s3_client,
        bucket=settings.bucket_intake,
        key=intake_key,
        zip_bytes=zip_bytes,
        source_zip_path=zip_path,
        watcher_version=settings.watcher_version,
    )

    # ── 5. Validate PDF ────────────────────────────────────────────────
    validation = validate_pdf(pdf_bytes)

    if isinstance(validation, ValidationFailure):
        rejected_key = f"{date_prefix}/{zip_path.stem}_{zip_hash[:8]}.zip"
        upload_rejected(
            s3_client=s3_client,
            bucket=settings.bucket_rejected,
            key=rejected_key,
            zip_bytes=zip_bytes,
            rejection_reason=str(validation.reason),
            actual_pages=validation.actual_pages,
            source_zip_path=zip_path,
            watcher_version=settings.watcher_version,
        )
        bound_logger.warning(
            "pdf_rejected",
            reason=str(validation.reason),
            actual_pages=validation.actual_pages,
        )
        files_rejected_total.labels(reason=str(validation.reason)).inc()
        return rejected_key

    assert isinstance(validation, ValidationSuccess)

    # ── 6. Rasterize, resize, encode as PNG ───────────────────────────
    original_pdf_hash: str = hash_bytes(pdf_bytes_raw)
    pil_image = rasterize_page2(pdf_bytes, dpi=settings.rasterization_dpi)
    resized_image, img_width, img_height = resize_image(
        pil_image, max_side_px=settings.image_max_side_px
    )
    image_bytes: bytes = image_to_png_bytes(resized_image)

    # ── 7. BLAKE3 hash of PNG ──────────────────────────────────────────
    content_hash: str = hash_bytes(image_bytes)
    image_key: str = f"{date_prefix}/{content_hash}.png.zst"
    bound_logger = bound_logger.bind(file_hash=content_hash[:12])

    # ── 8. Compress BEFORE building metadata (no copy_object race) ────
    cctx: zstandard.ZstdCompressor = zstandard.ZstdCompressor(
        level=settings.zstd_level
    )
    compressed_bytes: bytes = cctx.compress(image_bytes)

    # ── 9. Build metadata with exact compressed size ───────────────────
    metadata: dict[str, str] = build_metadata(
        content_hash=content_hash,
        source_zip_path=zip_path,
        page_count=validation.page_count,
        image_width=img_width,
        image_height=img_height,
        rasterization_dpi=settings.rasterization_dpi,
        original_pdf_hash=original_pdf_hash,
        file_size_zip_bytes=len(zip_bytes),
        file_size_compressed_bytes=len(compressed_bytes),
        processing_start=processing_start,
        pdf_producer=validation.pdf_producer,
        pdf_creator=validation.pdf_creator,
        watcher_version=settings.watcher_version,
        source_intake_key=intake_key,
    )

    # ── 10. Upload compressed image ────────────────────────────────────
    upload_image(
        s3_client=s3_client,
        bucket=settings.bucket_images,
        key=image_key,
        compressed_bytes=compressed_bytes,
        metadata=metadata,
    )

    # ── 11. Record deduplication ───────────────────────────────────────
    dedup_store.record_processed(
        zip_hash=zip_hash,
        source_path=zip_path,
        destination_key=image_key,
    )

    processing_end: datetime = datetime.now(UTC)
    elapsed_s: float = (processing_end - processing_start).total_seconds()

    files_processed_total.labels(bucket=settings.bucket_images).inc()
    processing_duration_seconds.observe(elapsed_s)

    bound_logger.info(
        "zip_processed_ok",
        image_key=image_key,
        compressed_bytes=len(compressed_bytes),
        elapsed_s=round(elapsed_s, 3),
    )
    return image_key


def submit_with_retry(
    zip_path: Path,
    s3_client: Any,
    settings: Settings,
    dedup_store: DedupStore,
    max_attempts: int = 3,
) -> None:
    """Process *zip_path* with exponential back-off retry and DLQ routing.

    Back-off schedule: 2 s after attempt 1, 4 s after attempt 2.
    After *max_attempts* failures the original ZIP and an ``_error.json``
    sidecar are uploaded to the DLQ bucket.

    Args:
        zip_path: ZIP file to process.
        s3_client: Configured boto3 S3 client.
        settings: Application settings.
        dedup_store: Deduplication store shared across workers.
        max_attempts: Maximum number of attempts before routing to DLQ.
    """
    first_attempt_at: datetime = datetime.now(UTC)
    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            process_zip(
                zip_path=zip_path,
                s3_client=s3_client,
                settings=settings,
                dedup_store=dedup_store,
            )
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "processing_attempt_failed",
                path=str(zip_path),
                attempt=attempt,
                error=str(exc),
            )
            if attempt < max_attempts:
                time.sleep(2**attempt)

    # All attempts exhausted — route to DLQ
    last_attempt_at: datetime = datetime.now(UTC)
    assert last_exc is not None

    error_type, error_message, stack_trace_str = format_exc_info(last_exc)

    try:
        zip_bytes: bytes = zip_path.read_bytes()
    except OSError:
        zip_bytes = b""

    dlq_key: str = f"{datetime.now(UTC).strftime('%Y/%m/%d')}/{zip_path.name}"

    upload_dlq(
        s3_client=s3_client,
        bucket=settings.bucket_dlq,
        source_key=dlq_key,
        original_bytes=zip_bytes,
        error_type=error_type,
        error_message=error_message,
        stack_trace_str=stack_trace_str,
        attempt_count=max_attempts,
        first_attempt_at=first_attempt_at,
        last_attempt_at=last_attempt_at,
    )
