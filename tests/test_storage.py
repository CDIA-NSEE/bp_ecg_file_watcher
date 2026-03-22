"""Tests for storage/minio_client.py.

Uses moto to mock the S3/MINIO API so that no real network calls are made.
Verifies:
- build_metadata() produces only string values and includes all required keys.
- upload_image() routes to the correct bucket.
- upload_rejected() routes to the rejected bucket with the right metadata.
- upload_intake() stores the ZIP in the intake bucket.
- upload_dlq() writes both the original file and the _error.json sidecar.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from bp_ecg_watcher.storage.minio_client import (
    build_metadata,
    build_rejection_metadata,
    create_s3_client,
    format_exc_info,
    upload_dlq,
    upload_image,
    upload_intake,
    upload_rejected,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

BUCKET_IMAGES = "bp-ecg-dev-images"
BUCKET_REJECTED = "bp-ecg-dev-rejected"
BUCKET_INTAKE = "bp-ecg-dev-intake"
BUCKET_DLQ = "bp-ecg-dev-dlq"

_ALL_BUCKETS = [BUCKET_IMAGES, BUCKET_REJECTED, BUCKET_INTAKE, BUCKET_DLQ]

_REQUIRED_METADATA_KEYS = {
    "content-hash",
    "compression-algorithm",
    "processed-at",
    "original-path",
    "source-zip",
    "pdf-pages",
    "image-width",
    "image-height",
    "image-format",
    "rasterization-dpi",
    "source-pdf-hash",
    "file-size-original-bytes",
    "file-size-compressed-bytes",
    "compression-ratio",
    "processing-duration-ms",
    "pdf-producer",
    "pdf-creator",
    "watcher-version",
    "source-intake-key",
}


@pytest.fixture()
def aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub AWS credentials so moto intercepts all boto3 calls."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture()
def s3(aws_credentials: None) -> Any:
    """Provide a moto-mocked S3 client with all required buckets pre-created."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        for bucket in _ALL_BUCKETS:
            client.create_bucket(Bucket=bucket)
        yield client


@pytest.fixture()
def source_zip(tmp_path: Path) -> Path:
    """A dummy source ZIP path (does not need to exist on disk for metadata tests)."""
    return tmp_path / "test_exam_001.zip"


@pytest.fixture()
def processing_start() -> datetime:
    """A fixed processing start time."""
    return datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)


