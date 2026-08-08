"""PDF text extraction for resume/JD scoring and document reading.

Uses pypdf (optional dependency, pure Python). Falls back to a clear error message
when pypdf is not installed.
"""
from __future__ import annotations

from pathlib import Path

from .tools import ToolError


def pdf_to_text(path: str | Path) -> str:
    """Extract all text from a PDF file. Returns '' if the PDF has no extractable text."""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        raise ToolError("pypdf not installed. Run: pip install pypdf") from exc

    p = Path(path)
    if not p.is_file():
        raise ToolError(f"file not found: {path}")
    try:
        reader = PdfReader(str(p))
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"could not open PDF: {exc}") from exc

    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            pages.append("")
    text = "\n".join(pages).strip()
    if not text:
        raise ToolError(
            f"no text could be extracted from {path} (it may be a scanned image; OCR is not enabled)"
        )
    return text
