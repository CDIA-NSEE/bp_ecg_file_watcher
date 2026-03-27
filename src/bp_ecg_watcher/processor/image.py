"""Image resizing and PDF encoding utilities for the bp_ecg_file_watcher processor.

Provides aspect-ratio-preserving downscaling of PIL Images and conversion of a
list of resized images into a multi-page PDF byte string ready for zstd compression.
"""

from __future__ import annotations

from io import BytesIO

import structlog
from PIL import Image

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def resize_image(
    image: Image.Image,
    max_side_px: int = 1200,
) -> tuple[Image.Image, int, int]:
    """Downscale *image* so that its longest side does not exceed *max_side_px*.

    Uses :meth:`PIL.Image.Image.thumbnail` which modifies the image in-place and
    guarantees aspect-ratio preservation. Images that are already smaller than
    the limit are returned unchanged.

    Args:
        image: Source PIL Image (any mode accepted).
        max_side_px: Maximum allowed length for the longest side in pixels.

    Returns:
        A tuple of ``(resized_image, width, height)``.
    """
    original_size = (image.width, image.height)
    image.thumbnail((max_side_px, max_side_px), Image.Resampling.LANCZOS)
    new_size = (image.width, image.height)

    if original_size != new_size:
        logger.debug(
            "image_resized",
            original_width=original_size[0],
            original_height=original_size[1],
            new_width=new_size[0],
            new_height=new_size[1],
            max_side_px=max_side_px,
        )
    else:
        logger.debug(
            "image_no_resize_needed",
            width=new_size[0],
            height=new_size[1],
            max_side_px=max_side_px,
        )

    return image, image.width, image.height


def images_to_pdf_bytes(images: list[Image.Image], dpi: int = 300) -> bytes:
    """Encode a list of PIL Images as a multi-page PDF byte string.

    Each image becomes one page in the output PDF. All images are converted to
    RGB mode if necessary (PDF does not support palette or transparency modes
    directly). The *dpi* value is embedded as the image resolution metadata.

    Args:
        images: One or more PIL Images, each becoming a page.
        dpi: Resolution to embed in the PDF. Should match the rasterization DPI.

    Returns:
        Raw PDF-encoded bytes containing one page per image.

    Raises:
        ValueError: If *images* is empty.
    """
    if not images:
        raise ValueError("images must not be empty")

    rgb_images = [
        img.convert("RGB") if img.mode != "RGB" else img for img in images
    ]
    buffer = BytesIO()
    rgb_images[0].save(
        buffer,
        format="PDF",
        resolution=dpi,
        save_all=True,
        append_images=rgb_images[1:],
    )
    return buffer.getvalue()
