"""PDF page rasterization using pypdfium2.

Rasterizes all pages of a PDF stream to a list of PIL Images entirely in
memory. No intermediate files are written to disk.
"""

from __future__ import annotations

from io import BytesIO

import pypdfium2 as pdfium
import structlog
from PIL import Image

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def rasterize_all_pages(pdf_bytes: BytesIO, dpi: int = 300) -> list[Image.Image]:
    """Rasterize every page of a PDF to a list of PIL Images.

    The PDF is read entirely from *pdf_bytes* — no temporary files are created.
    The scale factor converts between PDF points (72 pt/inch) and the desired DPI.

    Args:
        pdf_bytes: In-memory PDF content. Will be seeked to position 0 before use.
        dpi: Target rasterization resolution in dots per inch. Defaults to 300.

    Returns:
        A list of :class:`PIL.Image.Image` objects, one per page, in RGB mode.

    Raises:
        ValueError: If the PDF has no pages.
        RuntimeError: If pypdfium2 fails to render any page.
    """
    pdf_bytes.seek(0)
    doc = pdfium.PdfDocument(pdf_bytes)  # type: ignore[arg-type]

    page_count = len(doc)
    if page_count == 0:
        raise ValueError("PDF has no pages")

    scale: float = dpi / 72.0
    images: list[Image.Image] = []

    for page_index in range(page_count):
        page = doc[page_index]
        bitmap = page.render(scale=scale, rotation=0)
        pil_image: Image.Image = bitmap.to_pil()
        images.append(pil_image)

        logger.debug(
            "page_rasterized",
            page_index=page_index,
            dpi=dpi,
            scale=scale,
            width=pil_image.width,
            height=pil_image.height,
        )

    return images
