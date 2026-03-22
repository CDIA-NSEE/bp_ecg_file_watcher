"""Tests for the PDF validation logic in bp_ecg_watcher.validator.pdf_validator.

Uses real pypdf — nothing is mocked here. PDF fixtures are generated programmatically
via reportlab in conftest.py so no binary files are committed.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from bp_ecg_watcher.validator.pdf_validator import (
    EXPECTED_PAGES,
    RejectionReason,
    ValidationFailure,
    ValidationSuccess,
    validate_pdf,
)


class TestValidPDF:
    """A properly formed 2-page PDF must return ValidationSuccess."""

    def test_returns_success(self, valid_2page_pdf_bytes: BytesIO) -> None:
        result = validate_pdf(valid_2page_pdf_bytes)
        assert isinstance(result, ValidationSuccess)

    def test_page_count_is_two(self, valid_2page_pdf_bytes: BytesIO) -> None:
        result = validate_pdf(valid_2page_pdf_bytes)
        assert isinstance(result, ValidationSuccess)
        assert result.page_count == EXPECTED_PAGES

    def test_producer_and_creator_are_strings_or_none(
        self, valid_2page_pdf_bytes: BytesIO
    ) -> None:
        result = validate_pdf(valid_2page_pdf_bytes)
        assert isinstance(result, ValidationSuccess)
        assert result.pdf_producer is None or isinstance(result.pdf_producer, str)
        assert result.pdf_creator is None or isinstance(result.pdf_creator, str)

    def test_rewind_idempotent(self, valid_2page_pdf_bytes: BytesIO) -> None:
        """validate_pdf must work even when stream is not at position 0."""
        valid_2page_pdf_bytes.seek(50)  # deliberately mis-positioned
        result = validate_pdf(valid_2page_pdf_bytes)
        assert isinstance(result, ValidationSuccess)


class TestOnePage:
    """A 1-page PDF must be rejected with INVALID_PAGE_COUNT."""

    def test_returns_failure(self, invalid_1page_pdf_bytes: BytesIO) -> None:
        result = validate_pdf(invalid_1page_pdf_bytes)
        assert isinstance(result, ValidationFailure)

    def test_rejection_reason(self, invalid_1page_pdf_bytes: BytesIO) -> None:
        result = validate_pdf(invalid_1page_pdf_bytes)
        assert isinstance(result, ValidationFailure)
        assert result.reason == RejectionReason.INVALID_PAGE_COUNT

    def test_actual_pages_is_one(self, invalid_1page_pdf_bytes: BytesIO) -> None:
        result = validate_pdf(invalid_1page_pdf_bytes)
        assert isinstance(result, ValidationFailure)
        assert result.actual_pages == 1


class TestEmptyPDF:
    """A PDF produced by reportlab without explicit showPage has 1 page (not 0)."""

    def test_returns_failure_for_empty_pdf(self, empty_pdf: Path) -> None:
        """reportlab's default save() still produces 1 page — must be rejected."""
        result = validate_pdf(BytesIO(empty_pdf.read_bytes()))
        assert isinstance(result, ValidationFailure)
        assert result.reason == RejectionReason.INVALID_PAGE_COUNT


class TestZeroPagePDF:
    """A structurally valid PDF with 0 pages must be rejected."""

    def test_returns_failure(self, zero_page_pdf: Path) -> None:
        result = validate_pdf(BytesIO(zero_page_pdf.read_bytes()))
        assert isinstance(result, ValidationFailure)

    def test_rejection_reason_is_invalid_page_count(self, zero_page_pdf: Path) -> None:
        result = validate_pdf(BytesIO(zero_page_pdf.read_bytes()))
        assert isinstance(result, ValidationFailure)
        assert result.reason == RejectionReason.INVALID_PAGE_COUNT

    def test_actual_pages_is_zero(self, zero_page_pdf: Path) -> None:
        result = validate_pdf(BytesIO(zero_page_pdf.read_bytes()))
        assert isinstance(result, ValidationFailure)
        assert result.actual_pages == 0


class TestCorruptPDF:
    """Corrupt bytes must produce ValidationFailure without raising an exception."""

    def test_returns_failure_not_exception(self, corrupt_pdf_bytes: BytesIO) -> None:
        result = validate_pdf(corrupt_pdf_bytes)
        assert isinstance(result, ValidationFailure)

    def test_reason_is_corrupt(self, corrupt_pdf_bytes: BytesIO) -> None:
        result = validate_pdf(corrupt_pdf_bytes)
        assert isinstance(result, ValidationFailure)
        assert result.reason == RejectionReason.CORRUPT_PDF

    def test_actual_pages_is_zero(self, corrupt_pdf_bytes: BytesIO) -> None:
        result = validate_pdf(corrupt_pdf_bytes)
        assert isinstance(result, ValidationFailure)
        assert result.actual_pages == 0

    def test_detail_is_non_empty_string(self, corrupt_pdf_bytes: BytesIO) -> None:
        result = validate_pdf(corrupt_pdf_bytes)
        assert isinstance(result, ValidationFailure)
        assert isinstance(result.detail, str)
        assert len(result.detail) > 0

    def test_truncated_pdf_does_not_raise(self, tmp_path: Path) -> None:
        """Truncated PDF header should not crash the validator."""
        truncated = BytesIO(b"%PDF-1.4\n")
        result = validate_pdf(truncated)
        assert isinstance(result, ValidationFailure)