@pytest.fixture()
def sample_png_bytes() -> bytes:
    """Minimal valid PNG bytes (1×1 white pixel)."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
        b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
        b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


# ---------------------------------------------------------------------------
# build_metadata tests
# ---------------------------------------------------------------------------


class TestBuildMetadata:
    """Unit tests for build_metadata()."""

    def test_all_required_keys_present(
        self, source_zip: Path, processing_start: datetime
    ) -> None:
        """Every key in _REQUIRED_METADATA_KEYS must be present."""
        meta = build_metadata(
            content_hash="abc123",
            source_zip_path=source_zip,
            page_count=2,
            image_width=1200,
            image_height=800,
            rasterization_dpi=300,
            original_pdf_hash="pdf456",
            file_size_zip_bytes=50000,
            file_size_compressed_bytes=12000,
            processing_start=processing_start,
            pdf_producer="Adobe",
            pdf_creator="Writer",
            watcher_version="0.1.0",
        )
        assert _REQUIRED_METADATA_KEYS.issubset(meta.keys())

    def test_all_values_are_strings(
        self, source_zip: Path, processing_start: datetime
    ) -> None:
        """Every value in the returned dict must be a str."""
        meta = build_metadata(
            content_hash="abc123",
            source_zip_path=source_zip,
            page_count=2,
            image_width=1200,
            image_height=800,
            rasterization_dpi=300,
            original_pdf_hash="pdf456",
            file_size_zip_bytes=50000,
            file_size_compressed_bytes=12000,
            processing_start=processing_start,
            pdf_producer=None,
            pdf_creator=None,
            watcher_version="0.1.0",
        )
        for key, value in meta.items():
            assert isinstance(value, str), (
                f"Key '{key}' has non-string value: {value!r}"
            )

    def test_none_producer_and_creator_become_unknown(
        self, source_zip: Path, processing_start: datetime
    ) -> None:
        """None pdf_producer/pdf_creator must appear as 'unknown'."""
        meta = build_metadata(
            content_hash="x",
            source_zip_path=source_zip,
            page_count=2,
            image_width=100,
            image_height=100,
            rasterization_dpi=300,
            original_pdf_hash="y",
            file_size_zip_bytes=1000,
            file_size_compressed_bytes=500,
            processing_start=processing_start,
            pdf_producer=None,
            pdf_creator=None,
            watcher_version="0.1.0",
        )
        assert meta["pdf-producer"] == "unknown"
        assert meta["pdf-creator"] == "unknown"

    def test_compression_algorithm_is_zstd_9(
        self, source_zip: Path, processing_start: datetime
    ) -> None:
        """compression-algorithm must always be 'zstd-9'."""
        meta = build_metadata(
            content_hash="h",
            source_zip_path=source_zip,
            page_count=2,
            image_width=100,
            image_height=100,
            rasterization_dpi=300,
            original_pdf_hash="p",
            file_size_zip_bytes=1000,
            file_size_compressed_bytes=500,
            processing_start=processing_start,
            pdf_producer="p",
            pdf_creator="c",
            watcher_version="0.1.0",
        )
        assert meta["compression-algorithm"] == "zstd-9"

    def test_image_format_is_png(
        self, source_zip: Path, processing_start: datetime
    ) -> None:
        """image-format must always be 'PNG'."""
        meta = build_metadata(
            content_hash="h",
            source_zip_path=source_zip,
            page_count=2,
            image_width=100,
            image_height=100,
            rasterization_dpi=300,
            original_pdf_hash="p",
            file_size_zip_bytes=1000,
            file_size_compressed_bytes=500,
            processing_start=processing_start,
            pdf_producer="p",
            pdf_creator="c",
            watcher_version="0.1.0",
        )
        assert meta["image-format"] == "PNG"


# ---------------------------------------------------------------------------
# build_rejection_metadata tests
# ---------------------------------------------------------------------------


class TestBuildRejectionMetadata:
    """Unit tests for build_rejection_metadata()."""

    def test_all_values_are_strings(self, source_zip: Path) -> None:
        """All values in rejection metadata must be strings."""
        meta = build_rejection_metadata(
            rejection_reason="invalid-page-count",
            actual_pages=1,
            source_zip_path=source_zip,
            watcher_version="0.1.0",
        )
        for key, value in meta.items():
            assert isinstance(value, str), f"Key '{key}' is not a string"

    def test_expected_pages_always_two(self, source_zip: Path) -> None:
        """expected-pages must always be '2'."""
        meta = build_rejection_metadata(
            rejection_reason="corrupt-pdf",
            actual_pages=0,
            source_zip_path=source_zip,
            watcher_version="0.1.0",
        )
        assert meta["expected-pages"] == "2"


# ---------------------------------------------------------------------------
# upload_image tests
# ---------------------------------------------------------------------------


class TestUploadImage:
    """Integration tests for upload_image() using moto."""

    def test_object_in_images_bucket(
        self,
        s3: Any,
        source_zip: Path,
        processing_start: datetime,
        sample_png_bytes: bytes,
    ) -> None:
        """Uploaded object must appear in the images bucket."""
        import zstandard

        compressed = zstandard.ZstdCompressor(level=9).compress(sample_png_bytes)
        meta = build_metadata(
            content_hash="testhash",
            source_zip_path=source_zip,
            page_count=2,
            image_width=100,
            image_height=100,
            rasterization_dpi=300,
            original_pdf_hash="pdfhash",
            file_size_zip_bytes=5000,
            file_size_compressed_bytes=len(compressed),
            processing_start=processing_start,
            pdf_producer=None,
            pdf_creator=None,
            watcher_version="0.1.0",
        )
        upload_image(
            s3_client=s3,
            bucket=BUCKET_IMAGES,
            key="2024/01/01/testhash.png.zst",
            compressed_bytes=compressed,
            metadata=meta,
        )
        # Verify the object exists
        resp = s3.head_object(Bucket=BUCKET_IMAGES, Key="2024/01/01/testhash.png.zst")
        assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_stored_metadata_all_strings(
        self,
        s3: Any,
        source_zip: Path,
        processing_start: datetime,
        sample_png_bytes: bytes,
    ) -> None:
        """Metadata stored in S3 must all be string values."""
        import zstandard

        compressed = zstandard.ZstdCompressor(level=9).compress(sample_png_bytes)
        meta = build_metadata(
            content_hash="h2",
            source_zip_path=source_zip,
            page_count=2,
            image_width=100,
            image_height=100,
            rasterization_dpi=300,
            original_pdf_hash="p2",
            file_size_zip_bytes=1000,
            file_size_compressed_bytes=len(compressed),
            processing_start=processing_start,
            pdf_producer="Adobe",
            pdf_creator="Writer",
            watcher_version="0.1.0",
        )
        upload_image(
            s3_client=s3,
            bucket=BUCKET_IMAGES,
            key="2024/01/01/h2.png.zst",
            compressed_bytes=compressed,
            metadata=meta,
        )
        resp = s3.head_object(Bucket=BUCKET_IMAGES, Key="2024/01/01/h2.png.zst")
        stored_meta: dict[str, str] = resp["Metadata"]
        for key, value in stored_meta.items():
            assert isinstance(value, str), f"Key '{key}' is not a string"


# ---------------------------------------------------------------------------
# upload_rejected tests
# ---------------------------------------------------------------------------


class TestUploadRejected:
    """Integration tests for upload_rejected() using moto."""

    def test_object_in_rejected_bucket(self, s3: Any, source_zip: Path) -> None:
        """Rejected ZIP must land in the rejected bucket, not images."""
        zip_bytes = b"PK\x03\x04" + b"\x00" * 100
        upload_rejected(
            s3_client=s3,
            bucket=BUCKET_REJECTED,
            key="2024/01/01/bad.zip",
            zip_bytes=zip_bytes,
            rejection_reason="invalid-page-count",
            actual_pages=1,
            source_zip_path=source_zip,
            watcher_version="0.1.0",
        )
        resp = s3.head_object(Bucket=BUCKET_REJECTED, Key="2024/01/01/bad.zip")
        assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200
        # Verify not in images bucket
        objects = s3.list_objects_v2(Bucket=BUCKET_IMAGES).get("Contents", [])
        assert not objects

    def test_rejection_reason_in_metadata(self, s3: Any, source_zip: Path) -> None:
        """rejection-reason metadata key must be set correctly."""
        zip_bytes = b"data"
        upload_rejected(
            s3_client=s3,
            bucket=BUCKET_REJECTED,
            key="2024/01/01/corrupt.zip",
            zip_bytes=zip_bytes,
            rejection_reason="corrupt-pdf",
            actual_pages=0,
            source_zip_path=source_zip,
            watcher_version="0.1.0",
        )
        resp = s3.head_object(Bucket=BUCKET_REJECTED, Key="2024/01/01/corrupt.zip")
        assert resp["Metadata"]["rejection-reason"] == "corrupt-pdf"

    def test_all_rejection_metadata_strings(self, s3: Any, source_zip: Path) -> None:
        """All metadata values on rejected objects must be strings."""
        upload_rejected(
            s3_client=s3,
            bucket=BUCKET_REJECTED,
            key="2024/01/01/str_test.zip",
            zip_bytes=b"x",
            rejection_reason="zip-read-error",
            actual_pages=0,
            source_zip_path=source_zip,
            watcher_version="0.1.0",
        )
        resp = s3.head_object(Bucket=BUCKET_REJECTED, Key="2024/01/01/str_test.zip")
        for key, value in resp["Metadata"].items():
            assert isinstance(value, str), f"Metadata key '{key}' is not a string"


# ---------------------------------------------------------------------------
# upload_intake tests
# ---------------------------------------------------------------------------


class TestUploadIntake:
    """Integration tests for upload_intake() using moto."""

    def test_object_in_intake_bucket(self, s3: Any, source_zip: Path) -> None:
        """Original ZIP must be stored in the intake bucket."""
        zip_bytes = b"original zip content"
        upload_intake(
            s3_client=s3,
            bucket=BUCKET_INTAKE,
            key="2024/01/01/original.zip",
            zip_bytes=zip_bytes,
            source_zip_path=source_zip,
            watcher_version="0.1.0",
        )
        resp = s3.get_object(Bucket=BUCKET_INTAKE, Key="2024/01/01/original.zip")
        assert resp["Body"].read() == zip_bytes

    def test_intake_metadata_all_strings(self, s3: Any, source_zip: Path) -> None:
        """Intake metadata values must all be strings."""
        upload_intake(
            s3_client=s3,
            bucket=BUCKET_INTAKE,
            key="2024/01/01/meta_test.zip",
            zip_bytes=b"bytes",
            source_zip_path=source_zip,
            watcher_version="0.1.0",
        )
        resp = s3.head_object(Bucket=BUCKET_INTAKE, Key="2024/01/01/meta_test.zip")
        for key, value in resp["Metadata"].items():
            assert isinstance(value, str), f"Metadata key '{key}' is not a string"


# ---------------------------------------------------------------------------
# upload_dlq tests
# ---------------------------------------------------------------------------


class TestUploadDlq:
    """Integration tests for upload_dlq() using moto."""

    def test_original_file_and_sidecar_uploaded(self, s3: Any) -> None:
        """Both the original file and the _error.json sidecar must be present."""
        first = datetime(2024, 1, 1, tzinfo=UTC)
        last = datetime(2024, 1, 2, tzinfo=UTC)
        upload_dlq(
            s3_client=s3,
            bucket=BUCKET_DLQ,
            source_key="2024/01/01/failed.zip",
            original_bytes=b"zip content",
            error_type="ValueError",
            error_message="Something broke",
            stack_trace_str="Traceback...",
            attempt_count=3,
            first_attempt_at=first,
            last_attempt_at=last,
        )
        # Original file
        resp = s3.get_object(Bucket=BUCKET_DLQ, Key="2024/01/01/failed.zip")
        assert resp["Body"].read() == b"zip content"
        # Sidecar
        sidecar_resp = s3.get_object(
            Bucket=BUCKET_DLQ, Key="2024/01/01/failed.zip_error.json"
        )
        sidecar = json.loads(sidecar_resp["Body"].read())
        assert sidecar["error_type"] == "ValueError"
        assert sidecar["error_message"] == "Something broke"
        assert sidecar["attempt_count"] == 3

    def test_sidecar_json_structure(self, s3: Any) -> None:
        """DLQ sidecar must contain all expected fields."""
        first = datetime(2024, 3, 1, tzinfo=UTC)
        last = datetime(2024, 3, 1, tzinfo=UTC)
        upload_dlq(
            s3_client=s3,
            bucket=BUCKET_DLQ,
            source_key="2024/03/01/struct_test.zip",
            original_bytes=b"data",
            error_type="RuntimeError",
            error_message="boom",
            stack_trace_str="tb",
            attempt_count=3,
            first_attempt_at=first,
            last_attempt_at=last,
        )
        resp = s3.get_object(
            Bucket=BUCKET_DLQ, Key="2024/03/01/struct_test.zip_error.json"
        )
        sidecar = json.loads(resp["Body"].read())
        required_fields = {
            "source_key",
            "error_type",
            "error_message",
            "stack_trace",
            "attempt_count",
            "first_attempt_at",
            "last_attempt_at",
        }
        assert required_fields.issubset(sidecar.keys())


# ---------------------------------------------------------------------------
# format_exc_info tests
# ---------------------------------------------------------------------------


class TestFormatExcInfo:
    """Unit tests for format_exc_info()."""

    def test_returns_three_strings(self) -> None:
        """Must return a 3-tuple of strings."""
        try:
            raise ValueError("test error")
        except ValueError as exc:
            error_type, error_message, stack_trace = format_exc_info(exc)

        assert isinstance(error_type, str)
        assert isinstance(error_message, str)
        assert isinstance(stack_trace, str)

    def test_error_type_is_class_name(self) -> None:
        """error_type must be the exception class name."""
        try:
            raise TypeError("bad type")
        except TypeError as exc:
            error_type, _, _ = format_exc_info(exc)

        assert error_type == "TypeError"

    def test_stack_trace_contains_traceback(self) -> None:
        """stack_trace string must contain 'Traceback'."""
        try:
            raise RuntimeError("trace test")
        except RuntimeError as exc:
            _, _, stack_trace = format_exc_info(exc)

        assert "Traceback" in stack_trace


# ---------------------------------------------------------------------------
# create_s3_client tests
# ---------------------------------------------------------------------------


class TestCreateS3Client:
    """Unit tests for create_s3_client()."""

    def test_client_created(self, aws_credentials: None) -> None:
        """create_s3_client must return a boto3 S3 client without raising."""
        with mock_aws():
            client = create_s3_client(
                endpoint_url="http://localhost:9000",
                access_key="minioadmin",
                secret_key="minioadmin",
                use_ssl=False,
            )
            # The client object must have an upload_fileobj method
            assert hasattr(client, "upload_fileobj")

    def test_https_prefix_added(self, aws_credentials: None) -> None:
        """When use_ssl=True and endpoint lacks scheme, 'https://' must be prepended."""
        with mock_aws():
            client = create_s3_client(
                endpoint_url="minio.example.com:9000",
                access_key="key",
                secret_key="secret",
                use_ssl=True,
            )
            assert client.meta.endpoint_url.startswith("https://")  # type: ignore[attr-defined]
