"""PDF validation logic for the bp_ecg_file_watcher pipeline.

Validates that a PDF stream contains exactly 2 pages. Returns a typed result
carrying either a success value or a rejection reason, so callers never need to
catch exceptions for expected failure modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO

import pypdf
import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

EXPECTED_PAGES: int = 2


class RejectionReason(StrEnum):
    """Reasons a PDF may be rejected during validation."""

    INVALID_PAGE_COUNT = "invalid-page-count"
    CORRUPT_PDF = "corrupt-pdf"
    ZIP_READ_ERROR = "zip-read-error"


@dataclass(frozen=True)
class ValidationSuccess:
    """Returned when the PDF passes all validation checks."""

    page_count: int
    pdf_producer: str | None
    pdf_creator: str | None


@dataclass(frozen=True)
class ValidationFailure:
    """Returned when the PDF fails validation for any reason."""

    reason: RejectionReason
    actual_pages: int
    detail: str


ValidationResult = ValidationSuccess | ValidationFailure


def validate_pdf(pdf_bytes: BytesIO) -> ValidationResult:
    """Validate that *pdf_bytes* contains exactly 2 pages.

    Args:
        pdf_bytes: In-memory PDF content positioned at the start.

    Returns:
        :class:`ValidationSuccess` when the PDF is valid, or
        :class:`ValidationFailure` with an appropriate :class:`RejectionReason`.
    """
    pdf_bytes.seek(0)
    try:
        reader = pypdf.PdfReader(pdf_bytes)
        page_count = len(reader.pages)
    except pypdf.errors.PdfReadError as exc:
        logger.warning("pdf_corrupt", detail=str(exc))
        return ValidationFailure(
            reason=RejectionReason.CORRUPT_PDF,
            actual_pages=0,
            detail=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_read_unexpected_error", detail=str(exc))
        return ValidationFailure(
            reason=RejectionReason.CORRUPT_PDF,
            actual_pages=0,
            detail=str(exc),
        )

    if page_count != EXPECTED_PAGES:
        logger.warning(
            "pdf_invalid_page_count",
            expected=EXPECTED_PAGES,
            actual=page_count,
        )
        return ValidationFailure(
            reason=RejectionReason.INVALID_PAGE_COUNT,
            actual_pages=page_count,
            detail=f"expected {EXPECTED_PAGES} pages, got {page_count}",
        )

    raw_info = reader.metadata
    producer: str | None = None
    creator: str | None = None
    if raw_info is not None:
        prod_val = raw_info.get("/Producer")
        crea_val = raw_info.get("/Creator")
        producer = str(prod_val) if prod_val is not None else None
        creator = str(crea_val) if crea_val is not None else None

    logger.debug(
        "pdf_valid",
        page_count=page_count,
        producer=producer,
        creator=creator,
    )
    return ValidationSuccess(
        page_count=page_count,
        pdf_producer=producer,
        pdf_creator=creator,
    )
