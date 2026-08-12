"""Retrieval: in-memory index + hybrid (BM25 + embedding) ranking + persistence.

RagIndex owns the corpus documents, document-frequency table, chunk statistics
and per-doc hashes (for drift detection), and exposes ``query()`` returning the
top-k relevant chunks with source provenance. Persisted as JSON under DATA_DIR
so it survives restarts.

Enrichment over plain BM25 (the RAG-layer feature list):
- Embeddings: local hashed-vector embeddings give cosine similarity.
- Hybrid search: BM25 + embedding cosine are fused with configurable weight.
- Re-ranking: candidates are re-scored by query-chunk similarity to fix order.
- Metadata filter: chunks carry a ``section`` (top corpus subdir) and can be
  filtered by it at query time.
- Data drift: ``drift()`` compares the on-disk corpus hashes with the index to
  report added/changed/removed documents.
- Knowledge graph: ``graph()`` extracts a co-occurrence graph of salient terms.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .embeddings import bm25, cosine, embed, tokenise
from .ingest import RagDoc, index_files


class RagIndex:
    """TF-IDF/BM25 + embedding hybrid index over chunked documents, persisted to JSON."""

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

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def section_of(title: str) -> str:
        """Metadata: the top corpus subdir (e.g. 'sebi', 'risk', 'strategies')."""
        return title.split("/")[0].split("\\")[0] if title else ""

    @staticmethod
    def _idf(df: dict[str, int], total: int) -> dict[str, float]:
        import math

        return {t: math.log(1 + total / (c + 0.5)) for t, c in df.items()}

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

    def query(self, question: str, top_k: int = 5,
              section: str | None = None,
              hybrid: bool = True,
              bm25_weight: float = 0.6,
              min_score: float = 0.0) -> list[dict[str, Any]]:
        """Return top-k chunks ranked by hybrid (BM25 + embedding) relevance.

        ``section`` filters chunks by their metadata section (corpus subdir).
        ``hybrid=False`` reverts to pure BM25. ``bm25_weight`` blends the two
        scores (0.6 BM25 + 0.4 embedding cosine by default). Results are sorted
        by their reported hybrid ``score`` descending; chunks scoring below
        ``min_score`` are dropped.
        """
        self._load()
        if not self.total_chunks:
            return []
        q_terms = tokenise(question)
        if not q_terms:
            return []
        idf = self._idf(self.df, self.total_chunks)
        q_vec = embed(question, idf)
        scored: list[tuple[float, RagDoc, str, str]] = []  # (score, doc, chunk, section)
        for d in self.docs:
            for chunk in d.chunks:
                if section and self.section_of(d.title) != section:
                    continue
                b = bm25(chunk, q_terms, self.df, self.total_chunks, self.avg_chunk_len)
                if hybrid:
                    c = cosine(q_vec, embed(chunk, idf))
                    s = bm25_weight * b + (1 - bm25_weight) * c
                else:
                    s = b
                if s > 0:
                    scored.append((s, d, chunk, self.section_of(d.title)))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Re-rank: keep the top pool, re-score by pure query-chunk embedding
        # similarity so ordering reflects semantic closeness, not just length.
        pool = scored[: max(top_k * 3, top_k)]
        pool.sort(key=lambda x: cosine(q_vec, embed(x[2], idf)), reverse=True)
        results = []
        for _s, d, chunk, sec in pool[:top_k]:
            b = bm25(chunk, q_terms, self.df, self.total_chunks, self.avg_chunk_len)
            sim = cosine(q_vec, embed(chunk, idf))
            if hybrid:
                s = bm25_weight * b + (1 - bm25_weight) * sim
            else:
                s = b
            if s < min_score:
                continue
            results.append({
                "source": d.title,
                "path": d.path,
                "section": sec,
                "score": round(s, 4),
                "bm25": round(b, 4),
                "sim": round(sim, 4),
                "text": chunk,
            })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    # ------------------------------------------------------------------ drift

    def drift(self) -> dict[str, Any]:
        """Compare the on-disk corpus with the persisted index (data-drift check).

        Returns added/changed/removed document titles and a freshness verdict.
        """
        self._load()
        on_disk = index_files(self.corpus_dir)
        disk = {d.title: d.sha for d in on_disk}
        indexed = {d.title: d.sha for d in self.docs}
        added = sorted(set(disk) - set(indexed))
        removed = sorted(set(indexed) - set(disk))
        changed = sorted(t for t in set(disk) & set(indexed) if disk[t] != indexed[t])
        fresh = not (added or changed or removed)
        return {
            "fresh": fresh,
            "added": added,
            "changed": changed,
            "removed": removed,
            "total_on_disk": len(disk),
            "total_indexed": len(indexed),
            "verdict": "FRESH" if fresh else "STALE",
        }

    # ------------------------------------------------------------------ knowledge graph

    def graph(self, max_terms: int = 200, top_edges: int = 30) -> dict[str, Any]:
        """Salient-term co-occurrence graph over the corpus (knowledge-graph-lite).

        Nodes are the highest-IDF terms; an edge links two terms that co-occur
        in a chunk. Useful for query expansion and topic clustering.
        """
        self._load()
        if not self.docs:
            return {"nodes": [], "edges": [], "note": "empty index"}
        import math

        idf = self._idf(self.df, self.total_chunks)
        salient = {t for t, _ in sorted(idf.items(), key=lambda kv: kv[1], reverse=True)[:max_terms]}
        # prune stopword-ish terms
        salient = {t for t in salient if len(t) > 2}
        adjacency: dict[str, set[str]] = {}
        for d in self.docs:
            for chunk in d.chunks:
                terms = set(tokenise(chunk)) & salient
                tl = sorted(terms)
                for i, a in enumerate(tl):
                    adjacency.setdefault(a, set())
                    for b in tl[i + 1:]:
                        adjacency[a].add(b)
                        adjacency.setdefault(b, set()).add(a)
        nodes = [{"term": t, "degree": len(adjacency.get(t, ())), "idf": round(idf.get(t, 0.0), 3)}
                 for t in salient]
        nodes.sort(key=lambda n: n["degree"], reverse=True)
        edges: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for a, bs in adjacency.items():
            for b in bs:
                if (a, b) in seen or (b, a) in seen:
                    continue
                seen.add((a, b))
                edges.append({"source": a, "target": b})
        edges.sort(key=lambda e: len(adjacency.get(e["source"], ())) +
                   len(adjacency.get(e["target"], ())), reverse=True)
        return {"nodes": nodes[:max_terms], "edges": edges[:top_edges],
                "note": "term co-occurrence graph (deterministic, offline)"}

    # ------------------------------------------------------------------ stats

    def stats(self) -> dict[str, Any]:
        self._load()
        n_docs = len(self.docs)
        n_chunks = sum(len(d.chunks) for d in self.docs)
        sections: dict[str, int] = {}
        for d in self.docs:
            s = self.section_of(d.title)
            sections[s] = sections.get(s, 0) + 1
        return {
            "docs": n_docs,
            "chunks": n_chunks,
            "terms": len(self.df),
            "sections": sections,
            "corpus_dir": str(self.corpus_dir),
            "index_file": str(self.index_file),
            "indexed": self.index_file.exists(),
        }
