"""Document ingestion: read files, split into chunks, hash provenance.

Supports plain text/markdown and PDF (via the existing pypdf helper). Corpus
layout mirrors the trading_agent layout: knowledge/{sebi,nse,bse,broker,
strategies,risk,internal_policies}/**. Sub-paths become the ``title`` of each
RagDoc so provenance shows which category a chunk came from.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from ..pdfutil import pdf_to_text

TEXT_SUFFIXES = (".md", ".txt", ".markdown", ".rst")
PDF_SUFFIXES = (".pdf",)
SUPPORTED_SUFFIXES = TEXT_SUFFIXES + PDF_SUFFIXES
CHUNK_SIZE = 600
CHUNK_OVERLAP = 120


@dataclass
class RagDoc:
    path: str
    title: str
    chunks: list[str] = field(default_factory=list)
    sha: str = ""


def read_document(path: Path) -> str:
    if path.suffix.lower() in PDF_SUFFIXES:
        return pdf_to_text(path)
    return path.read_text(encoding="utf-8", errors="replace")


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into chunks on paragraph/heading boundaries where possible."""
    text = text.strip()
    if not text:
        return []
    blocks = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current = ""
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(current) + len(block) <= size:
            current = f"{current}\n{block}".strip()
            continue
        if current:
            chunks.append(current)
            current = block
        while len(block) > size:
            chunks.append(block[:size])
            block = block[size:]
        current = block
    if current:
        chunks.append(current)
    return chunks


def index_files(corpus: Path) -> list[RagDoc]:
    """Read all supported files under the corpus directory into chunked docs."""
    docs: list[RagDoc] = []
    if not corpus.exists():
        return docs
    for p in sorted(corpus.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        if p.name.startswith("."):
            continue
        try:
            raw = read_document(p)
        except Exception:  # noqa: BLE001
            continue
        chunks = chunk_text(raw)
        if not chunks:
            continue
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        rel = p.relative_to(corpus).as_posix()
        docs.append(RagDoc(path=str(p), title=rel, chunks=chunks, sha=sha))
    return docs


def default_corpus_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "knowledge"


def default_index_file() -> Path:
    return DATA_DIR / "rag_index.json"
