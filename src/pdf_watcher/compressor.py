from pathlib import Path

import zstandard

CHUNK_SIZE = 64 * 1024  # 64 KB


def compress_zstd(source: Path, dest_dir: Path, level: int = 9) -> Path:
    dest_path = dest_dir / (source.name + ".zst")
    compressor = zstandard.ZstdCompressor(level=level)

    with (
        source.open("rb") as src_file,
        dest_path.open("wb") as dst_file,
        compressor.stream_writer(dst_file) as writer,
    ):
        while True:
            chunk = src_file.read(CHUNK_SIZE)
            if not chunk:
                break
            writer.write(chunk)

    return dest_path
