from pathlib import Path

import pypdf


def count_pdf_pages(pdf_path: Path) -> int:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    try:
        reader = pypdf.PdfReader(str(pdf_path))
        return len(reader.pages)
    except Exception as exc:
        raise ValueError(f"Invalid PDF file: {pdf_path}") from exc


def is_valid_pdf(pdf_path: Path) -> bool:
    return count_pdf_pages(pdf_path) == 2
