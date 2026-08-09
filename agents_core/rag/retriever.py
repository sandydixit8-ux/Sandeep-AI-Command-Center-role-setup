"""Retrieval: the in-memory index + BM25 ranking + persistence.

RagIndex owns the corpus documents, document-frequency table and chunk
statistics, and exposes query() returning the top-k relevant chunks with
source provenance. Persisted as JSON under DATA_DIR so it survives restarts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .embeddings import bm25, tokenise
from .ingest import RagDoc, index_files


class RagIndex:
    """TF-IDF/BM25 index over chunked documents, persisted to JSON."""

    def __init__(self, corpus_dir: str | Path = "", index_file: str | Path = "") -> None:
        from .ingest import default_corpus_dir, default_index_file

        self.corpus_dir = Path(corpus_dir) if corpus_dir else default_corpus_dir()
        self.index_file = Path(index_file) if index_file else default_index_file()
        self.docs: list[RagDoc] = []
        self.df: dict[str, int] = {}
        self.total_chunks = 0
        self.avg_chunk_len = 1.0
        self._loaded = False

    # ------------------------------------------------------------------ persistence

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.index_file.exists():
            return
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
            self.docs = [RagDoc(**d) for d in data.get("docs", [])]
            self.df = data.get("df", {})
            self.total_chunks = data.get("total_chunks", 0)
            self.avg_chunk_len = data.get("avg_chunk_len", 1.0)
        except Exception:  # noqa: BLE001
            self.docs, self.df = [], {}
            self.total_chunks = 0

    def _save(self) -> None:
        try:
            self.index_file.parent.mkdir(parents=True, exist_ok=True)
            self.index_file.write_text(
                json.dumps({
                    "docs": [{"path": d.path, "title": d.title, "chunks": d.chunks, "sha": d.sha}
                             for d in self.docs],
                    "df": self.df,
                    "total_chunks": self.total_chunks,
                    "avg_chunk_len": self.avg_chunk_len,
                }, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ build

    def build(self) -> dict[str, Any]:
        """Rebuild the index from the corpus directory."""
        self.docs = index_files(self.corpus_dir)
        term_counts: dict[str, int] = {}
        chunk_lens: list[int] = []
        for d in self.docs:
            for chunk in d.chunks:
                terms = set(tokenise(chunk))
                for t in terms:
                    term_counts[t] = term_counts.get(t, 0) + 1
                chunk_lens.append(len(tokenise(chunk)))
        self.df = term_counts
        self.total_chunks = len(chunk_lens)
        self.avg_chunk_len = (sum(chunk_lens) / len(chunk_lens)) if chunk_lens else 1.0
        self._save()
        return self.stats()

    # ------------------------------------------------------------------ query

    def query(self, question: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Return top-k chunks ranked by BM25 relevance for a question."""
        self._load()
        if not self.total_chunks:
            return []
        q_terms = tokenise(question)
        if not q_terms:
            return []
        scored: list[tuple[float, RagDoc, str]] = []
        for d in self.docs:
            for chunk in d.chunks:
                s = bm25(chunk, q_terms, self.df, self.total_chunks, self.avg_chunk_len)
                if s > 0:
                    scored.append((s, d, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for s, d, chunk in scored[:top_k]:
            results.append({
                "source": d.title,
                "path": d.path,
                "score": round(s, 4),
                "text": chunk,
            })
        return results

    def stats(self) -> dict[str, Any]:
        self._load()
        n_docs = len(self.docs)
        n_chunks = sum(len(d.chunks) for d in self.docs)
        return {
            "docs": n_docs,
            "chunks": n_chunks,
            "terms": len(self.df),
            "corpus_dir": str(self.corpus_dir),
            "index_file": str(self.index_file),
            "indexed": self.index_file.exists(),
        }
