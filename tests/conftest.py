"""Shared pytest fixtures for the bp_ecg_file_watcher test suite.

PDF fixtures are generated programmatically via reportlab — binary PDFs are never
committed to the repository because they inflate repo size and break git diff.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]


@pytest.fixture
def valid_2page_pdf(tmp_path: Path) -> Path:
    """Generate a valid 2-page PDF using reportlab."""
    p = tmp_path / "valid.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(100, 750, "Page 1")
    c.showPage()
    c.drawString(100, 750, "Page 2")
    c.showPage()
    c.save()
    return p


@pytest.fixture
def invalid_1page_pdf(tmp_path: Path) -> Path:
    """Generate an invalid 1-page PDF using reportlab."""
    p = tmp_path / "invalid.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(100, 750, "Only page")
    c.showPage()
    c.save()
    return p


@pytest.fixture
def empty_pdf(tmp_path: Path) -> Path:
    """Generate a 0-page PDF using reportlab (canvas saved without showPage)."""
    p = tmp_path / "empty.pdf"
    c = canvas.Canvas(str(p))
    # Do NOT call showPage — canvas will produce a PDF with 0 usable pages
    # reportlab actually produces 1 blank page when saved without showPage,
    # so this tests the 1-page rejection path; a true 0-page is produced below.
    c.save()
    return p


@pytest.fixture
def zero_page_pdf(tmp_path: Path) -> Path:
    """Generate a minimal structurally-valid PDF with 0 pages.

    Crafted manually to bypass reportlab's minimum-1-page constraint.
    """
    # Minimal valid PDF with 0 pages in the page tree
    pdf_content = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
        b"xref\n0 3\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"trailer\n<< /Size 3 /Root 1 0 R >>\n"
        b"startxref\n110\n%%EOF\n"
    )
    p = tmp_path / "zero_pages.pdf"
    p.write_bytes(pdf_content)
    return p


@pytest.fixture
def corrupt_pdf(tmp_path: Path) -> Path:
    """Generate a file with invalid PDF content (not a real PDF)."""
    p = tmp_path / "corrupt.pdf"
    p.write_bytes(b"not a pdf at all --- garbage bytes 0x00 0xFF")
    return p


@pytest.fixture
def valid_2page_pdf_bytes(valid_2page_pdf: Path) -> BytesIO:
    """Return the valid 2-page PDF as a BytesIO stream."""
    return BytesIO(valid_2page_pdf.read_bytes())


@pytest.fixture
def invalid_1page_pdf_bytes(invalid_1page_pdf: Path) -> BytesIO:
    """Return the invalid 1-page PDF as a BytesIO stream."""
    return BytesIO(invalid_1page_pdf.read_bytes())


@pytest.fixture
def corrupt_pdf_bytes(corrupt_pdf: Path) -> BytesIO:
    """Return corrupt bytes as a BytesIO stream."""
    return BytesIO(corrupt_pdf.read_bytes())
