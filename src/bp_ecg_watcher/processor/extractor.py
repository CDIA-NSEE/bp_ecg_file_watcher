"""PDF page rasterization using pypdfium2.

Extracts page 2 (0-indexed: index 1) from a PDF stream and rasterizes it to a
PIL Image entirely in memory. No intermediate files are written to disk.
"""

from __future__ import annotations

from io import BytesIO

import pypdfium2 as pdfium
import structlog
from PIL import Image

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# Page index of the ECG/BP result page (0-indexed)
TARGET_PAGE_INDEX: int = 1


def rasterize_page2(pdf_bytes: BytesIO, dpi: int = 300) -> Image.Image:
    """Rasterize page 2 of a PDF to a PIL Image.

    The PDF is read entirely from *pdf_bytes* — no temporary files are created.
    The scale factor converts between PDF points (72 pt/inch) and the desired DPI.

    Args:
        pdf_bytes: In-memory PDF content. Will be seeked to position 0 before use.
        dpi: Target rasterization resolution in dots per inch. Defaults to 300.

    Returns:
        A :class:`PIL.Image.Image` in RGB mode.

    Raises:
        ValueError: If the PDF has fewer than 2 pages.
        RuntimeError: If pypdfium2 fails to render the page.
    """
    pdf_bytes.seek(0)
    doc = pdfium.PdfDocument(pdf_bytes)  # type: ignore[arg-type]

    page_count = len(doc)
    if page_count < TARGET_PAGE_INDEX + 1:
        raise ValueError(
            f"PDF has {page_count} page(s); "
            f"cannot access page index {TARGET_PAGE_INDEX}"
        )

    scale: float = dpi / 72.0
    page = doc[TARGET_PAGE_INDEX]
    bitmap = page.render(scale=scale, rotation=0)
    pil_image: Image.Image = bitmap.to_pil()

    logger.debug(
        "page_rasterized",
        dpi=dpi,
        scale=scale,
        width=pil_image.width,
        height=pil_image.height,
    )
    return pil_image
