"""MINIO/S3 client wrapper for the bp_ecg_file_watcher pipeline.

Provides upload helpers for processed images (bp-ecg-{env}-images),
intake ZIPs (bp-ecg-{env}-intake), rejected files (bp-ecg-{env}-rejected),
and dead-letter queue entries (bp-ecg-{env}-dlq).

All uploads use streaming — no intermediate files are written to disk.
"""

from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import boto3
import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def build_metadata(
    content_hash: str,
    source_zip_path: Path,
    page_count: int,
    image_width: int,
    image_height: int,
    rasterization_dpi: int,
    original_pdf_hash: str,
    file_size_zip_bytes: int,
    file_size_compressed_bytes: int,
    processing_start: datetime,
    pdf_producer: str | None,
    pdf_creator: str | None,
    watcher_version: str,
    source_intake_key: str = "",
) -> dict[str, str]:
    """Build the MINIO object metadata dict for a successfully processed image.

    All values are coerced to :class:`str` because S3/MINIO metadata only
    supports string values.

    Args:
        content_hash: BLAKE3 hex digest of the raw (uncompressed) PNG bytes.
        source_zip_path: Filesystem path of the source ZIP file.
        page_count: Number of pages in the validated PDF (always 2).
        image_width: Width in pixels of the rasterized image after resize.
        image_height: Height in pixels of the rasterized image after resize.
        rasterization_dpi: DPI used when rasterizing the PDF page.
        original_pdf_hash: BLAKE3 hex digest of the raw PDF bytes.
        file_size_zip_bytes: Size in bytes of the original ZIP file.
        file_size_compressed_bytes: Size in bytes of the uploaded zstd stream.
        processing_start: UTC timestamp when processing of this ZIP began.
        pdf_producer: Value of the /Producer key in the PDF metadata, or None.
        pdf_creator: Value of the /Creator key in the PDF metadata, or None.
        watcher_version: Version string of the running watcher service.

    Returns:
        A ``dict[str, str]`` ready to be passed as ``ExtraArgs["Metadata"]``
        to :func:`boto3.s3.upload_fileobj`.
    """
    now: datetime = datetime.now(UTC)
    processing_duration_ms: int = int((now - processing_start).total_seconds() * 1000)
    compression_ratio: float = round(
        file_size_zip_bytes / max(file_size_compressed_bytes, 1), 4
    )
    return {
        "content-hash": content_hash,
        "compression-algorithm": "zstd-9",
        "processed-at": now.isoformat(),
        "original-path": str(source_zip_path.absolute()),
        "source-zip": source_zip_path.name,
        "pdf-pages": str(page_count),
        "image-width": str(image_width),
        "image-height": str(image_height),
        "image-format": "PNG",
        "rasterization-dpi": str(rasterization_dpi),
        "source-pdf-hash": original_pdf_hash,
        "file-size-original-bytes": str(file_size_zip_bytes),
        "file-size-compressed-bytes": str(file_size_compressed_bytes),
        "compression-ratio": str(compression_ratio),
        "processing-duration-ms": str(processing_duration_ms),
        "pdf-producer": pdf_producer or "unknown",
        "pdf-creator": pdf_creator or "unknown",
        "watcher-version": watcher_version,
        "source-intake-key": source_intake_key,
    }


def build_rejection_metadata(
    rejection_reason: str,
    actual_pages: int,
    source_zip_path: Path,
    watcher_version: str,
) -> dict[str, str]:
    """Build metadata for a file routed to the rejected bucket.

    Args:
        rejection_reason: One of ``"invalid-page-count"``, ``"corrupt-pdf"``,
            or ``"zip-read-error"``.
        actual_pages: The actual number of pages found (0 when unreadable).
        source_zip_path: Filesystem path of the source ZIP file.
        watcher_version: Version string of the running watcher service.

    Returns:
        A ``dict[str, str]`` suitable for MINIO metadata.
    """
    return {
        "rejection-reason": rejection_reason,
        "expected-pages": "2",
        "actual-pages": str(actual_pages),
        "rejected-at": datetime.now(UTC).isoformat(),
        "source-zip": source_zip_path.name,
        "original-path": str(source_zip_path.absolute()),
        "watcher-version": watcher_version,
    }


