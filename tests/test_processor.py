"""Tests for the processor sub-package of bp_ecg_file_watcher.

Covers:
- hasher.py — BLAKE3 hash determinism
- compressor.py — ByteCountingReader byte accounting and context-manager behaviour
- image.py — resize_image and images_to_pdf_bytes
- extractor.py — rasterize_all_pages
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from bp_ecg_watcher.processor.compressor import (
    ByteCountingReader,
    compress_bytes,
    compressed_counting_reader,
)
from bp_ecg_watcher.processor.extractor import rasterize_all_pages
from bp_ecg_watcher.processor.hasher import hash_bytes
from bp_ecg_watcher.processor.image import images_to_pdf_bytes, resize_image

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
# Helpers shared by image tests
# ---------------------------------------------------------------------------


def _make_image(width: int, height: int, mode: str = "RGB") -> Image.Image:
    """Return a plain solid-colour PIL image of the given dimensions."""
    return Image.new(mode, (width, height), color=(200, 200, 200))


def _make_two_page_pdf_bytes() -> BytesIO:
    """Build a minimal 2-page PDF using reportlab and return it as BytesIO."""
    from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "Page 1")
    c.showPage()
    c.drawString(100, 750, "Page 2")
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# image.resize_image
# ---------------------------------------------------------------------------


class TestResizeImage:
    """Verify resize_image downscales oversized images correctly."""

    def test_large_image_is_downscaled(self) -> None:
        img = _make_image(2000, 1500)
        resized, w, h = resize_image(img, max_side_px=1200)
        assert max(w, h) <= 1200

    def test_returned_dimensions_match_image(self) -> None:
        img = _make_image(800, 600)
        resized, w, h = resize_image(img, max_side_px=1200)
        assert resized.size == (w, h)

    def test_small_image_not_upscaled(self) -> None:
        img = _make_image(400, 300)
        _, w, h = resize_image(img, max_side_px=1200)
        assert w == 400
        assert h == 300

    def test_returns_ints(self) -> None:
        img = _make_image(500, 400)
        _, w, h = resize_image(img, max_side_px=1200)
        assert isinstance(w, int)
        assert isinstance(h, int)

    def test_square_image_stays_square(self) -> None:
        img = _make_image(2400, 2400)
        _, w, h = resize_image(img, max_side_px=1200)
        assert w == h == 1200


# ---------------------------------------------------------------------------
# image.images_to_pdf_bytes
# ---------------------------------------------------------------------------


class TestImagesToPdfBytes:
    """Verify images_to_pdf_bytes produces a valid multi-page PDF."""

    def test_returns_bytes(self) -> None:
        result = images_to_pdf_bytes([_make_image(100, 100)])
        assert isinstance(result, bytes)

    def test_pdf_magic_bytes(self) -> None:
        result = images_to_pdf_bytes([_make_image(100, 100)])
        assert result[:4] == b"%PDF"

    def test_non_empty(self) -> None:
        result = images_to_pdf_bytes([_make_image(50, 50)])
        assert len(result) > 0

    def test_single_image_produces_one_page(self) -> None:
        import pypdf

        data = images_to_pdf_bytes([_make_image(100, 80)])
        reader = pypdf.PdfReader(BytesIO(data))
        assert len(reader.pages) == 1

    def test_two_images_produce_two_pages(self) -> None:
        """Two input images must produce a 2-page PDF."""
        import pypdf

        data = images_to_pdf_bytes([_make_image(100, 80), _make_image(100, 80)])
        reader = pypdf.PdfReader(BytesIO(data))
        assert len(reader.pages) == 2

    def test_non_rgb_images_converted(self) -> None:
        """Non-RGB images (e.g. RGBA) must not raise an error."""
        imgs = [_make_image(100, 100, mode="RGBA"), _make_image(80, 80, mode="RGBA")]
        result = images_to_pdf_bytes(imgs)
        assert result[:4] == b"%PDF"

    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            images_to_pdf_bytes([])


# ---------------------------------------------------------------------------
# extractor.rasterize_all_pages
# ---------------------------------------------------------------------------


class TestRasterizeAllPages:
    """Verify rasterize_all_pages extracts and rasterizes all PDF pages."""

    def test_returns_list_of_pil_images(self) -> None:
        pdf = _make_two_page_pdf_bytes()
        result = rasterize_all_pages(pdf, dpi=72)
        assert isinstance(result, list)
        assert all(isinstance(img, Image.Image) for img in result)

    def test_two_page_pdf_returns_two_images(self) -> None:
        pdf = _make_two_page_pdf_bytes()
        result = rasterize_all_pages(pdf, dpi=72)
        assert len(result) == 2

    def test_each_image_has_nonzero_size(self) -> None:
        pdf = _make_two_page_pdf_bytes()
        for img in rasterize_all_pages(pdf, dpi=72):
            assert img.width > 0
            assert img.height > 0

    def test_higher_dpi_produces_larger_images(self) -> None:
        pdf72 = _make_two_page_pdf_bytes()
        pdf150 = _make_two_page_pdf_bytes()
        imgs72 = rasterize_all_pages(pdf72, dpi=72)
        imgs150 = rasterize_all_pages(pdf150, dpi=150)
        assert imgs150[0].width > imgs72[0].width
