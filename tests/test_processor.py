"""Tests for the processor sub-package of bp_ecg_file_watcher.

Covers:
- image.py  — aspect-ratio-preserving resize
- hasher.py — BLAKE3 hash determinism
- compressor.py — ByteCountingReader byte accounting and context-manager behaviour

pypdfium2 rasterization is mocked with synthetic PIL Images to avoid requiring
a GPU or poppler in CI.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from bp_ecg_watcher.processor.compressor import (
    ByteCountingReader,
    compress_bytes,
    compressed_counting_reader,
)
from bp_ecg_watcher.processor.extractor import rasterize_page2
from bp_ecg_watcher.processor.hasher import hash_bytes
from bp_ecg_watcher.processor.image import image_to_png_bytes, resize_image

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_image(width: int, height: int) -> Image.Image:
    """Create a solid-colour RGB image for testing."""
    return Image.new("RGB", (width, height), color=(128, 200, 50))


# ---------------------------------------------------------------------------
# image.resize_image
# ---------------------------------------------------------------------------


class TestResizeImage:
    """Verify that resize_image honours the max_side_px constraint."""

    def test_portrait_longer_side_capped(self) -> None:
        """Portrait image: height is the longest side; must be capped."""
        img = _make_image(600, 2000)
        _resized, w, h = resize_image(img, max_side_px=1200)
        assert h <= 1200
        assert w <= 1200

    def test_landscape_longer_side_capped(self) -> None:
        """Landscape image: width is the longest side; must be capped."""
        img = _make_image(3000, 800)
        _resized, w, h = resize_image(img, max_side_px=1200)
        assert w <= 1200
        assert h <= 1200

    def test_aspect_ratio_preserved_portrait(self) -> None:
        """Aspect ratio must be preserved within 1-pixel rounding error."""
        original_ratio = 600 / 2000
        img = _make_image(600, 2000)
        _, w, h = resize_image(img, max_side_px=1200)
        resized_ratio = w / h
        assert abs(resized_ratio - original_ratio) < 0.01

    def test_aspect_ratio_preserved_landscape(self) -> None:
        """Landscape aspect ratio preserved within rounding."""
        original_ratio = 3000 / 800
        img = _make_image(3000, 800)
        _, w, h = resize_image(img, max_side_px=1200)
        resized_ratio = w / h
        assert abs(resized_ratio - original_ratio) < 0.01

    def test_small_image_unchanged(self) -> None:
        """An image already within the limit must not be altered."""
        img = _make_image(400, 300)
        _, w, h = resize_image(img, max_side_px=1200)
        assert w == 400
        assert h == 300

    def test_exact_max_side_unchanged(self) -> None:
        """An image exactly at the limit must not be altered."""
        img = _make_image(1200, 800)
        _, w, h = resize_image(img, max_side_px=1200)
        assert w == 1200
        assert h == 800

    def test_returns_tuple_of_three(self) -> None:
        """Return value must be a 3-tuple."""
        img = _make_image(500, 500)
        result = resize_image(img, max_side_px=1200)
        assert len(result) == 3

    def test_longest_side_exactly_max(self) -> None:
        """After resize, the longest side should equal max_side_px."""
        img = _make_image(2400, 1600)
        _, w, h = resize_image(img, max_side_px=1200)
        assert max(w, h) == 1200

    def test_square_image_resized_correctly(self) -> None:
        """Square images must be resized to max_side_px × max_side_px."""
        img = _make_image(2000, 2000)
        _, w, h = resize_image(img, max_side_px=1200)
        assert w == 1200
        assert h == 1200

    def test_width_and_height_match_returned_image(self) -> None:
        """Returned w, h must match the actual image dimensions."""
        img = _make_image(3000, 1500)
        resized_img, w, h = resize_image(img, max_side_px=1200)
        assert resized_img.width == w
        assert resized_img.height == h


# ---------------------------------------------------------------------------
# image.image_to_png_bytes
# ---------------------------------------------------------------------------


class TestImageToPngBytes:
    """Verify that image_to_png_bytes returns valid PNG-encoded bytes."""

    def test_returns_bytes(self) -> None:
        img = _make_image(100, 100)
        result = image_to_png_bytes(img)
        assert isinstance(result, bytes)

    def test_png_magic_bytes(self) -> None:
        img = _make_image(100, 100)
        result = image_to_png_bytes(img)
        # PNG files always start with the 8-byte PNG signature
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_non_empty(self) -> None:
        img = _make_image(50, 50)
        result = image_to_png_bytes(img)
        assert len(result) > 0

    def test_round_trip(self) -> None:
        """Bytes can be decoded back to the same image size."""
        img = _make_image(100, 100)
        data = image_to_png_bytes(img)
        decoded = Image.open(BytesIO(data))
        assert decoded.width == 100
        assert decoded.height == 100


# ---------------------------------------------------------------------------
# hasher.hash_bytes
# ---------------------------------------------------------------------------


class TestHashBytes:
    """BLAKE3 hash must be deterministic and hex-encoded."""

    def test_deterministic_same_bytes(self) -> None:
        data = b"hello bp_ecg"
        assert hash_bytes(data) == hash_bytes(data)

    def test_different_bytes_different_hash(self) -> None:
        assert hash_bytes(b"aaa") != hash_bytes(b"bbb")

    def test_returns_hex_string(self) -> None:
        result = hash_bytes(b"test")
        assert isinstance(result, str)
        int(result, 16)  # must be valid hex — raises ValueError if not

    def test_hash_length_is_64_chars(self) -> None:
        """BLAKE3 produces a 256-bit (32-byte) digest = 64 hex characters."""
        result = hash_bytes(b"test")
        assert len(result) == 64

    def test_empty_bytes_produces_hash(self) -> None:
        result = hash_bytes(b"")
        assert len(result) == 64

    def test_large_payload_is_deterministic(self) -> None:
        data = b"x" * 1_000_000
        assert hash_bytes(data) == hash_bytes(data)


# ---------------------------------------------------------------------------
# compressor.ByteCountingReader
# ---------------------------------------------------------------------------


class TestByteCountingReader:
    """Verify that ByteCountingReader correctly tracks bytes as they are read."""

    def test_counts_read_bytes(self) -> None:
        payload = b"hello world"
        reader = ByteCountingReader(BytesIO(payload))  # type: ignore[arg-type]
        data = reader.read()
        assert data == payload
        assert reader.bytes_read == len(payload)

    def test_incremental_reads_accumulate(self) -> None:
        payload = b"abcdefghij"
        reader = ByteCountingReader(BytesIO(payload))  # type: ignore[arg-type]
        reader.read(4)
        reader.read(3)
        reader.read(100)  # read past end — returns remaining 3
        assert reader.bytes_read == len(payload)

    def test_initial_count_is_zero(self) -> None:
        reader = ByteCountingReader(BytesIO(b"data"))  # type: ignore[arg-type]
        assert reader.bytes_read == 0

    def test_read_zero_bytes(self) -> None:
        reader = ByteCountingReader(BytesIO(b"data"))  # type: ignore[arg-type]
        result = reader.read(0)
        assert result == b""
        assert reader.bytes_read == 0

    def test_empty_stream(self) -> None:
        reader = ByteCountingReader(BytesIO(b""))  # type: ignore[arg-type]
        data = reader.read()
        assert data == b""
        assert reader.bytes_read == 0


# ---------------------------------------------------------------------------
# compressor.compressed_counting_reader — context manager
# ---------------------------------------------------------------------------


class TestCompressedCountingReader:
    """Verify the context-manager compressor pipeline."""

    def test_compressed_output_smaller_for_repeated_data(self) -> None:
        """Highly compressible data should compress to fewer bytes."""
        image_bytes = b"\x00" * 100_000
        with compressed_counting_reader(image_bytes, level=3) as reader:
            chunks: list[bytes] = []
            while True:
                chunk = reader.read(8192)
                if not chunk:
                    break
                chunks.append(chunk)
        total = sum(len(c) for c in chunks)
        assert reader.bytes_read == total
        assert reader.bytes_read < len(image_bytes)

    def test_bytes_read_matches_decompressed_roundtrip(self) -> None:
        """Compressed bytes must decompress back to the original content."""
        import zstandard

        image_bytes = b"ECG data " * 1000
        compressed_parts: list[bytes] = []
        with compressed_counting_reader(image_bytes, level=1) as reader:
            while True:
                chunk = reader.read(8192)
                if not chunk:
                    break
                compressed_parts.append(chunk)
        compressed = b"".join(compressed_parts)
        assert reader.bytes_read == len(compressed)

        # stream_reader produces no content-size header — decompress via stream_reader
        dctx = zstandard.ZstdDecompressor()
        with dctx.stream_reader(BytesIO(compressed)) as r:
            decompressed = r.read()
        assert decompressed == image_bytes

    def test_bytes_read_accessible_after_context_exit(self) -> None:
        """bytes_read must remain accessible after the with block closes."""
        image_bytes = b"some data " * 500
        with compressed_counting_reader(image_bytes, level=1) as reader:
            while reader.read(4096):
                pass
        assert reader.bytes_read > 0


# ---------------------------------------------------------------------------
# compressor.compress_bytes — convenience helper
# ---------------------------------------------------------------------------


class TestCompressBytes:
    """Verify the compress_bytes convenience function."""

    def test_returns_tuple(self) -> None:
        compressed, size = compress_bytes(b"hello", level=1)
        assert isinstance(compressed, bytes)
        assert isinstance(size, int)

    def test_size_matches_len(self) -> None:
        compressed, size = compress_bytes(b"hello world", level=1)
        assert size == len(compressed)

    def test_decompressible(self) -> None:
        import zstandard

        original = b"round trip data"
        compressed, _ = compress_bytes(original, level=3)
        dctx = zstandard.ZstdDecompressor()
        assert dctx.decompress(compressed) == original


# ---------------------------------------------------------------------------
# extractor.rasterize_page2 — mocked pypdfium2
# ---------------------------------------------------------------------------


class TestRasterizePage2:
    """Verify rasterize_page2 calls pypdfium2 correctly with a mocked document."""

    def test_returns_pil_image(self, valid_2page_pdf: Path) -> None:
        """When pypdfium2 is mocked, the function must return the mock PIL image."""
        synthetic_image = _make_image(2480, 3508)  # A4 at 300 DPI

        mock_bitmap = MagicMock()
        mock_bitmap.to_pil.return_value = synthetic_image

        mock_page = MagicMock()
        mock_page.render.return_value = mock_bitmap

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=2)
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)

        with patch("bp_ecg_watcher.processor.extractor.pdfium") as mock_pdfium:
            mock_pdfium.PdfDocument.return_value = mock_doc
            result = rasterize_page2(BytesIO(valid_2page_pdf.read_bytes()), dpi=300)

        assert result is synthetic_image

    def test_render_called_with_correct_scale(self, valid_2page_pdf: Path) -> None:
        """Scale must be dpi / 72."""
        synthetic_image = _make_image(100, 100)

        mock_bitmap = MagicMock()
        mock_bitmap.to_pil.return_value = synthetic_image

        mock_page = MagicMock()
        mock_page.render.return_value = mock_bitmap

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=2)
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)

        with patch("bp_ecg_watcher.processor.extractor.pdfium") as mock_pdfium:
            mock_pdfium.PdfDocument.return_value = mock_doc
            rasterize_page2(BytesIO(valid_2page_pdf.read_bytes()), dpi=150)

        mock_page.render.assert_called_once_with(scale=150 / 72, rotation=0)

    def test_raises_for_single_page_pdf(self) -> None:
        """A document with only 1 page must raise ValueError."""
        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=1)

        with patch("bp_ecg_watcher.processor.extractor.pdfium") as mock_pdfium:
            mock_pdfium.PdfDocument.return_value = mock_doc
            with pytest.raises(ValueError, match="page index"):
                rasterize_page2(BytesIO(b"dummy"), dpi=300)

    def test_page_index_1_accessed(self, valid_2page_pdf: Path) -> None:
        """Must access index 1 (page 2, 0-indexed)."""
        synthetic_image = _make_image(100, 100)

        mock_bitmap = MagicMock()
        mock_bitmap.to_pil.return_value = synthetic_image

        mock_page = MagicMock()
        mock_page.render.return_value = mock_bitmap

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=2)
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)

        with patch("bp_ecg_watcher.processor.extractor.pdfium") as mock_pdfium:
            mock_pdfium.PdfDocument.return_value = mock_doc
            rasterize_page2(BytesIO(valid_2page_pdf.read_bytes()), dpi=72)

        mock_doc.__getitem__.assert_called_once_with(1)