def create_s3_client(
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    use_ssl: bool = False,
) -> Any:
    """Create a boto3 S3 client configured for MINIO.

    Args:
        endpoint_url: HTTP(S) endpoint of the MINIO server.
        access_key: MINIO access key ID.
        secret_key: MINIO secret access key.
        use_ssl: Whether to use HTTPS. Defaults to False for local deployments.

    Returns:
        A configured boto3 S3 client.
    """
    protocol: str = "https" if use_ssl else "http"
    full_endpoint: str = (
        endpoint_url
        if endpoint_url.startswith(("http://", "https://"))
        else f"{protocol}://{endpoint_url}"
    )
    client: Any = boto3.client(
        "s3",
        endpoint_url=full_endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    return client


def upload_image(
    s3_client: Any,
    bucket: str,
    key: str,
    compressed_bytes: bytes,
    metadata: dict[str, str],
) -> None:
    """Upload a pre-compressed PNG image to MINIO.

    Compression must be applied by the caller before invoking this function.
    This ensures the exact compressed size is known before the upload and
    eliminates the two-pass ``copy_object`` workaround.

    Args:
        s3_client: A configured boto3 S3 client.
        bucket: Target MINIO bucket name.
        key: Object key (path within the bucket).
        compressed_bytes: Already-compressed bytes to upload (e.g. zstd).
        metadata: MINIO object metadata; all values must be strings.
    """
    s3_client.upload_fileobj(
        BytesIO(compressed_bytes),
        bucket,
        key,
        ExtraArgs={"Metadata": metadata},
    )
    logger.info(
        "image_uploaded",
        bucket=bucket,
        key=key,
        compressed_bytes=len(compressed_bytes),
    )


def upload_intake(
    s3_client: Any,
    bucket: str,
    key: str,
    zip_bytes: bytes,
    source_zip_path: Path,
    watcher_version: str,
) -> None:
    """Upload the original ZIP to the intake bucket (audit trail).

    The intake copy is required by Repo 3 (bp_ecg_raw_extractor) to retrieve
    the original PDF for text extraction via pdfplumber.

    Args:
        s3_client: A configured boto3 S3 client.
        bucket: Target MINIO intake bucket name.
        key: Object key (path within the bucket).
        zip_bytes: Raw bytes of the original ZIP file.
        source_zip_path: Filesystem path of the source ZIP.
        watcher_version: Version string of the running watcher service.
    """
    metadata: dict[str, str] = {
        "source-zip": source_zip_path.name,
        "original-path": str(source_zip_path.absolute()),
        "ingested-at": datetime.now(UTC).isoformat(),
        "file-size-bytes": str(len(zip_bytes)),
        "watcher-version": watcher_version,
    }
    s3_client.upload_fileobj(
        BytesIO(zip_bytes),
        bucket,
        key,
        ExtraArgs={"Metadata": metadata},
    )
    logger.info("intake_uploaded", bucket=bucket, key=key, size_bytes=len(zip_bytes))


def upload_rejected(
    s3_client: Any,
    bucket: str,
    key: str,
    zip_bytes: bytes,
    rejection_reason: str,
    actual_pages: int,
    source_zip_path: Path,
    watcher_version: str,
) -> None:
    """Upload a ZIP that failed validation to the rejected bucket.

    Args:
        s3_client: A configured boto3 S3 client.
        bucket: Target MINIO rejected bucket name.
        key: Object key (path within the bucket).
        zip_bytes: Raw bytes of the rejected ZIP file.
        rejection_reason: Human-readable reason string for rejection.
        actual_pages: Number of pages found (0 when the PDF is unreadable).
        source_zip_path: Filesystem path of the source ZIP.
        watcher_version: Version string of the running watcher service.
    """
    metadata: dict[str, str] = build_rejection_metadata(
        rejection_reason=rejection_reason,
        actual_pages=actual_pages,
        source_zip_path=source_zip_path,
        watcher_version=watcher_version,
    )
    s3_client.upload_fileobj(
        BytesIO(zip_bytes),
        bucket,
        key,
        ExtraArgs={"Metadata": metadata},
    )
    logger.warning(
        "zip_rejected",
        bucket=bucket,
        key=key,
        rejection_reason=rejection_reason,
        actual_pages=actual_pages,
    )


def upload_dlq(
    s3_client: Any,
    bucket: str,
    source_key: str,
    original_bytes: bytes,
    error_type: str,
    error_message: str,
    stack_trace_str: str,
    attempt_count: int,
    first_attempt_at: datetime,
    last_attempt_at: datetime,
) -> None:
    """Upload a failed file and its error sidecar to the dead-letter queue bucket.

    Two objects are written:

    * ``{source_key}`` — the original file bytes (for reprocessing).
    * ``{source_key}_error.json`` — JSON sidecar describing the failure.

    Args:
        s3_client: A configured boto3 S3 client.
        bucket: Target DLQ bucket name.
        source_key: Object key used for both the original file and the sidecar.
        original_bytes: Raw bytes of the file that failed processing.
        error_type: Exception class name.
        error_message: Short exception message.
        stack_trace_str: Full formatted traceback as a string.
        attempt_count: Number of processing attempts made.
        first_attempt_at: UTC timestamp of the first attempt.
        last_attempt_at: UTC timestamp of the most recent attempt.
    """
    # Upload the original file
    s3_client.upload_fileobj(
        BytesIO(original_bytes),
        bucket,
        source_key,
    )

    # Build and upload the error sidecar
    error_payload: dict[str, object] = {
        "source_key": source_key,
        "error_type": error_type,
        "error_message": error_message,
        "stack_trace": stack_trace_str,
        "attempt_count": attempt_count,
        "first_attempt_at": first_attempt_at.isoformat(),
        "last_attempt_at": last_attempt_at.isoformat(),
    }
    sidecar_key: str = f"{source_key}_error.json"
    sidecar_bytes: bytes = json.dumps(error_payload, indent=2).encode("utf-8")
    s3_client.upload_fileobj(
        BytesIO(sidecar_bytes),
        bucket,
        sidecar_key,
    )
    logger.error(
        "dlq_uploaded",
        bucket=bucket,
        source_key=source_key,
        sidecar_key=sidecar_key,
        error_type=error_type,
        attempt_count=attempt_count,
    )


def format_exc_info(exc: BaseException) -> tuple[str, str, str]:
    """Extract type name, message, and formatted traceback from an exception.

    Args:
        exc: The caught exception.

    Returns:
        A tuple of ``(error_type, error_message, stack_trace_str)``.
    """
    error_type: str = type(exc).__name__
    error_message: str = str(exc)
    stack_trace_str: str = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    return error_type, error_message, stack_trace_str
