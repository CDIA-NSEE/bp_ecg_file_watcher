"""Local filesystem writer for processed PDFs."""

from __future__ import annotations

from pathlib import Path


def write_processed(
    compressed_bytes: bytes,
    output_dir: Path,
    content_hash: str,
) -> Path:
    """Write zstd-compressed PDF bytes to the output directory.

    Places the file at ``output_dir/{content_hash}.pdf.zst``.
    The output directory is created automatically if it does not exist.

    Args:
        compressed_bytes: zstd-compressed PDF bytes to write.
        output_dir: Root output directory.
        content_hash: BLAKE3 hex digest used as the filename stem.

    Returns:
        Absolute path of the written file.
    """
    dest: Path = output_dir / f"{content_hash}.pdf.zst"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(compressed_bytes)
    return dest
