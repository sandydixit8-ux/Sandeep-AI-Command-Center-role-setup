"""Knowledge retrieval (RAG) engine for the AI Command Center.

A dependency-free local retrieval layer, organised to mirror the trading_agent
layout:

- ``ingest.py``      reading + chunking documents (text/PDF), corpus scanning
- ``embeddings.py``  lexical scoring primitives (TF / BM25)
- ``retriever.py``   the persisted index + ranking + query API
- ``citations.py``   renders retrieved chunks with numbered source citations

Public API (stable): ``rag_query``, ``rag_index``, ``rag_status``,
``get_index``, ``RagIndex``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .citations import citation_index, render_citations
from .retriever import RagIndex

_index = RagIndex()


def get_index() -> RagIndex:
    return _index


def rag_query(question: str, top_k: int = 5, section: str | None = None,
              hybrid: bool = True) -> dict[str, Any]:
    """Retrieve the most relevant knowledge chunks for a question.

    Supports optional ``section`` metadata filtering (e.g. 'sebi', 'risk') and
    hybrid BM25+embedding retrieval. Returns raw results plus a rendered citation
    block (``context``) ready to be injected into the agent prompt, and a compact
    citation map (``cites``).
    """
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    idx = get_index()
    if not idx.total_chunks:
        stats = idx.build()
        if not stats["chunks"]:
            return {
                "status": "empty",
                "note": f"no documents indexed under {idx.corpus_dir}",
                "results": [],
                "context": "",
                "cites": [],
            }
    results = idx.query(question, top_k, section=section, hybrid=hybrid)
    return {
        "status": "ok",
        "question": question,
        "section": section,
        "hybrid": hybrid,
        "results": results,
        "sources": sorted({r["source"] for r in results}),
        "sections": sorted({r["section"] for r in results}),
        "context": render_citations(results),
        "cites": citation_index(results),
    }


def rag_drift() -> dict[str, Any]:
    """Data-drift check: corpus vs index (added/changed/removed documents)."""
    return {"status": "ok", "drift": get_index().drift()}


def rag_index(path: str | None = None) -> dict[str, Any]:
    """Rebuild the knowledge index (optionally pointing at a different corpus dir)."""
    if path:
        p = Path(path)
        if not p.exists() or not p.is_dir():
            raise ValueError(f"corpus dir not found: {path}")
        get_index().corpus_dir = p
    stats = get_index().build()
    return {"status": "ok", "index": stats}


def rag_graph(max_terms: int = 200, top_edges: int = 30) -> dict[str, Any]:
    """Knowledge-graph-lite: salient term co-occurrence over the corpus."""
    return {"status": "ok", "graph": get_index().graph(max_terms=max_terms, top_edges=top_edges)}


def rag_status() -> dict[str, Any]:
    return {"status": "ok", "index": get_index().stats()}


__all__ = ["rag_query", "rag_index", "rag_status", "rag_drift", "rag_graph",
           "get_index", "RagIndex", "render_citations", "citation_index"]
