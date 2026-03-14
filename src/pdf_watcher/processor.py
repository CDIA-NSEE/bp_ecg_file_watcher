import shutil
import tempfile
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from uuid import uuid4

import structlog

from pdf_watcher.compressor import compress_zstd
from pdf_watcher.config import Settings
from pdf_watcher.pdf_validator import count_pdf_pages

log = structlog.get_logger(__name__)


def process_file(
    path: Path,
    settings: Settings,
    process_pool: ProcessPoolExecutor | None,
) -> None:
    tmp_dir = Path(tempfile.gettempdir()) / "pdf_watcher" / uuid4().hex

    try:
        tmp_dir.mkdir(parents=True)

        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(tmp_dir)

        pdf_files = list(tmp_dir.rglob("*.pdf"))

        if not pdf_files:
            log.warning("no_pdf_found", path=str(path))
            shutil.move(str(path), settings.rejected_dir / path.name)
            return

        pdf_path = pdf_files[0]

        count = count_pdf_pages(pdf_path)

        if count == 2:
            if process_pool is not None:
                future = process_pool.submit(compress_zstd, pdf_path, settings.output_dir)
                future.result()
            else:
                compress_zstd(pdf_path, settings.output_dir)
            log.info("file_accepted", path=str(path), pdf=str(pdf_path), page_count=count)
        else:
            shutil.move(str(path), settings.rejected_dir / path.name)
            log.info("file_rejected", path=str(path), pdf=str(pdf_path), page_count=count)

    except Exception:
        log.error("processing_error", path=str(path), exc_info=True)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
