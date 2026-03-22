"""Image resizing utilities for the bp_ecg_file_watcher processor.

Provides aspect-ratio-preserving downscaling of PIL Images using Pillow's
thumbnail() method, which guarantees that neither dimension exceeds the specified
maximum while maintaining the original proportions.
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
    image.thumbnail((max_side_px, max_side_px), Image.LANCZOS)
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


def image_to_png_bytes(image: Image.Image) -> bytes:
    """Encode a PIL Image as a PNG byte string.

    Args:
        image: Source PIL Image.

    Returns:
        Raw PNG-encoded bytes.
    """
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
